"""Tests for `_parse_since` and the start_timestamp_ms wiring on logs."""
import time

import pytest

from qzcli.cli import _parse_since
from qzcli.client import endpoints
from qzcli.errors import QzError

from conftest import FakeClient


# --- _parse_since ----------------------------------------------------------

def _assert_close(actual_ms_str: str, expected_secs_ago: int, *, tol_s: float = 2.0):
    actual_ms = int(actual_ms_str)
    now_ms = int(time.time() * 1000)
    delta_s = (now_ms - actual_ms) / 1000.0
    assert abs(delta_s - expected_secs_ago) < tol_s, (
        f"expected ~{expected_secs_ago}s ago, got {delta_s:.1f}s ago"
    )


def test_parse_since_seconds():
    _assert_close(_parse_since("30s"), 30)


def test_parse_since_minutes():
    _assert_close(_parse_since("5m"), 5 * 60)


def test_parse_since_hours():
    _assert_close(_parse_since("2h"), 2 * 3600)


def test_parse_since_days():
    _assert_close(_parse_since("1d"), 86400)


def test_parse_since_tolerates_whitespace():
    _assert_close(_parse_since("  10m  "), 600)


def test_parse_since_iso_z():
    # 2026-01-01T00:00:00Z → 1767225600 epoch seconds
    out = _parse_since("2026-01-01T00:00:00Z")
    assert out == str(1767225600 * 1000)


def test_parse_since_iso_offset():
    # 2026-01-01T08:00:00+08:00 is the same instant as 2026-01-01T00:00:00Z
    out = _parse_since("2026-01-01T08:00:00+08:00")
    assert out == str(1767225600 * 1000)


def test_parse_since_invalid_raises_usage_error():
    with pytest.raises(QzError) as excinfo:
        _parse_since("yesterday")
    assert excinfo.value.code == "usage_error"


# --- job_logs wire format --------------------------------------------------

def test_job_logs_includes_start_timestamp_ms_when_set():
    c = FakeClient({"v2:GetJobLog": {"logs": []},
                    "task/list_task_instance": {"task_instances": [{"name": "p1"}]}})
    endpoints.job_logs(c, "job-x", page_size=200,
                       start_timestamp_ms="1700000000000", sort="ascend")
    bodies = [b for k, b in c.calls if k == "v2:GetJobLog"]
    assert bodies and bodies[0]["filter"]["start_timestamp_ms"] == "1700000000000"
    assert bodies[0]["sorter"][0]["sort"] == "ascend"


def test_job_logs_omits_start_timestamp_ms_when_unset():
    c = FakeClient({"v2:GetJobLog": {"logs": []},
                    "task/list_task_instance": {"task_instances": [{"name": "p1"}]}})
    endpoints.job_logs(c, "job-x", page_size=200, sort="descend")
    bodies = [b for k, b in c.calls if k == "v2:GetJobLog"]
    assert bodies and "start_timestamp_ms" not in bodies[0]["filter"]
