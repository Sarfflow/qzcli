"""Tests for delete-image and the blocking-wait wiring on notebook stop."""
from qzcli.client import endpoints
from qzcli.core import notebook
from conftest import FakeClient


def test_delete_image_hits_delete_endpoint():
    c = FakeClient({"image/": {"code": 0}})
    out = endpoints.delete_image(c, "image-abc123")
    assert out == {"code": 0}
    assert c.calls == [("DELETE image/image-abc123", {})]


def test_nb_stop_blocks_until_stopped():
    # GetNotebook reports STOPPED on the first poll → wait returns reached at once.
    c = FakeClient({"v2:StopNotebook": {}, "v2:GetNotebook": {"status": "STOPPED"}})
    out = notebook.stop(c, "nb-1")  # wait defaults True
    assert out["stopped"] == "nb-1"
    assert out["wait"]["reached"] is True
    assert out["wait"]["final_status"] == "STOPPED"


def test_nb_stop_no_wait_skips_polling():
    c = FakeClient({"v2:StopNotebook": {}})
    out = notebook.stop(c, "nb-1", wait=False)
    assert "wait" not in out
    assert all(not k.startswith("v2:GetNotebook") for k, _ in c.calls)


def test_save_notebook_image_passes_description_when_set():
    c = FakeClient({"v2:SaveNotebookImage": {}})
    endpoints.save_notebook_image(c, "nb-1", "qzcli-foo", "20260617-1200",
                                  description="apt: htop; pip: einops")
    # the SaveNotebookImage body should include description verbatim
    bodies = [body for key, body in c.calls if key == "v2:SaveNotebookImage"]
    assert bodies and bodies[0].get("description") == "apt: htop; pip: einops"
    assert bodies[0]["notebookId"] == "nb-1"  # camelCase preserved


def test_save_notebook_image_omits_description_when_empty():
    c = FakeClient({"v2:SaveNotebookImage": {}})
    endpoints.save_notebook_image(c, "nb-1", "x", "1")
    bodies = [body for key, body in c.calls if key == "v2:SaveNotebookImage"]
    assert bodies and "description" not in bodies[0]


def test_image_to_dict_carries_description():
    from qzcli.domain.models import Image
    im = Image.from_api({
        "address": "registry/x:1", "name": "x:1", "image_id": "image-x",
        "description": "apt: htop; pip: einops",
    })
    assert im.description == "apt: htop; pip: einops"
    assert im.to_dict()["description"] == "apt: htop; pip: einops"


def test_set_image_description_preserves_visibility_and_brand():
    # Stage existing image in list_images so find_image_by_id locates it.
    c = FakeClient({
        "image/list": {"images": [{
            "image_id": "image-abc", "name": "qzcli-foo:20260617-1000",
            "address": "registry/qzcli-foo:20260617-1000",
            "description": "old", "visibility": "VISIBILITY_PRIVATE",
            "support_brand_info_list": [{"brand": "BRAND_X", "brand_name": "X"}],
            "source": "SOURCE_PUBLIC",
        }]},
        "image/update": {"code": 0},
    })
    out = endpoints.set_image_description(c, "ws-1", "image-abc", "fresh notes")
    assert out["old_description"] == "old"
    assert out["new_description"] == "fresh notes"
    # the update body must include all the read-back fields, not just description
    update_bodies = [b for k, b in c.calls if k == "image/update"]
    assert update_bodies, "no update call recorded"
    body = update_bodies[0]
    assert body["id"] == "image-abc"
    assert body["description"] == "fresh notes"
    assert body["visibility"] == "VISIBILITY_PRIVATE"
    assert body["support_brand_list"] == ["BRAND_X"]  # round-tripped from support_brand_info_list


def test_set_image_description_falls_back_when_no_brand():
    c = FakeClient({
        "image/list": {"images": [{
            "image_id": "image-abc", "name": "x:1", "address": "r/x:1",
            "description": "", "visibility": "VISIBILITY_PRIVATE",
            "source": "SOURCE_PUBLIC",
        }]},
        "image/update": {"code": 0},
    })
    endpoints.set_image_description(c, "ws-1", "image-abc", "hi")
    body = [b for k, b in c.calls if k == "image/update"][0]
    assert body["support_brand_list"] == [""]


def test_set_image_description_unknown_id_raises():
    c = FakeClient({"image/list": {"images": []}})
    try:
        endpoints.set_image_description(c, "ws-1", "image-nope", "x")
    except Exception as e:
        assert getattr(e, "code", "") == "invalid_image"
    else:
        raise AssertionError("expected QzError invalid_image")
