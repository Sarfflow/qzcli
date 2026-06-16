"""The single egress point for every ``/api/v1/*`` request.

All cookie-authed calls go through :meth:`Client.post_api`, which:
  - attaches the browser-style headers the APISIX gateway requires (without
    them ``/api/v1`` redirects to Keycloak and returns HTML, not JSON);
  - parses the ``{"code", "message", "data"}`` envelope into either ``data`` or
    a :class:`QzError` with the platform's message;
  - on a 401 (and only once), transparently re-logs-in with the stored
    credentials and retries. Set ``QZCLI_NO_AUTO_RELOGIN=1`` to disable.

There is intentionally no ``/openapi/`` path here — the platform's web API is
the only surface we use.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import requests

from .. import __version__, config
from ..errors import QzError
from . import cas

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)
# /api/v2 needs this header or APISIX 302s to Keycloak even with a valid cookie.
_V2_CLIENT_SOURCE = f"qzcli/{__version__}"


class Client:
    """Cookie-authenticated client for the 启智 web API."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        cookie: Optional[str] = None,
        proxy: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        self.base_url = (base_url or config.get_api_base_url()).rstrip("/")
        self.proxy = proxy if proxy is not None else config.get_proxy()
        self._cookie = cookie
        self._username = username
        self._password = password
        self._session = requests.Session()
        if self.proxy:
            self._session.trust_env = False
            proxy_url = self.proxy.replace("socks5h://", "socks5://")
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    # --- construction helpers --------------------------------------------

    @classmethod
    def from_config(cls) -> "Client":
        """Build a client from saved cookie + credentials + config."""
        cookie_data = config.get_cookie()
        cookie = cookie_data.get("cookie") if cookie_data else None
        username, password = config.get_credentials()
        return cls(cookie=cookie, username=username, password=password)

    # --- auth -------------------------------------------------------------

    @property
    def cookie(self) -> Optional[str]:
        return self._cookie

    def require_cookie(self) -> str:
        if not self._cookie:
            raise QzError(
                "未登录：本地没有有效 cookie",
                code="auth_required",
                hint="先运行: qzcli login",
            )
        return self._cookie

    def login(self, username: str, password: str, *, persist: bool = True) -> str:
        """Run the CAS login, update this client, and (by default) persist."""
        cookie = cas.login_with_cas(self.base_url, username, password, self.proxy)
        self._cookie = cookie
        self._username, self._password = username, password
        if persist:
            existing = config.get_cookie() or {}
            config.save_cookie(cookie, existing.get("workspace_id", ""))
            config.save_credentials(username, password)
        return cookie

    def _relogin(self) -> bool:
        """Re-login with stored credentials. Returns True on success."""
        if os.environ.get("QZCLI_NO_AUTO_RELOGIN") == "1":
            return False
        if not (self._username and self._password):
            return False
        try:
            self.login(self._username, self._password, persist=True)
            return True
        except QzError:
            return False

    # --- requests ---------------------------------------------------------

    def _headers(self, referer: Optional[str]) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "cache-control": "no-cache",
            "content-type": "application/json",
            "cookie": self.require_cookie(),
            "origin": self.base_url,
            "pragma": "no-cache",
            "referer": referer or f"{self.base_url}/operations/projects",
            "sec-ch-ua": '"Not(A:Brand";v="8", "Chromium";v="144"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"macOS"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": _BROWSER_UA,
        }

    def post_api(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        referer: Optional[str] = None,
        timeout: int = 60,
        _retried: bool = False,
    ) -> Any:
        """POST to ``/api/v1/<path>`` and return the ``data`` field.

        Raises :class:`QzError` on transport, HTTP, JSON or envelope errors.
        """
        url = f"{self.base_url}/api/v1/{path.lstrip('/')}"
        try:
            resp = self._session.post(
                url, json=payload, headers=self._headers(referer), timeout=timeout
            )
        except requests.RequestException as e:
            raise QzError(
                f"请求失败: {e}",
                code="network_error",
                hint="检查网络/代理设置",
            )

        if resp.status_code == 401:
            if not _retried and self._relogin():
                return self.post_api(
                    path, payload, referer=referer, timeout=timeout, _retried=True
                )
            raise QzError(
                "Cookie 已过期或无效",
                code="auth_expired",
                hint="重新登录: qzcli login",
                http_status=401,
            )

        if resp.status_code != 200:
            raise QzError(
                f"HTTP {resp.status_code}: {resp.text[:200]}",
                code="http_error",
                http_status=resp.status_code,
                hint="确认 endpoint 仍有效；接口可能已变更",
            )

        try:
            result = resp.json()
        except ValueError:
            raise QzError(
                "响应不是有效 JSON（可能被网关重定向到登录页）",
                code="bad_response",
                hint="cookie 可能失效，尝试 qzcli login",
            )

        if not isinstance(result, dict):
            return result

        code = result.get("code")
        if code not in (0, None):
            raise QzError(
                f"API 返回错误: {result.get('message', '未知错误')}",
                code="api_error",
                hint=f"平台错误码 {code}",
            )
        return result.get("data", result)

    def resolve_lab_url(
        self, notebook_id: str, *, timeout: int = 30, _retried: bool = False
    ) -> str:
        """Resolve a running notebook's JupyterLab URL (with auth token).

        ``GET /api/v1/notebook/lab/{id}/`` is a page-level proxy route: the qz
        server mints a per-session token and 302s to the notebook gateway
        ``nat2-notebook-inspire.sii.edu.cn/{ws}/{project}/{user}/jupyter/{id}/
        {token}/lab?token={token}``. We walk the redirects (through the proxy,
        same as every other qz call) and stop at the gateway hop *without*
        fetching it, returning that URL. Both the base and the token are
        derived from it (see :func:`endpoints.resolve_jupyter`).

        The route needs a *fresh* keycloak-backed session — a stale cookie that
        still works for ``/api/v2`` will 401 here; we relogin once and retry.
        """
        cur = f"{self.base_url}/api/v1/notebook/lab/{notebook_id}/"
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "en-US,en;q=0.9",
            "user-agent": _BROWSER_UA,
        }
        for _ in range(8):
            try:
                resp = self._session.get(
                    cur,
                    headers={**headers, "cookie": self.require_cookie()},
                    allow_redirects=False,
                    timeout=timeout,
                )
            except requests.RequestException as e:
                raise QzError(f"请求失败: {e}", code="network_error", hint="检查网络/代理设置")

            if resp.status_code == 401:
                if not _retried and self._relogin():
                    return self.resolve_lab_url(
                        notebook_id, timeout=timeout, _retried=True
                    )
                raise QzError(
                    "Cookie 已过期或无效", code="auth_expired",
                    hint="重新登录: qzcli login", http_status=401,
                )
            if resp.status_code in (301, 302, 303, 307, 308):
                nxt = requests.compat.urljoin(cur, resp.headers.get("location", ""))
                if "/jupyter/" in nxt:
                    return nxt
                cur = nxt
                continue
            raise QzError(
                f"无法解析 notebook 的 JupyterLab 地址 (HTTP {resp.status_code})",
                code="invalid_notebook_state",
                hint="notebook 可能未在运行；先 qzcli nb get <id> 确认 status=RUNNING",
                http_status=resp.status_code,
            )
        raise QzError("解析 JupyterLab 地址时重定向过多", code="bad_response")

    def post_v2(
        self,
        service: str,
        action: str,
        body: dict[str, Any],
        *,
        timeout: int = 60,
        _retried: bool = False,
    ) -> Any:
        """POST to ``/api/v2/{service}?Action={action}`` (cookie-authed).

        The v2 surface returns business fields directly (no ``{code,data}``
        envelope) and requires the ``x-inspire-client-source`` header. This is
        still the reverse-engineered web API — not ``/openapi/`` — so it is
        fair game under the "no OpenAPI" rule.
        """
        url = f"{self.base_url}/api/v2/{service}"
        headers = {
            "accept": "application/json, text/plain, */*",
            "content-type": "application/json",
            "cookie": self.require_cookie(),
            "origin": self.base_url,
            "referer": f"{self.base_url}/jobs",
            "user-agent": _BROWSER_UA,
            "x-inspire-client-source": _V2_CLIENT_SOURCE,
        }
        try:
            resp = self._session.post(
                url, params={"Action": action}, json=body,
                headers=headers, timeout=timeout,
            )
        except requests.RequestException as e:
            raise QzError(f"请求失败: {e}", code="network_error", hint="检查网络/代理设置")

        if resp.status_code == 401:
            if not _retried and self._relogin():
                return self.post_v2(service, action, body, timeout=timeout, _retried=True)
            raise QzError(
                "Cookie 已过期或无效", code="auth_expired",
                hint="重新登录: qzcli login", http_status=401,
            )

        ctype = resp.headers.get("Content-Type", "")
        if "application/json" not in ctype:
            raise QzError(
                f"v2 API 返回非 JSON (HTTP {resp.status_code}, content-type={ctype})",
                code="bad_response",
                hint="通常是认证失败/网关拒绝/无该工作空间权限；试试 qzcli login",
                http_status=resp.status_code,
            )
        try:
            result = resp.json()
        except ValueError:
            raise QzError("v2 API 响应不是合法 JSON", code="bad_response")
        if resp.status_code >= 400:
            raise QzError(
                f"v2 API 请求失败: {result}", code="http_error",
                http_status=resp.status_code,
            )
        return result
