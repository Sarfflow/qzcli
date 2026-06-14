"""Shared test fixtures: a fake client that returns canned API responses."""

from __future__ import annotations

from typing import Any


class FakeClient:
    """Stands in for client.http.Client.

    ``responses`` maps a path substring (for post_api) or "v2:Action" key (for
    post_v2) to the value those methods would return (i.e. the unwrapped
    ``data`` for post_api). The matching path substring's value is returned.
    """

    def __init__(self, responses: dict[str, Any], base_url: str = "https://qz.sii.edu.cn"):
        self.base_url = base_url
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def post_api(self, path: str, payload: dict, *, referer=None, timeout: int = 60, _retried: bool = False):
        self.calls.append((path, payload))
        for key, value in self.responses.items():
            if key in path:
                return value
        return {}

    def post_v2(self, service: str, action: str, body: dict, *, timeout: int = 60, _retried: bool = False):
        self.calls.append((f"v2:{action}", body))
        return self.responses.get(f"v2:{action}", {})

    def require_cookie(self) -> str:
        return "fake=cookie"


PROJECTS_RESPONSE = {
    "items": [
        {
            "id": "project-alpha",
            "name": "Alpha",
            "en_name": "alpha",
            "space_list": [
                {"id": "ws-001", "name": "alpha-space"},
                {"id": "ws-002", "name": "shared-space"},
            ],
        },
        {
            "id": "project-beta",
            "name": "Beta",
            "en_name": "beta",
            "space_list": [
                {"id": "ws-003", "name": "beta-space"},
                {"id": "ws-002", "name": "shared-space"},  # also owned by alpha
            ],
        },
    ]
}

# Mirrors the real cluster_basic_info shape: physical compute_groups own nested
# logic_compute_groups; top-level resource_types maps type → gpu_info.
COMPUTE_GROUPS_RESPONSE = {
    "compute_groups": [
        {
            "compute_group_id": "cg-train",
            "compute_group_name": "训练池",
            "logic_compute_groups": [
                {
                    "logic_compute_group_id": "lcg-gpu",
                    "logic_compute_group_name": "gpu-group",
                    "resource_types": ["NVIDIA_H100_SXM_80G"],
                },
                {
                    "logic_compute_group_id": "lcg-cpu",
                    "logic_compute_group_name": "cpu-group",
                    "resource_types": [],
                },
            ],
        }
    ],
    "resource_types": [
        {
            "resource_type": "NVIDIA_H100_SXM_80G",
            "gpu_info": {"gpu_product_simple": "H100", "gpu_type_display": "NVIDIA H100 (80GB)"},
        }
    ],
}

JOBS_WITH_SPEC = {
    "jobs": [
        {
            "job_id": "job-1",
            "name": "prev-run",
            "status": "SUCCEEDED",
            "logic_compute_group_id": "lcg-gpu",
            "framework_config": [
                {
                    "instance_spec_price_info": {
                        "quota_id": "quota-h100-1",
                        "cpu_count": 16,
                        "gpu_count": 1,
                        "memory_size_gib": 128,
                        "gpu_info": {
                            "gpu_product_simple": "H100",
                            "gpu_type": "NVIDIA_H100_SXM_80G",
                            "gpu_type_display": "NVIDIA H100 (80GB)",
                        },
                    }
                }
            ],
        }
    ]
}

# /api/v1/resource_prices/logic_compute_groups/ — the authoritative spec table.
SPECS_RESPONSE = {
    "lcg_resource_spec_prices": [
        {
            "quota_id": "quota-h100-1",
            "cpu_count": 16,
            "gpu_count": 1,
            "memory_size_gib": 128,
            "gpu_info": {
                "gpu_product_simple": "H100",
                "gpu_type": "NVIDIA_H100_SXM_80G",
                "gpu_type_display": "NVIDIA H100 (80GB)",
            },
            "total_price_per_hour": 1.5,
        },
        {
            "quota_id": "quota-h100-8",
            "cpu_count": 110,
            "gpu_count": 8,
            "memory_size_gib": 1600,
            "gpu_info": {
                "gpu_product_simple": "H100",
                "gpu_type": "NVIDIA_H100_SXM_80G",
                "gpu_type_display": "NVIDIA H100 (80GB)",
            },
            "total_price_per_hour": 12.0,
        },
    ]
}

IMAGES_RESPONSE = {
    "images": [
        {"address": "docker.sii/inspire/torch:1.0", "name": "torch", "source": "SOURCE_PUBLIC"},
    ]
}
