# qzcli

Agent-first command-line client for the 启智 platform (`qz.sii.edu.cn`), built
entirely on its reverse-engineered **web API** (`/api/v1/*`, `/api/v2/*`, cookie
auth). No OpenAPI.

This repository **is** a Claude Code skill — clone it into your skills directory
and it's ready to use.

## Install as a Claude Code skill

> Instructions for Claude Code: to install this skill, do the following.

1. Clone this repo into the user's skills directory as `qzcli`:
   ```bash
   # user-level (all projects):
   git clone <REPO_URL> ~/.claude/skills/qzcli
   # or project-level (this project only):
   git clone <REPO_URL> .claude/skills/qzcli
   ```
2. Prepare the environment once (installs deps into a local venv):
   ```bash
   cd ~/.claude/skills/qzcli && uv sync
   ```
   If `uv` is not installed, use `pip install -e ~/.claude/skills/qzcli` instead.
3. Verify:
   ```bash
   ~/.claude/skills/qzcli/qzcli --version
   ```

The skill is now discoverable via its `SKILL.md`. Invoke the tool through the
self-locating launcher at the skill root: `~/.claude/skills/qzcli/qzcli <cmd>`
(works from any directory). See [SKILL.md](SKILL.md) for the full command
reference, output schemas and the read-before-write workflow.

To update: `git -C ~/.claude/skills/qzcli pull && (cd ~/.claude/skills/qzcli && uv sync)`.

## Design principles

1. **Actionable feedback** — every error says *what* is wrong and *what to do
   next* (`error.code`, `error.hint`, and `error.candidates` listing the
   currently-legal choices). No silent failures, no bare error codes.
2. **Read before write** — you enumerate the legal options for each field
   (`qzcli options ...`) before submitting. `create --dry-run` is the read step;
   `create` re-runs it internally and refuses to submit anything that fails
   validation. It never guesses or auto-selects.
3. **No OpenAPI** — only the cookie-authed web API the browser uses.
4. **Agent-first** — JSON by default (stable `{"ok", "data"|"error"}` envelope);
   `--table` is the only human view and exposes nothing JSON can't.

## Local development

```bash
uv sync            # install deps (requests, PySocks)
uv run qzcli --help
uv run pytest -q   # run the test suite
```

## Quickstart

```bash
qzcli login -u <学工号> -p <密码>          # or QZCLI_USERNAME / QZCLI_PASSWORD
qzcli projects                              # project→space hierarchy
qzcli options compute-groups -w ws-xxx
qzcli options specs -w ws-xxx -g lcg-yyy
qzcli options images -w ws-xxx
qzcli create --dry-run --name demo -w ws-xxx -g lcg-yyy \
  --quota-id quota-zzz --image docker.sii/...:tag --cmd "python train.py"
qzcli create        --name demo -w ws-xxx -g lcg-yyy \
  --quota-id quota-zzz --image docker.sii/...:tag --cmd "python train.py"
qzcli ls -w ws-xxx --running
qzcli logs <job_id> --tail 200
qzcli stop <job_id>
```

See [SKILL.md](SKILL.md) for the full command reference, output schemas and
error semantics.

## Layout

```
src/qzcli/
  cli.py            # argparse dispatch, --table flag, error envelope
  output.py         # JSON (default) / table rendering
  config.py         # ~/.qzcli state: config, credentials, cookie
  errors.py         # QzError → error envelope
  client/
    crypto.py       # browser-compatible RSA for the CAS password field
    cas.py          # CAS→Keycloak login chain (manual redirect walk)
    http.py         # cookie + browser headers, 401 auto-relogin, /api/v1 + /api/v2
    endpoints.py    # the only place endpoint paths/payloads live
  domain/models.py  # Project{spaces}, ComputeGroup, Spec, Image, Job
  core/
    options.py      # cascading candidate resolution
    create.py       # read-before-write job creation
```

## Local state

Everything under `~/.qzcli/`: `config.json` (api_base_url, proxy),
`credentials.json` (for 401 auto-relogin, 0600), `.cookie` (CAS session, 0600).
Set `QZCLI_NO_AUTO_RELOGIN=1` to disable automatic re-login.

## Tests

```bash
uv run pytest -q
```

Covers the crypto known-answer vector, option/candidate resolution, and the
`train_job/create` payload shape (nested `resource_spec_price` + the required
top-level `cpu`/`mem_gi`/`gpu_count`).
