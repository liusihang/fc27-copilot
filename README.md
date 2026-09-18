# FC27 Copilot

本项目把 FC27 Web App 会话桥接、FUT.GG 球员目录与价格数据、本地俱乐部镜像和 OpenClaw MCP 整合为一个本地系统。

当前状态：项目初始化。实施按 GitHub Milestone 和 Issue 推进；在首次需要真实 EA 账户登录时停止，由用户登录后继续只读验收。

## 设计边界

- OpenClaw Agent 读取事实、比较候选并作出策略判断。
- MCP 提供查询、确定性计算、状态同步、校验和明确操作接口。
- `fc27d` 是运行时数据库的唯一写入者。
- Chrome 扩展只捕获当前 Web App 会话并调用 EA 页面能力。
- 原始 SID 和 phishing token 只保留在页面内存中。
- 系统初始运行模式为 `observe`，不执行账户写操作。

## 数据库

- `catalog.sqlite`：可由 FUT.GG 重建的全量目录。
- `data/accounts/<persona_id>/runtime.sqlite`：每个 EA Persona 独立的俱乐部、价格、SBC 和操作历史。

## 文档

- [需求](requirements.md)
- [实施计划](plan.md)
- [领域术语](CONTEXT.md)
- [架构](docs/architecture.md)
- [来源清单](docs/source-artifacts.md)
- [交接记录](handoff.md)

## 项目管理

GitHub Milestone 对应六个实施阶段。每项实现、验证和真实账户验收均通过 Issue 跟踪。未经只读验收，不启用写操作。

## 启动本地守护进程

先按 [数据目录说明](data/README.md) 构建 `data/catalog.sqlite`，然后运行：

```bash
python3 fc27d.py
```

默认监听 `http://127.0.0.1:3926`。可用端点：

- `GET /health`：守护进程、Catalog 和浏览器桥接状态；
- `POST /rpc`：本地内部 RPC；
- `GET /browser/poll`、`POST /browser/respond`：Chrome 桥接长轮询；
- `GET /`：浏览器桥接页面。

守护进程只绑定回环地址。通过 `FC27D_HOST` 和 `FC27D_PORT` 可以显式修改监听地址和端口。

## 声明

这是一个非官方的个人研究和本地工具项目，与 Electronic Arts、FUT.GG 或相关第三方没有隶属或认可关系。EA Web App 接口未公开支持，接口和服务规则可能变化。项目不提供验证码、验证流程、限流或访问控制绕过能力。
