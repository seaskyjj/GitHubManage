from __future__ import annotations

from collections import Counter
from typing import Iterable


CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("AI / 机器学习", ["ai", "llm", "ml", "machine learning", "deep learning", "nlp", "agent"]),
    ("前端 / Web", ["frontend", "react", "vue", "next.js", "css", "ui", "web"]),
    ("后端 / 服务", ["backend", "api", "server", "microservice", "fastapi", "django", "flask"]),
    ("数据工程", ["data", "etl", "pipeline", "spark", "analytics", "warehouse"]),
    ("开发工具", ["cli", "tooling", "automation", "devtool", "plugin", "extension"]),
    ("DevOps / 云原生", ["docker", "kubernetes", "devops", "terraform", "ci", "cd", "cloud"]),
    ("移动开发", ["android", "ios", "flutter", "react native", "mobile"]),
    ("安全", ["security", "auth", "encryption", "vulnerability", "pentest"]),
    ("区块链", ["blockchain", "web3", "solidity", "ethereum", "bitcoin"]),
    ("游戏", ["game", "unity", "unreal", "godot"]),
]


def infer_category(
    repo_name: str,
    description: str | None,
    topics: Iterable[str] | None,
    language: str | None,
) -> str:
    topics = topics or []
    normalized = " ".join(
        [
            repo_name.lower(),
            (description or "").lower(),
            " ".join(topic.lower() for topic in topics),
            (language or "").lower(),
        ]
    )
    for category, keywords in CATEGORY_RULES:
        if any(keyword in normalized for keyword in keywords):
            return category
    if (language or "").lower() in {"javascript", "typescript", "html", "css"}:
        return "前端 / Web"
    if (language or "").lower() in {"python", "go", "rust", "java", "c#", "kotlin"}:
        return "后端 / 服务"
    return "通用 / 其他"


def build_repo_blurb(full_name: str, description: str | None, category: str) -> str:
    desc = "暂无项目简介。"
    if description and description.strip():
        clean = " ".join(description.split())
        desc = clean[:110] + ("..." if len(clean) > 110 else "")
    return f"{full_name}：{desc} 归类为「{category}」。"


def build_trending_overview(repos: list[dict]) -> str:
    if not repos:
        return "暂无 Trending 数据。"

    category_counter = Counter(repo["category"] for repo in repos)
    language_counter = Counter((repo.get("language") or "未知语言") for repo in repos)
    top_categories = "、".join(name for name, _ in category_counter.most_common(3))
    top_languages = "、".join(name for name, _ in language_counter.most_common(3))
    top_projects = "、".join(repo["full_name"] for repo in repos[:5])

    return (
        f"本次共抓取 {len(repos)} 个 Trending 项目，热门类别集中在 {top_categories}；"
        f"常见语言为 {top_languages}。代表项目包括：{top_projects}。"
    )

