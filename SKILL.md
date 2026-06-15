---
name: qzcli
description: Drive the 启智 platform (qz.sii.edu.cn) from the command line — log in via CAS, browse projects/workspaces, enumerate legal compute-group/spec/image options, validate and submit training jobs, and list/inspect/stop jobs. Use whenever a task involves submitting or managing jobs on qz.sii.edu.cn, or reading its compute resources/quotas. All output is JSON by default.
---

# qzcli

Agent-first CLI for the 启智 platform, built entirely on its reverse-engineered
**web API** (`/api/v1/*` and `/api/v2/*`, cookie auth). It is designed so you
never have to guess: every selectable field can be *enumerated* before you
write, and every error tells you what is wrong and what to do next.

## Invocation

This skill directory ships a self-locating launcher named `qzcli`. Invoke it
with the skill directory's absolute path (the folder this SKILL.md is in):

```bash
<skill-dir>/qzcli <command> [args...]
```

It runs the bundled Python tool via `uv` (first run auto-syncs deps). If `uv`
is unavailable, run `pip install -e <skill-dir>` once and then call `qzcli`
directly.

All commands print a JSON envelope to stdout:

- success → `{"ok": true, "data": <value>}`
- failure → `{"ok": false, "error": {"code", "message", "hint"?, "candidates"?}}`, exit code 1

Add `--table` (before the subcommand) for human-readable tables, e.g.
`uv run qzcli --table projects`. JSON is the source of truth; `--table` never
shows anything JSON can't.

## The workflow — READ BEFORE WRITE

Always read your way to valid parameters before creating a job:

1. **`login`** — establish a CAS session cookie (saved to `~/.qzcli/`).
2. **`projects`** — see the project→space hierarchy; pick a workspace (`ws-...`).
3. **`rooms -w <ws>`** — see which 机房 (`lcg-...`) is emptiest *before* picking
   one. Rank by free cards; **low-priority (preemptible) cards count as free**
   because a job with `priority > 3` evicts them — so `effective_free`
   (`gpu_free + low_priority_preemptible`) is the real headroom, the same thing
   you'd eyeball per-node on the web. Prefer the 机房 with the most; for the spec
   you want, confirm there's room for `instances × gpu_count` cards.
4. **`options compute-groups -w <ws>`** — confirm the compute group (`lcg-...`)
   id you picked in step 3 (or list them if you skipped `rooms`).
5. **`options specs -w <ws> -g <lcg>`** — pick a spec (`quota_id`) with its cpu/gpu/mem.
6. **`options images -w <ws>`** — pick an image `address`.
7. **`create --dry-run ...`** — validate everything and preview the payload.
8. **`create ...`** — submit (it re-runs the dry-run internally first).

You normally **don't** pick individual nodes — the cluster scheduler places the
job. `rooms` (capacity per 机房) is the only availability check the submit flow
needs. If you do want to eyeball nodes, `avail -w <ws> -g <lcg>` lists just the
emptiest few schedulable ones (default `--top 10`), enough to confirm a job's
nodes exist (a 16-card job needs two free 8-card nodes).

When a selection is wrong or missing, the error's `candidates` array lists the
legal choices at that level. Read it, pick one, retry. Never invent ids.

## Tool vs. decision — who chooses what

The CLI only provides interaction capability: it reads options, validates, and
writes. It will **not** make choices for you and will **not** guess — that is
your job as the agent. In particular:

- **Choosing the project** for a multi-project workspace is yours. The tool
  errors with `ambiguous_project` and lists candidates; it never picks one.
- **Recommending from history** is a useful workflow — but it's *your* call, not
  the tool's. To recommend a likely project for a workspace, run
  `qzcli ls -w <ws>` and look at which `project_id` recent jobs used; to recommend
  a spec, look at recent jobs' compute group / size. Then pass your choice
  explicitly via `--project` / `--quota-id`. The tool deliberately does not read
  history to auto-fill anything.
- **Picking the target 机房/node** from `rooms` / `avail` is yours; the tool ranks
  by `effective_free` (idle + preemptible cards) but submits only what you tell it.

So: the tool surfaces facts and legal candidates; you make the decisions and
pass them in explicitly.

## Commands & output schemas

### `login [-u USER] [-p PASS] [--cookie STR] [-w WS]`
Runs the CAS→Keycloak login chain and saves the cookie + credentials (the
credentials enable transparent 401 re-login later). Credentials may also come
from `QZCLI_USERNAME` / `QZCLI_PASSWORD`. If a captcha is required, log in via a
browser and pass the exported cookie string with `--cookie`.
→ `data: {status, cookie_len, workspace_id}`

### `projects`
→ `data: [ {id: "project-...", name, en_name, spaces: [{id: "ws-...", name}]} ]`
The project→space hierarchy is preserved (a space's owning project is known),
so `create` can infer `project_id` from the workspace.

### `options compute-groups -w <ws>`
→ `data: [ {id: "lcg-...", name, workspace_id, gpu_type, gpu_type_display} ]`

### `options specs -w <ws> -g <lcg>`
→ `data: [ {quota_id, cpu_count, gpu_count, memory_gb, gpu_type, gpu_type_simple, gpu_type_display, total_price_per_hour, logic_compute_group_ids} ]`
The full predefined card-count table for the compute group (e.g. 1/2/4/8-card
quotas), straight from the platform. `gpu_count` is the **cards per node**;
`gpu_type` is the **full** type (e.g. `NVIDIA_H100_SXM_80G`) the create payload
requires; `gpu_type_simple` (e.g. `H100`) is for display. Pick a `quota_id`.

### `options images -w <ws> [--source ALL|SOURCE_OFFICIAL|SOURCE_PUBLIC]`
→ `data: [ {address, name, image_id, source, visibility, creator} ]`
Use `address` as the `--image` value.

### `avail -w <ws> [-g <lcg>] [--low-priority-threshold N] [--all] [--top N]`
Where can a job land — combines idle cards with low-priority (preemptible) ones.
`nodes` are **physical GPU machines** (e.g. `qb-prod-gpu001`, 8 cards each).
→ `data: {low_priority_threshold, n_nodes, n_nodes_shown, by_gpu_type:
[{gpu_type, n_nodes, gpu_total, gpu_free, low_priority_preemptible,
effective_free}], nodes: [{name, gpu_type, gpu_total, gpu_free, gpu_used,
low_priority_preemptible, effective_free, status}]}` — nodes sorted by
`effective_free` (= `gpu_free + low_priority_preemptible`). Submit higher-priority
to evict the preemptible ones. Tasks with `priority <= N` (default 3) count as
low-priority. `by_gpu_type` covers the **whole fleet** (paged in full, not just
the first 200 nodes). The scheduler places jobs itself, so you rarely need the
node list; by default it is just the **emptiest few schedulable nodes**
(`effective_free > 0`, `Ready`, capped at `--top 10`) — `n_nodes` is the true
total, `n_nodes_shown` the list size. `--top N` changes the cap (`--top 0` = all
schedulable), `--all` dumps every node (large — tens of thousands of tokens).
For submission, prefer the `rooms` overview (~500 tokens) over the node list.

### `rooms -w <ws> [--low-priority-threshold N]`
"Which 机房 is emptiest" — per-机房 (logic compute group) GPU rollup, ranked
roomiest-first. Each named 机房 in the UI is an `lcg-`, and a job submission
targets one, so this is the unit you actually pick from.
→ `data: {low_priority_threshold, n_rooms, rooms: [{room (lcg name), lcg_id,
gpu_type, cluster, n_nodes, n_nodes_ready, gpu_total, gpu_free, gpu_used,
low_priority_preemptible, effective_free}]}` — sorted by `gpu_free`, then
`effective_free`, then `gpu_total` (most idle cards, then evictable headroom,
then largest fleet). 机房 share physical nodes, so `gpu_total` overlaps across
rows and `gpu_free` can be **negative** (the 机房 is oversubscribed — rely on
`effective_free` there). Use `avail -g <lcg>` to drill into one 机房's nodes.

### `create --dry-run | create  (required: --name -w -g --image --cmd)`
Options: `--project`, `--quota-id`, `--cpu`, `--gpu`, `--mem`, `--framework`
(default `pytorch`), `--image-type` (default: inferred from the matched image's
source), `--instances` (default 1), `--shm` GiB (default: the spec's memory
size, matching the web), `--priority` 1–10 (default 10), `--no-image-check`.
- `--dry-run` → `data: {dry_run: true, resolved: {...}, payload: {...}}`, submits nothing.
- without `--dry-run` → `data: {job_id, workspace_id, name, url, resolved}`.
- Pick a spec with `--quota-id` from `options specs` (this is the normal path —
  the platform validates against these predefined quotas). `--cpu/--gpu/--mem`
  only override the resource amounts and may be rejected if they don't match the
  quota; prefer choosing the right `quota_id`.
- `--instances` is the **node count**; cards-per-node come from the spec's
  `gpu_count`.
- `--dataset id[:version]` (repeatable) mounts datasets. Each is validated
  before submit (read-before-write); an invalid one fails with `invalid_dataset`
  and the platform's reason. Validated refs go into the job's top-level
  `dataset_info` as `{dataset_id, version_id, path}`.

### `dataset validate -w <ws> --dataset id[:version] ...`
Check dataset/version refs without submitting anything.
→ `data: [ {dataset_id, version_id, success, path, error_message} ]`
`path` is the mount path on success; failures say missing dataset vs version.
- A workspace can belong to **multiple projects**; when it does, `--project` is
  required (the error `ambiguous_project` lists the owning projects). `--project`
  must be a project that actually owns the workspace.
- `--image-type` is auto-set to the image's own source (`SOURCE_OFFICIAL` /
  `SOURCE_PUBLIC` / `SOURCE_PRIVATE`) unless you pass it explicitly.

### `ls -w <ws> [--running] [--limit N]`
→ `data: {total, jobs: [{job_id, name, status, workspace_id, project_id, logic_compute_group_id, created_at}]}`

### `instances JOB_ID`
→ `data: [ {name, instance_type, node, instance_status, created_at, started_at, finished_at, running_time_ms} ]`
The job's real pods/instances. `name` is the actual pod name.

### `logs JOB_ID [--tail N]`
→ `data: {logs: [{message, pod_name, node, time, timestamp_ms, ...}], total}`
Pod names are resolved from `instances` (not guessed).

### `metrics JOB_ID [--minutes N] [--interval S] [--metric M ...]`
Per-instance resource utilization over time — use to check a *running* job is
actually using its GPUs (or is stuck/idle). Default metrics: `gpu_usage_rate`,
`gpu_memory_usage_rate`; available: gpu_usage_rate, gpu_memory_usage_rate,
cpu_usage_rate, memory_usage_rate, disk_io_read/write, network_io_read/write,
network_storage_io_read/write. Default window is the last 30 min.
→ `data: {window_minutes, summary: [{group_name (pod), metric_type, last, avg, max, points}], groups: [{metric_type, group_name, time_series: [{timestamp, data}]}]}`
Rates are 0..1. A running GPU job with `gpu_usage_rate` near 0 is likely stuck.

### `stop JOB_ID`
→ `data: {stopped: JOB_ID, result: {...}}`

### `events JOB_ID [--instance <job_id>-worker-N]`
→ `data: [ {type, reason, message, from, first_timestamp, last_timestamp, ...} ]`

### `detail JOB_ID`
→ `data: {...}` (full job detail)

## Error codes (the `error.code` field)

| code | meaning | next step |
|---|---|---|
| `auth_required` / `auth_expired` | no/expired cookie | `qzcli login` |
| `bad_credentials` | wrong user/pass | fix `-u/-p` |
| `captcha_required` | captcha blocking login | browser login + `--cookie` |
| `invalid_workspace` / `invalid_compute_group` / `invalid_spec` / `invalid_image` | bad selection | pick from `candidates` |
| `ambiguous_project` / `invalid_project` | workspace has multiple owning projects, or `--project` doesn't own it | pass `--project` from `candidates` |
| `missing_fields` / `missing_spec` | required input absent | supply it (read `options` first) |
| `incomplete_spec` | spec lacks cpu/mem | pass `--cpu`/`--mem` |
| `no_specs` / `no_compute_groups` / `no_workspaces` | nothing available at this level | see `hint` |
| `api_error` / `http_error` / `bad_response` | platform-side failure | inspect message/hint |

## Notes
- No `/openapi/` is ever called — only the cookie-authed web API.
- 401s auto-trigger one re-login using saved credentials; disable with
  `QZCLI_NO_AUTO_RELOGIN=1`.
- Proxy: set `proxy` in `~/.qzcli/config.json`, or use `all_proxy`/`https_proxy`
  env vars (SOCKS5 supported via PySocks).
