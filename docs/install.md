# 通用 MCP 安装指南

适用于把 FC27 Copilot 自行部署在运行 EA Web App 浏览器的电脑上。需要 Python 3.10 或更新版本、Node.js 18 或更新版本，以及支持 stdio MCP 的客户端。真实使用已在 macOS 和 Chromium 浏览器验证；Windows、Linux 还需要各自环境验收。

## 1. 获取代码并安装依赖

在自行选择的目录克隆或解压项目。私有仓库需要相应访问权限；本指南不意味着仓库已经公开。

```bash
git clone https://github.com/liusihang/fc27-copilot.git
cd fc27-copilot
```

macOS / Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

浏览器扩展没有 npm 第三方依赖，不需要先执行 `npm install`。

## 2. 准备本地目录数据库

仓库已附带 `data/catalog.sqlite`，首次安装无需从网络构建。它是 2026 年 9 月 18 日的 FUT.GG 快照，包含 19,676 张卡片和 19,595 名球员。先校验随仓库提供的数据库：

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Windows 把 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。需要新卡片时，按照[数据说明](../data/README.md#build-or-refresh-the-catalog)停止服务、备份目录、从 FUT.GG 重建并校验，再启动服务。该过程不要求 EA 登录，也不更新账号数据库。源数据库导入和可选代理设置也见该说明。第三方数据不因随仓库提供而获得 MIT 授权。

## 3. 启动服务并安装扩展

在一个保持运行的终端中启动：

```bash
.venv/bin/python fc27d.py
```

默认地址为 `http://127.0.0.1:3926`。浏览器打开该地址下的 `/health` 可检查目录和服务状态。服务关闭时，MCP 客户端不能执行查询；stdio 适配器不会自行启动守护进程。

在另一个终端、项目目录中准备扩展：

```bash
npm run check
npm run build:extension
```

Windows 若没有 `python3` 命令，检查时使用 `.\.venv\Scripts\python.exe -m compileall -q fc27 tests scripts fc27d.py mcp_stdio.py` 和 `npm run validate:extension`，再运行 `npm run build:extension`。

Chrome 打开 `chrome://extensions`，Edge 打开 `edge://extensions`，启用开发者模式并选择“加载解压缩的扩展”，加载本项目的 `dist` 目录。

打开官方 FC Ultimate Team Web App 并自行登录。扩展会自动连接和同步；无需填扩展 ID、复制 EA token 或打开额外桥接页。更改默认本机端口时，才需要在扩展弹窗修改服务地址。

macOS 可选安装登录后自动运行的服务，不需要同时保留手动启动进程：

```bash
.venv/bin/python scripts/install_macos_service.py
```

它使用实际项目目录和当前 Python 路径，不需要修改脚本中的用户名或路径。停止服务的方法见[OpenClaw 与 macOS 服务说明](openclaw.md)。

## 4. 在 MCP 客户端注册 stdio

下面是采用 `mcpServers` 配置格式的客户端示例。替换两处占位绝对路径；不要直接复制占位值。其他客户端使用相同的 command/args，但配置入口和外层字段可能不同。

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

Windows 的 `command` 为项目 `.venv\Scripts\python.exe` 的绝对路径，`args` 为 `mcp_stdio.py` 的绝对路径。JSON 中的反斜线需要写成 `\\`；也可以使用正斜线。

客户端若支持工具超时设置，至少设置为 **240 秒**，不要使用比 SBC 的 180 秒求解窗口更短的超时。当前 stdio 适配器的单次请求超时是 240 秒；高购买预算的多级规划可能更久，建议逐级比较。保持客户端对 `execute_actions` 的人工批准提示，不要全局设置为自动批准。

重新加载客户端的 MCP 配置后应发现 12 个工具。首先运行 `status`，再运行 `catalog_query`。登录后用 `club_query` 检查自动同步结果；仅在结果陈旧或状态报错时显式运行 `sync_club`。OpenClaw 命令见[专用示例](openclaw.md)。

## 5. 写操作使用约定

默认 `suggest` 策略允许已经实现的写动作，但每个新批次都需要 Agent 展示具体目标、金币上限或不可逆效果，询问用户并得到明确批准后，才能设置 `confirmed=true`。

查询、同步和求解不代表批准购买、保存或提交。SBC 保存与提交分别确认。若客户端不展示 Server instructions，应把[执行策略中的确认约定](execution-policy.md#user-confirmation)加入该客户端的 Agent 指令，并保留写工具批准提示。

额度不足时，Agent 应说明哪个额度阻止操作；不能自行修改 `policy.json`。用户可以在自己的部署中调整额度，也可以设为 `observe` 禁止所有账户写操作。

## 更新、故障与卸载

- 更新代码前保留本地策略差异，并备份自行刷新的 `data/catalog.sqlite` 和 `data/catalog-manifest.json`；这两个文件已纳入 Git，本地修改可能与上游快照冲突。更新后重新运行检查，重启服务，并在浏览器扩展页重新加载 `dist`。不要覆盖账户数据库。
- `DAEMON_UNAVAILABLE`：检查服务是否运行、地址是否一致及代理是否错误转发了本机请求。使用代理时将 `127.0.0.1,localhost` 加入 `NO_PROXY`。
- `EA_SESSION_REQUIRED` 或桥接断开：打开 Web App、自行登录或重新加载扩展；验证码由用户手动处理。
- 结果不完整：先看 `meta.complete`、来源警告、记录观察时间和分页信息，不把空列表直接理解为“没有任务”。
- 写操作超时或状态未知：先读取当前状态或协调原动作，不创建新批次重试。超时不证明 EA 没有完成操作。
- 卸载时先从 MCP 客户端移除 `FC27`，停止守护进程或常驻服务，再移除扩展。账户数据库可按用户需要保留；不提供自动删除账户数据的卸载步骤。

这是本机自部署工具，不应直接向公网暴露服务端口。代码许可、EA 账户规则和 FUT.GG 数据授权是不同问题，见[NOTICE](../NOTICE.md)。
