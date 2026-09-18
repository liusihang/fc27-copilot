# FC27 Copilot

本项目把 FC27 Web App 会话桥接、FUT.GG 球员目录与价格数据、本地俱乐部镜像和 OpenClaw MCP 整合为一个本地系统。

当前状态：M1–M5 已完成目录、俱乐部镜像、登录后只读验收、市场信息能力和受控执行。真实账户低价值验收已通过；系统运行在需要逐批明确确认的 `suggest` 模式，`auto` 模式仍关闭。

## 设计边界

- OpenClaw Agent 读取事实、比较候选并作出策略判断。
- MCP 提供查询、确定性计算、状态同步、校验和明确操作接口。
- `fc27d` 是运行时数据库的唯一写入者。
- Chrome 扩展只捕获当前 Web App 会话并调用 EA 页面能力。
- 原始 SID 和 phishing token 只保留在页面内存中。
- `policy.json` 是唯一执行授权面；当前 `suggest` 模式要求每批操作携带 `confirmed=true`。

## 数据库

- `catalog.sqlite`：可由 FUT.GG 重建的全量目录。
- `data/accounts/<persona_id>/runtime.sqlite`：每个 EA Persona 独立的俱乐部、价格、SBC 和操作历史。

## 文档

- [需求](requirements.md)
- [实施计划](plan.md)
- [领域术语](CONTEXT.md)
- [架构](docs/architecture.md)
- [OpenClaw 集成](docs/openclaw.md)
- [执行策略](docs/execution-policy.md)
- [M5 真实账户验收](docs/live-execution-acceptance-2026-09-18.md)
- [来源清单](docs/source-artifacts.md)
- [交接记录](handoff.md)

## 项目管理

GitHub Milestone 对应六个实施阶段。每项实现、验证和真实账户验收均通过 Issue 跟踪。账户操作受执行模式、金额、库存容量、状态版本和幂等键共同约束。

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

守护进程和 Chrome 桥接默认使用固定地址 `127.0.0.1:3926`。

## MCP stdio

`mcp_stdio.py` is the MCP process launched by an MCP client. It forwards newline-delimited JSON-RPC to the running daemon:

```bash
python3 mcp_stdio.py
```

The MCP server name is `FC27` and exposes exactly ten tools documented in [MCP contract](docs/mcp-contract.md). Start `fc27d.py` before launching the stdio adapter.

macOS 常驻服务和 OpenClaw 注册步骤见 [OpenClaw 集成](docs/openclaw.md)。

## 声明

这是一个非官方的个人研究和本地工具项目，与 Electronic Arts、FUT.GG 或相关第三方没有隶属或认可关系。EA Web App 接口未公开支持，接口和服务规则可能变化。项目不提供验证码、验证流程、限流或访问控制绕过能力。
