"""Command-line entry point.

Subcommands map onto the read-before-write workflow:

    login → projects / options (read) → create --dry-run → create (write)

Default output is JSON (``{"ok": ...}`` envelope); ``--table`` is a human view.
Every handler returns the value to emit, or raises :class:`QzError`; ``main``
turns that into the JSON error envelope and a non-zero exit code.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Optional

from . import __version__, config, output
from .client import endpoints
from .client.http import Client
from .core import avail as avail_core
from .core import create as create_core
from .core import notebook as notebook_core
from .core import options as options_core
from .core import wait as wait_core
from .errors import QzError


# --- helpers --------------------------------------------------------------

def _client() -> Client:
    return Client.from_config()


def _resolved_ws(client: Client, workspace: str) -> tuple[str, str]:
    """Resolve a workspace arg to (id, name) using the project hierarchy."""
    _project, space = options_core.resolve_workspace(client, workspace)
    return space.id, space.name


def _require_job(client: Client, job_id: str) -> dict[str, Any]:
    """Verify a job exists, returning its detail; else a clean invalid_job error.

    The read-side commands (logs/instances/metrics/events/detail) otherwise fail
    unhelpfully on a bad/typo'd id — `train_job/detail` returns a generic
    ``参数错误`` and the v2 log/instance paths return empty/blob with ok:true.
    """
    try:
        detail = endpoints.job_detail(client, job_id)
    except QzError as e:
        if e.code in ("api_error", "bad_response"):
            raise QzError(
                f"任务 {job_id} 不存在或无权访问（平台: {e.message}）",
                code="invalid_job",
                hint="用 qzcli ls -w <ws> 查看合法 job_id",
            ) from e
        raise
    if not detail:
        raise QzError(
            f"任务 {job_id} 不存在或无权访问",
            code="invalid_job",
            hint="用 qzcli ls -w <ws> 查看合法 job_id",
        )
    return detail


def _condense_events(events: list[dict], *, tail: Optional[int] = None) -> list[dict]:
    """Newest-first, with identical repeated events collapsed to a count.

    k8s emits the same reason/message many times (gang scheduling, restarts);
    raw oldest-first output buries the current state under startup churn.
    """
    grouped: dict[tuple, dict] = {}
    order: list[tuple] = []
    for e in events:
        key = (e.get("type"), e.get("reason"), e.get("message"),
               e.get("from"), e.get("object_id"))
        g = grouped.get(key)
        if g is None:
            g = {
                "type": e.get("type"), "reason": e.get("reason"),
                "message": e.get("message"), "from": e.get("from"),
                "object_type": e.get("object_type"), "object_id": e.get("object_id"),
                "count": 0,
                "first_timestamp": e.get("first_timestamp"),
                "last_timestamp": e.get("last_timestamp"),
            }
            grouped[key] = g
            order.append(key)
        g["count"] += 1
        if (e.get("first_timestamp") or "") < (g["first_timestamp"] or "9" * 20):
            g["first_timestamp"] = e.get("first_timestamp")
        if (e.get("last_timestamp") or "") > (g["last_timestamp"] or ""):
            g["last_timestamp"] = e.get("last_timestamp")
    out = [grouped[k] for k in order]
    out.sort(key=lambda g: g.get("last_timestamp") or "", reverse=True)
    return out[:tail] if tail else out


# --- command handlers (return data, optional table columns) ---------------

def cmd_login(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()

    if args.cookie:
        config.save_cookie(args.cookie, args.workspace or "")
        return {"status": "cookie saved", "workspace_id": args.workspace or ""}, None

    username = args.username
    password = args.password
    if not (username and password):
        env_user, env_pass = config.get_credentials()
        username = username or env_user
        password = password or env_pass
    if not (username and password):
        raise QzError(
            "缺少用户名/密码",
            code="missing_credentials",
            hint="传 -u/-p，或设置 QZCLI_USERNAME/QZCLI_PASSWORD，或 --cookie 手动导出",
        )

    cookie = client.login(username, password, persist=True)
    if args.workspace:
        config.save_cookie(cookie, args.workspace)
    return {"status": "logged in", "cookie_len": len(cookie),
            "workspace_id": args.workspace or ""}, None


def cmd_projects(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    rows = [p.to_dict() for p in endpoints.list_projects(client)]
    if getattr(args, "with_gpu", False):
        # space→gpu_types is platform meta; not a single API, but cheap if you
        # query each unique space's compute-groups once. Opt-in to keep the
        # default `projects` a single call.
        unique_spaces = {s["id"] for r in rows for s in r["spaces"] if s.get("id")}
        gpu_by_ws: dict[str, list[str]] = {}
        for ws_id in unique_spaces:
            try:
                cgs = endpoints.list_compute_groups(client, ws_id)
                gpu_by_ws[ws_id] = sorted({cg.gpu_type for cg in cgs if cg.gpu_type})
            except QzError:
                gpu_by_ws[ws_id] = []
        for r in rows:
            for s in r["spaces"]:
                s["gpu_types"] = gpu_by_ws.get(s.get("id", ""), [])
    return rows, None


def cmd_options(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    if args.options_target == "compute-groups":
        ws_id, _ = _resolved_ws(client, args.workspace)
        groups = options_core.compute_groups(client, ws_id)
        return [g.to_dict() for g in groups], ["id", "name", "gpu_type"]
    if args.options_target == "specs":
        ws_id, _ = _resolved_ws(client, args.workspace)
        cg = options_core.resolve_compute_group(client, ws_id, args.compute_group)
        specs = options_core.specs(client, ws_id, cg.id)
        return [s.to_dict() for s in specs], [
            "quota_id", "gpu_type", "gpu_count", "cpu_count", "memory_gb",
            "total_price_per_hour",
        ]
    if args.options_target == "images":
        ws_id, _ = _resolved_ws(client, args.workspace)
        sources = ["ALL", "SOURCE_OFFICIAL", "SOURCE_PUBLIC", "SOURCE_PRIVATE"]
        if args.source not in sources:
            raise QzError(
                f"--source '{args.source}' 非法",
                code="invalid_argument",
                hint=f"用其中之一: {', '.join(sources)}",
                candidates=sources,
            )
        images = options_core.images(client, ws_id, source=args.source)
        rows = [im.to_dict() for im in images]
        if getattr(args, "name", None):
            q = args.name.lower()
            rows = [r for r in rows if q in str(r.get("name", "")).lower()
                    or q in str(r.get("address", "")).lower()]
        if not args.verbose:
            # `creator` is a heavy nested object; `address` is all create needs.
            for r in rows:
                r.pop("creator", None)
        return rows, ["address", "source", "visibility"]
    raise QzError(f"未知 options 目标: {args.options_target}", code="usage_error")


def _parse_dataset_refs(specs: list[str]) -> list[dict[str, str]]:
    """Parse ``dataset_id`` or ``dataset_id:version_id`` into validate payload."""
    out = []
    for s in specs:
        dataset_id, _, version_id = s.partition(":")
        out.append({"dataset_id": dataset_id, "version_id": version_id})
    return out


def cmd_dataset(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    ws_id, _ = _resolved_ws(client, args.workspace)
    results = endpoints.validate_datasets(
        client, ws_id, _parse_dataset_refs(args.dataset)
    )
    return results, ["dataset_id", "version_id", "success", "path", "error_message"]


def cmd_avail(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    ws_id, _ = _resolved_ws(client, args.workspace)
    lcg_id = None
    if args.compute_group:
        lcg_id = options_core.resolve_compute_group(client, ws_id, args.compute_group).id
    data = avail_core.cluster_availability(
        client, ws_id,
        logic_compute_group_id=lcg_id,
        low_priority_threshold=args.low_priority_threshold,
        include_all=args.all,
        top=None if args.top == 0 else args.top,
    )
    if args.table:
        # Per-node rows are the reason to call avail; the by_gpu_type summary is
        # still in the JSON view (and `rooms` is the aggregate table).
        return data["nodes"], [
            "name", "gpu_type", "gpu_total", "gpu_free", "gpu_used",
            "low_priority_preemptible", "effective_free", "status",
        ]
    return data, None


def _parse_fit(spec: Optional[str]) -> Optional[tuple[int, int]]:
    """Parse a placement requirement ``IxC`` (instances × cards/node) or ``C``."""
    if not spec:
        return None
    s = spec.lower().replace("×", "x")
    try:
        inst, _, cards = s.partition("x")
        instances = int(inst) if cards else 1
        per = int(cards or inst)
        if instances < 1 or per < 1:
            raise ValueError
        return instances, per
    except ValueError:
        raise QzError(
            f"--fit '{spec}' 格式错误",
            code="invalid_argument",
            hint="用 IxC（节点数x每节点卡数，如 2x8）或单个 C（如 8）",
        )


def cmd_nb(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    if args.nb_target == "ls":
        ws_id, _ = _resolved_ws(client, args.workspace)
        rows = notebook_core.list_notebooks(client, ws_id)
        return rows, [
            "name", "status", "room", "gpu_count", "gpu_ram",
            "image", "backup_image", "notebook_id",
        ]
    if args.nb_target == "rooms":
        ws_id, _ = _resolved_ws(client, args.workspace)
        return notebook_core.rooms(client, ws_id), [
            "id", "name", "gpu_types", "node_count", "schedule_type",
        ]
    if args.nb_target == "specs":
        ws_id, _ = _resolved_ws(client, args.workspace)
        lcg = notebook_core.resolve_compute_group(client, ws_id, args.compute_group)
        specs = notebook_core.specs(client, ws_id, lcg["logic_compute_group_id"])
        return [s.to_dict() for s in specs], [
            "quota_id", "gpu_type", "gpu_count", "cpu_count", "memory_gb",
            "total_price_per_hour",
        ]
    if args.nb_target == "get":
        return endpoints.get_notebook(client, args.notebook_id), None
    if args.nb_target == "start":
        req = notebook_core.NotebookStartRequest(
            name=args.name, workspace=args.workspace, compute_group=args.compute_group,
            image=args.image, project=args.project, quota_id=args.quota_id,
            cpu=args.cpu, gpu=args.gpu, mem=args.mem, shm=args.shm,
            priority=args.priority, auto_stop=args.auto_stop,
        )
        return notebook_core.start(
            client, req, dry_run=args.dry_run,
            wait=args.wait, timeout_s=args.timeout), None
    if args.nb_target == "stop":
        return notebook_core.stop(
            client, args.notebook_id, wait=args.wait, timeout_s=args.timeout), None
    if args.nb_target == "rm":
        res = notebook_core.delete(
            client, args.notebook_id, stop_first=args.stop, timeout_s=args.timeout)
        return {"deleted": args.notebook_id, **res}, None
    if args.nb_target == "save-image":
        return notebook_core.save_image(
            client, args.notebook_id, args.name, args.version,
            wait=args.wait, timeout_s=args.timeout,
        ), None  # accessible=1 (private personal image — the confirmed common case)
    if args.nb_target == "rm-image":
        ref = args.image
        if ref.startswith("image-"):
            image_id = ref
        else:
            if not args.workspace:
                raise QzError(
                    "按名称删除镜像需要 -w <ws> 来解析 image_id",
                    code="usage_error", hint="或直接传 image-<id>（见 options images）",
                )
            ws_id, _ = _resolved_ws(client, args.workspace)
            imgs = endpoints.list_images(client, ws_id, source="ALL")
            match = [im for im in imgs if ref in (im.address, im.name)]
            if len(match) != 1:
                raise QzError(
                    f"镜像 '{ref}' 未在该空间唯一匹配（{len(match)} 个）",
                    code="invalid_image",
                    hint="直接传 image-<id>，或用 qzcli options images 查 address",
                    candidates=[{"name": im.name, "address": im.address,
                                 "image_id": im.image_id} for im in imgs][:20],
                )
            image_id = match[0].image_id
        res = endpoints.delete_image(client, image_id)
        return {"deleted_image": image_id, "result": res}, None
    if args.nb_target == "exec":
        cmd_parts = list(args.command or [])
        if cmd_parts and cmd_parts[0] == "--":
            cmd_parts = cmd_parts[1:]
        cmd = " ".join(cmd_parts).strip()
        if not cmd:
            raise QzError(
                "缺少要执行的命令", code="usage_error",
                hint='用法: qzcli nb exec <notebook_id> -- <命令>',
            )
        return notebook_core.exec_command(
            client, args.notebook_id, cmd,
            timeout=args.timeout, strip_ansi=not args.raw,
        ), None
    raise QzError(f"未知 nb 目标: {args.nb_target}", code="usage_error")


def cmd_rooms(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    ws_id, _ = _resolved_ws(client, args.workspace)
    data = avail_core.rooms_availability(
        client, ws_id, low_priority_threshold=args.low_priority_threshold,
        fit=_parse_fit(args.fit),
    )
    if args.table:
        cols = [
            "room", "lcg_id", "gpu_type", "cluster", "n_nodes_ready",
            "gpu_total", "gpu_free", "low_priority_preemptible", "effective_free",
            "max_free_on_single_node", "nodes_full_free",
        ]
        if args.fit:
            cols += ["fit_nodes_idle", "fit_nodes_effective", "fits"]
        return data["rooms"], cols
    return data, None


def cmd_create(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    req = create_core.CreateRequest(
        name=args.name,
        workspace=args.workspace,
        compute_group=args.compute_group,
        image=args.image,
        command=args.cmd,
        project=args.project,
        quota_id=args.quota_id,
        cpu=args.cpu,
        gpu=args.gpu,
        mem=args.mem,
        framework=args.framework,
        image_type=args.image_type,
        instances=args.instances,
        shm=args.shm,
        priority=args.priority,
        check_image=not args.no_image_check,
        datasets=args.dataset or [],
        allow_set_e=args.allow_set_e,
    )
    if args.dry_run:
        return create_core.dry_run(client, req), None
    return create_core.submit(client, req, wait=args.wait, timeout_s=args.timeout), None


def cmd_ls(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    ws_id, _ = _resolved_ws(client, args.workspace)
    data = endpoints.list_jobs(client, ws_id, page_size=args.limit)
    jobs = data.get("jobs") or data.get("items") or []
    if args.running:
        jobs = [j for j in jobs if "RUN" in str(j.get("status", "")).upper()]
    from .domain.models import Job
    rows = [Job.from_api(j).to_dict() for j in jobs]
    # Workspaces are shared across projects, so echo project_name (not just the
    # opaque project_id) to help confirm a row is your own project's job.
    if rows:
        names = {p.id: p.name for p in endpoints.list_projects(client)}
        for r in rows:
            if r.get("project_id") and not r.get("project_name"):
                r["project_name"] = names.get(r["project_id"], "")
    return {"total": data.get("total", len(rows)), "jobs": rows}, None


def cmd_logs(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    _require_job(client, args.job_id)
    # --tail = the most recent N: fetch newest-first, then restore chrono order.
    result = endpoints.job_logs(client, args.job_id, page_size=args.tail, sort="descend")
    meta = result.get("ResponseMetadata") if isinstance(result, dict) else None
    if isinstance(meta, dict) and meta.get("Error"):
        raise QzError(
            f"任务 {args.job_id} 暂无可取日志（实例可能尚未调度/启动）",
            code="no_instances",
            hint="用 qzcli instances <job_id> 查看实例状态",
        )
    logs = result.get("logs") if isinstance(result, dict) else None
    if isinstance(logs, list) and logs:
        result["logs"] = list(reversed(logs))
        return result, None
    # No log lines: disambiguate "not indexed yet" from "genuinely no output / not
    # retrievable" using the job's phase, so the caller stops polling when futile.
    if isinstance(result, dict):
        try:
            status = (endpoints.job_detail(client, args.job_id) or {}).get("status", "")
        except QzError:
            status = ""
        terminal = status in ("job_succeeded", "job_failed", "job_stopped")
        result["logs"] = []
        result["logs_available"] = False
        result["note"] = (
            f"0 条日志；job 状态 {status or '未知'}（终态）——平台未对该 job 的 pod 返回任何"
            "日志行。对终态 job 这通常表示日志不可取（并非确认无输出），不必继续轮询。"
            if terminal else
            f"0 条日志；job 状态 {status or '未知'}——日志可能尚未索引，稍后用同样命令重试。"
        )
    return result, None


def cmd_stop(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    return create_core.stop(client, args.job_id, wait=args.wait, timeout_s=args.timeout), None


def cmd_events(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    if args.instance:
        events = endpoints.instance_events(client, args.job_id, args.instance)
    else:
        _require_job(client, args.job_id)
        events = endpoints.job_events(client, args.job_id)
    rows = _condense_events(events, tail=args.tail)
    return rows, ["last_timestamp", "type", "reason", "count", "from", "message"]


_DETAIL_BRIEF_KEYS = [
    "job_id", "name", "status", "project_name", "framework", "gpu_count",
    "logic_compute_group_name", "task_priority", "created_at", "finished_at",
]


def cmd_detail(args) -> tuple[Any, Optional[list[str]]]:
    d = _require_job(_client(), args.job_id)
    if getattr(args, "brief", False):
        # quick status without the ~25-key dump (node_infos/timeline/envs/…)
        return {k: d.get(k) for k in _DETAIL_BRIEF_KEYS}, None
    return d, None


def cmd_instances(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    _require_job(client, args.job_id)
    items = endpoints.list_job_instances(client, args.job_id)
    return items, ["name", "instance_type", "node", "instance_status", "running_time_ms"]


def cmd_metrics(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    detail = _require_job(client, args.job_id)
    lcg = detail.get("logic_compute_group_id", "")
    if not lcg:
        raise QzError(
            f"无法获取任务 {args.job_id} 的计算组",
            code="invalid_job", hint="先用 qzcli ls -w <ws> 确认 job_id",
        )
    end = int(time.time())
    start = end - args.minutes * 60
    metrics = args.metric or ["gpu_usage_rate", "gpu_memory_usage_rate"]
    groups = endpoints.get_task_metric(
        client, logic_compute_group_id=lcg, task_id=args.job_id,
        metric_types=metrics, start_timestamp=start, end_timestamp=end,
        interval_second=args.interval, task_type=args.task_type,
    )
    summary = []
    for g in groups:
        vals = [p.get("data", 0) for p in (g.get("time_series") or [])]
        summary.append({
            "group_name": g.get("group_name"),
            "metric_type": g.get("metric_type"),
            "points": len(vals),
            "last": vals[-1] if vals else None,
            "avg": round(sum(vals) / len(vals), 4) if vals else None,
            "max": max(vals) if vals else None,
        })
    if args.table:
        return summary, ["group_name", "metric_type", "last", "avg", "max", "points"]
    return {"window_minutes": args.minutes, "summary": summary, "groups": groups}, None


# --- argument parser ------------------------------------------------------

class JsonArgumentParser(argparse.ArgumentParser):
    """argparse that emits the JSON error envelope (not a plain usage line).

    Keeps the "all output is JSON" contract for the most common mistake —
    a missing/invalid flag — so an agent piping JSON recovers instead of
    crashing on a parse error + exit code 2.
    """

    def error(self, message: str):  # noqa: D102
        err = QzError(
            f"参数错误: {message}",
            code="usage_error",
            hint=f"查看用法: qzcli {self.prog.replace('qzcli ', '')} -h",
        )
        # --table isn't parsed yet at error time; JSON is the contract default.
        output.emit_error(err, table=False)
        raise SystemExit(1)


def _add_wait_flags(p: argparse.ArgumentParser) -> None:
    """Add the blocking-wait flags shared by long-running commands.

    Default: block (qzcli polls) until the resource reaches its target state or
    the ACTIVE budget runs out. Queue time is never charged against --timeout.
    Tip: run these with the shell in the background to spend zero tokens waiting.
    """
    p.add_argument("--wait", action=argparse.BooleanOptionalAction, default=True,
                   help="阻塞直到达到目标状态/超时（默认开；--no-wait 提交即返回）")
    p.add_argument("--timeout", type=int, default=wait_core.DEFAULT_TIMEOUT_S,
                   help="ACTIVE 阶段超时秒数（默认 600；排队不计入）")


def build_parser() -> argparse.ArgumentParser:
    p = JsonArgumentParser(
        prog="qzcli",
        description="启智平台 agent-first CLI（逆向网页 API，cookie 认证，默认 JSON 输出）",
    )
    p.add_argument("--version", action="version", version=f"qzcli {__version__}")
    p.add_argument("--table", action="store_true", help="人类可读表格输出（默认 JSON）")
    p.add_argument("--fields", help="只保留这些字段（逗号分隔），裁剪 list 结果省 token，如 --fields room,gpu_free,effective_free")
    sub = p.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    sp = sub.add_parser("login", help="CAS 登录，保存 cookie 到 ~/.qzcli/")
    sp.add_argument("-u", "--username")
    sp.add_argument("-p", "--password")
    sp.add_argument("--cookie", help="直接保存浏览器导出的 cookie（验证码场景）")
    sp.add_argument("-w", "--workspace", help="默认工作空间 id")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("projects", help="项目→空间 层级（带 priority_cap；--with-gpu 加空间→GPU 型号）")
    sp.add_argument("--with-gpu", dest="with_gpu", action="store_true",
                    help="为每个 space 注入 gpu_types（每个空间多一次 compute-groups 查询）")
    sp.set_defaults(func=cmd_projects)

    sp = sub.add_parser("options", help="枚举某一级的合法候选")
    osub = sp.add_subparsers(dest="options_target", required=True)
    o = osub.add_parser("compute-groups", help="列出工作空间下的计算组")
    o.add_argument("-w", "--workspace", required=True)
    o.set_defaults(func=cmd_options)
    o = osub.add_parser("specs", help="列出计算组的规格候选（quota）")
    o.add_argument("-w", "--workspace", required=True)
    o.add_argument("-g", "--compute-group", required=True)
    o.set_defaults(func=cmd_options)
    o = osub.add_parser("images", help="列出工作空间可用镜像")
    o.add_argument("-w", "--workspace", required=True)
    o.add_argument("--source", default="ALL",
                   help="ALL（默认）/ SOURCE_OFFICIAL / SOURCE_PUBLIC / SOURCE_PRIVATE")
    o.add_argument("--name", help="按镜像 name/address 子串过滤（省得拉全表）")
    o.add_argument("--verbose", action="store_true",
                   help="包含完整 creator 等元数据（默认精简，只留 address 等关键字段）")
    o.set_defaults(func=cmd_options)

    sp = sub.add_parser("dataset", help="数据集相关")
    dsub = sp.add_subparsers(dest="dataset_target", required=True)
    dv = dsub.add_parser("validate", help="校验数据集/版本是否合法，返回挂载 path")
    dv.add_argument("-w", "--workspace", required=True)
    dv.add_argument("--dataset", action="append", required=True,
                    help="dataset_id 或 dataset_id:version_id，可重复")
    dv.set_defaults(func=cmd_dataset)

    sp = sub.add_parser("avail", help="集群空闲：空卡 + 低优可抢占，按空闲排序")
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("-g", "--compute-group")
    sp.add_argument("--low-priority-threshold", type=int,
                    default=avail_core.LOW_PRIORITY_THRESHOLD,
                    help=f"优先级 <= 此值视为低优可抢占（默认 {avail_core.LOW_PRIORITY_THRESHOLD}）")
    sp.add_argument("--all", action="store_true",
                    help="返回全部节点（默认只列最空闲的少数几台可用节点）")
    sp.add_argument("--top", type=int, default=avail_core.DEFAULT_TOP_NODES,
                    help=f"最多列出 N 个节点（按 effective_free 降序；默认 {avail_core.DEFAULT_TOP_NODES}，传 0 表示全部可调度）")
    sp.set_defaults(func=cmd_avail)

    sp = sub.add_parser("rooms", help="按机房(lcg)聚合空闲卡，最空闲优先排序")
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("--low-priority-threshold", type=int,
                    default=avail_core.LOW_PRIORITY_THRESHOLD,
                    help=f"优先级 <= 此值视为低优可抢占（默认 {avail_core.LOW_PRIORITY_THRESHOLD}）")
    sp.add_argument("--fit", metavar="IxC",
                    help="可落性检查：每个机房有几个节点能放下「每节点 C 卡」的任务，"
                         "是否够 I 个节点（如 2x8）。聚合空闲卡可能碎片化、放不下整节点任务")
    sp.set_defaults(func=cmd_rooms)

    sp = sub.add_parser("nb", help="交互式建模(notebook)：起停、保存个人镜像")
    nsub = sp.add_subparsers(dest="nb_target", required=True, parser_class=JsonArgumentParser)
    n = nsub.add_parser("ls", help="列出工作空间下的 notebook（运行中优先）")
    n.add_argument("-w", "--workspace", required=True)
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("rooms", help="列支持交互式建模的机房(lcg)")
    n.add_argument("-w", "--workspace", required=True)
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("specs", help="列机房的 DSW 规格(quota_id + cpu/gpu/mem/价格)")
    n.add_argument("-w", "--workspace", required=True)
    n.add_argument("-g", "--compute-group", required=True, help="机房 lcg（见 nb rooms）")
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("get", help="单个 notebook 详情（轮询状态用）")
    n.add_argument("notebook_id")
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("start", help="起一个交互式建模 notebook（--dry-run 先校验）")
    n.add_argument("--name", required=True)
    n.add_argument("-w", "--workspace", required=True)
    n.add_argument("-g", "--compute-group", required=True, help="机房 lcg（见 nb ls / 平台）")
    n.add_argument("--image", required=True, help="基础镜像 address（含完整 registry 前缀）")
    n.add_argument("--project", help="项目 id/名称（多项目空间必填）")
    n.add_argument("--quota-id", dest="quota_id", help="DSW 规格 quota_id")
    n.add_argument("--cpu", type=int)
    n.add_argument("--gpu", type=int)
    n.add_argument("--mem", type=int, help="内存 GiB")
    n.add_argument("--shm", type=int, help="共享内存 GiB（默认=内存）")
    n.add_argument("--priority", type=int, default=notebook_core.DEFAULT_PRIORITY)
    n.add_argument("--auto-stop", dest="auto_stop", action="store_true")
    n.add_argument("--dry-run", action="store_true", help="只校验+预览 payload，不创建")
    _add_wait_flags(n)  # default: block until RUNNING
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("stop", help="停止 notebook（默认阻塞至 STOPPED）")
    n.add_argument("notebook_id")
    _add_wait_flags(n)
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("rm", help="删除 notebook（需先 STOPPED；--stop 自动停后再删）")
    n.add_argument("notebook_id")
    n.add_argument("--stop", action="store_true", help="先停止并等待 STOPPED 再删除")
    n.add_argument("--timeout", type=int, default=wait_core.DEFAULT_TIMEOUT_S,
                   help="--stop 时等待 STOPPED 的 ACTIVE 超时秒数（默认 600）")
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("save-image", help="把运行中的 notebook 存为个人镜像（私有；默认阻塞至 SUCCESS）")
    n.add_argument("notebook_id")
    n.add_argument("--name", required=True, help="镜像名")
    n.add_argument("--version", required=True, help="镜像版本 tag")
    _add_wait_flags(n)  # default: block until image build SUCCESS
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser("rm-image", help="删除个人镜像（image-<id>，或 -w + 名称/address）")
    n.add_argument("image", help="image_id（image-…）或镜像 name/address（需配 -w）")
    n.add_argument("-w", "--workspace", help="按名称/address 解析 image_id 时需要")
    n.set_defaults(func=cmd_nb)
    n = nsub.add_parser(
        "exec", help="在运行中的 notebook 内执行命令（走 Jupyter 终端，无需 SSH）"
    )
    n.add_argument("notebook_id")
    n.add_argument("command", nargs="*",
                   help="要执行的命令；用 -- 分隔，如 nb exec <id> -- pip list")
    n.add_argument("--timeout", type=int, default=120, help="整体超时秒数（默认 120）")
    n.add_argument("--raw", action="store_true",
                   help="返回原始终端输出（保留 ANSI/banner，不裁剪）")
    n.set_defaults(func=cmd_nb)

    sp = sub.add_parser("create", help="创建任务（--dry-run 先读校验）")
    sp.add_argument("--name", required=True)
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("-g", "--compute-group", required=True)
    sp.add_argument("--image", required=True)
    sp.add_argument("--cmd", required=True, help="容器内执行的命令")
    sp.add_argument("--project", help="项目 id/名称（默认按层级从工作空间推断）")
    sp.add_argument("--quota-id", dest="quota_id", help="规格 quota_id（见 options specs）")
    sp.add_argument("--cpu", type=int, help="CPU 核数（覆盖/补全规格）")
    sp.add_argument("--gpu", type=int, help="GPU 数量")
    sp.add_argument("--mem", type=int, help="内存 GiB")
    sp.add_argument("--framework", default=create_core.DEFAULT_FRAMEWORK)
    sp.add_argument("--image-type", dest="image_type", default=None,
                    help="默认按镜像自身 source 推断（SOURCE_OFFICIAL/PUBLIC/PRIVATE）")
    sp.add_argument("--instances", type=int, default=create_core.DEFAULT_INSTANCES,
                    help="节点数（每节点卡数由规格 gpu_count 决定）")
    sp.add_argument("--shm", type=int, default=None,
                    help="共享内存 GiB（默认 = 规格内存大小，同网页）")
    sp.add_argument("--priority", type=int, default=create_core.DEFAULT_PRIORITY)
    sp.add_argument("--no-image-check", action="store_true",
                    help="跳过镜像存在性校验")
    sp.add_argument("--dataset", action="append",
                    help="挂载数据集 dataset_id 或 dataset_id:version_id，可重复（提交前自动校验）")
    sp.add_argument("--dry-run", action="store_true",
                    help="只校验+预览 payload，不提交")
    sp.add_argument("--allow-set-e", dest="allow_set_e", action="store_true",
                    help="保留 --cmd 里的 `set -e`（默认被拦截，避免无害非零让整 job 失败）")
    _add_wait_flags(sp)  # default: block until job_running (queue not counted)
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("ls", help="任务列表")
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("--running", action="store_true", help="只看运行中的")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("logs", help="任务日志")
    sp.add_argument("job_id")
    sp.add_argument("--tail", type=int, default=200, help="取最近 N 条（最新在后）")
    sp.set_defaults(func=cmd_logs)

    sp = sub.add_parser("stop", help="停止任务（默认阻塞至终态）")
    sp.add_argument("job_id")
    _add_wait_flags(sp)
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("events", help="任务/实例事件（最新优先，重复事件折叠计数）")
    sp.add_argument("job_id")
    sp.add_argument("--instance", help="实例名 <job_id>-worker-N（默认看 job 级事件）")
    sp.add_argument("--tail", type=int, default=None, help="只看最近 N 类事件")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("detail", help="任务详情（--brief 只看状态等关键字段，省 token）")
    sp.add_argument("job_id")
    sp.add_argument("--brief", action="store_true",
                    help="只返回 status/name/project_name/gpu_count 等关键字段")
    sp.set_defaults(func=cmd_detail)

    sp = sub.add_parser("instances", help="任务的实例/Pod 列表（真实 pod 名）")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_instances)

    sp = sub.add_parser("metrics", help="任务资源利用率时序（默认 GPU+显存，判断是否在用卡）")
    sp.add_argument("job_id")
    sp.add_argument("--minutes", type=int, default=30, help="回看时间窗（分钟，默认 30）")
    sp.add_argument("--interval", type=int, default=60, help="采样间隔秒（默认 60）")
    sp.add_argument("--metric", action="append",
                    help=f"指标，可重复。可选: {', '.join(endpoints.METRIC_TYPES)}")
    sp.add_argument("--task-type", dest="task_type", default="distributed_training")
    sp.set_defaults(func=cmd_metrics)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    table = bool(args.table)
    try:
        data, columns = args.func(args)
    except QzError as e:
        return output.emit_error(e, table=table)
    except KeyboardInterrupt:
        return output.emit_error(QzError("已中断", code="interrupted"), table=table)
    if getattr(args, "fields", None):
        data = _project_fields(data, args.fields)
    return output.emit_success(data, table=table, columns=columns)


def _project_fields(data: Any, fields_str: str) -> Any:
    """Keep only `fields_str` (comma-separated) on list-of-dict results.

    Applies to a top-level list, or to any list-of-dicts value inside a top-level
    dict (so it works for both `specs`/`images` (list) and `ls`/`rooms`
    ({...: [rows]})). Non-dict rows and scalar top-level keys pass through.
    """
    keys = [f.strip() for f in fields_str.split(",") if f.strip()]
    if not keys:
        return data

    def row(r):
        return {k: r.get(k) for k in keys} if isinstance(r, dict) else r

    if isinstance(data, list):
        return [row(r) for r in data]
    if isinstance(data, dict):
        return {k: ([row(r) for r in v]
                    if isinstance(v, list) and v and all(isinstance(x, dict) for x in v)
                    else v)
                for k, v in data.items()}
    return data


if __name__ == "__main__":
    sys.exit(main())
