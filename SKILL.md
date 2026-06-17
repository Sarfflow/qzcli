---
name: qzcli
description: Drive the 启智 platform (qz.sii.edu.cn) from the command line — log in via CAS, browse projects/workspaces, enumerate legal compute-group/spec/image options, validate and submit training jobs, and list/inspect/stop jobs. Use whenever a task involves submitting or managing jobs on qz.sii.edu.cn, or reading its compute resources/quotas. All output is JSON by default.
---

# qzcli

Agent-first CLI for the 启智 platform, over its cookie-authed web API
(`/api/v1/*`, `/api/v2/*`).

## Invocation

```bash
<skill-dir>/qzcli <command> [args...]
```

`<skill-dir>` is the folder this SKILL.md lives in. The launcher runs the tool
via `uv` (first run auto-syncs deps); if `uv` is missing, `pip install -e
<skill-dir>` once, then call `qzcli`.

Output is a JSON envelope on stdout:

- success → `{"ok": true, "data": <value>}`
- failure → `{"ok": false, "error": {"code", "message", "hint"?, "candidates"?}}`, exit 1

`--table` (before the subcommand) gives a human table over the same data.
`--fields a,b,c` (before the subcommand) trims list results to those keys — applies
to top-level lists and to the list inside a dict result (e.g. `--fields room,gpu_free,effective_free rooms -w …`,
`--fields job_id,name,status ls -w …`). Use it to cut tokens when you only need a few columns.

## Workflow — submit a job

```
login → projects → rooms → options specs → options images → create
```

1. **`login`** — CAS session cookie (saved to `~/.qzcli/`).
2. **`projects`** — pick a workspace (`ws-...`). A workspace can belong to several
   projects; if so, `create` needs `--project` (it errors with the candidates).
3. **`rooms -w <ws>`** — pick the emptiest 机房 (`lcg-...`); ranked by free cards.
   For a multi-node (gang) job add `--fit IxC` to check real placeability.
4. **`options specs -w <ws> -g <lcg>`** — pick a `quota_id`.
5. **`options images -w <ws>`** — pick an image `address`.
6. **`create ... [--project ...]`** — submit. Validates everything internally and
   refuses an illegal payload, so no separate check step is needed.

The tool never guesses: a missing/ambiguous choice errors with a `candidates`
list — pick one and pass it explicitly. The scheduler places jobs on nodes, so
you pick the 机房 (`rooms`), not individual nodes.

## Commands & output schemas

### `login [-u USER] [-p PASS] [--cookie STR] [-w WS]`
Credentials also read from `QZCLI_USERNAME` / `QZCLI_PASSWORD`. On captcha, log
in via browser and pass the exported cookie with `--cookie`.
→ `data: {status, cookie_len, workspace_id}`

### `projects [--with-gpu]`
→ `data: [ {id: "project-...", name, en_name, priority_cap, spaces: [{id: "ws-...", name [, gpu_types]}]} ]`
`priority_cap` is the per-project max for `--priority` (so `create` validates up
front). `--with-gpu` annotates each space with `gpu_types` (e.g. `["4090"]`,
`["H100","H200"]`) — opt-in because it costs one extra call per unique space;
once per session you have the whole platform meta in one shot.

### `options compute-groups -w <ws>`
→ `data: [ {id: "lcg-...", name, workspace_id, gpu_type, gpu_type_display} ]`

### `options specs -w <ws> -g <lcg>`
→ `data: [ {quota_id, cpu_count, gpu_count, memory_gb, gpu_type, gpu_type_simple, gpu_type_display, total_price_per_hour, logic_compute_group_ids} ]`
The predefined card-count quotas (1/2/4/8…). `gpu_count` is **cards per node**;
`gpu_type` (e.g. `NVIDIA_H100_SXM_80G`) is what `create` needs. Pick a `quota_id`.

### `options images -w <ws> [--source ALL|...] [--name SUBSTR] [--verbose]`
→ `data: [ {address, name, image_id, source, visibility} ]`
`--name SUBSTR` filters by name/address substring (cheaper than pulling all ~50).
Use `address` as `--image`. **A notebook-saved personal image surfaces as
`source=SOURCE_PUBLIC` + `visibility=VISIBILITY_PRIVATE` (NOT SOURCE_PRIVATE)** —
so to find an image you just saved, use `--source ALL`, not `--source SOURCE_PRIVATE`.
`--verbose` adds the heavy `creator` object (dropped
by default). A bad `--source` errors with `invalid_argument` + candidates.

### `rooms -w <ws> [--low-priority-threshold N] [--fit IxC]`
Per-机房 (lcg) GPU rollup, emptiest first — the unit you pick a job's target from.
→ `data: {low_priority_threshold, n_rooms, rooms: [{room, lcg_id, gpu_type, cluster, n_nodes, n_nodes_ready, gpu_total, gpu_free, gpu_used, low_priority_preemptible, effective_free, max_free_on_single_node, nodes_full_free, cpu_total, cpu_free, cpu_used}]}`
Sorted by `gpu_free`, then `effective_free` (= `gpu_free + low_priority_preemptible`),
then `gpu_total`. **CPU-only workspaces** (every room has `gpu_total=0`) are sorted
by `cpu_free` instead — `gpu_free` is uniformly 0 there and useless for picking a
CPU room. Low-priority cards (`priority <= N`, default 3) count as free
because a higher-priority job evicts them. 机房 share nodes, so `gpu_free` can be
negative (oversubscribed) — use `effective_free` there.
**Aggregates don't imply placeability**: free cards may be fragmented 1-2/node, so
`gpu_free=24` can't necessarily host one 8-card node. `max_free_on_single_node` /
`nodes_full_free` flag that. `--fit IxC` (e.g. `2x8` = 2 nodes × 8 cards/node)
adds per room `fit_nodes_idle`, `fit_nodes_effective`, `fits`, and ranks by fit.

### `avail -w <ws> [-g <lcg>] [--low-priority-threshold N] [--top N] [--all]`
Per-node view (`nodes` are physical GPU machines). Rarely needed — prefer `rooms`.
→ `data: {low_priority_threshold, n_nodes, n_nodes_shown, nodes_truncated, by_gpu_type: [{gpu_type, n_nodes, gpu_total, gpu_free, low_priority_preemptible, effective_free}], nodes: [{name, gpu_type, gpu_total, gpu_free, gpu_used, low_priority_preemptible, effective_free, status}]}`
`by_gpu_type` covers the whole fleet (authoritative totals). `nodes` lists only
**schedulable nodes with spare capacity** (`Ready`, `effective_free > 0`),
emptiest first, capped at `--top` (default 10; `nodes_truncated:true` when the cap
hid some). `--top 0` = all spare-capacity nodes; `--all` = every node in the fleet.
`--table` prints the per-node rows.

### `create [--dry-run]  (required: --name -w -g --image --cmd)`
Options: `--project` (id / 中文名 / en_name), `--quota-id`, `--cpu`, `--gpu`, `--mem`,
`--framework` (default `pytorch`), `--image-type` (default: the image's source),
`--instances` (default 1), `--shm` GiB (default: spec memory), `--priority` 1–10
(default 10; a project caps this — `--dry-run`/submit fail fast with `priority_too_high`
naming the exact cap, so set `--priority <= cap`), `--no-image-check`,
`--dataset id[:version]` (repeatable), `--wait/--no-wait`, `--timeout`, `--allow-set-e`.
`--cmd` containing `set -e` (or `set -eu` etc.) is **rejected** by default
(`set_e_footgun`) because any benign non-zero (empty glob, `grep` no match, `[ ]`
false) would abort the whole job with exitCode 2 — either drop `set -e` and
guard risky steps with `|| true`, or pass `--allow-set-e` to keep it.
- **Blocks until the job is running by default** (qzcli polls; queue time is not
  charged against `--timeout`, default 600s). Adds `wait:{final_status,reached,
  timed_out,active_s,queued_s}`. `--no-wait` returns at submit. Run long ones with
  the shell backgrounded → zero tokens spent waiting. job_failed → `job_failed`.
- → `data: {job_id, workspace_id, name, url, resolved}`
- `--dry-run` → `data: {dry_run: true, resolved, payload}`, submits nothing —
  optional preview of inferred values (project / image_type / shm / spec).
- `--instances` is the **node count**; cards-per-node come from the spec's `gpu_count`.
- Use `--quota-id` from `options specs`; `--cpu/--gpu/--mem` only override amounts
  and may be rejected if they don't match the quota.
- `--dataset` refs are validated before submit; they land in `dataset_info` as
  `{dataset_id, version_id, path}`.

### `dataset validate -w <ws> --dataset id[:version] ...`
→ `data: [ {dataset_id, version_id, success, path, error_message} ]`
There is **no dataset-listing command** — `dataset_id`s come from the web UI (or
a job's `detail.dataset_info`). Version is effectively required; omitting it often
fails (default-version resolution is dataset-dependent).

### `ls -w <ws> [--running] [--limit N]`
→ `data: {total, jobs: [{job_id, name, status, workspace_id, project_id, project_name, logic_compute_group_id, created_at}]}`
`project_name` is echoed (workspaces are shared across projects, so the id alone
is ambiguous). Job `status` is **`job_`-prefixed**: `job_queuing` / `job_creating` / `job_running`
/ `job_succeeded` / `job_failed` / `job_stopped`. (Match on these exact strings —
they are NOT the bare `RUNNING`/`SUCCEEDED` forms.) `--running` filters to running.

### `instances JOB_ID`
→ `data: [ {name, instance_type, node, instance_status, created_at, started_at, finished_at, running_time_ms} ]`

### `logs JOB_ID [--tail N]`
→ `data: {logs: [{message, pod_name, node, time, timestamp_ms, ...}], total}`
`--tail N` (default 200) returns the most recent N lines, oldest-first (newest
last). A bad JOB_ID → `invalid_job`; a job with no scheduled pods → `no_instances`.
When the platform returns **0 lines**, the response adds `logs_available:false` +
a `note`: if the job is terminal the logs are unavailable (some pods never get
indexed) — **stop polling**; if it's still starting, retry shortly. (Don't loop
on an empty `logs` without checking `note`.)

### `metrics JOB_ID [--minutes N] [--interval S] [--metric M ...]`
Per-instance utilization over time — check a running job is actually using GPUs.
Default metrics `gpu_usage_rate`, `gpu_memory_usage_rate` (also: cpu_usage_rate,
memory_usage_rate, disk_io_read/write, network_io_read/write,
network_storage_io_read/write); default window 30 min. Rates are 0..1; near-0 on
a running GPU job = likely stuck.
→ `data: {window_minutes, summary: [{group_name, metric_type, last, avg, max, points}], groups: [{metric_type, group_name, time_series: [{timestamp, data}]}]}`

### `stop JOB_ID`
→ `data: {stopped: JOB_ID, result: {...}}`

### `events JOB_ID [--instance <job_id>-worker-N] [--tail N]`
→ `data: [ {type, reason, message, from, object_type, object_id, count, first_timestamp, last_timestamp} ]`
Newest-first; identical repeated events collapse to one row with a `count` (k8s
emits the same scheduling/restart event many times). `--tail N` keeps the N most
recent. Startup `Unschedulable` warnings are normal transient noise.

### `detail JOB_ID [--brief]`
→ `data: {...}` (full job detail — a large object: status, project_name,
framework, gpu_count, instances, node_infos, framework_config, timeline, envs, …
— the exact key set is richer than a fixed schema). `--brief` returns just
`{job_id, name, status, project_name, framework, gpu_count, logic_compute_group_name,
task_priority, created_at, finished_at}` — the quick way to check one job's status
without scanning `ls` or dumping the full object.

## Interactive modeling — `nb ...` (notebook)

A *notebook* is a long-lived dev container (启智 "交互式建模"): start one on a 机房,
work in it (configure env, smoke-test), save it as a personal image, stop it —
then `create` distributed training from that image. Same GPFS as your other
instances in the project, so code is already shared. Read-before-write mirrors
`create` but on the notebook-specific 机房/specs (interactive-modeling lcgs, DSW
quotas) and the v2 `notebook` API.

**Where to start it (network constraint):** the H100/H200 distributed-training
clusters are **offline** (no internet). Only **可上网 GPU** can reach the network.
So when env setup needs the internet (pip/conda/git/HF), start the notebook on a
可上网 GPU 机房 — which usually also fits the model, so an H-card notebook is
rarely needed.

**用卡文明 (release GPU promptly):** a notebook holds its GPU the whole time. The
workflow is only *done* once the distributed job is **stably running** — at that
point `nb stop` the notebook to free the GPU. (A CPU-side agent then monitors the
job, e.g. polls every ~45 min, and re-runs this workflow on anomaly.)

Flow: `nb rooms` → `nb specs` → `nb start` (on 可上网 GPU) → `nb exec` (configure env / smoke-test) → `nb save-image` → `create` distributed → **once it runs stably** → `nb stop`.

### `nb ls -w <ws>`
→ `data: [ {name, status, room, gpu_count, gpu_ram, image, backup_image, notebook_id, ...} ]`
Running first. `notebook_id` (uuid) is the handle for the other `nb` commands.

### `nb rooms -w <ws>`
→ `data: [ {id (lcg), name, gpu_types, node_count, schedule_type} ]`
机房 that support interactive modeling (a different set than training's).

### `nb specs -w <ws> -g <lcg>`
→ `data: [ {quota_id, gpu_type, gpu_count, cpu_count, memory_gb, total_price_per_hour} ]`
DSW (interactive-modeling) quotas for the 机房. Pick a `quota_id`.

**Blocking by default:** `nb start` / `nb stop` / `nb save-image` (and `nb rm --stop`)
block until the target state (RUNNING / STOPPED / image SUCCESS) or `--timeout`
(default 600s ACTIVE; queue not counted); `--no-wait` returns immediately. Each
adds a `wait:{final_status,reached,timed_out,active_s,queued_s}` block. Run them
with the shell backgrounded to spend zero tokens while waiting. While blocking,
a heartbeat line is written to **stderr** every ~30s (status + elapsed; queue
time flagged) so a watcher sees progress — the stdout JSON result is unaffected.

### `nb start --name N -w <ws> -g <lcg> --image ADDR [--dry-run]`
Options: `--project` (multi-project ws), `--quota-id` (from `nb specs`),
`--cpu/--gpu/--mem`, `--shm`, `--priority` (default 6), `--auto-stop`.
- → `data: {notebook_id, name, workspace_id, resolved, result}`
- `--dry-run` → `{dry_run:true, resolved, payload}`, creates nothing.
- `--image` is the base image `address` (full registry URL, e.g. from `options images`).

### `nb get NOTEBOOK_ID`
→ full notebook detail (poll `status`: PENDING→CREATING→RUNNING; also STOPPED/FAILED).

### `nb exec NOTEBOOK_ID -- <command>`
Run a shell command **inside** a RUNNING notebook (drives its JupyterLab terminal
over WebSocket — the platform has no SSH). Use it to configure the env, install
deps, and smoke-test before `save-image`.
- → `data: {notebook_id, exit_code, timed_out, stdout}`. `exit_code` is the last
  statement's `$?`; `stdout` is the merged stdout+stderr (it's a PTY), cleaned of
  ANSI/banner/prompt. `--raw` returns the unprocessed terminal text.
- `--timeout S` (default 120) is the overall wall-clock budget; on overrun
  `timed_out:true`, `exit_code:null`, and stdout contains whatever streamed in
  before the cut. The command runs in a PTY — **the underlying shell dies on
  terminal teardown**, so don't expect partial work to persist past timeout.
  **Put `--timeout`/`--raw`/`--stream` before the id** (`nb exec --timeout 600 <id> -- ...`),
  like `kubectl exec`; the command goes after `--`.
- `--stream`: write each real-stdout line to **stderr** the moment it arrives
  (filtered of PTY echo + START/END markers); final JSON still lands on stdout.
  Combined with `run_in_background` + a Monitor watching stdout, this gives a
  live ssh-tail UX (one notification per line). Without `--stream`, you only
  get the full output at the end.
- ⚠ **Don't pipe `nb exec` through a stdin-reading JSON parser** (e.g.
  `... 2>&1 | python3 -c "json.load(sys.stdin)"`) — the parser buffers until
  EOF and the streaming + heartbeat output disappears with it. Either drop the
  pipe (read JSON from the captured file at the end) or split: redirect stderr
  to its own log (`... 2>/tmp/stream.log`) and pipe only stdout.
- The command form is `nb exec <id> -- <cmd>`: `nb exec <id> -- pip install -r req.txt && python smoke.py`.
- On official images pip may refuse with PEP 668 — use `pip install --break-system-packages`.
- Each call is a fresh shell (`/inspire/.../<user>` home, GPFS shared). Don't run
  `exit`; for env changes to persist into an image, `save-image` after. For
  **commands that might exceed `--timeout`** (long inference, big data downloads,
  hour-scale jobs), don't `--stream` them — the timeout WILL kill the terminal
  and the command with it. Detach to a GPFS logfile instead:
  ```
  nb exec <id> -- 'setsid bash -c "python video_infer.py > ~/run.log 2>&1" & echo pid=$!'
  nb exec <id> -- 'tail -n 50 ~/run.log; kill -0 <pid> && echo RUNNING || echo DONE'
  ```
  `setsid`/`nohup &` detaches from the PTY so terminal teardown doesn't kill it;
  poll the log with cheap follow-up `tail` calls. For truly multi-hour training,
  use `create` (distributed) instead — it has proper logs/events/metrics.

### `nb save-image NOTEBOOK_ID --name N --version V`
Save a **RUNNING** notebook as a private personal image (`accessible=1`). Blocks
until the build reaches SUCCESS by default (`--no-wait` to return at submit). On
success the result includes `image_address` (+ `image_id`) — feed it straight to
`create --image` (no need to go hunt it in `options images`). The image lists under
`options images --source ALL` (as `source=SOURCE_PUBLIC`, `visibility=PRIVATE`).
Can't stop the notebook while a save is BUILDING.

### `nb stop NOTEBOOK_ID`
→ `data: {stopped, result, wait}`. Blocks until STOPPED by default.

### `nb rm NOTEBOOK_ID [--stop]`
Delete a notebook — the platform requires it be STOPPED/FAILED first. `--stop`
stops it and waits for STOPPED, then deletes.

### `nb rm-image image-<id>`  (or `<name>/<address> -w <ws>`)
Delete a personal image. Pass the `image_id` (`image-…`, from `options images`)
directly, or a name/address plus `-w <ws>` to resolve it.

## Error codes (`error.code`)

| code | meaning | next step |
|---|---|---|
| `auth_required` / `auth_expired` | no/expired cookie | `qzcli login` |
| `bad_credentials` | wrong user/pass | fix `-u/-p` |
| `captcha_required` | captcha blocking login | browser login + `--cookie` |
| `invalid_workspace` / `invalid_compute_group` / `invalid_spec` / `invalid_image` | bad selection | pick from `candidates` |
| `ambiguous_project` / `invalid_project` | workspace has multiple owning projects | pass `--project` from `candidates` |
| `missing_fields` / `missing_spec` | required input absent | supply it (read `options` first) |
| `usage_error` / `invalid_argument` | bad/missing CLI flag or value | see `hint` / `candidates` |
| `incomplete_spec` | spec lacks cpu/mem | pass `--cpu`/`--mem` |
| `invalid_dataset` | dataset/version ref bad | fix the ref (see message) |
| `invalid_notebook` / `invalid_notebook_state` | bad notebook id, or wrong status for the op | `qzcli nb ls`; save/exec need RUNNING, rm needs STOPPED |
| `notebook_exec_failed` | notebook gateway rejected the terminal/exec | confirm `nb get` status=RUNNING; retry |
| `notebook_failed` / `job_failed` / `save_image_failed` | resource reached a failure state while waiting | `nb get` / `logs` / `events` for the cause |
| `priority_too_high` | `--priority` exceeds the project's cap | retry with a lower `--priority` (cap is on the project) |
| `set_e_footgun` | `--cmd` contains `set -e` | drop it (and guard risky steps with `\|\| true`), or pass `--allow-set-e` |
| `invalid_job` | bad/unknown JOB_ID | `qzcli ls -w <ws>` for valid ids |
| `no_instances` | job has no scheduled pods yet | `qzcli instances <job_id>` / wait |
| `no_specs` / `no_compute_groups` / `no_workspaces` | nothing available | see `hint` |
| `api_error` / `http_error` / `bad_response` | platform-side failure | inspect message/hint |

## Notes
- 401s auto-trigger one re-login from saved credentials; disable with `QZCLI_NO_AUTO_RELOGIN=1`.
- Proxy: set `proxy` in `~/.qzcli/config.json`, or `all_proxy`/`https_proxy` (SOCKS5 via PySocks).
