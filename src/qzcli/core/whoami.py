"""Self-inspection: are we inside a 启智 platform container, and if so, who?

The platform injects rich identity env vars into every container it schedules.
An agent running INSIDE a notebook / training pod can find its own
notebook/job/project just by reading them — no API call required (and the
in-container side typically has no cookie anyway).

This module is pure: takes ``os.environ``-style dict, returns a structured
identity. The CLI surfaces it as ``qzcli whoami``.

What env vars carry identity (observed live in a NOTEBOOK pod, 2026-06):
- ``NB_PREFIX`` = ``/{ws_id}/{project_id}/{user_id}/jupyter/{notebook_id}/{token}``
  — one string carries every id at once. Parsed by :data:`_NB_PREFIX_RE`.
- ``SERVER_TYPE`` = ``NOTEBOOK`` (set on the interactive-modeling instance).
- ``MY_POD_NAME`` / ``HOSTNAME`` = ``<name>--<last 12 hex of nb_id>-<k8s suffix>``.
- ``INSPIRE_PROJECT_USER_{hdd,ssd,qb_ilm}`` / ``INSPIRE_PROJECT_PUBLIC_*`` =
  per-tier GPFS paths for THIS PROJECT's user / public dirs. The shape
  ``/inspire/<tier>/project/<project_en_name>/<...>`` carries the project's
  English short name. The fact that ONLY this project's dirs are mounted is
  the platform's GPFS scoping rule: same project → same GPFS; different
  projects → different mounts.
- ``INSPIRE_GLOBAL_PUBLIC`` / ``INSPIRE_GLOBAL_USER`` = cross-project paths.
- ``JUPYTER_SERVER_URL`` = full per-pod Jupyter base (with the token).
- ``WORKSPACE_DIR`` = the project-user dir the shell starts in.

For training pods (no live sample yet), pytorch ``RANK``/``WORLD_SIZE``/
``MASTER_ADDR`` are the universal hint; the platform's pod_name typically
contains ``job-<uuid>``, captured here heuristically.
"""

from __future__ import annotations

import os
import re
import socket
from typing import Any, Mapping, Optional

# /{ws_id}/{project_id}/{user_id}/{kind}/{instance_id}/{token}[/...]
_NB_PREFIX_RE = re.compile(
    r"^/(?P<ws>ws-[0-9a-f-]+)"
    r"/(?P<project>project-[0-9a-f-]+)"
    r"/(?P<user>user-[0-9a-f-]+)"
    r"/(?P<kind>jupyter|vscode|terminal|tensorboard|[a-z_-]+)"
    r"/(?P<instance>[0-9a-f-]+)"
    r"/(?P<token>[0-9a-f-]+)"
)

# /inspire/<tier>/project/<en_name>/<user_or_public_subdir>
_PROJECT_PATH_RE = re.compile(r"/inspire/[^/]+/project/(?P<en_name>[^/]+)/(?P<sub>[^/]+)")

# A k8s pod name in training jobs often embeds the full job uuid:
# job-<8>-<4>-<4>-<4>-<12>-<role>-<i>  (worker-0, master-0, ...)
_JOB_IN_HOSTNAME_RE = re.compile(
    r"\bjob-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)


def inspect(env: Optional[Mapping[str, str]] = None) -> dict[str, Any]:
    """Return a structured identity for the current process.

    Detection rule for "inside a 启智 container": any ``INSPIRE_*`` env var, OR
    ``NB_PREFIX``, OR ``MY_POD_NAME``. Outside, returns just the hostname so
    callers know they're on the control side (the CPU machine where ``qzcli``
    is typically driven from).
    """
    env = env if env is not None else os.environ
    has_inspire = any(k.startswith("INSPIRE_") for k in env)
    has_nb_prefix = bool(env.get("NB_PREFIX"))
    has_pod = bool(env.get("MY_POD_NAME"))

    if not (has_inspire or has_nb_prefix or has_pod):
        return {"in_qz_container": False, "hostname": _hostname(env)}

    out: dict[str, Any] = {
        "in_qz_container": True,
        "hostname": env.get("MY_POD_NAME") or env.get("HOSTNAME") or _hostname(env),
        "server_type": env.get("SERVER_TYPE"),
    }

    # Notebook (or other XX_PREFIX) — single-string identity carrier.
    nb_prefix = env.get("NB_PREFIX") or env.get("VC_PREFIX") or ""
    m = _NB_PREFIX_RE.match(nb_prefix)
    if m:
        out["kind"] = "notebook" if m.group("kind") == "jupyter" else m.group("kind")
        out["workspace_id"] = m.group("ws")
        out["project_id"] = m.group("project")
        out["user_id"] = m.group("user")
        out["instance_id"] = m.group("instance")  # the notebook_id for a notebook
        out["jupyter_token"] = m.group("token")
        if out["kind"] == "notebook":
            out["notebook_id"] = m.group("instance")
    else:
        # No NB_PREFIX — likely a training/distributed pod or something else.
        is_dist = any(k in env for k in ("RANK", "WORLD_SIZE", "LOCAL_RANK"))
        out["kind"] = "training" if is_dist else "unknown"
        host = out["hostname"]
        jm = _JOB_IN_HOSTNAME_RE.search(host)
        if jm:
            out["job_id"] = jm.group(0)

    # Project en_name comes off the GPFS path (only THIS project's dirs are
    # mounted — same project = same GPFS, different project = different mount).
    proj_path = (env.get("WORKSPACE_DIR")
                 or env.get("INSPIRE_PROJECT_USER_hdd")
                 or env.get("INSPIRE_PROJECT_USER_ssd")
                 or "")
    pm = _PROJECT_PATH_RE.search(proj_path)
    if pm:
        out["project_en_name"] = pm.group("en_name")

    # GPFS mount paths surface as separate keys; same-project containers see
    # the same set, other-project containers see a DIFFERENT set.
    gpfs = {
        k: env[k] for k in (
            "INSPIRE_GLOBAL_PUBLIC", "INSPIRE_GLOBAL_USER",
            "INSPIRE_PROJECT_PUBLIC_hdd", "INSPIRE_PROJECT_PUBLIC_ssd",
            "INSPIRE_PROJECT_PUBLIC_qb_ilm",
            "INSPIRE_PROJECT_USER_hdd", "INSPIRE_PROJECT_USER_ssd",
            "INSPIRE_PROJECT_USER_qb_ilm",
        ) if k in env
    }
    if gpfs:
        out["gpfs"] = gpfs

    if env.get("JUPYTER_SERVER_URL"):
        out["jupyter_url"] = env["JUPYTER_SERVER_URL"]

    # PyTorch distributed hints (training pods).
    dist = {k: env[k] for k in (
        "RANK", "WORLD_SIZE", "LOCAL_RANK", "MASTER_ADDR", "MASTER_PORT",
        "NPROC_PER_NODE", "NODE_RANK",
    ) if k in env}
    if dist:
        out["dist"] = dist

    return out


def _hostname(env: Mapping[str, str]) -> str:
    return env.get("HOSTNAME") or socket.gethostname()
