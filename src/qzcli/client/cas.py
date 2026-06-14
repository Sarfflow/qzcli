"""CAS → Keycloak login chain.

Walks the browser login flow to obtain a ``qz.sii.edu.cn`` session cookie:

1. GET qz.sii.edu.cn → 302 to Keycloak.
2. Scrape the CAS broker URL out of the Keycloak page (it is in a JS object,
   so we regex for ``"loginUrl": "...broker/cas/login..."`` and unescape ``\\/``).
3. GET that broker URL → land on the cas.sii.edu.cn login page.
4. Scrape the hidden ``lt`` and ``execution`` fields.
5. POST the login form (password RSA-encrypted) and **manually walk the
   redirect chain** back to qz.sii.edu.cn.

The manual redirect walk is the load-bearing detail (2026-05-19 cookie-renewal
incident): if requests follows redirects automatically and any hop returns a
transient 4xx, it stops there and we silently keep the *pre-login* cookie.
Walking each Location ourselves means a glitch fails loudly instead.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import requests

from ..errors import QzError
from .crypto import encrypt_password

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
)

_CAS_BROKER_RE = re.compile(r'"loginUrl":\s*"([^"]*broker/cas/login[^"]*)"')
_LT_RE = re.compile(r'name="lt"\s+value="([^"]+)"')
_EXECUTION_RE = re.compile(r'name="execution"\s+value="([^"]+)"')


def _has_session_cookie(names) -> bool:
    """Any session-like cookie present (name may drift, e.g. inspire-session)."""
    return any("session" in name.lower() for name in names)


def _make_session(proxy: str) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": _BROWSER_UA,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        }
    )
    if proxy:
        # trust_env=False so an http_proxy env var can't override an explicit
        # socks5 proxy. socks5h is normalised to socks5 (PySocks handles both).
        session.trust_env = False
        proxy_url = proxy.replace("socks5h://", "socks5://")
        session.proxies = {"http": proxy_url, "https": proxy_url}
    return session


def _collect_qz_cookies(session: requests.Session) -> dict[str, str]:
    out: dict[str, str] = {}
    for cookie in session.cookies:
        if "qz.sii.edu.cn" in (cookie.domain or ""):
            out[cookie.name] = cookie.value
    return out


def login_with_cas(base_url: str, username: str, password: str, proxy: str = "") -> str:
    """Perform the CAS login and return the ``k=v; k=v`` cookie string.

    Raises :class:`QzError` with an actionable message/hint on every failure
    mode (bad credentials, captcha required, network, missing session cookie).
    """
    session = _make_session(proxy)

    # Step 1: hit the platform, trigger the OAuth flow.
    try:
        resp = session.get(base_url, timeout=30, allow_redirects=True)
    except requests.RequestException as e:
        raise QzError(
            f"无法连接到启智平台: {e}",
            code="network_error",
            hint="检查网络/代理 (QZCLI 走 config.json 的 proxy 或 all_proxy 环境变量)",
        )

    current_url = resp.url
    current_host = urlparse(current_url).netloc

    # Already logged in (landed back on qz with a session cookie).
    if current_host == "qz.sii.edu.cn":
        cookies = _collect_qz_cookies(session)
        if _has_session_cookie(cookies):
            return "; ".join(f"{k}={v}" for k, v in cookies.items())

    # Step 2: from Keycloak, find and follow the CAS broker URL.
    if "keycloak" in current_url:
        match = _CAS_BROKER_RE.search(resp.text)
        if not match:
            raise QzError(
                "Keycloak 页面中未找到 CAS 登录链接",
                code="login_flow_changed",
                hint="登录流程可能已变更，请在浏览器登录后手动导出 cookie",
            )
        cas_broker_url = match.group(1).replace("\\/", "/")
        if not cas_broker_url.startswith("http"):
            parsed = urlparse(current_url)
            cas_broker_url = f"{parsed.scheme}://{parsed.netloc}{cas_broker_url}"
        try:
            resp = session.get(cas_broker_url, timeout=30, allow_redirects=True)
            current_url = resp.url
        except requests.RequestException as e:
            raise QzError(f"跳转 CAS 失败: {e}", code="network_error")

    # Step 3: must be on the CAS login page now.
    if "cas.sii.edu.cn" not in current_url:
        raise QzError(
            f"未能到达 CAS 登录页面，当前 URL: {current_url}",
            code="login_flow_changed",
        )

    cas_login_url = current_url
    login_page_html = resp.text

    encrypted_password = encrypt_password(password)
    login_data = {
        "username": username,
        "password": encrypted_password,
        "_eventId": "submit",
        "submit": "登 录",
        "loginType": "1",
        "encrypted": "true",
    }
    lt_match = _LT_RE.search(login_page_html)
    execution_match = _EXECUTION_RE.search(login_page_html)
    if lt_match:
        login_data["lt"] = lt_match.group(1)
    if execution_match:
        login_data["execution"] = execution_match.group(1)

    login_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://cas.sii.edu.cn",
        "Referer": cas_login_url,
    }

    # Step 4: submit the form, do NOT auto-follow redirects.
    try:
        resp = session.post(
            cas_login_url,
            data=login_data,
            headers=login_headers,
            timeout=30,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise QzError(f"登录请求失败: {e}", code="network_error")

    # Explicit credential / captcha errors on the first response.
    if resp.status_code == 200:
        if "用户名或密码错误" in resp.text or "账号或密码错误" in resp.text:
            raise QzError(
                "用户名或密码错误",
                code="bad_credentials",
                hint="确认 -u/-p 或 QZCLI_USERNAME/QZCLI_PASSWORD 是否正确",
            )
        if "验证码" in resp.text:
            raise QzError(
                "需要输入验证码",
                code="captcha_required",
                hint="请在浏览器登录后手动导出 cookie (qzcli login --cookie '...')",
            )

    # Step 5: walk the redirect chain ourselves (max 10 hops).
    next_url = resp.headers.get("Location")
    prev_url = cas_login_url
    hops = 0
    while next_url and hops < 10:
        hops += 1
        if not next_url.startswith("http"):
            next_url = urljoin(prev_url, next_url)
        try:
            resp = session.get(next_url, timeout=30, allow_redirects=False)
        except requests.RequestException as e:
            raise QzError(f"跟随登录重定向失败: {e}", code="network_error")
        prev_url = next_url
        if 300 <= resp.status_code < 400 and resp.headers.get("Location"):
            next_url = resp.headers["Location"]
            continue
        break
    current_url = prev_url

    # Still stuck on the CAS login page → credentials/captcha.
    if "cas.sii.edu.cn" in current_url and "login" in current_url:
        if "用户名或密码错误" in resp.text or "账号或密码错误" in resp.text:
            raise QzError("用户名或密码错误", code="bad_credentials")
        if "验证码" in resp.text:
            raise QzError(
                "需要输入验证码",
                code="captcha_required",
                hint="请在浏览器登录后手动导出 cookie",
            )
        raise QzError("登录失败，请检查用户名和密码", code="bad_credentials")

    # Step 6: make sure we end up back on qz with a fresh session cookie.
    if urlparse(current_url).netloc != "qz.sii.edu.cn":
        try:
            session.get(base_url, timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            raise QzError(f"获取 session 失败: {e}", code="network_error")

    cookies = _collect_qz_cookies(session)
    if not _has_session_cookie(cookies):
        # One more bounce off the base URL before giving up.
        try:
            session.get(base_url, timeout=30, allow_redirects=True)
            cookies = _collect_qz_cookies(session)
        except requests.RequestException:
            pass

    if not _has_session_cookie(cookies):
        raise QzError(
            "登录成功但未获取到 session cookie",
            code="no_session_cookie",
            hint="登录流程可能已变更，请在浏览器登录后手动导出 cookie",
        )

    return "; ".join(f"{k}={v}" for k, v in cookies.items())
