# qzcli

**简体中文** | [English](README.en.md)

面向 coding agent 的启智平台（`qz.sii.edu.cn`）命令行工具。让在 tmux 里长期运行的
Claude 自己把「开发 → 调试 → 正式训练」这条循环跑起来。

完全基于逆向出来的 **web API**（`/api/v1/*`、`/api/v2/*`，cookie 鉴权），无 OpenAPI。
本仓库本身就是一个 Claude Code skill——clone 进 skills 目录即可用。

## 工作流

- 在**可联网的 CPU 实例**上让 agent 长期常驻写代码（共享 GPFS，写在哪都一样）；
- 起 **4090 实例**配环境、跑 smoke test，调通后保存为镜像；
- 用镜像起**分布式训练**，agent 盯着进展。

GPU 按需占用、用完即关，重计算交给分布式训练——好钢用在刀刃上。
详见 [工作流文档](docs/工作流.md)。

## 安装（作为 Claude Code skill）

```bash
# 用户级（所有项目可用）：
git clone https://github.com/Sarfflow/qzcli.git ~/.claude/skills/qzcli
cd ~/.claude/skills/qzcli && uv sync          # 没有 uv 就用 pip install -e .
~/.claude/skills/qzcli/qzcli --version         # 验证
```

通过 skill 根目录的自定位启动器调用：`~/.claude/skills/qzcli/qzcli <cmd>`（任意目录可用）。
更新：`git -C ~/.claude/skills/qzcli pull && (cd ~/.claude/skills/qzcli && uv sync)`。

## 怎么用

```bash
qzcli login -u <学工号> -p <密码>             # 或用 QZCLI_USERNAME / QZCLI_PASSWORD
qzcli projects                                 # project→space 层级
qzcli rooms -w ws-xxx                          # 哪个机房（lcg）最空
qzcli options specs  -w ws-xxx -g lcg-yyy      # 选 quota_id
qzcli options images -w ws-xxx                 # 选镜像
qzcli create --name demo -w ws-xxx -g lcg-yyy \
  --quota-id quota-zzz --image docker.sii/...:tag --cmd "python train.py"
# 先加 --dry-run 可预览解析后的 payload，不真正建任务
qzcli ls   -w ws-xxx --running
qzcli logs <job_id> --tail 200
qzcli stop <job_id>
```

交互式建模实例（GPU 调试）用 `qzcli nb` 系列；完整命令、输出 schema 与
read-before-write 语义见 **[SKILL.md](SKILL.md)**。

## 设计原则

- **Agent-first**：默认输出 JSON（稳定的 `{"ok", "data"|"error"}` 信封），`--table` 是唯一的人类视图。
- **Read before write**：先 `qzcli options ...` 枚举合法选项再提交；`create` 内部强制校验，绝不瞎猜或自动选。
- **可执行的报错**：每个错误都说清*哪里错了*和*下一步怎么办*（`error.code` / `error.hint` / `error.candidates`）。

## 本地开发

```bash
uv sync && uv run qzcli --help
uv run pytest -q
```

本地状态都在 `~/.qzcli/`：`config.json`、`credentials.json`（401 自动重登，0600）、
`.cookie`（CAS 会话，0600）。设 `QZCLI_NO_AUTO_RELOGIN=1` 关闭自动重登。
