"""Cascading option resolution — the heart of "先读再写" (principle #2).

Each level (workspace → compute-group → spec / image) can be *enumerated* and
*resolved*. Resolution accepts an id, an exact name, or a unique fuzzy match;
on any miss it raises a :class:`QzError` whose ``candidates`` field lists the
currently-legal choices at that level, so the agent can pick one and retry. We
never silently auto-select the first item.
"""

from __future__ import annotations

from typing import Any, Optional

from ..client import endpoints
from ..client.http import Client
from ..domain.models import ComputeGroup, Image, Project, Space, Spec
from ..errors import QzError


# --- workspaces (via project hierarchy) ----------------------------------

def resolve_workspace(
    client: Client, workspace: str, projects: Optional[list[Project]] = None
) -> tuple[Project, Space]:
    """Find the (project, space) for a workspace id or name.

    Uses the preserved project→space hierarchy so the owning project is known
    without a separate lookup. Raises with candidates listing every space.
    """
    if projects is None:
        projects = endpoints.list_projects(client)

    pairs = [(p, s) for p in projects for s in p.spaces]
    if not pairs:
        raise QzError(
            "当前账号没有任何可用工作空间",
            code="no_workspaces",
            hint="确认账号权限，或 qzcli projects 查看",
        )

    candidates = [
        {"workspace_id": s.id, "workspace_name": s.name,
         "project_id": p.id, "project_name": p.name}
        for p, s in pairs
    ]

    # Exact id, then exact name, then unique fuzzy on name/id.
    for p, s in pairs:
        if workspace == s.id:
            return p, s
    exact = [(p, s) for p, s in pairs if workspace == s.name]
    if len(exact) == 1:
        return exact[0]
    low = workspace.lower()
    fuzzy = [(p, s) for p, s in pairs if low in s.name.lower() or low in s.id.lower()]
    if len(fuzzy) == 1:
        return fuzzy[0]

    if len(exact) > 1 or len(fuzzy) > 1:
        raise QzError(
            f"工作空间 '{workspace}' 匹配到多个，请用完整 ws- id 指定",
            code="ambiguous_workspace",
            candidates=candidates,
        )
    raise QzError(
        f"未找到工作空间 '{workspace}'",
        code="invalid_workspace",
        hint="用下面 candidates 里的 workspace_id 或 workspace_name",
        candidates=candidates,
    )


# --- compute groups -------------------------------------------------------

def compute_groups(client: Client, workspace_id: str) -> list[ComputeGroup]:
    groups = endpoints.list_compute_groups(client, workspace_id)
    if not groups:
        raise QzError(
            f"工作空间 {workspace_id} 下没有可用计算组",
            code="no_compute_groups",
            hint="确认该工作空间是否分配了计算资源",
        )
    return groups


def resolve_compute_group(
    client: Client, workspace_id: str, value: str
) -> ComputeGroup:
    groups = compute_groups(client, workspace_id)
    candidates = [g.to_dict() for g in groups]
    for g in groups:
        if value == g.id:
            return g
    exact = [g for g in groups if value == g.name]
    if len(exact) == 1:
        return exact[0]
    low = value.lower()
    fuzzy = [g for g in groups if low in g.name.lower() or low in g.id.lower()]
    if len(fuzzy) == 1:
        return fuzzy[0]
    raise QzError(
        f"未找到/无法唯一匹配计算组 '{value}'",
        code="invalid_compute_group",
        hint="用下面 candidates 里的 id（lcg-...）",
        candidates=candidates,
    )


# --- specs (the authoritative per-compute-group table) --------------------

def specs(
    client: Client, workspace_id: str, compute_group_id: str
) -> list[Spec]:
    """List the spec/card-count options for a compute group (机房).

    Sourced from ``/api/v1/resource_prices/logic_compute_groups/`` — the same
    full 1/2/4/8-card table the web create form shows, with quota_id, concrete
    cpu/gpu/mem and price. Requires the logical compute group id.
    """
    if not compute_group_id:
        raise QzError(
            "列规格需要先指定计算组(机房)",
            code="missing_compute_group",
            hint="先 qzcli options compute-groups -w <ws> 选一个 lcg-，再 -g 传入",
        )
    raw = endpoints.list_resource_specs(client, workspace_id, compute_group_id)
    return [Spec.from_resource_spec_price(s, compute_group_id) for s in raw]


def resolve_spec(
    client: Client, workspace_id: str, compute_group_id: str, value: str
) -> Spec:
    found = specs(client, workspace_id, compute_group_id)
    candidates = [s.to_dict() for s in found]
    if not found:
        raise QzError(
            f"计算组 {compute_group_id} 没有可用规格",
            code="no_specs",
            hint="确认该机房是否有分配的资源规格",
        )
    for s in found:
        if value == s.quota_id:
            return s
    raise QzError(
        f"未找到规格(quota) '{value}'",
        code="invalid_spec",
        hint="用下面 candidates 里的 quota_id（gpu_count 即每节点卡数）",
        candidates=candidates,
    )


# --- images ---------------------------------------------------------------

def images(
    client: Client, workspace_id: str, source: str = "ALL"
) -> list[Image]:
    return endpoints.list_images(client, workspace_id, source)
