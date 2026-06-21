# qzcli

[简体中文](README.md) | **English**

Agent-first command-line client for the 启智 platform (`qz.sii.edu.cn`). It lets a
long-running Claude (in a tmux session) drive the whole **develop → debug → train**
loop by itself.

Built entirely on the platform's reverse-engineered **web API** (`/api/v1/*`,
`/api/v2/*`, cookie auth) — no OpenAPI. This repository **is** a Claude Code skill;
clone it into your skills directory and it's ready to use.

## Workflow

- Keep the agent on a **networked CPU instance** writing code long-term (shared
  GPFS — where you write doesn't matter);
- Spin up a **4090 instance** to set up the env and run a smoke test, then save it
  as an image;
- Launch **distributed training** from that image and let the agent watch it.

GPUs are claimed on demand and released when done; heavy compute goes to
distributed jobs — the right tool for the job. See the
[workflow doc](docs/工作流.md) (Chinese).

## Install (as a Claude Code skill)

```bash
# user-level (all projects):
git clone https://github.com/Sarfflow/qzcli.git ~/.claude/skills/qzcli
cd ~/.claude/skills/qzcli && uv sync          # no uv? use pip install -e .
~/.claude/skills/qzcli/qzcli --version         # verify
```

Invoke via the self-locating launcher at the skill root:
`~/.claude/skills/qzcli/qzcli <cmd>` (works from any directory).
Update: `git -C ~/.claude/skills/qzcli pull && (cd ~/.claude/skills/qzcli && uv sync)`.

## Usage

```bash
qzcli login -u <id> -p <password>             # or QZCLI_USERNAME / QZCLI_PASSWORD
qzcli projects                                 # project→space hierarchy
qzcli rooms -w ws-xxx                          # which 机房 (lcg) is emptiest
qzcli options specs  -w ws-xxx -g lcg-yyy      # pick a quota_id
qzcli options images -w ws-xxx                 # pick an image
qzcli create --name demo -w ws-xxx -g lcg-yyy \
  --quota-id quota-zzz --image docker.sii/...:tag --cmd "python train.py"
# add --dry-run first to preview the resolved payload without creating a job
qzcli ls   -w ws-xxx --running
qzcli logs <job_id> --tail 200
qzcli stop <job_id>
```

Interactive GPU instances live under `qzcli nb`. Full command reference, output
schemas and the read-before-write semantics are in **[SKILL.md](SKILL.md)**.

## Design principles

- **Agent-first** — JSON by default (stable `{"ok", "data"|"error"}` envelope);
  `--table` is the only human view.
- **Read before write** — enumerate legal options (`qzcli options ...`) before
  submitting; `create` enforces validation internally and never guesses or
  auto-selects.
- **Actionable errors** — every error says *what* is wrong and *what to do next*
  (`error.code` / `error.hint` / `error.candidates`).

## Local development

```bash
uv sync && uv run qzcli --help
uv run pytest -q
```

Local state lives in `~/.qzcli/`: `config.json`, `credentials.json` (401
auto-relogin, 0600), `.cookie` (CAS session, 0600). Set
`QZCLI_NO_AUTO_RELOGIN=1` to disable auto-relogin.
