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
