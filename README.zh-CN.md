# FC27 Copilot

[English](README.md) | 简体中文

面向 EA SPORTS FC 27 Ultimate Team 的本地自部署 Model Context Protocol（MCP）服务器。FC27 Copilot 将已登录的 Web App 会话与本地球员目录、俱乐部库存、SBC 规划器及用户确认后的账户操作连接起来。

Agent 负责策略选择和方案比较。MCP 提供事实、状态、计算、校验及明确的操作接口。用户通过官方 EA Web App 自行登录，原始会话凭据仅保留在页面内存中。

项目以源码形式提供，面向本机单用户部署。当前组件版本为 `0.6.0`，尚未发布正式 tag 或浏览器商店安装包。变更见[更新记录](CHANGELOG.md)，测试和问题报告方式见[贡献指南](CONTRIBUTING.md)。

## 功能

- **球员与库存查询** — 查询卡片定义，识别俱乐部、SBC 仓库、未分配物品和转会列表中的具体球员副本。
- **俱乐部自动同步** — 登录或成功操作后更新库存；库存未变化时保留原状态版本。
- **SBC 规划** — 优先生成已有球员方案，比较需要购买一张或多张卡片的备选方案，支持指定必选球员，并按照挑战实际开放的槽位求解。
- **任务与进化查询** — 获取通行证奖励、任务要求及账户进度，覆盖 FC Objectives、Foundations、Milestones、Mastery、Seasonal、FC Pro 和 Evolutions。
- **球队管理** — 读取阵型、球员槽位、战术及 Web App 支持的配置选项，并执行明确批准的变更。
- **市场分析** — 结合 FUT.GG 参考价格、EA 实时挂牌样本、本地观察记录、购入成本和税后计算。
- **受控执行** — 在本地策略范围内购买、竞价、上架、移动、重新上架、清理已售物品、保存或提交 SBC，以及修改球队和战术。

## 快速开始

### 环境要求

- Python 3.10 或更新版本。
- Node.js 18 或更新版本。
- 启用浏览器扩展的 Chrome 或 Edge。
- 支持 stdio 的 MCP 客户端。

仓库已附带可直接使用的球员目录。首次安装无需下载目录，离线查询球员也不需要 EA 登录。

当前真实账户验收覆盖 macOS 和 Chromium 浏览器。以下命令使用 POSIX Shell；Windows 对应命令见[安装指南](docs/install.zh-CN.md)。

### 安装并启动本地服务

克隆仓库，然后创建 Python 环境：

```bash
git clone https://github.com/liusihang/fc27-copilot.git
cd fc27-copilot
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

校验随仓库提供的目录，并准备扩展：

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
npm run check
npm run build:extension
```

启动守护进程，并保持其运行：

```bash
.venv/bin/python fc27d.py
```

默认地址为 `http://127.0.0.1:3926`。通过 `/health` 检查服务和目录状态。macOS 常驻服务安装方式见[OpenClaw 与服务说明](docs/openclaw.md)。

### 连接浏览器和 MCP 客户端

1. 打开 `chrome://extensions` 或 `edge://extensions`，启用开发者模式，将项目的 `dist` 目录作为解压缩扩展加载。
2. 打开[官方 Ultimate Team Web App](https://www.ea.com/ea-sports-fc/ultimate-team/web-app/)并登录。扩展会自动连接和同步。
3. 在 MCP 客户端中注册 stdio 适配器。采用 `mcpServers` 配置格式的客户端可参考以下示例，将两处占位路径替换为实际项目路径：

```json
{
  "mcpServers": {
    "FC27": {
      "command": "/absolute/path/fc27-copilot/.venv/bin/python",
      "args": ["/absolute/path/fc27-copilot/mcp_stdio.py"]
    }
  }
}
```

守护进程必须保持运行，stdio 适配器不会自行启动它。客户端支持超时设置时，将工具超时设为至少 **240 秒**，并保留 `execute_actions` 的批准提示。首次连接先调用 `status` 和 `catalog_query`，登录后通过 `club_query` 查看同步结果。

无需填写扩展 ID 或复制 EA token。扩展只提供一个可选设置，用于修改本机服务地址。内部 `/mcp` 入口不作为通用 Streamable HTTP 传输接口提供。

Windows 命令、更新、故障排查和卸载方式见[安装指南](docs/install.zh-CN.md)；OpenClaw CLI 注册方式见[专用说明](docs/openclaw.md)。

升级时先停止服务并保留本地策略及目录差异，再更新代码、Python 依赖，执行检查和 `npm run build:extension`。随后启动服务、重载解压缩扩展、刷新 Web App，并重新连接 MCP 客户端。只重载旧 `dist` 不会更新扩展文件。具体命令和冲突处理见[升级步骤](docs/install.zh-CN.md#更新)。

## 球员目录与更新

仓库附带的 `data/catalog.sqlite` 是 **2026 年 9 月 18 日**完成的 FUT.GG 快照，包含 **19,676 张卡片**和 **19,595 名球员**，使用目录 schema v3，大小约 **18.4 MiB**。它保存卡片定义及映射，不含账号库存、凭据或市场价格历史。`data/catalog-manifest.json` 记录该快照的数量、元数据和校验值。

这份快照不是实时数据。`catalog_query` 只查询本地目录；登录和 `sync_club` 更新的是账号库存，不会更新目录。需要获取新卡片时，在项目目录中执行维护。访问前应确认数据提供方允许此类使用。

1. 替换数据库前先停止守护进程。前台运行时按 `Ctrl+C`；macOS 常驻服务使用[数据说明中的停止和启动命令](data/README.md#build-or-refresh-the-catalog)。
2. 保留备份，从 FUT.GG 重建目录，并校验新快照：

```bash
cp data/catalog.sqlite data/catalog.sqlite.backup
.venv/bin/python scripts/refresh_catalog.py --out data/catalog.sqlite
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite \
  --write-manifest data/catalog-manifest.json
```

3. 确认校验结果为 `"ok": true`，再按原启动方式重启服务。前台启动命令为：

```bash
.venv/bin/python fc27d.py
```

更新脚本下载临时源数据，将其转换为标准目录，并在转换器的完整性、映射和数量检查通过后替换数据库。最终校验会输出快照时间、数量及校验值。此过程不修改 `data/accounts/`，也不需要 EA 登录。维护失败时先检查错误，再决定是否启动服务；新目录通过校验前保留备份。

可在需要新卡片时更新，也可自行每几天维护一次。项目不会安装定时更新任务。Windows 命令、可选代理、源数据库导入及恢复方式见[数据说明](data/README.md)。该文件现已纳入 Git；本地刷新后，后续代码更新可能发生文件冲突，更新代码前应备份目录和 manifest。

## MCP 工具

服务器名称为 `FC27`，共提供 12 个工具。不同客户端添加的工具名称前缀可能不同。

| 工具 | 用途 |
| --- | --- |
| `status` | 检查服务、会话、同步、策略及请求退避状态。 |
| `catalog_query` | 查询和比较本地球员卡定义。 |
| `club_query` | 读取最近一次完整同步的本地库存镜像。 |
| `squad_query` | 读取实时球队槽位、阵型、战术及支持的配置选项。 |
| `sync_club` | 将金币和库存状态刷新到本地账户数据库。 |
| `market_search` | 获取 EA 实时挂牌信息及具体交易 ID。 |
| `price_context` | 刷新参考价格，比较历史观察、库存、成本和净收入。 |
| `content_query` | 获取通行证、任务及进化内容和进度。 |
| `sbc_query` | 读取缓存中的 SBC 集合、挑战、要求和状态。 |
| `sbc_refresh` | 从已登录的 Web App 刷新当前 SBC 状态。 |
| `sbc_solve` | 生成经过校验的已有球员方案及购买预算方案。 |
| `execute_actions` | 执行用户批准的明确账户操作批次。 |

完整参数、返回值及错误定义见[MCP 契约](docs/mcp-contract.md)。

## 执行策略

默认 `suggest` 策略允许已实现的账户操作，但**每个新执行批次之前，Agent 都必须询问用户并获得明确批准**。询问前应展示具体目标、球员副本、价格上限或战术变更，以及不可逆效果。

- 要求完成 SBC 只授权查询和规划，不代表批准购买或提交。
- 保存和提交 SBC 分别确认；只保存的请求不能消耗球员。
- 目标、额度或因状态过期而重建的批次需要重新批准。
- 查询、本地价格记录、自动同步和规划不需要账户写入批准。
- 写入结果未知时，需要核验或协调原动作，不能自动重试。

`policy.json` 是执行授权依据，Agent 不能覆盖其限制。默认每批一个动作，单次购买、批次花费和每日花费上限均为 700 金币。用户可以调整本地额度，也可以选择 `observe` 禁止所有账户写操作。完整规则见[执行策略](docs/execution-policy.md)。

所有启用的执行模式，包括 `auto`，均要求新写操作携带 `confirmed=true`。该值是调用方对批准的声明，不能独立证明人类同意。工具描述和 Server instructions 不能保证模型行为，应使用会展示账户操作并请求批准的客户端。

## 数据与使用限制

- **球员目录：** `data/catalog.sqlite` 是随仓库提供的卡片定义快照。查询不刷新目录，目录维护由部署者单独执行。
- **账户状态：** `data/accounts/<persona_id>/runtime.sqlite` 保存对应 Persona 的库存、价格观察、SBC 方案和操作历史。
- **私有数据：** 账号数据库、原始输入包、会话凭据、日志及 SQLite 辅助文件不随代码分发；只附带球员目录。分享诊断资料前应先脱敏。
- **求解范围：** SBC 自动候选排除保护球员、租借卡、特殊卡、进化卡及当前活动球队阵容中的球员。未知条件会阻止求解，不保证支持所有 SBC。
- **结果解释：** 最优性仅对结果注明的候选域或局部邻域成立。参考价格不保证可买到、卖出或获利。较高购买预算可能超过请求超时，应逐级比较。
- **部署范围：** 本项目是本地单用户系统，不是公网多用户服务。不要将本机端口公开到互联网。

## 开发检查

```bash
npm run check
.venv/bin/python -m unittest discover -s tests
```

`check` 检查 Python 语法及浏览器扩展。自动测试使用本地 fixture，不执行真实 EA 账户操作；来源数据库缺失时，相应测试可能跳过。真实安装验收需要用户自行登录 Web App。

CI 在干净副本上运行离线测试、目录校验和扩展检查及构建，不验证真实 EA 行为，也不能证明旧 Git 历史不含个人信息。手动只读检查和公开内容边界见[贡献指南](CONTRIBUTING.md)。

## 文档

- [安装指南](docs/install.zh-CN.md)
- [OpenClaw 集成与 macOS 常驻服务](docs/openclaw.md)
- [MCP 契约](docs/mcp-contract.md)
- [执行策略](docs/execution-policy.md)
- [SBC 规划与执行](docs/sbc-contract.md)
- [系统架构](docs/architecture.md)
- [数据库设计](docs/database-schema.md)
- [贡献与测试](CONTRIBUTING.md)
- [更新记录](CHANGELOG.md)

## 许可与第三方服务

项目原创代码和文档采用[MIT 许可](LICENSE)。第三方代码、数据、素材、商标及服务访问仍受各自的许可和条款约束。

本项目为非官方工具，与 Electronic Arts 或 FUT.GG 没有隶属或认可关系。自动访问可能违反适用的服务规则或导致账户限制。项目不绕过验证码、验证流程、限流、转会限制或访问控制。使用前请阅读[NOTICE](NOTICE.md)及相关服务条款。
