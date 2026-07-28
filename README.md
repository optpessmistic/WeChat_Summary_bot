# 微信脉络

微信脉络是一个只监听本机的微信聊天 AI 总结工具。它通过
[WeChatDataAnalysis](https://github.com/LifeArchiveProject/WeChatDataAnalysis)
导出聊天 JSON，也支持直接上传其 ZIP/JSON 导出文件。

## 能做什么

- 总结与某个人的单聊；
- 总结指定群聊；
- 分析某位成员在群内的主要观点、贡献、承诺与互动结果；
- 从长聊天中提炼主题、结论、决定、待办、未决问题和时间线；
- 所有事实性结论链接到本地原消息证据；
- 使用 OpenAI 兼容云端 API，或本地 Ollama；
- 云端发送前默认将成员名和 wxid 假名化；
- 报告可导出 Markdown 和 JSON。

## Windows 启动

前置条件：

1. 安装 Python 3.11 或更高版本；
2. 安装 [uv](https://docs.astral.sh/uv/)；
3. 如需自动导出，安装并启动 WeChatDataAnalysis。

在资源管理器中右键 `start.ps1`，选择“使用 PowerShell 运行”。脚本会初始化依赖和
SQLite 数据库，然后打开 `http://127.0.0.1:10420`。

首次打开后，请在“连接设置”中编辑默认的“OpenAI 兼容云端”配置，填写服务商的
Base URL、模型名与 API Key。页面同时预置了 `Ollama 本地` 配置，可按需切换。
开发环境也可通过 `WECHAT_SUMMARY_API_KEY` 或 `OPENAI_API_KEY` 提供密钥。

如果 PowerShell 阻止本地脚本，可在当前终端运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

## 开发

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn wechat_summary_bot.main:app --host 127.0.0.1 --port 10420 --reload
```

运行测试和代码检查：

```bash
uv run pytest
uv run ruff check .
```

## 数据与隐私

- 服务固定监听 `127.0.0.1`；
- WeChatDataAnalysis 地址只允许 `localhost` 或回环 IP；
- API Key 优先保存在 Windows Credential Manager，不写入 SQLite 或日志；
- 上传和下载的临时 ZIP/JSON 在导入完成后删除；
- 规范化聊天、任务和报告保存在 `%LOCALAPPDATA%\WeChatSummaryBot`；
- 删除本地会话时会级联删除相关消息、分析任务和报告；
- 也可只删除某份报告，不影响本地聊天资料；
- 选择云端模型时，正文仍会发送给相应服务商，请根据内容敏感程度选择模型。

## 上游兼容性

当前按 WeChatDataAnalysis 1.18.5 的以下接口进行能力探测：

- `GET /api/chat/accounts`
- `GET /api/chat/exports/targets`
- `POST /api/chat/exports`
- `GET /api/chat/exports/{id}`
- `GET /api/chat/exports/{id}/download`

如果上游未启动或接口不兼容，网页会保留 ZIP/JSON 手动上传入口。
