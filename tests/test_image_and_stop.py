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
