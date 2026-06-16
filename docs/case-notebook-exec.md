# 案例:在交互式建模实例里执行命令(`nb exec`)

> 通用方法论见 [reverse-engineering-playbook.md](reverse-engineering-playbook.md)。
> 这一篇是该方法在「`nb exec`」这个具体需求上的完整应用记录 + 维护参考。

## 需求背景

工作流的闭环:

```
可联网 CPU 实例改代码
  → 在【可上网 GPU】起交互式建模,在实例里配环境、跑通 smoke   ← nb exec 填的就是这一步
  → 保存镜像
  → 用该镜像起分布式训练(H100/H200 离线集群)
  → 确认分布式稳定运行后,及时停止交互式建模,释放 GPU
```

两个关键约束:

- **网络分区**:H100/H200 所在的分布式训练空间**不能联网**;只有「可上网 GPU」能访问外网。
  因此凡是配环境需要联网(pip/conda/git/HF 下载…)的步骤,交互式建模必须起在
  **可上网 GPU** 上,而不是 H 卡。可上网 GPU 通常也够放下大多数模型,所以一般**不需要**
  起 H 卡交互式建模。
- **用卡文明**:交互式建模会一直占着 GPU。**只有在分布式训练成功启动并稳定运行后**,这个
  工作流才算完成 —— 此时要**及时停止交互式建模**释放资源。之后由 CPU 节点上的 agent 持续
  监控分布式任务(如每 45 分钟轮询一次),发现异常再重新走这个工作流。

其中「在实例里执行命令」是缺口 —— 启智平台**没有可用的 SSH**(notebook 元数据里有
`ProxyJump`/`SshDomain`/`SshPort` 字段,但都是摆设;团队历史上靠 code-server 端口转发 +
wstunnel 的 hack 来连)。所以目标变成:**找到网页 IDE 执行命令的通道,直接走它,绕开 SSH。**

---

## 逆向过程(浓缩)

1. **F12 看 IDE 页只有噪声**。在平台控制台页抓包,只有 `GetRealtimeNotebookMetric`
   之类轮询 —— 因为真正的 IDE/终端流量**不在控制台这个 origin 上**。

2. **驱动真实浏览器走完整流程**。用 jshook + 真 Chrome:CAS 表单登录(填可见的用户名/密码
   框、点登录,让页面 JS 自己加密)→ 进入交互式建模列表 → 点某个运行中实例的「打开」。

3. **「打开」弹出新标签**,URL = `https://qz.sii.edu.cn/ide?notebook_id={notebook_id}`。
   在该标签上开 `network_enable` + `ws_monitor` 并重载,抓到 IDE 启动时建立的 WebSocket:

   ```
   wss://nat2-notebook-inspire.sii.edu.cn/{ws_id}/{project_id}/{user_id}/
        jupyter/{notebook_id}/{token}/terminals/websocket/2
   wss://.../jupyter/{notebook_id}/{token}/api/events/subscribe?token={token}
   ```

   **一眼认出是 Jupyter Server**:`/api/kernels`、`/api/sessions`、`/api/terminals`、
   `/terminals/websocket/N`、`/lab/api/...`、`_xsrf` cookie —— 全是标准 JupyterLab。
   这是协议最简单、最稳的情况(Jupyter 终端协议公开)。

4. **决定性实验:到底什么在鉴权?** 用 curl 对网关分别试:
   - 只带 `inspire-session` cookie + 真 token 路径 → **403**
   - 不带任何东西 → 403
   - 带 `?token={token}` query → **200** ✅
   - 带 `Authorization: token {token}` header → **200** ✅

   结论:**网关只认 Jupyter token**,cookie 一概不需要。浏览器里那两个 path-scoped
   cookie(`username-...` httpOnly 会话 cookie、`_xsrf`)对 CLI 来说无关紧要 ——
   token-header 鉴权还会跳过 XSRF 检查。

5. **顺藤摸瓜:token 从哪来?** 它**不在** `GetNotebook` 响应、不在 localStorage、不在
   任何 API 响应体。最后在 IDE 页面的 DOM 里找到:

   ```html
   <iframe src="https://qz.sii.edu.cn/api/v1/notebook/lab/{notebook_id}/?timestamp=..."></iframe>
   ```

   这个 **qz 自己的端点**才是入口。curl 它(带新鲜 cookie、跟随重定向):

   ```
   GET /api/v1/notebook/lab/{notebook_id}/
     → 301  /api/v1/notebook/lab/{notebook_id}
     → 302  https://nat2-notebook-inspire.sii.edu.cn/{ws_id}/{project_id}/{user_id}/
            jupyter/{notebook_id}/{token}/lab?token={token}
   ```

   **qz 服务端(持有用户会话)铸 token 并 302 到带 token 的网关地址。** token 既是最后一段
   路径、也是 `?token=` 的值。`nb exec` 要做的就是发这一个请求、跟随重定向、从最终 URL 解析
   出 `base` 和 `token`。

6. **cookie 新鲜度的坑**。第一次用 qzcli 保存的(略旧的)cookie curl 这个 `lab` 端点 →
   401 跳 keycloak,尽管同一个 cookie 拉 `/api/v2` 列表完全正常。原因:`lab` 是**页面级
   路由**,要求 keycloak 会话仍存活。`qzcli login` 重新登录拿的新鲜 cookie → 200。
   ⇒ 实现里复用 qzcli 现有的「401 自动重登录」兜底。

7. **纯命令行端到端复现**(脱离浏览器):resolve token → `POST /api/terminals` 建终端 →
   连终端 WS → 发命令 → 收输出 → `DELETE /api/terminals/{name}`。一次跑通,确认可落地。

---

## 架构全貌

```
                 浏览器地址栏 / iframe
                         │  (页面级路由,需新鲜 keycloak SSO 会话)
                         ▼
   qz.sii.edu.cn  ──────────────────────────────────────────────┐
   ├─ /ide?notebook_id=…                  SPA 壳,内嵌 iframe        │
   ├─ /api/v1/notebook/lab/{id}/   ──►  服务端铸 token,302 重定向 ──┘
   ├─ /api/v1/*                           cookie-authed XHR
   └─ /api/v2/{svc}?Action=…              cookie-authed XHR (notebook 生命周期)
                         │
                         ▼  302 Location
   nat2-notebook-inspire.sii.edu.cn        ← 后端服务网关 (nginx)
   └─ /{ws_id}/{project_id}/{user_id}/jupyter/{notebook_id}/{token}/
        ├─ /api/me                  鉴权: Authorization: token {token}
        ├─ /api/terminals           POST 建终端 → {"name":"N"}
        ├─ /terminals/websocket/N   WS,terminado 协议
        ├─ /api/kernels, /api/sessions, /lab/api/…   (完整 JupyterLab)
        └─ 背后是该 notebook pod 里跑的 Jupyter Server
```

三层鉴权对照:

| 层 | 端点 | 认什么 |
|---|---|---|
| 页面路由 | `/ide`, `/api/v1/notebook/lab/{id}/` | 新鲜 `inspire-session`(keycloak SSO 存活) |
| API XHR | `/api/v1/*`, `/api/v2/*` | `inspire-session` cookie(校验较宽松) |
| 服务网关 | `nat2-*/.../jupyter/...` | **Jupyter token**(query 或 `Authorization: token`),cookie 不需要 |

---

## 完整 `nb exec` 配方

### 1. 解析 jupyter base + token

```
GET https://qz.sii.edu.cn/api/v1/notebook/lab/{notebook_id}/
    Cookie: inspire-session=<fresh>
    (跟随重定向;若首请求 401 → 重登录后重试)
最终 URL: https://nat2-notebook-inspire.sii.edu.cn/{ws}/{project}/{user}/jupyter/{notebook_id}/{token}/lab?token={token}
解析: base = ".../jupyter/{notebook_id}/{token}"   token = 最后一段 == ?token= 的值
```

### 2. 鉴权

所有对网关的请求加 header `Authorization: token {token}`。不需要 cookie。网关直连
(不走代理)。

### 3. 建终端 → WS 执行 → 删终端(terminado 协议)

```
POST {base}/api/terminals                          → {"name": "N"}
WS   wss://{base}/terminals/websocket/{N}?token={token}   (header 也带 Authorization)

发送(client→server):  ["stdin", "<text>\n"]
接收(server→client):  ["stdout", "<带 ANSI 的文本>"] / ["setup", {}] / ["disconnect", N]

DELETE {base}/api/terminals/{N}                    → 204  (收尾)
```

### 4. 从 PTY 输出里干净地取出 stdout + 退出码

终端是 PTY:会**回显**你发的命令、夹着欢迎横幅和 shell 提示符。用唯一标记把真正的输出夹起来,
**整条命令放在一行**(用 `;` 串联),这样两个输出标记之间不会再夹入提示符/回显:

```
echo {NONCE}START; {用户命令}; echo {NONCE}EXIT$?END
```

- 新建终端会先打印欢迎横幅 → 发命令前先**排空到空闲**(≈shell 就绪)。
- 读到正则 `{NONCE}EXIT(-?\d+)END` 即完成,捕获组就是退出码。
- 命令行的**回显**里是字面量 `...EXIT$?END`(`$?` 未展开),只有**真实输出**里才是
  `...EXIT0END`(数字)——所以判完成不会误中回显。
- stdout = 行 `^{NONCE}START$`(输出标记,非带 `echo ` 前缀的回显行)与 END 标记之间的内容。
- 退出码反映命令里**最后一条语句**的 `$?`(标准 shell 语义)。
- 清理 ANSI(CSI `\x1b[…`、OSC `\x1b]…\x07`)和 `\r`。

---

## 代码落点(便于维护)

| 文件 | 函数 | 职责 |
|---|---|---|
| `src/qzcli/client/http.py` | `Client.resolve_lab_url()` | GET `lab` 端点、手动走重定向、停在含 `/jupyter/` 的那跳并返回;401 自动重登录 |
| `src/qzcli/client/endpoints.py` | `resolve_jupyter()` | 用正则 `_JUPYTER_BASE_RE` 从最终 URL 拆出 `(base, token)` |
| | `create_terminal()` / `delete_terminal()` | 网关 `POST`/`DELETE /api/terminals`(直连、不走代理的 session) |
| `src/qzcli/core/notebook.py` | `exec_command()` | 预检 RUNNING → 建终端 → WS 跑 → START/END marker 提取 → 删终端 |
| | `_strip_ansi()` / `_extract_between()` / `_recv_stdout()` | 纯函数,已被单测覆盖(`tests/test_nb_exec.py`) |
| `src/qzcli/cli.py` | `cmd_nb()` + `nb exec` 子命令 | `qzcli nb exec <id> -- <cmd>`,`--timeout` / `--raw` |

依赖:`websocket-client`(同步 WS 客户端)。

---

## 维护 / debug 指南(接口挂了怎么修)

按"从外到内"逐层定位 —— 先确认是哪一层断了:

1. **`nb exec` 报错先看 `error.code`**:
   - `invalid_notebook_state` → 实例不在 RUNNING(或 id 错)。正常。
   - `auth_expired` / 解析 lab URL 401 → cookie 失效层面的问题,`qzcli login` 重登录。
   - `notebook_exec_failed`(网关 403/404)→ token 解析对了但网关拒了,见下。

2. **重新抓一遍**(平台发版后字段/路径可能变):用 jshook + 真 Chrome 重走「打开实例」,
   对照这几处是否变化:
   - iframe 入口是否仍是 `/api/v1/notebook/lab/{id}/`(可能改名/改路径)。
   - 重定向最终 URL 的**路径结构**是否仍是 `…/jupyter/{id}/{token}/`
     (变了就要改 `_JUPYTER_BASE_RE`)。
   - 网关鉴权是否仍认 `Authorization: token`(用 curl 三选一实验复测)。
   - 终端是否仍是 terminado(`/terminals/websocket/N` + `["stdin"/"stdout", …]` 帧);
     若平台换成 code-server,则要改走 code-server 的 WS 协议(更复杂)。

3. **决定性实验复测鉴权**(见 playbook §3):curl 分别只带 token-query / token-header /
   cookie,确认哪个仍 200。

4. **常见漂移点排序**(按概率):cookie 新鲜度策略 > token 解析正则 > 网关鉴权方式 >
   终端协议本身(最稳,Jupyter 标准)。

---

## 可迁移性

这套架构(**主站 SPA + cookie API + 一个把请求代理到「每用户/每实例」后端服务的网关**,
后端常是 JupyterLab / code-server / VS Code Server)在算力平台里非常普遍。迁移要点:

- 找到「打开 IDE」实际指向的 URL(多半是主站一个铸 token + 重定向的端点,或 iframe src)。
- 跟重定向,从最终 URL 解析出后端 base + token。
- 认出后端是什么(Jupyter? code-server?),套对应的标准协议。
- 用决定性实验确认网关认哪个凭证 —— 往往 token 足矣,cookie 反而不需要。
