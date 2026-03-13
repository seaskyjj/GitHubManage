from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import analyzer, db, github


APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

app = FastAPI(title="GitHub Stars Manager")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("APP_SECRET", "dev-secret-change-me"),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.on_event("startup")
def startup_event() -> None:
    db.init_db()


def _auth(request: Request) -> dict[str, str] | None:
    token = request.session.get("github_token")
    login = request.session.get("github_login")
    if not token or not login:
        return None
    return {
        "token": token,
        "login": login,
        "name": request.session.get("github_name", login),
    }


def _flash(request: Request, message: str, kind: str = "info") -> None:
    request.session["flash_message"] = message
    request.session["flash_kind"] = kind


def _pop_flash(request: Request) -> dict[str, str] | None:
    message = request.session.pop("flash_message", None)
    if not message:
        return None
    return {
        "message": message,
        "kind": request.session.pop("flash_kind", "info"),
    }


def _redirect_home() -> RedirectResponse:
    return RedirectResponse(url="/", status_code=303)


def _repo_payload(raw: dict[str, Any]) -> dict[str, Any]:
    topics = raw.get("topics") or []
    category = analyzer.infer_category(
        repo_name=raw.get("full_name", ""),
        description=raw.get("description"),
        topics=topics,
        language=raw.get("language"),
    )
    return {
        "repo_id": raw["id"],
        "repo_node_id": raw.get("node_id"),
        "full_name": raw["full_name"],
        "name": raw.get("name", raw["full_name"].split("/")[-1]),
        "owner_name": (raw.get("owner") or {}).get("login", ""),
        "html_url": raw.get("html_url", f"https://github.com/{raw['full_name']}"),
        "description": raw.get("description") or "",
        "language": raw.get("language") or "",
        "topics": topics,
        "category": category,
        "blurb": analyzer.build_repo_blurb(raw["full_name"], raw.get("description"), category),
        "stargazers_count": raw.get("stargazers_count", 0),
    }


def _normalize_full_name(value: str) -> str:
    candidate = value.strip().strip("/")
    parts = [part for part in candidate.split("/") if part]
    if len(parts) != 2:
        return ""
    return f"{parts[0]}/{parts[1]}"


def _collect_repo_remote_memberships(remote_lists: list[dict[str, Any]]) -> dict[str, set[str]]:
    memberships: dict[str, set[str]] = {}
    for item in remote_lists:
        remote_id = (item.get("remote_id") or "").strip()
        if not remote_id:
            continue
        for full_name in item.get("repos") or []:
            memberships.setdefault(full_name, set()).add(remote_id)
    return memberships


def _sync_remote_lists_from_github(auth: dict[str, str]) -> tuple[int, int, list[dict[str, Any]]]:
    viewer_login, remote_lists = github.fetch_viewer_star_lists(auth["token"])
    if viewer_login.lower() != auth["login"].lower():
        raise github.GitHubAPIError(
            f"当前 Token 对应用户为 {viewer_login}，与登录会话 {auth['login']} 不一致。"
        )
    synced_lists, synced_items = db.sync_remote_lists(auth["login"], remote_lists)
    return synced_lists, synced_items, remote_lists


def _local_only_list_tip() -> str:
    return "检测到历史本地 List（无远端 ID）。请点击“清理本地遗留 Lists”后再操作，或先执行“从 GitHub 同步 Lists”。"


def _trending_payload(raw: dict[str, Any]) -> dict[str, Any]:
    category = analyzer.infer_category(
        repo_name=raw.get("full_name", ""),
        description=raw.get("description"),
        topics=[],
        language=raw.get("language"),
    )
    return {
        "rank_no": raw["rank_no"],
        "full_name": raw["full_name"],
        "html_url": raw["html_url"],
        "description": raw.get("description") or "",
        "language": raw.get("language") or "",
        "stars_today": raw.get("stars_today", 0),
        "stars_total": raw.get("stars_total", 0),
        "category": category,
        "blurb": analyzer.build_repo_blurb(raw["full_name"], raw.get("description"), category),
    }


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    flash = _pop_flash(request)
    auth = _auth(request)
    if not auth:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "flash": flash},
        )

    owner_login = auth["login"]
    sort_by = request.query_params.get("sort_by", "synced_at")
    if sort_by not in {"synced_at", "full_name", "category", "list", "language"}:
        sort_by = "synced_at"
    sort_order = request.query_params.get("sort_order")
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc" if sort_by == "synced_at" else "asc"

    repos = db.get_starred_repos(
        owner_login,
        limit=500,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    custom_lists = db.list_custom_lists(owner_login)
    list_items: dict[int, list[dict[str, Any]]] = {}
    for item in custom_lists:
        list_items[item["id"]] = db.get_list_items(owner_login, item["id"])

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "flash": flash,
            "auth": auth,
            "repos": repos,
            "lists": custom_lists,
            "list_items": list_items,
            "local_only_list_count": db.count_local_only_lists(owner_login),
            "stars_synced_at": db.get_meta(f"stars_synced_at:{owner_login}"),
            "lists_synced_at": db.get_meta(f"lists_synced_at:{owner_login}"),
            "trending": db.get_trending_repos(limit=30),
            "trending_summary": db.get_meta("trending_summary"),
            "trending_synced_at": db.get_meta("trending_synced_at"),
            "sort_by": sort_by,
            "sort_order": sort_order,
        },
    )


@app.post("/login")
def login(request: Request, token: str = Form(...)) -> RedirectResponse:
    token = token.strip()
    if not token:
        _flash(request, "Token 不能为空。", "error")
        return _redirect_home()

    try:
        user = github.get_authenticated_user(token)
    except github.GitHubAPIError as exc:
        _flash(request, f"登录失败：{exc}", "error")
        return _redirect_home()

    request.session["github_token"] = token
    request.session["github_login"] = user["login"]
    request.session["github_name"] = user["name"]
    _flash(request, f"登录成功：{user['login']}", "success")
    return _redirect_home()


@app.post("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    _flash(request, "已退出登录。", "info")
    return _redirect_home()


@app.post("/sync-stars")
def sync_stars(request: Request) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    try:
        raw_repos = github.fetch_starred_repos(auth["token"])
        repos = [_repo_payload(raw) for raw in raw_repos]
        count = db.sync_starred_repos(auth["login"], repos)
    except github.GitHubAPIError as exc:
        _flash(request, f"同步失败：{exc}", "error")
        return _redirect_home()

    _flash(request, f"Stars 同步完成，共 {count} 个项目。", "success")
    return _redirect_home()


@app.post("/manual-star")
def manual_star(request: Request, repo_full_name: str = Form(...)) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    repo_full_name = repo_full_name.strip().strip("/")
    if "/" not in repo_full_name:
        _flash(request, "仓库格式应为 owner/repo。", "error")
        return _redirect_home()

    try:
        github.star_repo(auth["token"], repo_full_name)
        raw = github.fetch_repo(auth["token"], repo_full_name)
        db.upsert_starred_repo(auth["login"], _repo_payload(raw))
    except github.GitHubAPIError as exc:
        _flash(request, f"手动加星失败：{exc}", "error")
        return _redirect_home()

    _flash(request, f"已加星并更新：{repo_full_name}", "success")
    return _redirect_home()


@app.post("/lists")
def create_list(request: Request, list_name: str = Form(...)) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    name = list_name.strip()
    if not name:
        _flash(request, "List 名称不能为空。", "error")
        return _redirect_home()

    try:
        github.create_user_list(auth["token"], name=name, is_private=False)
        synced_lists, synced_items, _ = _sync_remote_lists_from_github(auth)
    except github.GitHubAPIError as exc:
        _flash(request, f"创建 List 失败：{exc}", "error")
        return _redirect_home()

    _flash(
        request,
        f"已在 GitHub 创建 List：{name}。当前共 {synced_lists} 个列表，{synced_items} 个项目。",
        "success",
    )
    return _redirect_home()


@app.post("/lists/{list_id}/delete")
def delete_list(request: Request, list_id: int) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    target_list = db.get_custom_list(auth["login"], list_id)
    if not target_list:
        _flash(request, "List 不存在。", "error")
        return _redirect_home()

    remote_id = (target_list.get("remote_id") or "").strip()
    if remote_id:
        try:
            github.delete_user_list(auth["token"], remote_id)
            _sync_remote_lists_from_github(auth)
        except github.GitHubAPIError as exc:
            _flash(request, f"删除 GitHub List 失败：{exc}", "error")
            return _redirect_home()
        _flash(request, f"已删除 GitHub List：{target_list['name']}", "success")
        return _redirect_home()

    ok = db.delete_custom_list(auth["login"], list_id)
    _flash(
        request,
        "该 List 仅存在本地，已删除。" if ok else "List 不存在。",
        "success" if ok else "error",
    )
    return _redirect_home()


@app.post("/lists/{list_id}/items")
def add_list_item(
    request: Request,
    list_id: int,
    repo_full_name: str = Form(""),
    repo_from_sync: str = Form(""),
) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    target_list = db.get_custom_list(auth["login"], list_id)
    if not target_list:
        _flash(request, "List 不存在。", "error")
        return _redirect_home()

    remote_id = (target_list.get("remote_id") or "").strip()
    if not remote_id:
        _flash(request, _local_only_list_tip(), "error")
        return _redirect_home()

    candidate = _normalize_full_name((repo_full_name or "").strip() or (repo_from_sync or "").strip())
    if not candidate:
        _flash(request, "仓库格式应为 owner/repo。", "error")
        return _redirect_home()

    try:
        node_id = db.get_repo_node_id(auth["login"], candidate)
        if not node_id:
            github.star_repo(auth["token"], candidate)
            raw = github.fetch_repo(auth["token"], candidate)
            db.upsert_starred_repo(auth["login"], _repo_payload(raw))
            node_id = raw.get("node_id")
        if not node_id:
            raise github.GitHubAPIError(f"无法获取仓库节点 ID：{candidate}")

        _, _, remote_lists = _sync_remote_lists_from_github(auth)
        membership = _collect_repo_remote_memberships(remote_lists)
        list_ids = sorted(membership.get(candidate, set()) | {remote_id})
        github.update_user_lists_for_item(auth["token"], node_id, list_ids)
        _sync_remote_lists_from_github(auth)
    except github.GitHubAPIError as exc:
        _flash(request, f"添加到 GitHub List 失败：{exc}", "error")
        return _redirect_home()

    _flash(request, f"已添加 {candidate} 到 GitHub List：{target_list['name']}", "success")
    return _redirect_home()


@app.post("/lists/{list_id}/items/remove")
def remove_list_item(
    request: Request, list_id: int, repo_full_name: str = Form(...)
) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    target_list = db.get_custom_list(auth["login"], list_id)
    if not target_list:
        _flash(request, "List 不存在。", "error")
        return _redirect_home()

    remote_id = (target_list.get("remote_id") or "").strip()
    full_name = _normalize_full_name(repo_full_name)
    if not full_name:
        _flash(request, "仓库格式应为 owner/repo。", "error")
        return _redirect_home()

    if not remote_id:
        ok = db.remove_repo_from_list(auth["login"], list_id, full_name)
        _flash(
            request,
            "该 List 是历史本地数据（未同步到 GitHub），已仅在本地移除项目。"
            if ok
            else "List 项不存在。",
            "success" if ok else "error",
        )
        return _redirect_home()

    try:
        node_id = db.get_repo_node_id(auth["login"], full_name)
        if not node_id:
            raw = github.fetch_repo(auth["token"], full_name)
            db.upsert_starred_repo(auth["login"], _repo_payload(raw))
            node_id = raw.get("node_id")
        if not node_id:
            raise github.GitHubAPIError(f"无法获取仓库节点 ID：{full_name}")

        _, _, remote_lists = _sync_remote_lists_from_github(auth)
        membership = _collect_repo_remote_memberships(remote_lists)
        current_ids = set(membership.get(full_name, set()))
        if remote_id in current_ids:
            current_ids.remove(remote_id)
            github.update_user_lists_for_item(auth["token"], node_id, sorted(current_ids))
        _sync_remote_lists_from_github(auth)
    except github.GitHubAPIError as exc:
        _flash(request, f"从 GitHub List 移除失败：{exc}", "error")
        return _redirect_home()

    _flash(request, f"已从 GitHub List 移除：{full_name}", "success")
    return _redirect_home()


@app.post("/lists/refresh")
def refresh_lists(request: Request) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    try:
        synced_lists, synced_items, _ = _sync_remote_lists_from_github(auth)
    except github.GitHubAPIError as exc:
        _flash(request, f"List 同步失败：{exc}", "error")
        return _redirect_home()

    _flash(
        request,
        f"List 同步完成：{synced_lists} 个列表，{synced_items} 个项目。来源 GitHub GraphQL。",
        "success",
    )
    return _redirect_home()


@app.post("/lists/cleanup-local")
def cleanup_local_lists(request: Request) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    deleted = db.clear_local_only_lists(auth["login"])
    if deleted:
        _flash(request, f"已清理 {deleted} 个历史本地 List。", "success")
    else:
        _flash(request, "没有可清理的本地遗留 List。", "info")
    return _redirect_home()


@app.post("/lists/bulk-assign")
def bulk_assign_list(
    request: Request,
    target_list_id: int = Form(...),
    repo_full_names: list[str] = Form([]),
) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    target_list = db.get_custom_list(auth["login"], target_list_id)
    if not target_list:
        _flash(request, "List 不存在。", "error")
        return _redirect_home()

    target_remote_id = (target_list.get("remote_id") or "").strip()
    if not target_remote_id:
        _flash(request, _local_only_list_tip(), "error")
        return _redirect_home()

    cleaned: list[str] = []
    seen = set()
    for full_name in repo_full_names:
        candidate = _normalize_full_name(full_name)
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        cleaned.append(candidate)

    if not cleaned:
        _flash(request, "请先勾选要分配的项目。", "error")
        return _redirect_home()

    try:
        _, _, remote_lists = _sync_remote_lists_from_github(auth)
        memberships = _collect_repo_remote_memberships(remote_lists)
    except github.GitHubAPIError as exc:
        _flash(request, f"读取 GitHub Lists 失败：{exc}", "error")
        return _redirect_home()

    added = 0
    skipped = 0
    failed = 0
    for full_name in cleaned:
        try:
            node_id = db.get_repo_node_id(auth["login"], full_name)
            if not node_id:
                github.star_repo(auth["token"], full_name)
                raw = github.fetch_repo(auth["token"], full_name)
                db.upsert_starred_repo(auth["login"], _repo_payload(raw))
                node_id = raw.get("node_id")
            if not node_id:
                raise github.GitHubAPIError(f"无法获取仓库节点 ID：{full_name}")

            current_ids = memberships.get(full_name, set())
            if target_remote_id in current_ids:
                skipped += 1
                continue
            github.update_user_lists_for_item(
                auth["token"], node_id, sorted(set(current_ids) | {target_remote_id})
            )
            added += 1
        except github.GitHubAPIError:
            failed += 1

    try:
        _sync_remote_lists_from_github(auth)
    except github.GitHubAPIError:
        pass

    _flash(
        request,
        (
            f"批量分配完成：已加入 {added} 项到 {target_list['name']}，"
            f"跳过 {skipped} 项（已在列表），失败 {failed} 项。"
        ),
        "success" if failed == 0 else "info",
    )
    return _redirect_home()


@app.post("/sync-trending")
def sync_trending(request: Request, since: str = Form("daily")) -> RedirectResponse:
    auth = _auth(request)
    if not auth:
        _flash(request, "请先登录。", "error")
        return _redirect_home()

    try:
        raw_repos, source = github.fetch_trending_repos(auth["token"], since=since)
        repos = [_trending_payload(raw) for raw in raw_repos]
        summary = analyzer.build_trending_overview(repos)
        count = db.replace_trending(repos, summary)
    except github.GitHubAPIError as exc:
        _flash(request, f"Trending 同步失败：{exc}", "error")
        return _redirect_home()

    suffix = ""
    if source == "github-search-fallback":
        suffix = "（Trending 页面不可达，已自动回退到 GitHub Search。）"
    _flash(request, f"Trending 同步完成，共 {count} 个项目。{suffix}", "success")
    return _redirect_home()
