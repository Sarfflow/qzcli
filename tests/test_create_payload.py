import pytest

from qzcli.core import create as create_core
from qzcli.errors import QzError
from conftest import (
    COMPUTE_GROUPS_RESPONSE,
    FakeClient,
    IMAGES_RESPONSE,
    PROJECTS_RESPONSE,
    SPECS_RESPONSE,
)


def _full_client():
    return FakeClient(
        {
            "project/list": PROJECTS_RESPONSE,
            "cluster_basic_info": COMPUTE_GROUPS_RESPONSE,
            "resource_prices/logic_compute_groups": SPECS_RESPONSE,
            "image/list": IMAGES_RESPONSE,
        }
    )


def _req(**over):
    base = dict(
        name="my-job",
        workspace="ws-001",
        compute_group="lcg-gpu",
        image="docker.sii/inspire/torch:1.0",
        command="python train.py",
        quota_id="quota-h100-1",
    )
    base.update(over)
    return create_core.CreateRequest(**base)


def test_prepare_builds_payload_with_nested_and_toplevel_resources():
    c = _full_client()
    out = create_core.prepare(c, _req())
    payload = out["payload"]

    # hierarchy resolved
    assert payload["workspace_id"] == "ws-001"
    assert payload["project_id"] == "project-alpha"
    assert payload["logic_compute_group_id"] == "lcg-gpu"

    fc = payload["framework_config"][0]
    # top-level cpu/mem_gi/gpu_count must be present (else platform rejects)
    assert fc["cpu"] == 16
    assert fc["mem_gi"] == 128
    assert fc["gpu_count"] == 1
    # nested resource_spec_price carries quota + translated memory field
    rsp = fc["resource_spec_price"]
    assert rsp["quota_id"] == "quota-h100-1"
    assert rsp["memory_size_gib"] == 128
    assert rsp["logic_compute_group_id"] == "lcg-gpu"
    assert rsp["gpu_type"] == "NVIDIA_H100_SXM_80G"  # full type, not simplified
    # no spec_id (deprecated field) anywhere
    assert "spec_id" not in fc


def test_missing_spec_lists_candidates():
    c = _full_client()
    with pytest.raises(QzError) as ei:
        create_core.prepare(c, _req(quota_id=None))
    assert ei.value.code == "missing_spec"
    assert ei.value.candidates[0]["quota_id"] == "quota-h100-1"


def test_unknown_image_blocks_with_candidates():
    c = _full_client()
    with pytest.raises(QzError) as ei:
        create_core.prepare(c, _req(image="docker.sii/typo:9"))
    assert ei.value.code == "invalid_image"


def test_no_image_check_skips_validation():
    c = _full_client()
    out = create_core.prepare(c, _req(image="docker.sii/anything:9", check_image=False))
    assert out["payload"]["framework_config"][0]["image"] == "docker.sii/anything:9"


def test_explicit_cpu_mem_without_history():
    # empty history but explicit resources → no spec lookup needed
    c = FakeClient(
        {
            "project/list": PROJECTS_RESPONSE,
            "cluster_basic_info": COMPUTE_GROUPS_RESPONSE,
            "train_job/list": {"jobs": []},
            "image/list": IMAGES_RESPONSE,
        }
    )
    out = create_core.prepare(
        c, _req(quota_id="quota-custom", cpu=8, gpu=0, mem=64)
    )
    fc = out["payload"]["framework_config"][0]
    assert fc["cpu"] == 8 and fc["mem_gi"] == 64
    assert fc["resource_spec_price"]["quota_id"] == "quota-custom"


def test_multi_owner_space_requires_explicit_project():
    c = _full_client()
    with pytest.raises(QzError) as ei:
        create_core.prepare(c, _req(workspace="ws-002"))
    assert ei.value.code == "ambiguous_project"
    ids = {cand["id"] for cand in ei.value.candidates}
    assert ids == {"project-alpha", "project-beta"}


def test_multi_owner_space_with_explicit_project():
    c = _full_client()
    out = create_core.prepare(c, _req(workspace="ws-002", project="Beta"))
    assert out["payload"]["project_id"] == "project-beta"


def test_explicit_project_must_own_the_space():
    c = _full_client()
    with pytest.raises(QzError) as ei:
        # project-beta does not own ws-001
        create_core.prepare(c, _req(workspace="ws-001", project="Beta"))
    assert ei.value.code == "invalid_project"


def test_submit_runs_prepare_then_posts():
    c = _full_client()
    c.responses["train_job/create"] = {"job_id": "job-new", "workspace_id": "ws-001"}
    out = create_core.submit(c, _req())
    assert out["job_id"] == "job-new"
    assert "distributedTrainingDetail/job-new" in out["url"]
    # the create POST actually happened
    assert any(path == "train_job/create" for path, _ in c.calls)
