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
from typing import Any, Optional

from . import __version__, config, output
from .client import endpoints
from .client.http import Client
from .core import avail as avail_core
from .core import create as create_core
from .core import options as options_core
from .errors import QzError


# --- helpers --------------------------------------------------------------

def _client() -> Client:
    return Client.from_config()


def _resolved_ws(client: Client, workspace: str) -> tuple[str, str]:
    """Resolve a workspace arg to (id, name) using the project hierarchy."""
    _project, space = options_core.resolve_workspace(client, workspace)
    return space.id, space.name


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
    projects = endpoints.list_projects(_client())
    return [p.to_dict() for p in projects], None


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
        images = options_core.images(client, ws_id, source=args.source)
        return [im.to_dict() for im in images], ["address", "source", "visibility"]
    raise QzError(f"未知 options 目标: {args.options_target}", code="usage_error")


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
    )
    if args.table:
        return data["by_gpu_type"], [
            "gpu_type", "n_nodes", "gpu_total", "gpu_free",
            "low_priority_preemptible", "effective_free",
        ]
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
    )
    if args.dry_run:
        return create_core.dry_run(client, req), None
    return create_core.submit(client, req), None


def cmd_ls(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    ws_id, _ = _resolved_ws(client, args.workspace)
    data = endpoints.list_jobs(client, ws_id, page_size=args.limit)
    jobs = data.get("jobs") or data.get("items") or []
    if args.running:
        jobs = [j for j in jobs if "RUN" in str(j.get("status", "")).upper()]
    from .domain.models import Job
    rows = [Job.from_api(j).to_dict() for j in jobs]
    return {"total": data.get("total", len(rows)), "jobs": rows}, None


def cmd_logs(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    result = endpoints.job_logs(client, args.job_id, page_size=args.tail)
    return result, None


def cmd_stop(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    result = endpoints.stop_job(client, args.job_id)
    return {"stopped": args.job_id, "result": result}, None


def cmd_events(args) -> tuple[Any, Optional[list[str]]]:
    client = _client()
    if args.instance:
        return endpoints.instance_events(client, args.job_id, args.instance), None
    return endpoints.job_events(client, args.job_id), None


def cmd_detail(args) -> tuple[Any, Optional[list[str]]]:
    return endpoints.job_detail(_client(), args.job_id), None


def cmd_instances(args) -> tuple[Any, Optional[list[str]]]:
    items = endpoints.list_job_instances(_client(), args.job_id)
    return items, ["name", "instance_type", "node", "instance_status", "running_time_ms"]


# --- argument parser ------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qzcli",
        description="启智平台 agent-first CLI（逆向网页 API，cookie 认证，默认 JSON 输出）",
    )
    p.add_argument("--version", action="version", version=f"qzcli {__version__}")
    p.add_argument("--table", action="store_true", help="人类可读表格输出（默认 JSON）")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("login", help="CAS 登录，保存 cookie 到 ~/.qzcli/")
    sp.add_argument("-u", "--username")
    sp.add_argument("-p", "--password")
    sp.add_argument("--cookie", help="直接保存浏览器导出的 cookie（验证码场景）")
    sp.add_argument("-w", "--workspace", help="默认工作空间 id")
    sp.set_defaults(func=cmd_login)

    sp = sub.add_parser("projects", help="项目→空间 层级（不压平）")
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
                   help="SOURCE_OFFICIAL / SOURCE_PUBLIC / ALL（默认）")
    o.set_defaults(func=cmd_options)

    sp = sub.add_parser("avail", help="集群空闲：空卡 + 低优可抢占，按空闲排序")
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("-g", "--compute-group")
    sp.add_argument("--low-priority-threshold", type=int,
                    default=avail_core.LOW_PRIORITY_THRESHOLD,
                    help=f"优先级 <= 此值视为低优可抢占（默认 {avail_core.LOW_PRIORITY_THRESHOLD}）")
    sp.set_defaults(func=cmd_avail)

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
    sp.add_argument("--dry-run", action="store_true",
                    help="只校验+预览 payload，不提交")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("ls", help="任务列表")
    sp.add_argument("-w", "--workspace", required=True)
    sp.add_argument("--running", action="store_true", help="只看运行中的")
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(func=cmd_ls)

    sp = sub.add_parser("logs", help="任务日志")
    sp.add_argument("job_id")
    sp.add_argument("--tail", type=int, default=200, help="拉取条数")
    sp.set_defaults(func=cmd_logs)

    sp = sub.add_parser("stop", help="停止任务")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("events", help="任务/实例事件")
    sp.add_argument("job_id")
    sp.add_argument("--instance", help="实例名 <job_id>-worker-N（默认看 job 级事件）")
    sp.set_defaults(func=cmd_events)

    sp = sub.add_parser("detail", help="任务详情")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_detail)

    sp = sub.add_parser("instances", help="任务的实例/Pod 列表（真实 pod 名）")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_instances)

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
    return output.emit_success(data, table=table, columns=columns)


if __name__ == "__main__":
    sys.exit(main())
