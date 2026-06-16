"""Interactive-modeling (notebook) helpers.

启智's "interactive modeling" is a *notebook* in the API (``server_type:
NOTEBOOK``). A notebook is a long-lived dev container you start on a 机房, work
in (configure env, run smoke tests), optionally save as a personal image, and
stop. The list endpoint returns rich objects; we project the fields a user
actually acts on, and keep the ids + ssh block needed by other `nb` commands.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

from ..client import endpoints
from ..client.http import Client
from ..domain.models import Spec
from ..errors import QzError
from . import options
from . import wait as waitlib


def _notebook_status(client: Client, notebook_id: str) -> str:
    return endpoints.get_notebook(client, notebook_id).get("status") or ""


def _save_status(client: Client, notebook_id: str) -> str:
    nb = endpoints.get_notebook(client, notebook_id)
    return (nb.get("save_mirror_status") or {}).get("status") or ""

# Defaults observed in the web CreateNotebook payload.
DEFAULT_RUNTIME = "standard"
DEFAULT_PRIORITY = 6
DEFAULT_VSCODE_VERSION = "1.101.2"
DSW_SCHEDULE_TYPE = "SCHEDULE_CONFIG_TYPE_DSW"


def _img(d: dict[str, Any]) -> str:
    return (d or {}).get("address", "") or ""


def _project_notebook(nb: dict[str, Any]) -> dict[str, Any]:
    quota = nb.get("quota") or {}
    sc = nb.get("start_config") or {}
    ei = nb.get("extra_info") or {}
    lcg = nb.get("logic_compute_group") or {}
    save = nb.get("save_mirror_status") or {}
    backup = _img(nb.get("backup_image"))
    return {
        "id": nb.get("id", ""),
        "notebook_id": nb.get("notebook_id", ""),
        "name": nb.get("name", ""),
        "status": nb.get("status", ""),
        "sub_status": nb.get("sub_status", ""),
        "room": lcg.get("name", ""),
        "gpu_count": quota.get("gpu_count", 0),
        "gpu_ram": quota.get("gpu_ram", 0),
        "cpu_count": quota.get("cpu_count", 0),
        "memory_size": quota.get("memory_size", 0),
        "image": _img(nb.get("image")),
        "backup_image": backup,
        "save_image_status": save.get("status", "") if backup else "",
        "allow_ssh": bool(sc.get("allow_ssh")),
        "ssh": {
            "proxy_jump": ei.get("ProxyJump", ""),
            "host": ei.get("SshDomain", ""),
            "port": ei.get("SshPort", 0),
            "pod": ei.get("PodName", ""),
            "node": ei.get("NodeName", ""),
        },
        "left_time": nb.get("left_time", "0"),
    }


def list_notebooks(client: Client, workspace_id: str) -> list[dict[str, Any]]:
    """Projected notebook rows, running first then by name."""
    raw = endpoints.list_notebooks(client, workspace_id)
    rows = [_project_notebook(nb) for nb in raw]
    rows.sort(key=lambda r: (r["status"] != "RUNNING", r["name"]))
    return rows


def resolve_notebook(client: Client, workspace_id: str, ref: str) -> dict[str, Any]:
    """Find a notebook by notebook_id (uuid), numeric id, or exact name."""
    raw = endpoints.list_notebooks(client, workspace_id)
    cands = [{"notebook_id": n.get("notebook_id"), "name": n.get("name"),
              "status": n.get("status")} for n in raw]
    for n in raw:
        if ref in (n.get("notebook_id"), n.get("id"), str(n.get("id"))):
            return n
    exact = [n for n in raw if ref == n.get("name")]
    if len(exact) == 1:
        return exact[0]
    raise QzError(
        f"未找到/无法唯一匹配 notebook '{ref}'",
        code="invalid_notebook",
        hint="用 qzcli nb ls -w <ws> 查看 notebook_id",
        candidates=cands,
    )


# --- 机房 / specs / image resolution for `nb start` -----------------------

def rooms(client: Client, workspace_id: str) -> list[dict[str, Any]]:
    """Projected interactive-modeling 机房 rows (read-before-write for `nb start`)."""
    out = []
    for g in compute_groups(client, workspace_id):
        gpu_types = sorted({s.get("gpu_type_simple") or s.get("gpu_type") or ""
                            for s in (g.get("gpu_type_stats") or [])} - {""})
        out.append({
            "id": g.get("logic_compute_group_id", ""),
            "name": g.get("name", ""),
            "gpu_types": ",".join(gpu_types),
            "node_count": g.get("node_count", 0),
            "schedule_type": g.get("schedule_type", ""),
        })
    return out


def delete(client: Client, notebook_id: str, *, stop_first: bool = False,
           timeout_s: int = waitlib.DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Delete a notebook. The platform only deletes STOPPED/FAILED notebooks;
    ``stop_first`` stops it and waits for STOPPED before deleting."""
    out: dict[str, Any] = {}
    if stop_first and _notebook_status(client, notebook_id) not in ("STOPPED", "FAILED"):
        endpoints.stop_notebook(client, notebook_id)
        out["wait"] = waitlib.wait_until(
            lambda: _notebook_status(client, notebook_id),
            waitlib.classify_notebook_stopped, timeout_s=timeout_s,
            label=f"nb {notebook_id} stop")
    out["result"] = endpoints.delete_notebook(client, notebook_id)
    return out


def compute_groups(client: Client, workspace_id: str) -> list[dict[str, Any]]:
    gs = endpoints.list_notebook_compute_groups(client, workspace_id)
    if not gs:
        raise QzError(
            f"工作空间 {workspace_id} 下没有支持交互式建模的机房",
            code="no_compute_groups",
            hint="确认该工作空间是否分配了可用于 notebook 的资源",
        )
    return gs


def resolve_compute_group(client: Client, workspace_id: str, value: str) -> dict[str, Any]:
    gs = compute_groups(client, workspace_id)
    cands = [{"id": g.get("logic_compute_group_id"), "name": g.get("name")} for g in gs]
    for g in gs:
        if value == g.get("logic_compute_group_id"):
            return g
    exact = [g for g in gs if value == g.get("name")]
    if len(exact) == 1:
        return exact[0]
    low = value.lower()
    fuzzy = [g for g in gs if low in (g.get("name") or "").lower()
             or low in (g.get("logic_compute_group_id") or "").lower()]
    if len(fuzzy) == 1:
        return fuzzy[0]
    raise QzError(
        f"未找到/无法唯一匹配机房 '{value}'",
        code="invalid_compute_group",
        hint="用下面 candidates 里的 id（lcg-...）",
        candidates=cands,
    )


def specs(client: Client, workspace_id: str, lcg_id: str) -> list[Spec]:
    """DSW (interactive-modeling) spec table for a 机房."""
    raw = endpoints.list_resource_specs(
        client, workspace_id, lcg_id, schedule_config_type=DSW_SCHEDULE_TYPE
    )
    return [Spec.from_resource_spec_price(s, lcg_id) for s in raw]


def resolve_spec(client: Client, workspace_id: str, lcg_id: str, quota_id: Optional[str]) -> Spec:
    found = specs(client, workspace_id, lcg_id)
    cands = [s.to_dict() for s in found]
    if not found:
        raise QzError(f"机房 {lcg_id} 没有可用的交互式建模规格", code="no_specs")
    if quota_id:
        for s in found:
            if s.quota_id == quota_id:
                return s
        raise QzError(f"未找到规格(quota) '{quota_id}'", code="invalid_spec",
                      hint="用下面 candidates 里的 quota_id", candidates=cands)
    raise QzError("未指定规格(quota)", code="missing_spec",
                  hint="传 --quota-id <id>（候选见 candidates）", candidates=cands)


def _all_images(client: Client, workspace_id: str):
    """OFFICIAL + PUBLIC + PRIVATE — a saved personal image is PRIVATE."""
    seen: dict[str, Any] = {}
    for src in ("SOURCE_OFFICIAL", "SOURCE_PUBLIC", "SOURCE_PRIVATE"):
        try:
            for im in endpoints.list_images(client, workspace_id, source=src):
                if im.address:
                    seen.setdefault(im.address, im)
        except QzError:
            continue
    return seen


def resolve_image(client: Client, workspace_id: str, address: str):
    """Return (mirror_id, mirror_url) for an image address. Address IS the url."""
    by_addr = _all_images(client, workspace_id)
    im = by_addr.get(address)
    if im is None:
        import difflib
        close = difflib.get_close_matches(address, list(by_addr), n=5, cutoff=0.5)
        raise QzError(
            f"镜像 '{address}' 不在可用镜像列表中（共 {len(by_addr)} 个）",
            code="invalid_image",
            hint="用 candidates 里的 address（含完整 registry 前缀）",
            candidates=close or list(by_addr)[:20],
        )
    return im.image_id, im.address


@dataclass
class NotebookStartRequest:
    name: str
    workspace: str
    compute_group: str
    image: str
    project: Optional[str] = None
    quota_id: Optional[str] = None
    cpu: Optional[int] = None
    gpu: Optional[int] = None
    mem: Optional[int] = None
    shm: Optional[int] = None
    priority: int = DEFAULT_PRIORITY
    runtime: str = DEFAULT_RUNTIME
    auto_stop: bool = False


def _resolve_ws_project(client: Client, workspace: str, project: Optional[str]):
    projects = endpoints.list_projects(client)
    _p, space = options.resolve_workspace(client, workspace, projects)
    ws_id = space.id
    owners = [p for p in projects if any(s.id == ws_id for s in p.spaces)]
    cands = [{"id": p.id, "name": p.name} for p in owners]
    if project:
        match = [p for p in owners if project in (p.id, p.name, p.en_name)] or \
                [p for p in owners if project.lower() in p.name.lower()]
        if len(match) != 1:
            raise QzError(f"项目 '{project}' 不唯一或不拥有该工作空间",
                          code="invalid_project",
                          hint="--project 必须是拥有该空间的项目之一", candidates=cands)
        proj = match[0]
    elif len(owners) == 1:
        proj = owners[0]
    else:
        raise QzError(f"工作空间 {ws_id} 归属多个项目，必须用 --project 指定",
                      code="ambiguous_project",
                      hint="从 candidates 中选一个传给 --project", candidates=cands)
    return proj.id, proj.name, ws_id, space.name


def build_start_payload(client: Client, req: NotebookStartRequest) -> dict[str, Any]:
    """Resolve every field and build the CreateNotebook payload (the read step)."""
    if not (req.name and req.workspace and req.compute_group and req.image):
        raise QzError("缺少必填字段: --name -w -g --image", code="missing_fields",
                      hint="补齐后重试；先用 qzcli nb ls / options 读候选")
    proj_id, proj_name, ws_id, ws_name = _resolve_ws_project(client, req.workspace, req.project)
    lcg = resolve_compute_group(client, ws_id, req.compute_group)
    lcg_id = lcg["logic_compute_group_id"]
    spec = resolve_spec(client, ws_id, lcg_id, req.quota_id)
    cpu = req.cpu if req.cpu is not None else spec.cpu_count
    gpu = req.gpu if req.gpu is not None else spec.gpu_count
    mem = req.mem if req.mem is not None else spec.memory_gb
    shm = req.shm if req.shm is not None else mem
    mirror_id, mirror_url = resolve_image(client, ws_id, req.image)
    payload = {
        "workspace_id": ws_id,
        "name": req.name,
        "project_id": proj_id,
        "project_name": proj_name,
        "logic_compute_group_id": lcg_id,
        "quota_id": spec.quota_id,
        "resource_spec_price": {
            "cpu_type": "", "cpu_count": cpu,
            "gpu_type": spec.gpu_type or "", "gpu_count": gpu,
            "memory_size_gib": mem,
            "logic_compute_group_id": lcg_id,
            "quota_id": spec.quota_id,
        },
        "cpu_count": cpu,
        "gpu_count": gpu,
        "memory_size": mem,
        "shared_memory_size": shm,
        "mirror_id": mirror_id,
        "mirror_url": mirror_url,
        "runtime": req.runtime,
        "task_priority": req.priority,
        "vscode_version": DEFAULT_VSCODE_VERSION,
        "auto_stop": req.auto_stop,
    }
    resolved = {
        "name": req.name, "workspace_id": ws_id, "workspace_name": ws_name,
        "project_id": proj_id, "project_name": proj_name,
        "compute_group_id": lcg_id, "compute_group_name": lcg.get("name"),
        "quota_id": spec.quota_id, "cpu": cpu, "gpu_count": gpu, "mem_gi": mem,
        "shm_gi": shm, "image": mirror_url, "priority": req.priority,
    }
    return {"resolved": resolved, "payload": payload}


def start(client: Client, req: NotebookStartRequest, *, dry_run: bool = False,
          wait: bool = True, timeout_s: int = waitlib.DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    prepared = build_start_payload(client, req)
    if dry_run:
        return {"dry_run": True, **prepared}
    res = endpoints.create_notebook(client, prepared["payload"])
    nid = (res.get("notebook_id") or res.get("id")
           or (res.get("notebook") or {}).get("notebook_id", ""))
    out = {"notebook_id": nid, "name": req.name,
           "workspace_id": prepared["resolved"]["workspace_id"],
           "resolved": prepared["resolved"], "result": res}
    if wait and nid:
        w = waitlib.wait_until(
            lambda: _notebook_status(client, nid),
            waitlib.classify_notebook_running, timeout_s=timeout_s,
            label=f"nb {nid} start")
        out["wait"] = w
        if w["failed"]:
            raise QzError(
                f"notebook {nid} 启动失败，最终状态 {w['final_status']!r}",
                code="notebook_failed",
                hint=f"查看原因: qzcli nb get {nid}",
            )
    return out


def stop(client: Client, notebook_id: str, *, wait: bool = True,
         timeout_s: int = waitlib.DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Stop a notebook; by default block until STOPPED."""
    res = endpoints.stop_notebook(client, notebook_id)
    out = {"stopped": notebook_id, "result": res}
    if wait:
        out["wait"] = waitlib.wait_until(
            lambda: _notebook_status(client, notebook_id),
            waitlib.classify_notebook_stopped, timeout_s=timeout_s,
            label=f"nb {notebook_id} stop")
    return out


def save_image(client: Client, notebook_id: str, name: str, version: str,
               *, accessible: int = 1, wait: bool = True,
               timeout_s: int = waitlib.DEFAULT_TIMEOUT_S) -> dict[str, Any]:
    """Save a RUNNING notebook as a personal image; by default block until the
    image build reaches SUCCESS."""
    nb = endpoints.get_notebook(client, notebook_id)
    if nb.get("status") != "RUNNING":
        raise QzError(
            f"notebook {notebook_id} 当前状态 {nb.get('status')!r}，保存镜像需要 RUNNING",
            code="invalid_notebook_state",
            hint="先启动 notebook（保存的是运行容器的当前状态）",
        )
    res = endpoints.save_notebook_image(client, notebook_id, name, version, accessible=accessible)
    out = {"notebook_id": notebook_id, "image_name": name, "version": version,
           "accessible": accessible, "image_address": "", "result": res}
    if wait:
        w = waitlib.wait_until(
            lambda: _save_status(client, notebook_id),
            waitlib.classify_save, timeout_s=timeout_s,
            label=f"save-image {name}:{version}")
        out["wait"] = w
        if w["failed"]:
            raise QzError(
                f"镜像构建失败，save_mirror_status={w['final_status']!r}",
                code="save_image_failed",
                hint=f"通常是构建中途被停/基础镜像问题；qzcli nb get {notebook_id} 看详情",
            )
        if w["reached"]:
            ws = nb.get("workspace")
            ws_id = ws.get("id") if isinstance(ws, dict) else (ws or "")
            img = endpoints.find_saved_image(client, ws_id, name, version) if ws_id else None
            if img:
                out["image_address"] = img.address
                out["image_id"] = img.image_id
    return out


# --- nb exec --------------------------------------------------------------
#
# Run a shell command inside a RUNNING notebook over its JupyterLab terminal
# WebSocket (terminado protocol). No SSH — the platform has none. We wrap the
# command in unique START/END markers so stdout is isolated cleanly from the
# PTY echo, the shell prompt, and the welcome banner, and we recover the exit
# code from the END marker.

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r", "")


def exec_command(
    client: Client,
    notebook_id: str,
    command: str,
    *,
    timeout: int = 120,
    strip_ansi: bool = True,
) -> dict[str, Any]:
    """Run ``command`` inside a running notebook; return stdout + exit code.

    Drives the notebook's JupyterLab terminal over its WebSocket. ``timeout`` is
    the overall wall-clock budget for the command to finish.
    """
    try:
        from websocket import (
            create_connection,
            WebSocketTimeoutException,
            WebSocketConnectionClosedException,
        )
    except ImportError:
        raise QzError(
            "缺少依赖 websocket-client", code="missing_dependency",
            hint="安装: uv add websocket-client（或 pip install websocket-client）",
        )

    nb = endpoints.get_notebook(client, notebook_id)
    if nb.get("status") != "RUNNING":
        raise QzError(
            f"notebook {notebook_id} 当前状态 {nb.get('status')!r}，执行命令需要 RUNNING",
            code="invalid_notebook_state",
            hint="先启动 notebook: qzcli nb start ...（或确认 id 正确）",
        )

    base, token = endpoints.resolve_jupyter(client, notebook_id)
    name = endpoints.create_terminal(base, token)
    ws_url = base.replace("https://", "wss://", 1) + f"/terminals/websocket/{name}?token={token}"

    # Unique markers (avoid Date/random deps; notebook_id + name + clock is enough).
    nonce = "QZX" + re.sub(r"[^0-9a-f]", "", notebook_id)[:8] + str(name)
    start_mark, end_re = f"{nonce}START", re.compile(rf"{nonce}EXIT(-?\d+)END")
    wrapped = f"echo {start_mark}; {command}; echo {nonce}EXIT$?END"

    deadline = time.monotonic() + timeout
    buf: list[str] = []
    exit_code: Optional[int] = None
    try:
        ws = create_connection(
            ws_url, header=[f"Authorization: token {token}"],
            timeout=5, max_size=None, enable_multithread=True,
        )
        try:
            # Drain the welcome banner until the shell goes idle (= ready).
            idle_until = time.monotonic() + 0.8
            while time.monotonic() < idle_until and time.monotonic() < deadline:
                try:
                    _recv_stdout(ws)
                    idle_until = time.monotonic() + 0.8
                except WebSocketTimeoutException:
                    break
                except (WebSocketConnectionClosedException, OSError):
                    break
            ws.send(json.dumps(["stdin", wrapped + "\n"]))
            while time.monotonic() < deadline:
                try:
                    chunk = _recv_stdout(ws)
                except WebSocketTimeoutException:
                    continue
                except (WebSocketConnectionClosedException, OSError):
                    break  # shell/terminal closed (e.g. command ran `exit`)
                if chunk is None:
                    continue
                buf.append(chunk)
                m = end_re.search(_strip_ansi("".join(buf)))
                if m:
                    exit_code = int(m.group(1))
                    break
        finally:
            try:
                ws.close()
            except Exception:
                pass
    finally:
        endpoints.delete_terminal(base, token, name)

    raw = _strip_ansi("".join(buf))
    stdout = _extract_between(raw, start_mark, end_re)
    timed_out = exit_code is None
    return {
        "notebook_id": notebook_id,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout": stdout if strip_ansi else "".join(buf),
    }


def _recv_stdout(ws) -> Optional[str]:
    """Receive one terminado frame; return its stdout text or None."""
    raw = ws.recv()
    if not raw:
        return None
    try:
        kind, *rest = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if kind == "stdout" and rest:
        return rest[0]
    return None


def _extract_between(text: str, start_mark: str, end_re: re.Pattern) -> str:
    """Return the lines between the START output marker and the END marker.

    The wrapped command is one shell line, so between the two output markers
    there is no prompt or input echo — just the command's own output.
    """
    lines = text.split("\n")
    out: list[str] = []
    capturing = False
    for ln in lines:
        if not capturing:
            # the *output* marker is the line equal to start_mark (not the
            # echoed `echo ...START` input line, which has the `echo ` prefix)
            if ln.strip() == start_mark:
                capturing = True
            continue
        if end_re.search(ln):
            break
        out.append(ln)
    return "\n".join(out).strip("\n")
