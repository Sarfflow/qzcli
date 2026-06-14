import pytest

from qzcli.core import options
from qzcli.errors import QzError
from conftest import (
    COMPUTE_GROUPS_RESPONSE,
    FakeClient,
    PROJECTS_RESPONSE,
    SPECS_RESPONSE,
)


def test_resolve_workspace_by_id_keeps_owning_project():
    c = FakeClient({"project/list": PROJECTS_RESPONSE})
    project, space = options.resolve_workspace(c, "ws-003")
    assert space.id == "ws-003"
    assert project.id == "project-beta"  # hierarchy preserved


def test_resolve_workspace_by_name():
    c = FakeClient({"project/list": PROJECTS_RESPONSE})
    project, space = options.resolve_workspace(c, "alpha-space")
    assert space.id == "ws-001"
    assert project.id == "project-alpha"


def test_resolve_workspace_unknown_lists_candidates():
    c = FakeClient({"project/list": PROJECTS_RESPONSE})
    with pytest.raises(QzError) as ei:
        options.resolve_workspace(c, "does-not-exist")
    err = ei.value
    assert err.code == "invalid_workspace"
    ids = {cand["workspace_id"] for cand in err.candidates}
    assert ids == {"ws-001", "ws-002", "ws-003"}


def test_resolve_compute_group_unknown_lists_candidates():
    c = FakeClient({"cluster_basic_info": COMPUTE_GROUPS_RESPONSE})
    with pytest.raises(QzError) as ei:
        options.resolve_compute_group(c, "ws-001", "nope")
    assert ei.value.code == "invalid_compute_group"
    assert {c["id"] for c in ei.value.candidates} == {"lcg-gpu", "lcg-cpu"}


def test_specs_from_resource_prices_endpoint():
    c = FakeClient({"resource_prices/logic_compute_groups": SPECS_RESPONSE})
    specs = options.specs(c, "ws-001", "lcg-gpu")
    assert [s.gpu_count for s in specs] == [1, 8]  # full card-count table
    s = specs[0]
    assert s.quota_id == "quota-h100-1"
    assert s.cpu_count == 16
    assert s.memory_gb == 128
    assert s.gpu_type == "NVIDIA_H100_SXM_80G"  # full type for the payload
    assert s.gpu_type_simple == "H100"
    assert s.total_price_per_hour == 1.5


def test_specs_requires_compute_group():
    c = FakeClient({})
    with pytest.raises(QzError) as ei:
        options.specs(c, "ws-001", "")
    assert ei.value.code == "missing_compute_group"
