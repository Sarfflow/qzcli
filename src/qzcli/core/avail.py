"""Cluster availability — find where you can actually land a job.

Two notions of "free", because a full cluster can still be submittable:
  - **free**: GPUs reported idle right now (``node.gpu.available``).
  - **preemptible**: GPUs currently held by *low-priority* tasks
    (``task.priority <= threshold``) — a higher-priority job can evict them.

``effective_free = free + preemptible`` is what you can realistically get.
Results are ranked by it, by gpu_type and by node, so an agent can pick the
roomiest target. Everything is derived from the cookie-authed
``cluster_metric`` endpoints; nothing is UI-only.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from ..client import endpoints
from ..client.http import Client

# Jobs at or below this task_priority are treated as low-priority (preemptible).
# Matches the platform's convention used in the web overview.
LOW_PRIORITY_THRESHOLD = 3

# Default per-node list size for `avail` — you only need the emptiest few.
DEFAULT_TOP_NODES = 10


def _num(d: dict[str, Any], key: str) -> float:
    v = (d or {}).get(key)
    return v if isinstance(v, (int, float)) else 0


def _fetch_all_nodes(
    client: Client,
    workspace_id: str,
    *,
    logic_compute_group_id: Optional[str] = None,
    page_size: int = 200,
) -> list[dict[str, Any]]:
    """All node rows, paging to the reported ``total`` (not just page 1).

    The endpoint caps each response at ``page_size``; a busy workspace has many
    hundreds of nodes, so a single page silently under-counts the fleet.
    """
    rows: list[dict[str, Any]] = []
    page_num = 1
    while True:
        nd = endpoints.list_node_dimension(
            client, workspace_id,
            logic_compute_group_id=logic_compute_group_id,
            page_num=page_num, page_size=page_size,
        )
        batch = nd.get("node_dimensions") or nd.get("nodes") or []
        rows.extend(batch)
        total = nd.get("total") or 0
        if not batch or len(rows) >= total:
            break
        page_num += 1
    return rows


def _preemptible_by_node(
    client: Client, workspace_id: str, low_priority_threshold: int
) -> dict[str, float]:
    """GPU held by low-priority tasks, per node name (a higher-prio job evicts)."""
    td = endpoints.list_task_dimension(client, workspace_id, page_size=200)
    tasks = td.get("task_dimensions") or td.get("tasks") or []
    out: dict[str, float] = defaultdict(float)
    for t in tasks:
        prio = t.get("priority")
        if prio is None or prio > low_priority_threshold:
            continue
        gpu_total = _num(t.get("gpu"), "total")
        occ = t.get("nodes_occupied") or {}
        node_names = occ.get("nodes") or []
        count = occ.get("count") or len(node_names) or 1
        if not node_names or gpu_total <= 0:
            continue
        per = gpu_total / count  # spread a task's GPUs over its nodes
        for nm in node_names:
            out[nm] += per
    return out


def cluster_availability(
    client: Client,
    workspace_id: str,
    *,
    logic_compute_group_id: Optional[str] = None,
    low_priority_threshold: int = LOW_PRIORITY_THRESHOLD,
    include_all: bool = False,
    top: Optional[int] = DEFAULT_TOP_NODES,
) -> dict[str, Any]:
    """Where a job can land. ``by_gpu_type`` always covers the whole fleet.

    The cluster schedules jobs itself, so the per-node list is usually not
    needed — and the fleet is hundreds of physical nodes. By default ``nodes``
    is the emptiest few SCHEDULABLE nodes WITH SPARE CAPACITY (``effective_free
    > 0`` and ``Ready`` — fully-busy and unschedulable nodes are never listed),
    capped at ``top``; that's enough to confirm a job's nodes exist (a 16-card
    job needs two free 8-card nodes). ``include_all`` returns every node in the
    fleet (and ignores ``top``); ``top=None`` keeps all the spare-capacity ones.
    ``n_nodes`` is always the true fleet size, ``n_nodes_shown`` the list size,
    and ``nodes_truncated`` is true when ``top`` hid some spare-capacity nodes.
    """
    nodes_raw = _fetch_all_nodes(
        client, workspace_id, logic_compute_group_id=logic_compute_group_id,
    )
    preemptible_by_node = _preemptible_by_node(
        client, workspace_id, low_priority_threshold
    )

    nodes: list[dict[str, Any]] = []
    by_type_free: dict[str, float] = defaultdict(float)
    by_type_total: dict[str, float] = defaultdict(float)
    by_type_preempt: dict[str, float] = defaultdict(float)
    by_type_nodes: dict[str, int] = defaultdict(int)

    for n in nodes_raw:
        gpu = n.get("gpu") or {}
        name = n.get("name", "")
        gtype = n.get("gpu_type", "") or (n.get("gpu_info") or {}).get("gpu_type", "")
        free = _num(gpu, "available")
        total = _num(gpu, "total")
        used = _num(gpu, "used")
        preempt = round(preemptible_by_node.get(name, 0), 2)
        nodes.append({
            "name": name,
            "gpu_type": gtype,
            "gpu_total": int(total),
            "gpu_free": int(free),
            "gpu_used": int(used),
            "low_priority_preemptible": preempt,
            "effective_free": round(free + preempt, 2),
            "status": n.get("status", ""),
        })
        by_type_free[gtype] += free
        by_type_total[gtype] += total
        by_type_preempt[gtype] += preempt
        by_type_nodes[gtype] += 1

    nodes.sort(key=lambda x: (x["effective_free"], x["gpu_free"]), reverse=True)

    n_total = len(nodes)
    truncated = False
    if not include_all:
        # The scheduler places jobs itself; you rarely need the node list, and
        # when you do you only need the emptiest few (a 16-card job = 2 free
        # 8-card nodes). So default to schedulable nodes with spare capacity,
        # capped at `top`. `include_all` returns every node (ignores `top`).
        nodes = [n for n in nodes
                 if n["effective_free"] > 0 and n["status"] == "Ready"]
        n_schedulable = len(nodes)
        if top is not None and top < n_schedulable:
            nodes = nodes[:top]
            truncated = True

    by_gpu_type = [
        {
            "gpu_type": gt,
            "n_nodes": by_type_nodes[gt],
            "gpu_total": int(by_type_total[gt]),
            "gpu_free": int(by_type_free[gt]),
            "low_priority_preemptible": round(by_type_preempt[gt], 1),
            "effective_free": round(by_type_free[gt] + by_type_preempt[gt], 1),
        }
        for gt in by_type_total
    ]
    by_gpu_type.sort(key=lambda x: x["effective_free"], reverse=True)

    return {
        "low_priority_threshold": low_priority_threshold,
        "n_nodes": n_total,
        "n_nodes_shown": len(nodes),
        "nodes_truncated": truncated,
        "by_gpu_type": by_gpu_type,
        "nodes": nodes,
    }


def _join(values: set[str]) -> str:
    """One value bare, several joined — for gpu_type / cluster columns."""
    vs = sorted(v for v in values if v)
    return vs[0] if len(vs) == 1 else ",".join(vs)


def rooms_availability(
    client: Client,
    workspace_id: str,
    *,
    low_priority_threshold: int = LOW_PRIORITY_THRESHOLD,
    fit: Optional[tuple[int, int]] = None,
) -> dict[str, Any]:
    """Per-机房 (logic compute group) GPU availability, ranked roomiest-first.

    Each named 机房 in the platform UI is a logic compute group (``lcg-``); a job
    submission targets one. We aggregate its nodes' free/total GPU and rank by
    idle cards (then by fleet size), so an agent can pick where to land.

    机房 share physical nodes, so totals across rows overlap and ``gpu_free`` can
    go negative (the 机房 is oversubscribed). ``effective_free`` adds GPUs held by
    low-priority tasks a higher-priority job could evict.

    Room aggregates do NOT imply node-level placeability: free cards may be
    fragmented 1-2 per node, so a 机房 with gpu_free=24 can be unable to host a
    single 8-card node. ``max_free_on_single_node`` and ``nodes_full_free`` flag
    that; ``fit=(instances, cards_per_node)`` adds, per room, how many Ready
    nodes can host ``cards_per_node`` idle (``fit_nodes_idle``) or effective-free
    (``fit_nodes_effective``) cards, and whether that meets ``instances``
    (``fits``). When ``fit`` is set, rooms are ranked by fit first.
    """
    groups = endpoints.list_compute_groups(client, workspace_id)
    preemptible_by_node = _preemptible_by_node(
        client, workspace_id, low_priority_threshold
    )
    fit_inst, fit_cards = fit if fit else (0, 0)

    rooms: list[dict[str, Any]] = []
    for g in groups:
        nodes_raw = _fetch_all_nodes(
            client, workspace_id, logic_compute_group_id=g.id,
        )
        gpu_total = gpu_free = gpu_used = preempt = 0.0
        cpu_total = cpu_free = cpu_used = 0.0
        ready = 0
        max_free = 0
        nodes_full_free = 0
        fit_idle = fit_eff = 0
        gpu_types: set[str] = set()
        clusters: set[str] = set()
        for n in nodes_raw:
            gpu = n.get("gpu") or {}
            cpu = n.get("cpu") or {}
            n_total = _num(gpu, "total")
            n_free = _num(gpu, "available")
            gpu_total += n_total
            gpu_free += n_free
            gpu_used += _num(gpu, "used")
            cpu_total += _num(cpu, "total")
            cpu_free += _num(cpu, "available")
            cpu_used += _num(cpu, "used")
            n_pre = preemptible_by_node.get(n.get("name", ""), 0)
            preempt += n_pre
            is_ready = n.get("status") == "Ready"
            if is_ready:
                ready += 1
                # Node-level placeability is what gang-scheduled jobs need.
                if n_free > max_free:
                    max_free = int(n_free)
                if n_total > 0 and n_free >= n_total:
                    nodes_full_free += 1
                if fit_cards:
                    if n_free >= fit_cards:
                        fit_idle += 1
                    if n_free + n_pre >= fit_cards:
                        fit_eff += 1
            gpu_types.add(n.get("gpu_type", "")
                          or (n.get("gpu_info") or {}).get("gpu_type", ""))
            clusters.add(n.get("cluster_name", ""))
        room = {
            "room": g.name,
            "lcg_id": g.id,
            "gpu_type": _join(gpu_types) or g.gpu_type,
            "cluster": _join(clusters),
            "n_nodes": len(nodes_raw),
            "n_nodes_ready": ready,
            "gpu_total": int(gpu_total),
            "gpu_free": int(gpu_free),
            "gpu_used": int(gpu_used),
            "low_priority_preemptible": round(preempt, 1),
            "effective_free": round(gpu_free + preempt, 1),
            "max_free_on_single_node": max_free,
            "nodes_full_free": nodes_full_free,
            "cpu_total": int(cpu_total),
            "cpu_free": int(cpu_free),
            "cpu_used": int(cpu_used),
        }
        if fit_cards:
            room["fit_nodes_idle"] = fit_idle
            room["fit_nodes_effective"] = fit_eff
            room["fits"] = fit_eff >= fit_inst
        rooms.append(room)

    if fit_cards:
        # Placeability first: fits, then idle-fitting nodes, then effective.
        rooms.sort(
            key=lambda r: (r["fits"], r["fit_nodes_idle"], r["fit_nodes_effective"],
                           r["effective_free"]),
            reverse=True,
        )
    elif rooms and all(r["gpu_total"] == 0 for r in rooms):
        # CPU-only workspace: gpu_free is uniformly 0 (useless). Sort by cpu_free,
        # then by fleet size — so an agent picks an actually-uncongested room.
        rooms.sort(
            key=lambda r: (r["cpu_free"], r["n_nodes_ready"], r["n_nodes"]),
            reverse=True,
        )
    else:
        # Roomiest first: idle cards, then evictable headroom, then fleet size.
        rooms.sort(
            key=lambda r: (r["gpu_free"], r["effective_free"], r["gpu_total"]),
            reverse=True,
        )
    out: dict[str, Any] = {
        "low_priority_threshold": low_priority_threshold,
        "n_rooms": len(rooms),
        "rooms": rooms,
    }
    if fit_cards:
        out["fit"] = {"instances": fit_inst, "cards_per_node": fit_cards}
    return out
