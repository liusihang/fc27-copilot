# 安装与维护

[English](install.md) | 简体中文

FC27 Copilot 部署在运行 EA Web App 浏览器的同一台电脑上。需要 Python 3.10+、Node.js 18+、Chrome 或 Edge，以及支持 stdio 的 MCP 客户端。真实账户检查覆盖 macOS 和 Chromium 浏览器；离线 CI 不代表其他平台已通过真实 EA 验证。

项目以源码形式安装，不是 npm 安装包、浏览器商店扩展或托管 MCP 服务。以下命令均在项目目录中执行。

## 1. 安装依赖

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

扩展没有 npm 第三方依赖，无需执行 `npm install`。

## 2. 校验附带的目录

仓库附带 `data/catalog.sqlite`，是 2026 年 9 月 18 日完成的 FUT.GG 快照，包含 19,676 张卡片和 19,595 名球员。首次安装无需下载目录或登录 EA。

```bash
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Windows 将 `.venv/bin/python` 替换为 `.\.venv\Scripts\python.exe`。需要新卡片时，按[目录维护说明](../data/README.md#build-or-refresh-the-catalog)更新。目录维护和账号同步是两种不同操作。

## 3. 构建扩展并启动服务

```bash
npm run check
npm run build:extension
```

Windows 若没有 `python3` 命令，改用以下检查，再执行同一构建命令：

```powershell
.\.venv\Scripts\python.exe -m compileall -q fc27 tests scripts fc27d.py mcp_stdio.py
npm run validate:extension
npm run build:extension
```

启动守护进程，并保持运行：

```bash
.venv/bin/python fc27d.py
```

默认地址为 `http://127.0.0.1:3926`，通过该地址下的 `/health` 检查就绪状态。stdio 适配器不会自行启动服务。

macOS 可选安装常驻服务：

```bash
.venv/bin/python scripts/install_macos_service.py
```

同一端口只能运行前台进程或常驻服务之一。服务和可选代理设置见[OpenClaw 说明](openclaw.md)。

打开 `chrome://extensions` 或 `edge://extensions`，启用开发者模式，将项目 `dist` 目录作为解压缩扩展加载。打开官方 Ultimate Team Web App 并自行登录，扩展会自动连接和同步。不需要填写扩展 ID、复制 EA token 或打开桥接页面。只有使用其他本机端口时才需要修改扩展的服务地址。

## 4. 注册 stdio MCP

采用 `mcpServers` 格式的客户端可以使用以下示例。替换两处占位绝对路径：

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

Windows 使用 `.venv\Scripts\python.exe` 的绝对路径。JSON 中的反斜线需要写两次，也可以使用正斜线。其他客户端的外层配置格式可能不同，但 command 和 args 相同。

客户端支持时将工具超时设为至少 **240 秒**。stdio 适配器请求超时为 240 秒，已有球员求解窗口为 180 秒。较高购买预算可能耗时更久，应逐级比较。保留 `execute_actions` 的人工批准提示。

重新连接 MCP 后应发现 12 个工具。先调用 `status` 和 `catalog_query`，登录后检查 `club_query`。库存陈旧或同步报错时，再显式调用 `sync_club`。OpenClaw CLI 命令见[集成说明](openclaw.md)。

## 5. 确认账户写操作

默认 `suggest` 策略在本地额度内允许已实现的写动作。每个新批次前，Agent 必须展示具体目标、价格上限或不可逆效果，询问用户并得到明确批准，才能声明 `confirmed=true`。

查询、同步和求解不代表批准购买、保存或提交。SBC 保存和提交分别确认。客户端未展示 Server instructions 时，将[确认规则](execution-policy.md#user-confirmation)加入 Agent 指令。Agent 不能为了绕过拒绝而自行修改 `policy.json`。

## 更新

1. 先完成或协调正在执行的账户动作，再停止服务。前台进程按 `Ctrl+C`；已安装的 macOS 常驻服务使用：

```bash
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

2. 检查 `git status --short`，在仓库外保留本地策略、自行刷新的目录与 manifest，以及账号数据的私有备份。`policy.json`、`data/catalog.sqlite` 和 `data/catalog-manifest.json` 均纳入 Git，本地修改可能与上游冲突。Git 拒绝更新时，先保留并处理这些差异，不使用破坏性 reset 强行更新。
3. 更新代码、依赖和扩展产物。每一步成功后再执行下一步：

```bash
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
npm run check
npm run build:extension
.venv/bin/python scripts/validate_catalog.py data/catalog.sqlite
```

Windows 使用上面的解释器及检查命令。代码更新使用所选的仓库快照或本地目录；需要新卡片时另行执行目录更新。

4. 按原方式启动服务。前台运行 `.venv/bin/python fc27d.py`；恢复 macOS 常驻服务时使用以下命令，不改其原设置：

```bash
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/io.github.liusihang.fc27d.plist"
```

5. 在浏览器扩展页重载解压缩扩展，再刷新 Web App，必要时自行登录。重新连接或重载客户端 MCP，检查 `status` 和 `club_query`。

**没有执行 `npm run build:extension`，只重载旧 `dist`，不会安装新的扩展代码。** 构建只替换 `dist`，不修改账号数据库。

## 故障排查与卸载

- `DAEMON_UNAVAILABLE`：检查服务、地址和代理路由。使用代理时将 `127.0.0.1,localhost` 加入 `NO_PROXY`。
- `EA_SESSION_REQUIRED` 或桥接断开：打开 Web App、自行登录或重载扩展；验证流程由用户处理。
- 结果不完整：检查 `meta.complete`、警告、观察时间和分页信息。空页面不代表没有任务。
- 写入结果未知：先核验或协调原动作，不创建新批次重试。超时不证明 EA 没有执行。
- 卸载时从客户端移除 `FC27`，停止服务，再移除扩展。按需要保留账号数据库，卸载不要求删除数据。

不要将本机端口公开到互联网。代码许可、第三方数据权限和账号规则是不同问题，见[NOTICE](../NOTICE.md)。
