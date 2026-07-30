# 安全政策

## 支持范围

本项目目前只维护 `main` 分支的最新提交和最新发布版本。旧版本可能不会获得安全修复；
报告问题前请先确认最新版本中是否仍可复现。

当前应用的安全边界是单用户、本机运行，只监听回环地址。公开本仓库的源代码不等于允许
把运行实例直接暴露到公网。未经额外的身份认证、用户与数据隔离、CSRF 防护、限流、密钥
管理和持久任务队列改造，公网部署不在当前支持范围内。

## 私下报告漏洞

请不要为尚未修复的安全漏洞创建公开 Issue、Discussion 或 Pull Request，也不要公开
披露可利用细节。请通过本仓库的 GitHub Security Advisory 私下提交：

[私下报告安全漏洞](https://github.com/optpessmistic/WeChat_Summary_bot/security/advisories/new)

在仓库页面也可以进入 **Security → Advisories → Report a vulnerability**。如果没有看到
该入口，说明仓库尚未启用 GitHub Private Vulnerability Reporting；此时请勿将漏洞细节
转发到公开 Issue。可以只创建一条不含漏洞详情的 Issue，提醒维护者启用私密报告入口。

报告中建议包含：

- 受影响的版本、提交哈希、操作系统和 Python 版本；
- 影响范围以及所需的攻击前提；
- 已去除隐私信息的最小复现步骤或概念验证；
- 建议的缓解措施（如有）；
- 是否已经向其他项目或服务商报告。

维护者会在同一条私密 Advisory 中确认、讨论修复并协调披露。此项目暂不承诺固定响应
时限或安全修复 SLA。

## 请勿提交敏感数据

无论通过私密 Advisory 还是公开 Issue，都不要提交真实的：

- 微信聊天原文、联系人信息、群成员信息或媒体文件；
- WeChatDataAnalysis 导出的 ZIP、`messages.json` 或其他解密数据；
- SQLite 数据库、完整日志或系统凭据库内容；
- API Key、访问令牌、Cookie、微信数据库密钥或其他凭据。

请使用合成数据复现问题，并在提交前删除本机用户名、路径、wxid、群名、服务地址和密钥。
如果某项秘密已经泄露，请先在相应服务商处撤销或轮换；仅从 Git 历史或报告正文中删除并
不能使它重新安全。

## 报告边界

- 本仓库自身的密钥处理、上传校验、权限边界、提示词注入防护或数据泄露问题，请按上述
  流程报告。
- WeChatDataAnalysis 自身的问题应优先报告给其维护者；若漏洞由本项目的集成方式触发，
  请同时说明本项目受到的影响。
- 模型总结不准确、遗漏或产生幻觉通常属于质量问题，可以在不包含真实聊天内容的前提下
  创建普通 Issue。

## 维护者发布前设置

仓库维护者应在公开仓库前进入 **Settings → Security → Code security and analysis**，启用
**Private vulnerability reporting**，并验证上方私密报告链接可用。建议同时启用依赖更新、
secret scanning 和 push protection。
