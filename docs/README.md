# qzcli 文档

qzcli 是构建在**逆向网页 API** 之上的启智平台 CLI——没有官方 SDK / OpenAPI，
所有能力都来自抓包还原平台前端真实发出的请求。这类实现的代价是**不稳定**:平台
前端一次发版就可能让某个端点、字段或鉴权流程失效。

这个目录记录的是「怎么逆向出来的」，不是「API 长什么样」(那些在代码 `endpoints.py`
和注释里)。三个目的:

1. **维护** —— 接口挂了能照着同样的流程重新抓、定位、修复。
2. **迁移** —— 同类平台(JupyterHub / code-server / 各类算力平台的网页控制台)大多是
   相似的架构,这里的套路可以直接套用。
3. **学习** —— 一份「黑盒 Web 系统逆向」的完整实战记录。

## 目录

| 文档 | 内容 |
|---|---|
| [reverse-engineering-playbook.md](reverse-engineering-playbook.md) | **通用方法论**:抓包优先、决定性实验、鉴权分层模型、工具链、常见坑。看这一篇就能上手逆向任意类似平台。 |
| [case-notebook-exec.md](case-notebook-exec.md) | **案例深挖**:如何从「在交互式建模实例里执行命令」这个需求,一路挖到「IDE 其实是 JupyterLab,走终端 WebSocket」并落地 `nb exec`。含完整架构、鉴权链路、终端协议、维护/debug 指南。 |

## 约定

- 文中所有 id / token / 用户名 / cookie 均为占位符(`{notebook_id}`、`{token}`、`<user>` …)。
  真实值因实例/会话而异,且不应出现在公开文档里。
- 平台主机名(`qz.sii.edu.cn`、`cas.sii.edu.cn`、`nat2-notebook-inspire.sii.edu.cn` 等)
  是平台公开端点,保留以便对照。
