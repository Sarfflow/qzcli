"""Typed views over the platform's raw JSON.

The cardinal rule (the old code's worst bug): preserve the project→space
hierarchy. ``/api/v1/project/list`` returns projects that each own a
``space_list``; we keep that nesting instead of flattening to a workspace table
and losing "which project owns which space".

Each model keeps the original ``raw`` dict so nothing is lost, and exposes a
``to_dict()`` with stable field names for JSON output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass
class Space:
    """A workspace (``ws-...``) owned by a project."""

    id: str
    name: str
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Space":
        return cls(id=d.get("id", ""), name=d.get("name", ""), raw=d)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name}


@dataclass
class Project:
    """A project (``project-...``) and the spaces it owns."""

    id: str
    name: str
    en_name: str
    spaces: list[Space] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Project":
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            en_name=d.get("en_name", ""),
            spaces=[Space.from_api(s) for s in (d.get("space_list") or [])],
            raw=d,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "en_name": self.en_name,
            "spaces": [s.to_dict() for s in self.spaces],
        }


@dataclass
class ComputeGroup:
    """A logical compute group (``lcg-...``) within a workspace.

    ``id`` is the ``lcg-`` id used as ``logic_compute_group_id`` when submitting
    a job. ``compute_group_id`` is the parent physical group (``cg-``).
    """

    id: str
    name: str
    workspace_id: str
    compute_group_id: str = ""
    gpu_type: str = ""
    gpu_type_display: str = ""
    resource_types: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_logic_group(
        cls,
        lcg: dict[str, Any],
        *,
        workspace_id: str,
        compute_group_id: str,
        gpu_info_by_type: dict[str, dict[str, Any]],
    ) -> "ComputeGroup":
        """Build from a nested ``logic_compute_groups[]`` entry of cluster_basic_info.

        ``gpu_info_by_type`` maps a ``resource_type`` string to the platform's
        gpu_info dict (from the top-level ``resource_types``), so gpu fields are
        resolved without guessing.
        """
        rts = lcg.get("resource_types") or []
        gpu = gpu_info_by_type.get(rts[0], {}) if rts else {}
        return cls(
            id=lcg.get("logic_compute_group_id", ""),
            name=lcg.get("logic_compute_group_name", ""),
            workspace_id=workspace_id,
            compute_group_id=compute_group_id,
            gpu_type=gpu.get("gpu_product_simple", ""),
            gpu_type_display=gpu.get("gpu_type_display", ""),
            resource_types=rts,
            raw=lcg,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "workspace_id": self.workspace_id,
            "compute_group_id": self.compute_group_id,
            "gpu_type": self.gpu_type,
            "gpu_type_display": self.gpu_type_display,
            "resource_types": self.resource_types,
        }


@dataclass
class Spec:
    """A resource spec / quota — the unit a job's resources are billed against.

    ``quota_id`` is the id submitted in ``resource_spec_price.quota_id``.
    cpu/gpu/mem are the concrete resource amounts required to build the payload.
    """

    quota_id: str
    cpu_count: int = 0
    gpu_count: int = 0
    memory_gb: int = 0
    gpu_type: str = ""  # FULL type, e.g. "NVIDIA_H100_SXM_80G" — required in the payload
    gpu_type_simple: str = ""  # e.g. "H100" — for display
    gpu_type_display: str = ""
    total_price_per_hour: float = 0
    logic_compute_group_ids: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_resource_spec_price(
        cls, d: dict[str, Any], lcg_id: str = ""
    ) -> "Spec":
        """Build from a ``lcg_resource_spec_prices[]`` entry.

        This is the authoritative per-compute-group spec table (the 1/2/4/8-card
        options the web create form shows), returned by
        ``/api/v1/resource_prices/logic_compute_groups/``.
        """
        gpu = d.get("gpu_info") or {}
        return cls(
            quota_id=d.get("quota_id", ""),
            cpu_count=_to_int(d.get("cpu_count")),
            gpu_count=_to_int(d.get("gpu_count")),
            memory_gb=_to_int(d.get("memory_size_gib")),
            gpu_type=gpu.get("gpu_type", ""),
            gpu_type_simple=gpu.get("gpu_product_simple", ""),
            gpu_type_display=gpu.get("gpu_type_display", ""),
            total_price_per_hour=d.get("total_price_per_hour", 0) or 0,
            logic_compute_group_ids=[lcg_id] if lcg_id else [],
            raw=d,
        )

    @classmethod
    def from_spec_price_info(
        cls, info: dict[str, Any], lcg_id: str = ""
    ) -> "Spec":
        """Build from a job's ``framework_config[0].instance_spec_price_info``.

        ``gpu_type`` must be the *full* platform type (``gpu_info.gpu_type``),
        not the simplified one — the create endpoint rejects the mismatch with
        "gpu_type \"H100\" does not match predefined spec ... (allowed: ...)".
        """
        gpu = info.get("gpu_info") or {}
        return cls(
            quota_id=info.get("quota_id", ""),
            cpu_count=_to_int(info.get("cpu_count")),
            gpu_count=_to_int(info.get("gpu_count")),
            memory_gb=_to_int(info.get("memory_size_gib")),
            gpu_type=gpu.get("gpu_type", ""),
            gpu_type_simple=gpu.get("gpu_product_simple", ""),
            gpu_type_display=gpu.get("gpu_type_display", ""),
            logic_compute_group_ids=[lcg_id] if lcg_id else [],
            raw=info,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "quota_id": self.quota_id,
            "cpu_count": self.cpu_count,
            "gpu_count": self.gpu_count,
            "memory_gb": self.memory_gb,
            "gpu_type": self.gpu_type,
            "gpu_type_simple": self.gpu_type_simple,
            "gpu_type_display": self.gpu_type_display,
            "total_price_per_hour": self.total_price_per_hour,
            "logic_compute_group_ids": self.logic_compute_group_ids,
        }


@dataclass
class Image:
    """A container image available on the platform."""

    address: str
    name: str
    image_id: str = ""
    source: str = ""
    visibility: str = ""
    creator: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Image":
        return cls(
            address=d.get("address", ""),
            name=d.get("name", ""),
            image_id=d.get("image_id", ""),
            source=d.get("source") or d.get("_source", ""),
            visibility=d.get("visibility", ""),
            creator=d.get("creator", ""),
            raw=d,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "name": self.name,
            "image_id": self.image_id,
            "source": self.source,
            "visibility": self.visibility,
            "creator": self.creator,
        }


@dataclass
class Job:
    """A training job summary."""

    job_id: str
    name: str
    status: str
    workspace_id: str = ""
    project_id: str = ""
    logic_compute_group_id: str = ""
    created_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> "Job":
        return cls(
            job_id=d.get("job_id") or d.get("id", ""),
            name=d.get("name", ""),
            status=d.get("status", ""),
            workspace_id=d.get("workspace_id", ""),
            project_id=d.get("project_id", ""),
            logic_compute_group_id=d.get("logic_compute_group_id", ""),
            created_at=d.get("created_at") or d.get("create_time", ""),
            raw=d,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "logic_compute_group_id": self.logic_compute_group_id,
            "created_at": self.created_at,
        }
