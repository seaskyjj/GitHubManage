# GitHub Stars Manager

[中文](./README.md) | [English](./README_EN.md)

一个用 `uv` 管理的本地 Web 项目，支持：

1. 手动触发同步当前登录 GitHub 用户的 Stars。
2. 基于项目简介自动生成中文短描述，并做项目类别判定。
3. 管理自定义 Stars List（从 GitHub GraphQL 同步现有 List，新建/删除/项目分配都会写回 GitHub）。
4. 手动添加（并在 GitHub 上加星）指定仓库并更新本地数据。
5. 抓取 [GitHub Trending](https://github.com/trending) 并生成流行项目概述（5xx 时自动回退 GitHub Search）。

## 运行方式

```bash
uv sync
./start.sh
```

也可以用等价命令：

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 17001
```

打开浏览器访问：

- `http://127.0.0.1:17001`

## 登录说明

页面使用 GitHub Personal Access Token 登录。建议创建一个最小权限 Token：

- 读取 Stars：`read:user`
- 写入 Star（手动加星功能）：`public_repo`（公开仓库）或对应私有仓库权限
- 写入 GitHub Stars Lists（新建 List / 分配项目）：建议使用 classic PAT（`ghp_`）或 OAuth token；
  fine-grained PAT（`github_pat_`）可能返回 `Resource not accessible by personal access token`

Token 仅保存在本地会话 Cookie（用于本地开发）。生产环境请改为 OAuth + 服务端加密存储。

## 清理历史本地 List

如果你之前有“仅本地存在、无 GitHub 远端 ID”的旧数据，可用以下方式清理：

- UI：进入「2) Stars List 管理」点击 `清理本地遗留 Lists`
- 命令行（SQLite）：

```bash
sqlite3 data/app.db "DELETE FROM custom_lists WHERE remote_id IS NULL OR remote_id = '';"
```

## 项目结构

```text
app/
  analyzer.py      # 分类与描述生成逻辑
  db.py            # SQLite 持久化
  github.py        # GitHub API / Trending 抓取
  main.py          # FastAPI 路由
  static/style.css # 页面样式
  templates/
    login.html
    dashboard.html
data/
  app.db           # 运行后自动创建
```
