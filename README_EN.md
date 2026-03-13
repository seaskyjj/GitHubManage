# GitHub Stars Manager

[中文](./README.md) | [English](./README_EN.md)

A local web project managed with `uv`, supporting:

1. Manually sync the logged-in user's GitHub Stars.
2. Generate short Chinese blurbs from repository descriptions and classify project categories.
3. Manage custom Stars Lists (sync existing lists from GitHub GraphQL; create/delete/assign items and write back to GitHub).
4. Manually add a starred repository (also stars it on GitHub) and update local data.
5. Fetch [GitHub Trending](https://github.com/trending) and generate a popularity summary (automatically falls back to GitHub Search on 5xx errors).

## Run

```bash
uv sync
./start.sh
```

Equivalent command:

```bash
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 17001
```

Open in browser:

- `http://127.0.0.1:17001`

## Login

The app uses a GitHub Personal Access Token for login. Recommended minimum token permissions:

- Read Stars: `read:user`
- Write Star (manual star feature): `public_repo` (public repos) or matching private-repo permissions
- Write GitHub Stars Lists (create list / assign items): classic PAT (`ghp_`) or OAuth token is recommended  
  fine-grained PAT (`github_pat_`) may return `Resource not accessible by personal access token`

The token is stored only in local session cookies (for local development). For production, use OAuth and encrypted server-side storage.

## Clean Legacy Local Lists

If you have old local-only list data without GitHub remote IDs, you can clean it up by:

- UI: go to `Stars List 管理` and click `清理本地遗留 Lists`
- Command line (SQLite):

```bash
sqlite3 data/app.db "DELETE FROM custom_lists WHERE remote_id IS NULL OR remote_id = '';"
```

## Project Structure

```text
app/
  analyzer.py      # Category inference and blurb generation
  db.py            # SQLite persistence
  github.py        # GitHub API / Trending fetch
  main.py          # FastAPI routes
  static/style.css # UI styles
  templates/
    login.html
    dashboard.html
data/
  app.db           # Created automatically at runtime
```

