# QQ Mail Reader

QQ 邮箱只读插件：通过 IMAP 访问收件箱，提供邮件列表、最近 1–30 天搜索及单封邮件读取。不会发信、删除、移动邮件或标记已读。

## 安装

需要 macOS、Python 3.12+、`uv` 和支持插件的 Codex。克隆此仓库后运行：

```bash
codex plugin marketplace add /absolute/path/to/qq-mail-reader
codex plugin add qq-mail-reader@qq-mail-community
```

若仓库已发布到 GitHub，也可用 `codex plugin marketplace add OWNER/REPO` 注册远程 marketplace。

每位使用者都必须在自己的 QQ 邮箱中启用 IMAP、生成授权码，并在本机运行：

```bash
cd plugins/qq-mail-reader
uv sync --all-groups
uv run python scripts/configure_credentials.py
```

授权码保存在 macOS 钥匙串中，不应上传到 Git 或发给他人。详细功能、限制与排查方法见[插件说明](plugins/qq-mail-reader/README.md)。

## 开发

```bash
cd plugins/qq-mail-reader
uv run pytest
uv run ruff check .
```
