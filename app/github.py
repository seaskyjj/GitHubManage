from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup


API_BASE = "https://api.github.com"
GRAPHQL_URL = f"{API_BASE}/graphql"
TRENDING_URL = "https://github.com/trending"
GITHUB_WEB_BASE = "https://github.com"
USER_AGENT = "github-stars-manager"
PAT_RESOURCE_DENIED = "Resource not accessible by personal access token"


class GitHubAPIError(RuntimeError):
    """Raised when GitHub API request fails."""


def token_kind(token: str) -> str:
    if token.startswith("github_pat_"):
        return "fine-grained-pat"
    if token.startswith("ghp_"):
        return "classic-pat"
    return "unknown"


def is_pat_resource_denied(message: str) -> bool:
    return PAT_RESOURCE_DENIED.lower() in (message or "").lower()


def list_write_auth_guidance(token: str) -> str:
    kind = token_kind(token)
    if kind == "fine-grained-pat":
        return (
            "当前 token 是 fine-grained PAT（github_pat_），GitHub 用户 Lists 写入常会被拒绝。"
            "请改用 classic PAT（ghp_）或 OAuth token，并授予 `read:user` + `public_repo` "
            "（私有仓库请用 `repo`）；如账号在组织内请完成该 token 的 SSO 授权。"
        )
    if kind == "classic-pat":
        return (
            "当前 token 为 classic PAT，但缺少权限或未完成 SSO 授权。"
            "请确认至少包含 `read:user` + `public_repo`（私有仓库请用 `repo`），"
            "并在组织 SSO 页面授权该 token。"
        )
    return (
        "当前 token 可能不支持用户 Lists 写入。请改用 classic PAT（ghp_）或 OAuth token，"
        "并授予 `read:user` + `public_repo`（私有仓库请用 `repo`）。"
    )


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }


def _request(method: str, url: str, token: str, **kwargs: Any) -> httpx.Response:
    headers = kwargs.pop("headers", {})
    merged = {**_headers(token), **headers}
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            response = client.request(method, url, headers=merged, **kwargs)
    except httpx.HTTPError as exc:
        raise GitHubAPIError(f"请求 GitHub 失败: {exc}") from exc

    if response.status_code >= 400:
        message = response.text
        try:
            data = response.json()
            message = data.get("message", message)
        except ValueError:
            pass
        raise GitHubAPIError(f"GitHub API 错误 {response.status_code}: {message}")
    return response


def get_authenticated_user(token: str) -> dict[str, Any]:
    response = _request("GET", f"{API_BASE}/user", token)
    data = response.json()
    return {
        "login": data["login"],
        "name": data.get("name") or data["login"],
    }


def fetch_starred_repos(token: str, per_page: int = 100) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    while True:
        response = _request(
            "GET",
            f"{API_BASE}/user/starred",
            token,
            params={"per_page": per_page, "page": page},
        )
        page_items = response.json()
        if not page_items:
            break
        repos.extend(page_items)
        if len(page_items) < per_page:
            break
        page += 1
    return repos


def fetch_repo(token: str, full_name: str) -> dict[str, Any]:
    response = _request("GET", f"{API_BASE}/repos/{full_name}", token)
    return response.json()


def star_repo(token: str, full_name: str) -> None:
    response = _request("PUT", f"{API_BASE}/user/starred/{full_name}", token)
    if response.status_code not in (204, 304):
        raise GitHubAPIError(f"加星失败，状态码: {response.status_code}")


def _graphql_request(token: str, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    response = _request(
        "POST",
        GRAPHQL_URL,
        token,
        json={"query": query, "variables": variables or {}},
    )
    data = response.json()
    errors = data.get("errors") or []
    if errors:
        messages = "; ".join(str(err.get("message", "Unknown GraphQL error")) for err in errors[:3])
        if is_pat_resource_denied(messages):
            messages = f"{messages}。{list_write_auth_guidance(token)}"
        raise GitHubAPIError(f"GitHub GraphQL 错误: {messages}")
    return data.get("data") or {}


def create_user_list(
    token: str, name: str, is_private: bool = False, description: str = ""
) -> dict[str, Any]:
    mutation = """
    mutation($input: CreateUserListInput!) {
      createUserList(input: $input) {
        list {
          id
          name
          slug
          isPrivate
          description
        }
      }
    }
    """
    variables = {"input": {"name": name.strip(), "isPrivate": is_private}}
    if description.strip():
        variables["input"]["description"] = description.strip()

    data = _graphql_request(token, mutation, variables)
    list_obj = (data.get("createUserList") or {}).get("list")
    if not list_obj:
        raise GitHubAPIError("创建 List 失败，未返回 list 对象。")
    return list_obj


def delete_user_list(token: str, list_id: str) -> None:
    mutation = """
    mutation($input: DeleteUserListInput!) {
      deleteUserList(input: $input) {
        clientMutationId
      }
    }
    """
    _graphql_request(token, mutation, {"input": {"listId": list_id}})


def update_user_lists_for_item(token: str, item_id: str, list_ids: list[str]) -> list[dict[str, Any]]:
    mutation = """
    mutation($input: UpdateUserListsForItemInput!) {
      updateUserListsForItem(input: $input) {
        lists {
          id
          name
          slug
        }
      }
    }
    """
    data = _graphql_request(
        token,
        mutation,
        {"input": {"itemId": item_id, "listIds": list_ids}},
    )
    return (data.get("updateUserListsForItem") or {}).get("lists") or []


def _extract_repo_names_from_list_nodes(nodes: list[dict[str, Any]] | None) -> list[str]:
    repos: list[str] = []
    seen: set[str] = set()
    for node in nodes or []:
        if not node or node.get("__typename") != "Repository":
            continue
        full_name = (node.get("nameWithOwner") or "").strip()
        if not full_name or full_name in seen:
            continue
        seen.add(full_name)
        repos.append(full_name)
    return repos


def _fetch_user_list_items_graphql(token: str, list_id: str) -> list[str]:
    query = """
    query($listId: ID!, $after: String) {
      node(id: $listId) {
        ... on UserList {
          items(first: 100, after: $after) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              __typename
              ... on Repository {
                nameWithOwner
              }
            }
          }
        }
      }
    }
    """
    repos: list[str] = []
    seen: set[str] = set()
    after: str | None = None

    while True:
        data = _graphql_request(token, query, {"listId": list_id, "after": after})
        node = data.get("node") or {}
        items = node.get("items") or {}
        for full_name in _extract_repo_names_from_list_nodes(items.get("nodes")):
            if full_name not in seen:
                seen.add(full_name)
                repos.append(full_name)
        page_info = items.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break
    return repos


def fetch_viewer_star_lists(token: str) -> tuple[str, list[dict[str, Any]]]:
    query = """
    query($after: String) {
      viewer {
        login
        lists(first: 50, after: $after) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            id
            name
            slug
            isPrivate
            items(first: 100) {
              pageInfo {
                hasNextPage
                endCursor
              }
              nodes {
                __typename
                ... on Repository {
                  nameWithOwner
                }
              }
            }
          }
        }
      }
    }
    """
    all_lists: list[dict[str, Any]] = []
    after: str | None = None
    viewer_login = ""

    while True:
        data = _graphql_request(token, query, {"after": after})
        viewer = data.get("viewer") or {}
        viewer_login = viewer.get("login") or viewer_login
        conn = viewer.get("lists") or {}
        for node in conn.get("nodes") or []:
            if not node:
                continue
            repos = _extract_repo_names_from_list_nodes((node.get("items") or {}).get("nodes"))
            if (node.get("items") or {}).get("pageInfo", {}).get("hasNextPage"):
                repos = _fetch_user_list_items_graphql(token, node["id"])
            all_lists.append(
                {
                    "remote_id": node["id"],
                    "name": node.get("name") or "",
                    "slug": node.get("slug") or "",
                    "is_private": bool(node.get("isPrivate", False)),
                    "html_url": f"{GITHUB_WEB_BASE}/stars/{viewer_login}/lists/{node.get('slug') or ''}",
                    "repos": repos,
                }
            )

        page_info = conn.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        after = page_info.get("endCursor")
        if not after:
            break

    if not viewer_login:
        raise GitHubAPIError("读取 viewer 信息失败。")
    return viewer_login, all_lists


def _extract_int(text: str | None) -> int:
    if not text:
        return 0
    match = re.search(r"(\d[\d,]*)", text)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def _web_headers(token: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _web_get(url: str, params: dict[str, Any] | None = None, token: str | None = None) -> str:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                response = client.get(url, params=params, headers=_web_headers(token))
                response.raise_for_status()
            return response.text
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.0 + attempt)
    raise GitHubAPIError(f"请求 GitHub 页面失败: {last_error}")


def _parse_star_lists_html(owner_login: str, html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select(f'a[href^="/stars/{owner_login}/lists/"]')
    seen: set[str] = set()
    results: list[dict[str, str]] = []

    for anchor in anchors:
        href = anchor.get("href", "").strip()
        match = re.match(rf"^/stars/{re.escape(owner_login)}/lists/([^/?#]+)$", href)
        if not match:
            continue
        slug = match.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        name = " ".join(anchor.get_text(" ", strip=True).split()) or slug
        results.append(
            {
                "name": name,
                "slug": slug,
                "html_url": f"{GITHUB_WEB_BASE}{href}",
            }
        )
    return results


def _parse_star_list_repos_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    anchors = soup.select("#user-list-repositories h2.h3 > a[href]")
    repos: list[str] = []
    seen: set[str] = set()

    for anchor in anchors:
        href = anchor.get("href", "").strip()
        match = re.match(r"^/([^/]+)/([^/]+)$", href)
        if not match:
            continue
        full_name = f"{match.group(1)}/{match.group(2)}"
        if full_name in seen:
            continue
        seen.add(full_name)
        repos.append(full_name)
    return repos


def fetch_user_star_lists(owner_login: str, token: str | None = None) -> list[dict[str, Any]]:
    if token:
        try:
            viewer_login, lists = fetch_viewer_star_lists(token)
            if viewer_login.lower() == owner_login.lower():
                return lists
        except GitHubAPIError:
            pass

    html = _web_get(
        f"{GITHUB_WEB_BASE}/{owner_login}",
        params={"tab": "stars"},
        token=token,
    )
    lists = _parse_star_lists_html(owner_login, html)
    results: list[dict[str, Any]] = []
    failed_slugs: list[str] = []

    for item in lists:
        try:
            list_html = _web_get(item["html_url"], token=token)
            repos = _parse_star_list_repos_html(list_html)
            results.append(
                {
                    "remote_id": "",
                    "name": item["name"],
                    "slug": item["slug"],
                    "is_private": False,
                    "html_url": item["html_url"],
                    "repos": repos,
                }
            )
        except GitHubAPIError:
            failed_slugs.append(item["slug"])

    if not results and failed_slugs:
        raise GitHubAPIError(f"无法读取任何 List 页面，失败列表: {', '.join(failed_slugs)}")
    return results


def _fetch_trending_via_search_api(token: str, since: str) -> list[dict[str, Any]]:
    days = {"daily": 1, "weekly": 7, "monthly": 30}.get(since, 1)
    created_from = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    response = _request(
        "GET",
        f"{API_BASE}/search/repositories",
        token,
        params={
            "q": f"created:>={created_from}",
            "sort": "stars",
            "order": "desc",
            "per_page": 30,
            "page": 1,
        },
    )
    items = response.json().get("items", [])
    repos: list[dict[str, Any]] = []
    for idx, item in enumerate(items, start=1):
        repos.append(
            {
                "rank_no": idx,
                "full_name": item.get("full_name", ""),
                "html_url": item.get("html_url", ""),
                "description": item.get("description") or "",
                "language": item.get("language") or "",
                "stars_total": item.get("stargazers_count", 0),
                "stars_today": 0,
            }
        )
    return repos


def fetch_trending_repos(token: str, since: str = "daily") -> tuple[list[dict[str, Any]], str]:
    since = since if since in {"daily", "weekly", "monthly"} else "daily"
    html_error: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                response = client.get(
                    TRENDING_URL,
                    params={"since": since},
                    headers=_web_headers(),
                )
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            rows = soup.select("article.Box-row")
            results: list[dict[str, Any]] = []
            for idx, row in enumerate(rows, start=1):
                title_link = row.select_one("h2 a")
                if not title_link or not title_link.get("href"):
                    continue

                full_name = title_link.get("href", "").strip("/").replace(" ", "")
                html_url = f"https://github.com/{full_name}"
                description_tag = row.select_one("p")
                language_tag = row.select_one("span[itemprop='programmingLanguage']")
                stars_total_tag = row.select_one("a[href$='/stargazers']")
                stars_today_tag = row.select_one("span.d-inline-block.float-sm-right")

                results.append(
                    {
                        "rank_no": idx,
                        "full_name": full_name,
                        "html_url": html_url,
                        "description": description_tag.get_text(" ", strip=True) if description_tag else "",
                        "language": language_tag.get_text(" ", strip=True) if language_tag else "",
                        "stars_total": _extract_int(
                            stars_total_tag.get_text(" ", strip=True) if stars_total_tag else ""
                        ),
                        "stars_today": _extract_int(
                            stars_today_tag.get_text(" ", strip=True) if stars_today_tag else ""
                        ),
                    }
                )
            if results:
                return results, "github-trending-page"
            html_error = RuntimeError("Trending 页面结构未命中可解析节点。")
        except httpx.HTTPStatusError as exc:
            html_error = exc
            if exc.response.status_code < 500:
                break
        except httpx.HTTPError as exc:
            html_error = exc

        if attempt < 2:
            time.sleep(1.0 + attempt)

    try:
        fallback = _fetch_trending_via_search_api(token, since)
        if fallback:
            return fallback, "github-search-fallback"
    except GitHubAPIError as exc:
        raise GitHubAPIError(f"抓取 Trending 失败: {html_error}; 回退也失败: {exc}") from exc

    raise GitHubAPIError(f"抓取 Trending 失败: {html_error}")
