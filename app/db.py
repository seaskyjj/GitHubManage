from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "app.db"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS starred_repos (
                owner_login TEXT NOT NULL,
                repo_id INTEGER NOT NULL,
                repo_node_id TEXT,
                full_name TEXT NOT NULL,
                name TEXT NOT NULL,
                owner_name TEXT NOT NULL,
                html_url TEXT NOT NULL,
                description TEXT,
                language TEXT,
                topics_json TEXT NOT NULL,
                category TEXT NOT NULL,
                blurb TEXT NOT NULL,
                stargazers_count INTEGER NOT NULL DEFAULT 0,
                synced_at TEXT NOT NULL,
                PRIMARY KEY (owner_login, repo_id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_star_owner_full_name
                ON starred_repos(owner_login, full_name);

            CREATE TABLE IF NOT EXISTS custom_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_login TEXT NOT NULL,
                remote_id TEXT,
                slug TEXT,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(owner_login, name)
            );

            CREATE TABLE IF NOT EXISTS list_items (
                list_id INTEGER NOT NULL,
                owner_login TEXT NOT NULL,
                repo_full_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (list_id, repo_full_name),
                FOREIGN KEY (list_id) REFERENCES custom_lists(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS trending_repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rank_no INTEGER NOT NULL,
                full_name TEXT NOT NULL,
                html_url TEXT NOT NULL,
                description TEXT,
                language TEXT,
                stars_today INTEGER NOT NULL DEFAULT 0,
                stars_total INTEGER NOT NULL DEFAULT 0,
                category TEXT NOT NULL,
                blurb TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        _ensure_column(conn, "starred_repos", "repo_node_id", "TEXT")
        _ensure_column(conn, "custom_lists", "remote_id", "TEXT")
        _ensure_column(conn, "custom_lists", "slug", "TEXT")
        conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_custom_list_owner_remote_id
            ON custom_lists(owner_login, remote_id)
            WHERE remote_id IS NOT NULL AND remote_id <> ''
            """
        )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_starred_repo(owner_login: str, repo: dict) -> None:
    now = utc_now()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO starred_repos (
                owner_login, repo_id, repo_node_id, full_name, name, owner_name, html_url,
                description, language, topics_json, category, blurb, stargazers_count, synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(owner_login, repo_id) DO UPDATE SET
                repo_node_id=excluded.repo_node_id,
                full_name=excluded.full_name,
                name=excluded.name,
                owner_name=excluded.owner_name,
                html_url=excluded.html_url,
                description=excluded.description,
                language=excluded.language,
                topics_json=excluded.topics_json,
                category=excluded.category,
                blurb=excluded.blurb,
                stargazers_count=excluded.stargazers_count,
                synced_at=excluded.synced_at
            """,
            (
                owner_login,
                repo["repo_id"],
                repo.get("repo_node_id"),
                repo["full_name"],
                repo["name"],
                repo["owner_name"],
                repo["html_url"],
                repo.get("description"),
                repo.get("language"),
                json.dumps(repo.get("topics", []), ensure_ascii=False),
                repo["category"],
                repo["blurb"],
                repo.get("stargazers_count", 0),
                now,
            ),
        )


def sync_starred_repos(owner_login: str, repos: list[dict]) -> int:
    now = utc_now()
    with get_conn() as conn:
        conn.execute("DELETE FROM starred_repos WHERE owner_login = ?", (owner_login,))
        for repo in repos:
            conn.execute(
                """
                INSERT INTO starred_repos (
                    owner_login, repo_id, repo_node_id, full_name, name, owner_name, html_url,
                    description, language, topics_json, category, blurb, stargazers_count, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner_login, repo_id) DO UPDATE SET
                    repo_node_id=excluded.repo_node_id,
                    full_name=excluded.full_name,
                    name=excluded.name,
                    owner_name=excluded.owner_name,
                    html_url=excluded.html_url,
                    description=excluded.description,
                    language=excluded.language,
                    topics_json=excluded.topics_json,
                    category=excluded.category,
                    blurb=excluded.blurb,
                    stargazers_count=excluded.stargazers_count,
                    synced_at=excluded.synced_at
                """,
                (
                    owner_login,
                    repo["repo_id"],
                    repo.get("repo_node_id"),
                    repo["full_name"],
                    repo["name"],
                    repo["owner_name"],
                    repo["html_url"],
                    repo.get("description"),
                    repo.get("language"),
                    json.dumps(repo.get("topics", []), ensure_ascii=False),
                    repo["category"],
                    repo["blurb"],
                    repo.get("stargazers_count", 0),
                    now,
                ),
            )

    set_meta(f"stars_synced_at:{owner_login}", now)
    return len(repos)


def _repo_sort_key(repo: dict, sort_by: str) -> tuple:
    if sort_by == "full_name":
        return (repo["full_name"].lower(),)
    if sort_by == "category":
        return (repo["category"].lower(), repo["full_name"].lower())
    if sort_by == "language":
        return ((repo.get("language") or "").lower(), repo["full_name"].lower())
    if sort_by == "list":
        if repo["lists"]:
            return (0, repo["lists"][0].lower(), repo["full_name"].lower())
        return (1, "", repo["full_name"].lower())
    return (repo["synced_at"], repo["full_name"].lower())


def get_repo_lists_map(owner_login: str) -> dict[str, list[str]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.repo_full_name, l.name
            FROM list_items i
            INNER JOIN custom_lists l
              ON l.id = i.list_id AND l.owner_login = i.owner_login
            WHERE i.owner_login = ?
            ORDER BY lower(l.name) ASC
            """,
            (owner_login,),
        ).fetchall()

    mapping: dict[str, list[str]] = {}
    for row in rows:
        full_name = row["repo_full_name"]
        mapping.setdefault(full_name, []).append(row["name"])
    return mapping


def get_starred_repos(
    owner_login: str,
    limit: int = 200,
    sort_by: str = "synced_at",
    sort_order: str = "desc",
) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                repo_id, full_name, name, owner_name, html_url, description,
                language, topics_json, category, blurb, stargazers_count, synced_at
            FROM starred_repos
            WHERE owner_login = ?
            ORDER BY lower(full_name) ASC
            LIMIT ?
            """,
            (owner_login, limit),
        ).fetchall()

    repo_lists_map = get_repo_lists_map(owner_login)
    repos = []
    for row in rows:
        item = dict(row)
        item["topics"] = json.loads(item.pop("topics_json") or "[]")
        lists = repo_lists_map.get(item["full_name"], [])
        item["lists"] = lists
        item["lists_label"] = "、".join(lists) if lists else "-"
        repos.append(item)

    valid_sort_by = {"synced_at", "full_name", "category", "language", "list"}
    if sort_by not in valid_sort_by:
        sort_by = "synced_at"
    reverse = sort_order == "desc"
    repos.sort(key=lambda repo: _repo_sort_key(repo, sort_by), reverse=reverse)
    return repos


def create_custom_list(owner_login: str, name: str) -> tuple[bool, str]:
    if not name.strip():
        return False, "List 名称不能为空。"
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO custom_lists(owner_login, name, created_at) VALUES (?, ?, ?)",
                (owner_login, name.strip(), utc_now()),
            )
        except sqlite3.IntegrityError:
            return False, "List 名称已存在。"
    return True, "List 创建成功。"


def delete_custom_list(owner_login: str, list_id: int) -> bool:
    with get_conn() as conn:
        result = conn.execute(
            "DELETE FROM custom_lists WHERE id = ? AND owner_login = ?",
            (list_id, owner_login),
        )
        return result.rowcount > 0


def list_custom_lists(owner_login: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                l.id,
                l.name,
                l.remote_id,
                l.slug,
                l.created_at,
                COUNT(i.repo_full_name) AS item_count
            FROM custom_lists l
            LEFT JOIN list_items i
              ON i.list_id = l.id AND i.owner_login = l.owner_login
            WHERE l.owner_login = ?
            GROUP BY l.id
            ORDER BY l.created_at ASC
            """,
            (owner_login,),
        ).fetchall()
    return [dict(row) for row in rows]


def count_local_only_lists(owner_login: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM custom_lists
            WHERE owner_login = ?
              AND (remote_id IS NULL OR remote_id = '')
            """,
            (owner_login,),
        ).fetchone()
    return int(row["total"]) if row else 0


def clear_local_only_lists(owner_login: str) -> int:
    with get_conn() as conn:
        result = conn.execute(
            """
            DELETE FROM custom_lists
            WHERE owner_login = ?
              AND (remote_id IS NULL OR remote_id = '')
            """,
            (owner_login,),
        )
    return int(result.rowcount)


def sync_remote_lists(owner_login: str, remote_lists: list[dict]) -> tuple[int, int]:
    now = utc_now()
    synced_lists = 0
    synced_items = 0
    remote_ids: set[str] = set()

    with get_conn() as conn:
        for item in remote_lists:
            name = (item.get("name") or "").strip()
            remote_id = (item.get("remote_id") or "").strip()
            slug = (item.get("slug") or "").strip()
            if not name:
                continue
            repos = item.get("repos") or []

            row = None
            if remote_id:
                remote_ids.add(remote_id)
                row = conn.execute(
                    "SELECT id FROM custom_lists WHERE owner_login = ? AND remote_id = ?",
                    (owner_login, remote_id),
                ).fetchone()

            if not row:
                row = conn.execute(
                    """
                    SELECT id
                    FROM custom_lists
                    WHERE owner_login = ? AND name = ? AND (remote_id IS NULL OR remote_id = '')
                    """,
                    (owner_login, name),
                ).fetchone()

            if row:
                conn.execute(
                    """
                    UPDATE custom_lists
                    SET name = ?, remote_id = ?, slug = ?
                    WHERE id = ? AND owner_login = ?
                    """,
                    (name, remote_id or None, slug or None, row["id"], owner_login),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO custom_lists(owner_login, remote_id, slug, name, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (owner_login, remote_id or None, slug or None, name, now),
                )
                row = conn.execute(
                    "SELECT id FROM custom_lists WHERE owner_login = ? AND name = ?",
                    (owner_login, name),
                ).fetchone()

            if not row:
                continue

            list_id = row["id"]
            conn.execute(
                "DELETE FROM list_items WHERE owner_login = ? AND list_id = ?",
                (owner_login, list_id),
            )

            deduped = []
            seen = set()
            for repo_full_name in repos:
                candidate = (repo_full_name or "").strip()
                if not candidate or candidate in seen:
                    continue
                seen.add(candidate)
                deduped.append(candidate)

            for repo_full_name in deduped:
                conn.execute(
                    """
                    INSERT INTO list_items(list_id, owner_login, repo_full_name, added_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (list_id, owner_login, repo_full_name, now),
                )
                synced_items += 1

            synced_lists += 1

        if remote_ids:
            placeholders = ",".join("?" for _ in remote_ids)
            conn.execute(
                f"""
                DELETE FROM custom_lists
                WHERE owner_login = ?
                  AND remote_id IS NOT NULL
                  AND remote_id <> ''
                  AND remote_id NOT IN ({placeholders})
                """,
                (owner_login, *sorted(remote_ids)),
            )
        else:
            conn.execute(
                """
                DELETE FROM custom_lists
                WHERE owner_login = ?
                  AND remote_id IS NOT NULL
                  AND remote_id <> ''
                """,
                (owner_login,),
            )

    set_meta(f"lists_synced_at:{owner_login}", now)
    return synced_lists, synced_items


def get_custom_list(owner_login: str, list_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, owner_login, remote_id, slug, name, created_at
            FROM custom_lists
            WHERE owner_login = ? AND id = ?
            """,
            (owner_login, list_id),
        ).fetchone()
    return dict(row) if row else None


def get_repo_node_id(owner_login: str, repo_full_name: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT repo_node_id
            FROM starred_repos
            WHERE owner_login = ? AND full_name = ?
            """,
            (owner_login, repo_full_name),
        ).fetchone()
    return row["repo_node_id"] if row and row["repo_node_id"] else None


def add_repo_to_list(owner_login: str, list_id: int, repo_full_name: str) -> tuple[bool, str]:
    repo_full_name = repo_full_name.strip()
    if not repo_full_name:
        return False, "仓库名不能为空。"

    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM custom_lists WHERE id = ? AND owner_login = ?",
            (list_id, owner_login),
        ).fetchone()
        if not row:
            return False, "List 不存在。"

        try:
            conn.execute(
                """
                INSERT INTO list_items(list_id, owner_login, repo_full_name, added_at)
                VALUES (?, ?, ?, ?)
                """,
                (list_id, owner_login, repo_full_name, utc_now()),
            )
        except sqlite3.IntegrityError:
            return False, "该仓库已在 List 中。"
    return True, "已添加到 List。"


def add_repos_to_list(
    owner_login: str, list_id: int, repo_full_names: list[str]
) -> tuple[int, int, str]:
    cleaned = []
    seen = set()
    for name in repo_full_names:
        candidate = name.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)

    if not cleaned:
        return 0, 0, "请先选择项目。"

    added = 0
    skipped = 0
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM custom_lists WHERE id = ? AND owner_login = ?",
            (list_id, owner_login),
        ).fetchone()
        if not row:
            return 0, 0, "List 不存在。"

        for repo_full_name in cleaned:
            try:
                conn.execute(
                    """
                    INSERT INTO list_items(list_id, owner_login, repo_full_name, added_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (list_id, owner_login, repo_full_name, utc_now()),
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1

    return added, skipped, row["name"]


def remove_repo_from_list(owner_login: str, list_id: int, repo_full_name: str) -> bool:
    with get_conn() as conn:
        result = conn.execute(
            """
            DELETE FROM list_items
            WHERE owner_login = ? AND list_id = ? AND repo_full_name = ?
            """,
            (owner_login, list_id, repo_full_name),
        )
        return result.rowcount > 0


def get_list_items(owner_login: str, list_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT i.repo_full_name, i.added_at, s.html_url
            FROM list_items i
            LEFT JOIN starred_repos s
              ON s.owner_login = i.owner_login AND s.full_name = i.repo_full_name
            WHERE i.owner_login = ? AND i.list_id = ?
            ORDER BY i.added_at DESC
            """,
            (owner_login, list_id),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_trending(repos: list[dict], summary: str) -> int:
    now = utc_now()
    with get_conn() as conn:
        conn.execute("DELETE FROM trending_repos")
        for repo in repos:
            conn.execute(
                """
                INSERT INTO trending_repos (
                    rank_no, full_name, html_url, description, language,
                    stars_today, stars_total, category, blurb, synced_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repo["rank_no"],
                    repo["full_name"],
                    repo["html_url"],
                    repo.get("description"),
                    repo.get("language"),
                    repo.get("stars_today", 0),
                    repo.get("stars_total", 0),
                    repo["category"],
                    repo["blurb"],
                    now,
                ),
            )

    set_meta("trending_summary", summary)
    set_meta("trending_synced_at", now)
    return len(repos)


def get_trending_repos(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT rank_no, full_name, html_url, description, language,
                   stars_today, stars_total, category, blurb, synced_at
            FROM trending_repos
            ORDER BY rank_no ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def set_meta(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO app_meta(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def get_meta(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
