"""Block until a remote resource reaches a target state — qzcli-side polling.

Notebook/job operations on the platform are asynchronous: ``CreateNotebook`` /
``create_job`` / ``SaveNotebookImage`` / stop all return immediately while the
resource is still provisioning. Rather than make the *caller* (an agent) burn
tokens on a poll loop, these helpers poll here and return only once the resource
reaches a terminal-enough state — or the active-phase budget runs out.

Key rule: **queue time is not charged against the timeout.** A job can sit
queued for scheduling for a whole day; only time spent actively provisioning
(pulling image, initializing, …) counts toward ``timeout_s``.

Each status is mapped to one of four categories:

- ``QUEUED``  waiting for scheduling — timeout clock paused
- ``ACTIVE``  provisioning/working — timeout clock runs
- ``OK``      reached the desired state — return
- ``FAIL``    reached a failure state — return

Unknown statuses default to ``ACTIVE`` (keep waiting, count time), so a new
platform state never silently breaks the loop.
"""

from __future__ import annotations

import time
from typing import Any, Callable

QUEUED = "QUEUED"
ACTIVE = "ACTIVE"
OK = "OK"
FAIL = "FAIL"

DEFAULT_TIMEOUT_S = 600  # 10 min of ACTIVE time; queue excluded


def wait_until(
    poll: Callable[[], str],
    classify: Callable[[str], str],
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    interval_s: float = 3.0,
    max_interval_s: float = 20.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Poll ``poll()`` until ``classify`` says OK/FAIL, or ACTIVE time runs out.

    ``poll`` returns the current raw status string; ``classify`` maps it to one
    of QUEUED/ACTIVE/OK/FAIL. Returns a result dict (see ``_result``). Time spent
    in QUEUED states is tracked separately and never triggers the timeout.
    """
    active_s = 0.0
    queued_s = 0.0
    interval = float(interval_s)
    polls = 0

    status = poll()
    polls += 1
    cat = classify(status)
    while cat in (QUEUED, ACTIVE):
        if cat == ACTIVE and active_s >= timeout_s:
            return _result(status, cat, active_s, queued_s, polls, timed_out=True)
        sleep(interval)
        if cat == ACTIVE:
            active_s += interval
        else:
            queued_s += interval
        interval = min(interval * 1.5, max_interval_s)
        status = poll()
        polls += 1
        cat = classify(status)
    return _result(status, cat, active_s, queued_s, polls)


def _result(
    status: str, cat: str, active_s: float, queued_s: float, polls: int,
    *, timed_out: bool = False,
) -> dict[str, Any]:
    return {
        "final_status": status,
        "reached": (cat == OK) and not timed_out,
        "failed": (cat == FAIL) and not timed_out,
        "timed_out": timed_out,
        "active_s": round(active_s, 1),
        "queued_s": round(queued_s, 1),
        "polls": polls,
    }


# --- per-operation status classifiers ------------------------------------
# Statuses observed live (2026-06): notebook PENDING/CREATING/RUNNING/STOPPED/
# FAILED; save_mirror_status BUILDING/SUCCESS/ERROR/UNKNOWN_STATUS; job
# job_running/job_succeeded/job_failed/job_stopped (queue/creating enums unseen,
# so matched defensively by substring).


def classify_notebook_running(status: str) -> str:
    """For `nb start`: target RUNNING."""
    s = (status or "").upper()
    if s == "RUNNING":
        return OK
    if s in ("FAILED", "STOPPED", "DELETED"):
        return FAIL  # unexpected terminal during a start
    if s in ("PENDING", "QUEUED", "QUEUEING", "WAITING"):
        return QUEUED
    return ACTIVE  # CREATING, INITIALIZING, …


def classify_notebook_stopped(status: str) -> str:
    """For `nb stop` / `nb rm`: target STOPPED."""
    s = (status or "").upper()
    if s in ("STOPPED", "FAILED", "DELETED"):
        return OK
    return ACTIVE  # RUNNING, STOPPING, …


def classify_save(status: str) -> str:
    """For `nb save-image`: target save_mirror_status SUCCESS."""
    s = (status or "").upper()
    if s == "SUCCESS":
        return OK
    if s in ("ERROR", "FAILED", "FAIL"):
        return FAIL
    return ACTIVE  # BUILDING, UNKNOWN_STATUS


def classify_job_started(status: str) -> str:
    """For `create` (distributed): target job_running (a fast job may go
    straight to job_succeeded — also OK)."""
    s = status or ""
    if s in ("job_running", "job_succeeded"):
        return OK
    if s == "job_failed":
        return FAIL
    if s == "job_stopped":
        return FAIL  # stopped before it ever ran
    if any(k in s for k in ("pending", "queue", "wait")):
        return QUEUED
    return ACTIVE  # job_creating, job_pulling, …


def classify_job_stopped(status: str) -> str:
    """For `stop` (job): any terminal state is done."""
    s = status or ""
    if s in ("job_stopped", "job_failed", "job_succeeded"):
        return OK
    return ACTIVE
