"""Unit tests for the blocking-wait core (qzcli-side polling).

`wait_until` is driven by injected poll sequences + a no-op sleep, so these are
deterministic and instant. The three behaviours that matter: reach OK, reach
FAIL, and time out on ACTIVE — while QUEUED time is never charged.
"""
import pytest

from qzcli.core import wait


def _seq_poller(states):
    it = iter(states)
    # repeat the last state forever once the sequence is exhausted
    last = {"v": None}

    def poll():
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    return poll


def _noop_sleep(_):
    pass


def test_reaches_ok():
    poll = _seq_poller(["PENDING", "CREATING", "RUNNING"])
    r = wait.wait_until(poll, wait.classify_notebook_running,
                        timeout_s=600, interval_s=1, sleep=_noop_sleep)
    assert r["reached"] and not r["failed"] and not r["timed_out"]
    assert r["final_status"] == "RUNNING"
    assert r["polls"] == 3


def test_reaches_fail():
    poll = _seq_poller(["CREATING", "FAILED"])
    r = wait.wait_until(poll, wait.classify_notebook_running,
                        timeout_s=600, interval_s=1, sleep=_noop_sleep)
    assert r["failed"] and not r["reached"] and not r["timed_out"]
    assert r["final_status"] == "FAILED"


def test_active_times_out():
    poll = _seq_poller(["CREATING"])  # never leaves ACTIVE
    r = wait.wait_until(poll, wait.classify_notebook_running,
                        timeout_s=5, interval_s=1, max_interval_s=1, sleep=_noop_sleep)
    assert r["timed_out"] and not r["reached"] and not r["failed"]
    assert r["active_s"] >= 5
    assert r["queued_s"] == 0


def test_queue_time_not_charged_against_timeout():
    # 100 polls of PENDING (queue) then RUNNING — with a tiny timeout, queue must
    # NOT trip it; only active time counts (here active time is ~0).
    poll = _seq_poller(["PENDING"] * 100 + ["RUNNING"])
    r = wait.wait_until(poll, wait.classify_notebook_running,
                        timeout_s=3, interval_s=1, max_interval_s=1, sleep=_noop_sleep)
    assert r["reached"] and not r["timed_out"]
    assert r["queued_s"] >= 100
    assert r["active_s"] == 0


def test_immediate_ok_no_sleep():
    calls = {"n": 0}

    def sleep(_):
        calls["n"] += 1

    poll = _seq_poller(["RUNNING"])
    r = wait.wait_until(poll, wait.classify_notebook_running,
                        timeout_s=600, interval_s=1, sleep=sleep)
    assert r["reached"] and r["polls"] == 1 and calls["n"] == 0


@pytest.mark.parametrize("status,expected", [
    ("RUNNING", wait.OK), ("running", wait.OK),
    ("FAILED", wait.FAIL), ("STOPPED", wait.FAIL),
    ("PENDING", wait.QUEUED), ("CREATING", wait.ACTIVE), ("WeirdNewState", wait.ACTIVE),
])
def test_classify_notebook_running(status, expected):
    assert wait.classify_notebook_running(status) == expected


@pytest.mark.parametrize("status,expected", [
    ("job_running", wait.OK), ("job_succeeded", wait.OK),
    ("job_failed", wait.FAIL), ("job_stopped", wait.FAIL),
    ("job_pending", wait.QUEUED), ("job_queued", wait.QUEUED),
    ("job_creating", wait.ACTIVE), ("job_pulling_image", wait.ACTIVE),
])
def test_classify_job_started(status, expected):
    assert wait.classify_job_started(status) == expected


@pytest.mark.parametrize("status,expected", [
    ("SUCCESS", wait.OK), ("ERROR", wait.FAIL),
    ("BUILDING", wait.ACTIVE), ("UNKNOWN_STATUS", wait.ACTIVE),
])
def test_classify_save(status, expected):
    assert wait.classify_save(status) == expected
