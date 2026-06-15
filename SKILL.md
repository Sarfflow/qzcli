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

### `projects`
→ `data: [ {id: "project-...", name, en_name, spaces: [{id: "ws-...", name}]} ]`

### `options compute-groups -w <ws>`
→ `data: [ {id: "lcg-...", name, workspace_id, gpu_type, gpu_type_display} ]`

### `options specs -w <ws> -g <lcg>`
→ `data: [ {quota_id, cpu_count, gpu_count, memory_gb, gpu_type, gpu_type_simple, gpu_type_display, total_price_per_hour, logic_compute_group_ids} ]`
The predefined card-count quotas (1/2/4/8…). `gpu_count` is **cards per node**;
`gpu_type` (e.g. `NVIDIA_H100_SXM_80G`) is what `create` needs. Pick a `quota_id`.

### `options images -w <ws> [--source ALL|SOURCE_OFFICIAL|SOURCE_PUBLIC|SOURCE_PRIVATE] [--verbose]`
→ `data: [ {address, name, image_id, source, visibility} ]`
Use `address` as `--image`. `--verbose` adds the heavy `creator` object (dropped
by default). A bad `--source` errors with `invalid_argument` + candidates.

### `rooms -w <ws> [--low-priority-threshold N] [--fit IxC]`
Per-机房 (lcg) GPU rollup, emptiest first — the unit you pick a job's target from.
→ `data: {low_priority_threshold, n_rooms, rooms: [{room, lcg_id, gpu_type, cluster, n_nodes, n_nodes_ready, gpu_total, gpu_free, gpu_used, low_priority_preemptible, effective_free, max_free_on_single_node, nodes_full_free}]}`
Sorted by `gpu_free`, then `effective_free` (= `gpu_free + low_priority_preemptible`),
then `gpu_total`. Low-priority cards (`priority <= N`, default 3) count as free
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
Options: `--project`, `--quota-id`, `--cpu`, `--gpu`, `--mem`, `--framework`
(default `pytorch`), `--image-type` (default: the image's source), `--instances`
(default 1), `--shm` GiB (default: spec memory), `--priority` 1–10 (default 10),
`--no-image-check`, `--dataset id[:version]` (repeatable).
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
→ `data: {total, jobs: [{job_id, name, status, workspace_id, project_id, logic_compute_group_id, created_at}]}`

### `instances JOB_ID`
→ `data: [ {name, instance_type, node, instance_status, created_at, started_at, finished_at, running_time_ms} ]`

### `logs JOB_ID [--tail N]`
→ `data: {logs: [{message, pod_name, node, time, timestamp_ms, ...}], total}`
`--tail N` (default 200) returns the most recent N lines, oldest-first (newest
last). A bad JOB_ID → `invalid_job`; a job with no scheduled pods → `no_instances`.

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

### `detail JOB_ID`
→ `data: {...}` (full job detail)

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
| `invalid_job` | bad/unknown JOB_ID | `qzcli ls -w <ws>` for valid ids |
| `no_instances` | job has no scheduled pods yet | `qzcli instances <job_id>` / wait |
| `no_specs` / `no_compute_groups` / `no_workspaces` | nothing available | see `hint` |
| `api_error` / `http_error` / `bad_response` | platform-side failure | inspect message/hint |

## Notes
- 401s auto-trigger one re-login from saved credentials; disable with `QZCLI_NO_AUTO_RELOGIN=1`.
- Proxy: set `proxy` in `~/.qzcli/config.json`, or `all_proxy`/`https_proxy` (SOCKS5 via PySocks).
