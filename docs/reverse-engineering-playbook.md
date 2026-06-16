# 逆向方法论:黑盒 Web 平台 API

这是一套从零逆向一个「只有网页、没有 API 文档」的算力/开发平台的通用流程。
qzcli 整个项目都是这么做出来的。配套的实战案例见 [case-notebook-exec.md](case-notebook-exec.md)。

---

## 0. 核心原则:抓包优先,而非暴力试探

**永远优先「捕获平台前端的真实请求」,而不是对着写接口反复试参数。**

- 读接口(list/get)试错代价低,但仍然慢且容易误判字段含义。
- **写/变更接口(create/stop/delete/save)绝不能暴力试** —— 它们操作的是共享生产环境的
  真实资源。靠反复「创建→删除真实实例」来猜字段集,既污染环境、又慢、又危险。
- 正确做法:在浏览器里**手动操作一次**那个功能,把对应的请求(URL + headers + body +
  响应)完整抓下来,直接照抄。前端已经把正确的参数组装好了,你只需要复制。

> 教训实例:还原 `CreateNotebook` 时一度靠「建一个真 notebook 看报错 → 改字段 → 再建」
> 来猜 `resource_spec_price` 的结构,既慢又在动真实资源。换成「在网页点一次新建、把展开的
> 请求体贴出来」后,7 个字段一次看清。

把这条原则刻进肌肉记忆:**先抓,再抄,最后才在必要时小范围验证。**

---

## 1. 鉴权分层模型(最关键的心智模型)

现代 Web 平台的鉴权几乎总是**分层**的,不同层用不同的凭证。逆向时第一件事就是搞清楚
你面对的是哪一层 —— 用错层的凭证会得到误导性的 401/302。典型三层:

| 层 | 谁在用 | 凭证 | 失败表现 |
|---|---|---|---|
| **页面路由** (SPA、`/ide`、`/lab` 这类) | 浏览器地址栏导航 | 完整 SSO 会话(OIDC/CAS,常配合 keycloak) | 302 跳转到登录页 |
| **API XHR** (`/api/v1`、`/api/v2`) | 前端 JS 的 fetch/XHR | 一个会话 cookie(可能比页面层校验更宽松) | 401 / 被网关重定向、返回 HTML 而非 JSON |
| **后端服务网关** (Jupyter、code-server、对象存储…) | iframe / 子资源 | 该服务自己的 token / 专属 cookie(常 path-scoped) | 403 / 404 |

**关键洞察**:同一个 cookie 在不同层的待遇可能完全不同。本项目里 `inspire-session`
cookie 对 `/api/v2` 一直有效,但对页面级的 `/api/v1/notebook/lab/{id}/` 路由会 401——
因为后者要求一个**新鲜的、keycloak 会话仍然存活的** cookie。一个能正常拉列表的"有效"
cookie,在页面路由上可能照样被拒。debug 鉴权问题时,先问:**这是哪一层?它认哪个凭证?**

识别"标准组件"能极大加速:看到 `_xsrf` cookie + `?token=` + `/api/kernels` + `/terminals/`
就知道后端是 **Jupyter Server**,直接套 Jupyter 的鉴权规则(token 走 query 或
`Authorization: token <t>` header,且 token-header 鉴权会跳过 XSRF 检查)。

---

## 2. 工具链

### 2.1 浏览器自动化 + 抓包:jshook MCP + 真实 Chrome

- 用 [jshook MCP](https://github.com/vmoranv/jshookmcp) 驱动一个**真实** Chrome(不是
  headless-only 的玩具):它能启动浏览器、注入 cookie、导航、点 DOM、读 localStorage,
  并提供 `network_enable` / `ws_monitor` 抓 HTTP 请求和 **WebSocket 帧**。
- **要装系统级真浏览器**。Ubuntu 上 `snap` 版 chromium 受 AppArmor 沙箱限制,对自动化
  很不友好;直接装 Google Chrome 官方 `.deb` 最稳。
- 关键能力清单:`browser_launch` / `page_navigate` / `page_evaluate`(在页面里跑任意
  JS)/ `page_cookies`(读 CDP cookie,含 httpOnly)/ `network_get_requests` +
  `network_get_response_body` / `ws_get_connections` + `ws_get_frames`。

### 2.2 命令行复现:curl + 小脚本

抓到流程后,**一定要脱离浏览器、用 curl / 一段 python 复现**。这是把"浏览器里能用"
变成"CLI 里能用"的必经一步,也是判定「到底哪个凭证在起作用」的决定性手段(见下)。

### 2.3 登录:驱动真实表单,别复刻加密

平台登录页常对密码做客户端加密(RSA/AES 后塞进隐藏字段)。**不要去逆向那段加密 JS**——
直接在真实浏览器里填可见的用户名/密码框、点登录按钮,让页面自己的 JS 完成加密提交。
驱动真实浏览器的全部意义就在于"白嫖"前端已经写好的逻辑。

---

## 3. 决定性实验(Decisive Experiment)

逆向最浪费时间的是"以为知道、其实在猜"。对每个关键未知,设计一个**能一锤定音**的最小实验:

- **到底哪个凭证在鉴权?** —— 用 curl 分别只带 cookie、只带 `?token=`、只带
  `Authorization` header 各打一次,看谁返回 200。本项目正是这样确认:Jupyter 网关只认
  token(cookie 完全不需要),`nb exec` 因此根本不必管那堆 path-scoped cookie。
- **这个值是每次新生成还是持久的?** —— 重开一次、或重载页面,看它变不变。
- **这个接口需要这个字段吗?** —— 删掉再打一次。

一次决定性实验胜过十次"看起来对"的猜测。

---

## 4. 定位"那个值从哪来"——顺藤摸瓜

逆向里最常见的卡点:你看到一个请求带着某个神秘参数(token / 签名 / 会话 id),但不知道
它从哪生成。排查顺序:

1. **它在某个响应体里吗?** 把所有相关响应全文搜这个值(别只搜 url 字段名,token 字段名
   可能叫 `session`、`ticket`、什么都不带)。
2. **在前端存储里吗?** 搜 `localStorage` / `sessionStorage`。
3. **在 DOM 里吗?** 比如 `iframe.src`、`data-*` 属性、内联 script。**这是本案的突破口**:
   神秘 token 既不在任何 API 响应、也不在 storage,最后发现 IDE 页面的 `<iframe src>`
   指向一个 qz 自己的端点 `/api/v1/notebook/lab/{id}/`,该端点服务端铸 token 并 302
   重定向到带 token 的网关地址。**值的来源是一次服务端重定向,不是一个 JSON 字段。**
4. **是网关握手时铸的吗?** 跟踪首次访问该服务时的重定向链(`curl -L -D -`),看哪一跳的
   `Location` 头里第一次出现这个值。

技巧:**跟重定向但停在目标那一跳**。要拿"最终带 token 的 URL",可以
`allow_redirects=False` 手动走每一跳,发现某个 `Location` 指向目标网关(含特征路径如
`/jupyter/`)就停下、直接返回它,不必真的去 GET 它(避免触发不必要的副作用 / 代理问题)。

---

## 5. 网络与代理

- 内网/教育网平台往往**直连和走代理都能到**,但下游服务网关(那台只在内网可达的机器)
  可能只有直连能到。给 HTTP 客户端配代理时要分清:哪些 host 走代理、哪些必须直连。
- 本项目:qz API 走代理 OK,但 notebook 网关 `nat2-*` 用的是**不带代理**的独立 session
  和直连 WebSocket。`websocket-client` 默认不读环境代理变量,直连即可。
- 抓包时如果 jshook 自带的"服务端 fetch"(如 bundle 下载)拿不到需要登录的资源,改用
  **页面内 fetch**(`page_evaluate` 里 `fetch(...)`)—— 它自动带上浏览器的会话。

---

## 6. 常见坑

- **cookie 新鲜度**:能拉列表 ≠ 能过页面路由。页面级路由可能要求 SSO 会话仍存活。CLI 侧
  对策:遇 401 自动用保存的凭证重登录一次再重试。
- **PTY 回显污染输出**:通过终端跑命令时,你发的命令会被 PTY **回显**回来,混在输出里。
  用唯一标记(START/END marker)把真正的 stdout 夹起来;判完成时**别匹配到回显里的标记**
  (回显里的 `$?` 还没展开成数字,真实输出里才是数字——用这点区分)。详见案例文档。
- **子进程/shell 关闭**:命令里若执行 `exit`,会话 shell 直接死,WebSocket 关闭。读循环要
  捕获"连接关闭"异常并优雅收尾,而不是抛栈。
- **字段名误导**:proxy / session / token 这些词可能出现在无关字段(比如 SSH 的
  `ProxyJump`),全文搜时别被同名字段带偏。
- **平台发版漂移**:逆向 API 没有兼容性承诺。把"怎么抓出来的"记下来(就是这份文档的意义),
  挂了能照流程重抓。

---

## 7. 一句话总结

> 驱动真实浏览器手动跑一遍 → 抓全请求和 WS 帧 → 识别鉴权分层、认出标准组件 →
> 用 curl 做决定性实验确认凭证 → 顺藤摸瓜定位神秘值的来源 → 脱离浏览器纯命令行复现 →
> 落地成代码,并把易变点和坑记入文档。
