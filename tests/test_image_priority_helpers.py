"""Tests for find_saved_image and project_priority_cap helpers."""
from qzcli.client import endpoints
from conftest import FakeClient


def test_find_saved_image_matches_name_version_any_source():
    # A saved personal image surfaces under SOURCE_PUBLIC; find it by name:version.
    c = FakeClient({"image/list": {"images": [
        {"address": "docker.x/inspire-studio/other:v1", "name": "other:v1", "image_id": "image-aaa"},
        {"address": "docker.x/inspire-studio/e2emt-img1:v1", "name": "e2emt-img1:v1", "image_id": "image-bbb"},
    ]}})
    im = endpoints.find_saved_image(c, "ws-1", "e2emt-img1", "v1")
    assert im is not None and im.image_id == "image-bbb"
    assert im.address.endswith("/e2emt-img1:v1")


def test_find_saved_image_none_when_absent():
    c = FakeClient({"image/list": {"images": [
        {"address": "docker.x/ns/foo:v2", "name": "foo:v2", "image_id": "image-ccc"}]}})
    assert endpoints.find_saved_image(c, "ws-1", "e2emt-img1", "v1") is None


def test_project_priority_cap_reads_priority_name():
    c = FakeClient({"project/list_for_page": {"list": [
        {"id": "project-a", "priority_name": "6"},
        {"id": "project-b", "priority_name": "3"}]}})
    assert endpoints.project_priority_cap(c, "project-a") == 6
    assert endpoints.project_priority_cap(c, "project-b") == 3
    assert endpoints.project_priority_cap(c, "project-missing") is None


def test_project_priority_cap_none_on_bad_value():
    c = FakeClient({"project/list_for_page": {"list": [
        {"id": "project-a", "priority_name": None}]}})
    assert endpoints.project_priority_cap(c, "project-a") is None
