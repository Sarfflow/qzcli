"""Job creation with an enforced read-before-write step (principle #2).

``prepare()`` does the whole "read": it resolves and validates every field
(workspace, project, compute group, spec, image), and either returns a fully
built payload or raises a :class:`QzError` that says exactly what is missing /
illegal and lists the legal candidates. It never guesses or auto-selects.

``dry_run()`` is just ``prepare()`` surfaced to the user — it returns the
resolved selections + payload and submits nothing. ``submit()`` runs the very
same ``prepare()`` and only POSTs if it passed, so a real create can never
bypass the read.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Any, Optional

from ..client import endpoints
from ..client.http import Client
from ..domain.models import Spec
from ..errors import QzError
from . import options

DEFAULT_FRAMEWORK = "pytorch"
DEFAULT_IMAGE_TYPE = "SOURCE_PRIVATE"
DEFAULT_INSTANCES = 1
DEFAULT_SHM = 1200
DEFAULT_PRIORITY = 10


@dataclass
class CreateRequest:
    name: str
    workspace: str
    compute_group: str
    image: str
    command: str
    project: Optional[str] = None
    quota_id: Optional[str] = None
    cpu: Optional[int] = None
    gpu: Optional[int] = None
    mem: Optional[int] = None
    framework: str = DEFAULT_FRAMEWORK
    image_type: Optional[str] = None  # None → derive from the matched image's source
    instances: int = DEFAULT_INSTANCES
    shm: Optional[int] = None  # None → default to the spec's memory (web behaviour)
    priority: int = DEFAULT_PRIORITY
    check_image: bool = True
    datasets: list[str] = field(default_factory=list)  # "id" or "id:version" specs


def _resource_spec_price(spec: Spec, compute_group_id: str) -> dict[str, Any]:
    return {
        "cpu_type": "",
        "cpu_count": spec.cpu_count,
        "gpu_type": spec.gpu_type,
        "gpu_count": spec.gpu_count,
        "memory_size_gib": spec.memory_gb,
        "logic_compute_group_id": compute_group_id,
        "quota_id": spec.quota_id,
    }


def _resolve_spec(
    client: Client, workspace_id: str, compute_group_id: str, req: CreateRequest
) -> Spec:
    """Resolve the spec, honoring explicit cpu/gpu/mem overrides.

    - explicit quota_id + cpu + mem given → build directly (no history needed);
    - quota_id given without cpu/mem → look it up in job history for the amounts;
    - nothing given → error listing the spec candidates.
    """
    explicit_resources = req.cpu is not None and req.mem is not None
    if req.quota_id and explicit_resources:
        return Spec(
            quota_id=req.quota_id,
            cpu_count=int(req.cpu),
            gpu_count=int(req.gpu or 0),
            memory_gb=int(req.mem),
            logic_compute_group_ids=[compute_group_id],
        )
    if req.quota_id:
        spec = options.resolve_spec(
            client, workspace_id, compute_group_id, req.quota_id
        )
        # allow partial overrides on top of the history-derived spec
        if req.cpu is not None:
            spec.cpu_count = int(req.cpu)
        if req.gpu is not None:
            spec.gpu_count = int(req.gpu)
        if req.mem is not None:
            spec.memory_gb = int(req.mem)
        return spec
    # No quota id at all → enumerate candidates.
    found = options.specs(client, workspace_id, compute_group_id)
    raise QzError(
        "未指定规格(quota)",
        code="missing_spec",
        hint="传 --quota-id <id>（候选见 candidates），或 --quota-id 加 --cpu/--gpu/--mem 自定义",
        candidates=[s.to_dict() for s in found],
    )


def _resolve_datasets(
    client: Client, workspace_id: str, specs: list[str]
) -> list[dict[str, str]]:
    """Validate dataset refs (read-before-write) and build the payload's
    top-level ``dataset_info`` ``[{dataset_id, version_id, path}]``.

    Raises with the platform's per-dataset reason on any failure (missing
    dataset / missing version), so an agent knows exactly which ref is wrong.
    """
    if not specs:
        return []
    refs = []
    for s in specs:
        dataset_id, _, version_id = s.partition(":")
        refs.append({"dataset_id": dataset_id, "version_id": version_id})
    results = endpoints.validate_datasets(client, workspace_id, refs)
    bad = [r for r in results if not r.get("success")]
    if bad:
        detail = "; ".join(
            f"{r.get('dataset_id')}:{r.get('version_id') or '默认'} → {r.get('error_message')}"
            for r in bad
        )
        raise QzError(
            f"数据集校验失败: {detail}",
            code="invalid_dataset",
            hint="确认 dataset_id/version_id；可先 qzcli dataset validate 预检",
        )
    return [
        {
            "dataset_id": r.get("dataset_id", ""),
            "version_id": r.get("version_id", ""),
            "path": r.get("path", ""),
        }
        for r in results
    ]


def _check_image(client: Client, workspace_id: str, image: str):
    """Validate the image exists; return the matched Image (for image_type)."""
    images = options.images(client, workspace_id, source="ALL")
    by_addr = {im.address: im for im in images if im.address}
    if image in by_addr:
        return by_addr[image]
    close = difflib.get_close_matches(image, list(by_addr), n=5, cutoff=0.6)
    raise QzError(
        f"镜像 '{image}' 不在平台镜像列表中（共 {len(by_addr)} 个可用）",
        code="invalid_image",
        hint="用 candidates 里的 address，或加 --no-image-check 跳过校验",
        candidates=close or list(by_addr)[:20],
    )


def prepare(client: Client, req: CreateRequest) -> dict[str, Any]:
    """The read step. Returns {resolved, payload} or raises QzError."""
    # 1. required fields present?
    missing = [
        label
        for label, val in (
            ("--name", req.name),
            ("--workspace", req.workspace),
            ("--compute-group", req.compute_group),
            ("--image", req.image),
            ("--cmd", req.command),
        )
        if not val
    ]
    if missing:
        raise QzError(
            f"缺少必填字段: {', '.join(missing)}",
            code="missing_fields",
            hint="补齐上述字段后重试；先用 qzcli options 读候选",
        )

    # 2. numeric sanity.
    if req.instances < 1:
        raise QzError(f"--instances 必须 >= 1，当前 {req.instances}", code="invalid_value")
    if not (1 <= req.priority <= 10):
        raise QzError(f"--priority 必须在 [1,10]，当前 {req.priority}", code="invalid_value")
    if req.shm is not None and req.shm < 1:
        raise QzError(f"--shm 必须 >= 1 GiB，当前 {req.shm}", code="invalid_value")

    # 3. resolve hierarchy (raises with candidates on miss).
    #    A space often belongs to MULTIPLE projects. The owning project is a
    #    real choice the tool must NOT guess (not even from history) — the agent
    #    picks it. We accept an explicit --project, or auto-use the single owner
    #    when there is genuinely only one; anything else demands --project.
    projects = endpoints.list_projects(client)
    _project, space = options.resolve_workspace(client, req.workspace, projects)
    workspace_id = space.id
    owners = [p for p in projects if any(s.id == workspace_id for s in p.spaces)]
    owner_candidates = [{"id": p.id, "name": p.name} for p in owners]
    project_source = "explicit"

    if req.project:
        match = [p for p in owners if req.project in (p.id, p.name)]
        if not match:
            match = [p for p in owners if req.project.lower() in p.name.lower()]
        if len(match) != 1:
            raise QzError(
                f"项目 '{req.project}' 不唯一或不拥有该工作空间",
                code="invalid_project",
                hint="--project 必须是下面 candidates 中拥有该空间的项目之一",
                candidates=owner_candidates,
            )
        project = match[0]
    elif len(owners) == 1:
        project = owners[0]
        project_source = "sole_owner"
    else:
        raise QzError(
            f"工作空间 {workspace_id} 归属多个项目，必须用 --project 明确指定",
            code="ambiguous_project",
            hint="从 candidates 中选一个项目传给 --project（由调用方/agent 决定，不由工具猜测）",
            candidates=owner_candidates,
        )
    project_id = project.id

    cg = options.resolve_compute_group(client, workspace_id, req.compute_group)
    compute_group_id = cg.id

    # 4. resolve spec + concrete resources.
    spec = _resolve_spec(client, workspace_id, compute_group_id, req)
    if spec.cpu_count <= 0 or spec.memory_gb <= 0:
        raise QzError(
            f"规格 '{spec.quota_id}' 缺少 cpu/mem（平台会拒绝 'Cpu and Mem can't be empty'）",
            code="incomplete_spec",
            hint="显式传 --cpu 和 --mem，或换一个有完整资源记录的 quota",
        )

    # 5. image existence (soft-but-blocking; skip with check_image=False).
    #    When found, default image_type to the image's own source so an
    #    OFFICIAL/PUBLIC image isn't submitted as SOURCE_PRIVATE by accident.
    image_type = req.image_type
    if req.check_image:
        matched = _check_image(client, workspace_id, req.image)
        if image_type is None:
            image_type = matched.source or DEFAULT_IMAGE_TYPE
    if image_type is None:
        image_type = DEFAULT_IMAGE_TYPE

    # shm defaults to the spec's memory (matches the web platform default).
    shm = req.shm if req.shm is not None else spec.memory_gb

    # datasets: validate refs (read-before-write) → top-level dataset_info.
    dataset_info = _resolve_datasets(client, workspace_id, req.datasets)

    # 6. build payload. framework_config[0] needs BOTH the nested
    #    resource_spec_price AND the top-level cpu/mem_gi/gpu_count, else the
    #    platform rejects with "Cpu and Mem can't be empty.".
    spec_price = _resource_spec_price(spec, compute_group_id)
    payload = {
        "name": req.name,
        "logic_compute_group_id": compute_group_id,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "framework": req.framework,
        "command": req.command,
        "task_priority": req.priority,
        "auto_fault_tolerance": False,
        "dataset_info": dataset_info,
        "framework_config": [
            {
                "cpu": spec.cpu_count,
                "gpu_count": spec.gpu_count,
                "mem_gi": spec.memory_gb,
                "resource_spec_price": spec_price,
                "image": req.image,
                "image_type": image_type,
                "instance_count": req.instances,
                "shm_gi": shm,
            }
        ],
    }
    resolved = {
        "name": req.name,
        "workspace_id": workspace_id,
        "workspace_name": space.name,
        "project_id": project_id,
        "project_name": project.name,
        "project_source": project_source,
        "compute_group_id": compute_group_id,
        "compute_group_name": cg.name,
        "quota_id": spec.quota_id,
        "cpu": spec.cpu_count,
        "gpu_count": spec.gpu_count,
        "mem_gi": spec.memory_gb,
        "gpu_type": spec.gpu_type,
        "image": req.image,
        "image_type": image_type,
        "framework": req.framework,
        "instances": req.instances,
        "shm_gi": shm,
        "priority": req.priority,
        "datasets": dataset_info,
    }
    return {"resolved": resolved, "payload": payload}


def dry_run(client: Client, req: CreateRequest) -> dict[str, Any]:
    """Validate everything and return the payload preview. Submits nothing."""
    prepared = prepare(client, req)
    return {"dry_run": True, **prepared}


def submit(client: Client, req: CreateRequest) -> dict[str, Any]:
    """Run the same read step, then POST only if it passed."""
    prepared = prepare(client, req)
    result = endpoints.create_job(client, prepared["payload"])
    job_id = result.get("job_id", "")
    workspace_id = result.get("workspace_id") or prepared["resolved"]["workspace_id"]
    if not job_id:
        raise QzError(
            "任务创建失败：响应中未包含 job_id",
            code="create_failed",
            hint=f"平台原始响应: {result}",
        )
    return {
        "job_id": job_id,
        "workspace_id": workspace_id,
        "name": req.name,
        "url": (
            f"{client.base_url}/jobs/distributedTrainingDetail/"
            f"{job_id}?spaceId={workspace_id}"
        ),
        "resolved": prepared["resolved"],
    }
