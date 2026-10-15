# QQ 邮箱读取器

面向 Codex 的本地只读 QQ 邮箱插件。它通过 TLS 连接 `imap.qq.com:993`，只访问 `INBOX`，提供列出、搜索和读取邮件内容的 MCP 工具。Agent 可按用户要求总结或提取邮件中的信息；网购交易和 tracking 更新只是使用示例，不是插件内置的专用解析功能。

## 安全边界

- 只读：不发信、删除、移动、归档或标记已读。读取正文使用 `BODY.PEEK`。
- 使用 QQ 邮箱授权码，不使用 QQ 登录密码。交互式配置会把授权码存入 macOS 钥匙串；本地配置文件仅保存邮箱地址。
- 不保存或返回附件内容，也不打开邮件里的链接或加载远程资源。正文最多返回 50,000 个字符；超过 10 MiB 的邮件拒绝读取正文。
- 邮件内容可能包含诱导 Agent 执行操作的文字，应始终作为不可信数据处理。

## 准备与配置

需要 macOS、Python 3.12 或更新版本及 `uv`。在 QQ 邮箱设置中开启 IMAP 服务并生成授权码，然后从插件目录运行：

```bash
uv sync --all-groups
uv run python scripts/configure_credentials.py
```

脚本在本机终端隐藏输入授权码。不要把授权码贴进 Codex 对话、`.env` 或 Git 文件。若使用环境变量进行开发或测试，必须同时提供 `QQ_MAIL_ADDRESS` 与 `QQ_MAIL_AUTH_CODE`；这一对值会优先于本地钥匙串配置，使用后应从环境中移除。

开发者可在同一目录运行：

```bash
uv run pytest
uv run ruff check .
```

## 安装

从独立仓库安装插件。在本机克隆仓库后，可从任意目录运行：

```bash
codex plugin marketplace add /absolute/path/to/qq-mail-reader
codex plugin add qq-mail-reader@qq-mail-community
```

也可用 `codex plugin marketplace add oceanjustinlin/qq-mail-reader` 注册 GitHub 仓库，然后运行同一条 `codex plugin add` 命令。每位使用者都需在自己的电脑上配置邮箱地址与 IMAP 授权码。

安装后打开新的 Codex 任务，让新工具与 skill 加载。未配置凭据时可先要求“检查 QQ 邮箱读取器是否已经配置，不要连接邮箱”；这只调用状态工具，不访问邮箱。

## 工具与使用

| 工具 | 用途 |
| --- | --- |
| `qq_mail_status(check_connection=false)` | 检查本地配置；显式设为 `true` 时测试 IMAP 连接。 |
| `qq_mail_list(limit=20)` | 列出 `INBOX` 最近邮件摘要，最多 100 封。 |
| `qq_mail_search(since_days=7, sender=None, subject=None, unread=None, limit=20)` | 在最近 1–30 天按发件人、主题、已读状态筛选邮件摘要，最多 100 封。 |
| `qq_mail_read(uid)` | 读取一封邮件的头、正文和附件元数据，不改变已读状态。 |

例如可以问：“找出最近 7 天某个发件人的邮件，并总结要点”，或“查看近几天的网购交易和有没有新的 tracking info”。Agent 应先查看摘要，再读取与请求有关的邮件，并按发件人、主题、日期和 UID 标明依据。`qq_mail_search` 在日期与已读条件内只检查最新 500 个 UID 候选，再按发件人和主题过滤；即使结果少于 `limit`，较早的匹配邮件也可能被遗漏。列表和搜索都不能当作完整的邮箱盘点。

## 限制与排查

- 只检查 `INBOX`，不扫描其他文件夹，也不持续监控或持久化索引。
- 插件不查询邮件里提到的外部网站。仅凭邮件内的运单号或链接，不能断言承运商的实时运输状态。
- 未配置时请重新运行本机配置脚本；认证失败时检查邮箱地址、IMAP 是否启用，以及使用的是否为授权码。网络或 TLS 失败时先检查连接，稍后再试。不要在问题报告里附上授权码或完整邮件内容。
