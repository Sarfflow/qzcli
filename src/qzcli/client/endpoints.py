"""High-level wrappers over the reverse-engineered web endpoints.

These functions take a :class:`~qzcli.client.http.Client` and return parsed
domain models (or raw dicts where there is no useful structure). They are the
*only* place endpoint paths and payload shapes live. No ``/openapi/`` here.
"""

from __future__ import annotations

from typing import Any, Optional

from ..domain.models import ComputeGroup, Image, Job, Project
from ..errors import QzError
from .http import Client


def _v2_result(client: Client, service: str, action: str, body: dict[str, Any]) -> Any:
    """POST a v2 action, unwrap ``Result``, and raise on a ResponseMetadata error.

    The v2 surface returns 200 with a ``{ResponseMetadata:{Error:{Code,Message}}}``
    body on business failures (it does not use HTTP status), so callers that only
    check the transport would treat a failure as success.
    """
    r = client.post_v2(service, action, body)
    if isinstance(r, dict):
        err = (r.get("ResponseMetadata") or {}).get("Error")
        if err:
            raise QzError(
                f"{action} 失败: {err.get('Message') or err.get('Code')}",
                code="api_error",
                hint=f"平台错误码 {err.get('Code')}",
            )
        if isinstance(r.get("Result"), dict):
            return r["Result"]
    return r


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


def validate_datasets(
    client: Client, workspace_id: str, datasets: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Validate dataset/version references against a workspace.

    Endpoint: ``POST /api/v1/dataset/validate_dataset``. ``datasets`` is a list
    of ``{"dataset_id", "version_id"}`` (empty version = default/latest).
    Returns ``datasets_result``: per-dataset ``{dataset_id, version_id, success,
    error_message, path}`` — ``path`` is the mount path on success; failures
    distinguish missing dataset (2000) vs missing version (2001).
    """
    data = client.post_api(
        "dataset/validate_dataset",
        {"workspace_id": workspace_id, "datasets": datasets},
        referer=_ws_referer(client, "distributedTraining", workspace_id),
    ) or {}
    return data.get("datasets_result") or []


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


METRIC_TYPES = [
    "gpu_usage_rate", "gpu_memory_usage_rate", "cpu_usage_rate", "memory_usage_rate",
    "disk_io_read", "disk_io_write", "network_io_read", "network_io_write",
    "network_storage_io_read", "network_storage_io_write",
]


def get_task_metric(
    client: Client,
    *,
    logic_compute_group_id: str,
    task_id: str,
    metric_types: list[str],
    start_timestamp: int,
    end_timestamp: int,
    interval_second: int = 60,
    task_type: str = "distributed_training",
) -> list[dict[str, Any]]:
    """Per-instance resource time series via v2 ``GetTaskMetric``.

    Returns ``time_seris_metric_groups`` (sic — the platform misspells it):
    ``[{resource_name, metric_type, group_name (pod), time_series: [{timestamp, data}]}]``.
    Timestamps are unix seconds; ``data`` is the rate (0..1 for *_usage_rate).

    The endpoint only honours ONE metric per call — batching multiple
    ``metric_types`` silently returns just the first. So we issue one request
    per metric (like the web does) and merge the resulting groups.
    """
    filt = {
        "logic_compute_group_id": logic_compute_group_id,
        "task_type": task_type,
        "task_id": task_id,
    }
    time_range = {
        "start_timestamp": start_timestamp,
        "end_timestamp": end_timestamp,
        "interval_second": interval_second,
    }
    groups: list[dict[str, Any]] = []
    for metric in metric_types:
        result = client.post_v2(
            "train", "GetTaskMetric",
            {"metric_types": [metric], "filter": filt, "time_range": time_range},
        )
        if isinstance(result, dict) and isinstance(result.get("Result"), dict):
            result = result["Result"]
        groups.extend((result or {}).get("time_seris_metric_groups") or [])
    return groups


def list_job_instances(client: Client, job_id: str) -> list[dict[str, Any]]:
    """A job's real instances/pods via v2 ``ListJobInstances``.

    Returns ``items`` like ``{name, instance_type, node, instance_status,
    created_at, started_at, finished_at, running_time_ms}``. ``name`` is the
    actual pod name — authoritative, not guessed.
    """
    result = client.post_v2(
        "train", "ListJobInstances",
        {"job_id": job_id, "page_num": 1, "page_size": -1},
    )
    if isinstance(result, dict) and isinstance(result.get("Result"), dict):
        result = result["Result"]
    return (result or {}).get("items") or []


def resolve_pod_names(
    client: Client, job_id: str, n_instances: Optional[int] = None
) -> list[str]:
    """Real pod names from ListJobInstances; fall back to the naming convention
    only if the instance list is unavailable (e.g. job not yet scheduled)."""
    try:
        names = [i.get("name") for i in list_job_instances(client, job_id) if i.get("name")]
        if names:
            return names
    except Exception:
        pass
    # Fallback: the platform names pods {job_id}-worker-{i}.
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


# --- notebooks (interactive modeling) -------------------------------------

def list_notebooks(
    client: Client, workspace_id: str, *, page: int = 0, page_size: int = 100
) -> list[dict[str, Any]]:
    """Interactive-modeling notebooks via ``/api/v1/notebook/list``.

    Returns the raw notebook objects (the list endpoint already carries full
    detail — image, backup_image, quota, logic_compute_group, start_config,
    extra_info(ssh), save_mirror_status — so there is no separate detail call).
    """
    data = client.post_api(
        "notebook/list",
        {"workspace_id": workspace_id, "page": page, "page_size": page_size},
        referer=_ws_referer(client, "interactiveModeling", workspace_id),
    ) or {}
    return data.get("list") or []


def list_notebook_compute_groups(
    client: Client, workspace_id: str
) -> list[dict[str, Any]]:
    """机房 (logic compute groups) that support interactive modeling.

    Notebooks use a different 机房 set than training — via v2
    ``ListLogicComputeGroups`` filtered by ``support_job_type=interactive_modeling``.
    """
    r = _v2_result(
        client, "workspace", "ListLogicComputeGroups",
        {"filter": {"workspace_id": workspace_id, "include_gpu_type_stats": True,
                    "support_job_type": "interactive_modeling"},
         "page_num": 1, "page_size": -1, "sorter": []},
    )
    return (r or {}).get("logic_compute_groups") or []


def get_notebook(client: Client, notebook_id: str) -> dict[str, Any]:
    return _v2_result(client, "notebook", "GetNotebook", {"notebook_id": notebook_id})


def create_notebook(client: Client, payload: dict[str, Any]) -> dict[str, Any]:
    return _v2_result(client, "notebook", "CreateNotebook", payload)


def stop_notebook(client: Client, notebook_id: str) -> dict[str, Any]:
    return _v2_result(client, "notebook", "StopNotebook", {"notebook_id": notebook_id})


def delete_notebook(client: Client, notebook_id: str) -> dict[str, Any]:
    return _v2_result(client, "notebook", "DeleteNotebook", {"notebook_id": notebook_id})


def save_notebook_image(
    client: Client, notebook_id: str, name: str, version: str, *, accessible: int = 1
) -> dict[str, Any]:
    """Save a running notebook as a personal image (``SaveNotebookImage``).

    Body uses camelCase ``notebookId``. ``accessible=1`` = private personal image
    (the common case). The notebook must be RUNNING.
    """
    return _v2_result(
        client, "notebook", "SaveNotebookImage",
        {"notebookId": notebook_id, "name": name, "version": version,
         "accessible": accessible},
    )


def estimate_save_size(client: Client, notebook_id: str) -> dict[str, Any]:
    """Estimate the size of saving a notebook's image (running notebooks only)."""
    return client.post_api("mirror/save/estimate_size",
                           {"notebook_id": notebook_id}) or {}


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
