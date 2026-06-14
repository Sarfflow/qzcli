"""High-level wrappers over the reverse-engineered web endpoints.

These functions take a :class:`~qzcli.client.http.Client` and return parsed
domain models (or raw dicts where there is no useful structure). They are the
*only* place endpoint paths and payload shapes live. No ``/openapi/`` here.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import ComputeGroup, Image, Job, Project
from .http import Client


def _ws_referer(client: Client, page: str, workspace_id: str) -> str:
    return f"{client.base_url}/jobs/{page}?spaceId={workspace_id}"


# --- projects & hierarchy -------------------------------------------------

def list_projects(client: Client) -> list[Project]:
    """All projects with their owned spaces (``/api/v1/project/list``)."""
    data = client.post_api(
        "project/list",
        {"page": 1, "page_size": 100, "filter": {}},
        referer=f"{client.base_url}/operations/projects",
    )
    items = (data or {}).get("items") or []
    return [Project.from_api(p) for p in items]


def cluster_basic_info(client: Client, workspace_id: str) -> dict[str, Any]:
    """Clusters + compute_groups + resource_types for a workspace."""
    return client.post_api(
        "cluster_metric/cluster_basic_info",
        {"workspace_id": workspace_id},
        referer=_ws_referer(client, "spacesOverview", workspace_id),
    ) or {}


def list_resource_specs(
    client: Client,
    workspace_id: str,
    logic_compute_group_id: str,
    *,
    schedule_config_type: str = "SCHEDULE_CONFIG_TYPE_TRAIN",
) -> list[dict[str, Any]]:
    """The authoritative per-compute-group spec/price table.

    Endpoint: ``POST /api/v1/resource_prices/logic_compute_groups/`` (trailing
    slash required). Returns ``lcg_resource_spec_prices`` — the full set of
    card-count options (1/2/4/8…) for the selected 机房, each with quota_id,
    cpu/gpu/mem and price. This is the create form's progressive-disclosure step
    after a compute group is chosen.
    """
    data = client.post_api(
        "resource_prices/logic_compute_groups/",
        {
            "workspace_id": workspace_id,
            "schedule_config_type": schedule_config_type,
            "logic_compute_group_id": logic_compute_group_id,
        },
        referer=_ws_referer(client, "distributedTraining", workspace_id),
    ) or {}
    return data.get("lcg_resource_spec_prices") or []


def list_compute_groups(client: Client, workspace_id: str) -> list[ComputeGroup]:
    """Logical compute groups (``lcg-``) available in a workspace.

    cluster_basic_info nests them two levels deep: physical ``compute_groups[]``
    each own ``logic_compute_groups[]``. We flatten to the logical groups (what
    a job submission targets) and resolve gpu fields via the top-level
    ``resource_types`` map.
    """
    info = cluster_basic_info(client, workspace_id)
    gpu_info_by_type = {
        rt.get("resource_type", ""): rt.get("gpu_info", {})
        for rt in (info.get("resource_types") or [])
    }
    out: list[ComputeGroup] = []
    for physical in info.get("compute_groups") or []:
        cg_id = physical.get("compute_group_id", "")
        for lcg in physical.get("logic_compute_groups") or []:
            out.append(
                ComputeGroup.from_logic_group(
                    lcg,
                    workspace_id=workspace_id,
                    compute_group_id=cg_id,
                    gpu_info_by_type=gpu_info_by_type,
                )
            )
    return out


# --- jobs -----------------------------------------------------------------

def list_jobs(
    client: Client,
    workspace_id: str,
    *,
    page_num: int = 1,
    page_size: int = 100,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    """Raw {jobs, total} from ``/api/v1/train_job/list``."""
    payload: dict[str, Any] = {
        "page_num": page_num,
        "page_size": page_size,
        "workspace_id": workspace_id,
    }
    if created_by:
        payload["created_by"] = created_by
    return client.post_api(
        "train_job/list",
        payload,
        referer=_ws_referer(client, "distributedTraining", workspace_id),
    ) or {}


def list_job_models(client: Client, workspace_id: str, **kw) -> list[Job]:
    data = list_jobs(client, workspace_id, **kw)
    return [Job.from_api(j) for j in (data.get("jobs") or data.get("items") or [])]


def create_job(client: Client, payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a training job (``/api/v1/train_job/create``)."""
    workspace_id = payload.get("workspace_id", "")
    return client.post_api(
        "train_job/create",
        payload,
        referer=_ws_referer(client, "distributedTraining", workspace_id),
    ) or {}


def job_detail(client: Client, job_id: str) -> dict[str, Any]:
    """Job detail via cookie path (``/api/v1/train_job/detail``)."""
    return client.post_api(
        "train_job/detail",
        {"job_id": job_id},
        referer=f"{client.base_url}/jobs/distributedTrainingDetail/{job_id}",
    ) or {}


def stop_job(client: Client, job_id: str) -> dict[str, Any]:
    """Stop a job via cookie path (OpenAPI stop fails with invalid_grant for CAS users)."""
    return client.post_api(
        "train_job/stop",
        {"job_id": job_id},
        referer=f"{client.base_url}/jobs/distributedTrainingDetail/{job_id}",
    ) or {}


def job_events(client: Client, job_id: str) -> list[dict[str, Any]]:
    """Job-level events. NOTE: body uses camelCase ``jobId`` (proto rejects snake)."""
    data = client.post_api(
        "train_job/job_event_list",
        {"jobId": job_id},
        referer=f"{client.base_url}/jobs/distributedTrainingDetail/{job_id}",
    ) or {}
    return data.get("events") or []


def instance_events(
    client: Client, job_id: str, instance_name: str
) -> list[dict[str, Any]]:
    """Per-pod events. instance_name like ``<job_id>-worker-0``."""
    data = client.post_api(
        "train_job/instance_event_list",
        {"jobId": job_id, "instanceName": instance_name},
        referer=f"{client.base_url}/jobs/distributedTrainingDetail/{job_id}",
    ) or {}
    return data.get("events") or []


def job_logs(
    client: Client,
    job_id: str,
    *,
    page_size: int = 200,
    pod_names: Optional[list[str]] = None,
    start_timestamp_ms: Optional[str] = None,
    end_timestamp_ms: Optional[str] = None,
    sort: str = "ascend",
) -> dict[str, Any]:
    """Container logs via the cookie-authed v2 ``GetJobLog`` action.

    Returns ``{"logs": [...], "total": int}``; each entry has message, pod_name,
    node, time, timestamp_ms, etc.
    """
    if pod_names is None:
        pod_names = resolve_pod_names(client, job_id)
    body: dict[str, Any] = {
        "page_size": page_size,
        "filter": {"podNames": pod_names},
        "sorter": [
            {"field": "time", "sort": sort},
            {"field": "log-id.keyword", "sort": sort},
        ],
    }
    if start_timestamp_ms is not None:
        body["filter"]["start_timestamp_ms"] = str(start_timestamp_ms)
    if end_timestamp_ms is not None:
        body["filter"]["end_timestamp_ms"] = str(end_timestamp_ms)
    result = client.post_v2("train", "GetJobLog", body)
    if isinstance(result, dict) and isinstance(result.get("Result"), dict):
        result = result["Result"]
    return result if isinstance(result, dict) else {"logs": result}


def resolve_pod_names(
    client: Client, job_id: str, n_instances: Optional[int] = None
) -> list[str]:
    """Pods follow ``{job_id}-worker-{i}``; infer count from detail if needed."""
    if n_instances is None:
        try:
            d = job_detail(client, job_id)
            fc = d.get("framework_config")
            if isinstance(fc, list) and fc and isinstance(fc[0], dict):
                n_instances = fc[0].get("instance_count")
            if not n_instances:
                n_instances = (
                    d.get("instance_count") or d.get("instances") or d.get("replica_count")
                )
        except Exception:
            n_instances = None
    if not n_instances or n_instances < 1:
        n_instances = 1
    return [f"{job_id}-worker-{i}" for i in range(int(n_instances))]


# --- images ---------------------------------------------------------------

def list_images(
    client: Client, workspace_id: str, source: str = "ALL"
) -> list[Image]:
    """Images via ``/api/v1/image/list``. ``ALL`` merges OFFICIAL + PUBLIC."""
    sources = [source]
    if source == "ALL":
        sources = ["SOURCE_OFFICIAL", "SOURCE_PUBLIC"]
    out: list[Image] = []
    for src in sources:
        data = client.post_api(
            "image/list",
            {
                "page": 0,
                "page_size": -1,
                "filter": {
                    "source": src,
                    "source_list": [],
                    "registry_hint": {"workspace_id": workspace_id},
                },
            },
            referer=_ws_referer(client, "distributedTraining", workspace_id),
        ) or {}
        for im in data.get("images") or []:
            im.setdefault("_source", src)
            out.append(Image.from_api(im))
    return out


# --- cluster availability -------------------------------------------------

def list_node_dimension(
    client: Client,
    workspace_id: str,
    *,
    logic_compute_group_id: Optional[str] = None,
    compute_group_id: Optional[str] = None,
    page_num: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    """Per-node free/used resource view (``cluster_metric/list_node_dimension``)."""
    flt: dict[str, Any] = {"workspace_id": workspace_id}
    if logic_compute_group_id:
        flt["logic_compute_group_id"] = logic_compute_group_id
    if compute_group_id:
        flt["compute_group_id"] = compute_group_id
    return client.post_api(
        "cluster_metric/list_node_dimension",
        {"page_num": page_num, "page_size": page_size, "filter": flt},
        referer=_ws_referer(client, "spacesOverview", workspace_id),
    ) or {}


def list_task_dimension(
    client: Client,
    workspace_id: str,
    *,
    project_id: Optional[str] = None,
    page_num: int = 1,
    page_size: int = 200,
) -> dict[str, Any]:
    """Per-task resource view (``cluster_metric/list_task_dimension``)."""
    flt: dict[str, Any] = {"workspace_id": workspace_id}
    if project_id:
        flt["project_id"] = project_id
    return client.post_api(
        "cluster_metric/list_task_dimension",
        {"page_num": page_num, "page_size": page_size, "filter": flt},
        referer=_ws_referer(client, "spacesOverview", workspace_id),
    ) or {}
