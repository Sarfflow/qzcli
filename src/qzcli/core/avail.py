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


def _num(d: dict[str, Any], key: str) -> float:
    v = (d or {}).get(key)
    return v if isinstance(v, (int, float)) else 0


def cluster_availability(
    client: Client,
    workspace_id: str,
    *,
    logic_compute_group_id: Optional[str] = None,
    low_priority_threshold: int = LOW_PRIORITY_THRESHOLD,
) -> dict[str, Any]:
    nd = endpoints.list_node_dimension(
        client, workspace_id,
        logic_compute_group_id=logic_compute_group_id, page_size=200,
    )
    nodes_raw = nd.get("node_dimensions") or nd.get("nodes") or []

    td = endpoints.list_task_dimension(client, workspace_id, page_size=200)
    tasks = td.get("task_dimensions") or td.get("tasks") or []

    # Low-priority GPU held per node name (spread a task's GPUs over its nodes).
    preemptible_by_node: dict[str, float] = defaultdict(float)
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
        per = gpu_total / count
        for nm in node_names:
            preemptible_by_node[nm] += per

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
        "n_nodes": len(nodes),
        "by_gpu_type": by_gpu_type,
        "nodes": nodes,
    }
