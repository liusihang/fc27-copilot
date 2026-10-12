<p align="center">
  <img src="docs/assets/hero.svg" alt="FC27 Copilot — Know your club. Plan your next move." width="100%">
</p>

# FC27 Copilot

[English](README.md) | **简体中文**

## 看清你的俱乐部，再决定下一步。

**让 AI 帮你盘点球员、规划 SBC、比较市场价格。** FC27 Copilot 将你的 Ultimate Team 俱乐部连接到支持 MCP 的 AI 客户端，让建议有真实库存和明确约束作依据。

[**开始安装 →**](docs/install.zh-CN.md) · [先看看怎么用](#你可以这样问) · [接入 OpenClaw](docs/openclaw.md) · [反馈问题](https://github.com/liusihang/fc27-copilot/issues)

**本地自部署 · Chrome / Edge · MCP stdio · MIT 开源**

> 当前为源码安装（组件版本 `0.6.0`），需要本地服务、浏览器扩展和 MCP 客户端。尚无浏览器商店安装包或正式 Release。非官方项目；自动访问可能违反服务规则或导致账户限制，使用前请阅读 [NOTICE](NOTICE.md)。

## 先从这三件事开始

| 你想解决的问题 | FC27 Copilot 能帮你做什么 |
| --- | --- |
| **这个 SBC，能用手里的卡完成吗？** | 优先计算已有球员方案，再按需比较需要补购的方案；支持指定必选球员和评分上限。 |
| **我的卡都在哪？哪些还能用？** | 查询俱乐部、SBC 仓库、未分配物品和转会列表，区分具体持有副本。 |
| **这张卡现在买卖，价格合适吗？** | 对照 FUT.GG 参考价、EA 实时挂牌样本、自己的购入成本与税后计算。 |

还可以查询通行证、任务和进化进度，读取当前阵型与战术，并在逐批批准后执行支持的账户操作。

## 你可以这样问

接入后，在 MCP 客户端中试试这些提问：

- **先体验离线查询：**「在本地目录里找速度至少 90、评分 85–90 的 ST，列出 PlayStyle+。」
- **盘点俱乐部：**「看看俱乐部和 SBC 仓库里的不可交易球员，按评分整理。」
- **规划 SBC：**「这个 SBC 先只用已有球员求解，不要用超过 82 评分的卡。先给我方案，不要保存或提交。」
- **比较补购：**「再比较需要补买一张卡和两张卡的方案，列出缺卡和估算成本，先不要购买。」
- **了解进度：**「列出当前可领取的任务，以及正在进行的进化。」
- **看清卖出成本：**「比较这张卡的参考价、实时挂牌和购入成本，算出指定售价下的税后净收入。」

**AI 负责理解需求与比较方案；Copilot 提供库存、计算、校验和操作接口。** SBC 规划器本身不会购买、保存或提交。

## 快速开始

需要 **Python 3.10+、Node.js 18+、Chrome 或 Edge，以及支持 stdio 的 MCP 客户端**。目前真实账户验收覆盖 macOS 与 Chromium 浏览器；Windows 命令见[中文安装指南](docs/install.zh-CN.md)。

### 1. 准备本地服务

以下命令适用于 macOS / Linux 的 POSIX Shell。扩展没有 npm 依赖，无需 `npm install`。

```bash
git clone https://github.com/liusihang/fc27-copilot.git
cd fc27-copilot
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
npm run check
npm run build:extension
.venv/bin/python fc27d.py
```

保持服务运行，默认地址为 `http://127.0.0.1:3926`，可打开 `/health` 检查状态。仓库附带球员目录，**离线查卡无需 EA 登录或首次下载目录**。

### 2. 加载浏览器扩展

打开 `chrome://extensions` 或 `edge://extensions` → 开启开发者模式 → 加载已解压的扩展 → 选择项目的 **`dist`** 目录。

随后打开[官方 Ultimate Team Web App](https://www.ea.com/ea-sports-fc/ultimate-team/web-app/)并自行登录，扩展会自动连接和同步。无需复制 EA token 或填写扩展 ID。

### 3. 接入 AI 客户端

支持 `mcpServers` 配置格式的客户端可使用以下配置，替换两处绝对路径：

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

先调用 `status` 和 `catalog_query`；登录后用 `club_query` 检查库存。**stdio 适配器不会自动启动服务**。客户端支持时，将工具超时设为至少 **240 秒**，并保留 `execute_actions` 的批准提示。

[完整安装与故障排查](docs/install.zh-CN.md) · [OpenClaw CLI 与 macOS 常驻服务](docs/openclaw.md)

## 执行前，由你逐批确认

查询和规划可先完成。购买、上架、球队修改和 SBC 提交等操作，需要展示具体目标、额度和不可逆影响，并获得你的逐批批准。**SBC 保存与提交分别批准；提交会消耗球员。**

默认每批最多一个动作，单次购买、批次及每日花费上限均为 700 金币。想先观察，可将 `policy.json` 的模式设为 `observe`，禁止所有账户写操作。请保留客户端的批准提示。

[阅读完整执行策略](docs/execution-policy.md)

## 使用前值得了解

- **目录是快照。** 内置 FUT.GG 目录完成于 **2026-09-18**，含 **19,676 张卡片 / 19,595 名球员**，不是实时卡库。登录同步的是账户库存；新卡需按[数据指南](data/README.md)另行更新目录。
- **SBC 有支持边界。** 自动候选排除受保护、租借、特殊、进化及当前活动球队中的球员；不支持的挑战条件会阻止求解。最优性仅针对结果注明的候选域或局部邻域。
- **价格是决策参考。** 不保证买得到、卖得出或获利；较大的补购搜索可能超时，建议逐级比较。
- **在自己电脑上运行。** 本项目面向本地单用户，不应将服务端口暴露到公网。账户数据库与操作历史保存在本地；原始 EA 会话凭据保留在 Web App 页面内存中。AI 客户端仍可能将工具结果发送给其模型服务，请检查客户端的数据处理方式。
- **升级需要重建扩展。** 先备份本地策略和目录、停止服务，再按[更新步骤](docs/install.zh-CN.md#更新)升级。只重载旧 `dist` 不会更新扩展。

<details>
<summary>进阶：执行策略、MCP 工具与开发检查</summary>

## 执行策略细节

查询和规划可先完成；购买、上架、球队修改和 SBC 提交等账户操作，应在展示具体目标、球员、额度与不可逆影响后，获得你的明确批准。

- **SBC 保存与提交分别批准。** 提交会消耗球员；「帮我做 SBC」不等于授权提交。
- **额度在本地设置。** 默认 `suggest` 模式，每批最多一个动作；单次购买、批次及每日花费上限均为 700 金币。
- **可以只观察。** 将 `policy.json` 的模式设为 `observe` 可禁止所有账户写操作。
- **确认仍需客户端配合。** 后端要求 `confirmed=true`，但这个值本身不能证明人类同意。请使用会展示并请求批准的客户端。
- **结果未知时先核验。** 不自动重复可能已执行的写入；目标或额度变化后重新确认。

[阅读完整执行策略](docs/execution-policy.md)



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

## 开发检查

```bash
npm run check
.venv/bin/python -m unittest discover -s tests
```

`check` 检查 Python 语法及浏览器扩展。自动测试使用本地 fixture，不执行真实 EA 账户操作；来源数据库缺失时，相应测试可能跳过。真实安装验收需要用户自行登录 Web App。

CI 在干净副本上运行离线测试、目录校验和扩展检查及构建，不验证真实 EA 行为，也不能证明旧 Git 历史不含个人信息。手动只读检查和公开内容边界见[贡献指南](CONTRIBUTING.md)。

</details>

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

