"""Shared core logic for llm-wiki operations.

Used by both the built-in Tool classes (`nanobot/agent/tools/wiki.py`) and the
MCP server (`nanobot/mcp_servers/wiki/server.py`). All functions are pure file-system
operations with no dependency on nanobot internals.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ── wikilink regex ──────────────────────────────────────────────────────────
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)

# ── directory layout constants ──────────────────────────────────────────────
_WIKI_SUBDIRS = ["sources", "entities", "concepts", "comparisons", "queries"]


# ── data classes ─────────────────────────────────────────────────────────────


@dataclass
class LintIssue:
    """A single issue found during linting."""

    kind: str  # "dead_link", "orphan", "index_missing", "index_extra"
    file: str  # relative path from wiki root
    detail: str
    line: int | None = None


@dataclass
class LintReport:
    """Full lint report."""

    issues: list[LintIssue] = field(default_factory=list)
    dead_links: int = 0
    orphans: int = 0
    index_missing: int = 0
    index_extra: int = 0

    @property
    def total(self) -> int:
        return len(self.issues)

    @property
    def healthy(self) -> bool:
        return self.total == 0


@dataclass
class PageInfo:
    """Metadata extracted from a wiki page."""

    rel_path: str  # e.g. "entities/foo.md"
    title: str
    page_type: str  # source, entity, concept, comparison, query
    wikilinks: list[str] = field(default_factory=list)


# ── helpers ──────────────────────────────────────────────────────────────────


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_if_missing(path: Path, content: str) -> bool:
    """Write content to path only if the file does not already exist.
    Returns True if the file was created, False if it already existed.
    """
    if path.exists():
        return False
    path.write_text(content, encoding="utf-8")
    return True


def _git_init(root: Path) -> str:
    """Initialize a git repo in root. Returns the outcome message."""
    git_dir = root / ".git"
    if git_dir.exists():
        return "git 仓库已存在，跳过初始化"

    try:
        subprocess.run(
            ["git", "init"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "wiki: init"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        return "git 仓库已初始化并完成首次提交"
    except FileNotFoundError:
        return "git 未安装或不在 PATH 中，跳过仓库初始化"
    except subprocess.CalledProcessError as e:
        return f"git 操作失败: {e.stderr.strip() if e.stderr else e}"


def _git_commit(root: Path, message: str) -> str:
    """Stage all changes and commit. Returns the outcome message."""
    git_dir = root / ".git"
    if not git_dir.exists():
        return "git 仓库不存在，跳过提交"

    try:
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(root),
            capture_output=True,
            text=True,
            check=True,
        )
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return f"已提交: {message}"
        # Exit code 1 with "nothing to commit" is not an error
        if "nothing to commit" in (result.stderr + result.stdout):
            return "没有需要提交的更改"
        return f"提交失败: {result.stderr.strip()}"
    except FileNotFoundError:
        return "git 未安装，跳过提交"


def parse_frontmatter(content: str) -> dict[str, str]:
    """Extract YAML frontmatter from markdown content as a simple key-value dict.

    Only handles flat ``key: value`` lines. Nested YAML structures are returned
    as raw strings.
    """
    m = _FRONTMATTER_RE.search(content)
    if not m:
        return {}
    raw = m.group(1)
    result: dict[str, str] = {}
    for line in raw.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, val = line.partition(":")
            result[key.strip()] = val.strip()
    return result


def extract_wikilinks(content: str) -> list[str]:
    """Return all unique `[[wikilink]]` targets from markdown content."""
    return list(dict.fromkeys(_WIKILINK_RE.findall(content)))


def _resolve_wikilink_target(target: str, wiki_dir: Path) -> Path | None:
    """Resolve a `[[wikilink]]` target to a filesystem path.

    The target is relative to the ``wiki/`` subdirectory (e.g. ``entities/foo``
    maps to ``wiki/entities/foo.md``).
    """
    clean = target.strip()
    if clean.endswith(".md"):
        candidate = wiki_dir / clean
    else:
        candidate = wiki_dir / f"{clean}.md"
    return candidate if candidate.exists() else None


def _page_rel_path(file_path: Path, wiki_dir: Path) -> str:
    """Return the page-relative path used in wikilinks (e.g. ``entities/foo``)."""
    try:
        rel = file_path.resolve().relative_to(wiki_dir.resolve())
    except ValueError:
        return str(file_path)
    stem = str(rel.with_suffix("")).replace("\\", "/")
    return stem


# ── wiki structure ───────────────────────────────────────────────────────────


def generate_schema_md() -> str:
    """Return the canonical schema.md content."""
    return """# Wiki Schema — 行为契约

> 本文件定义 wiki 的页面类型、格式要求与链接规范。初始化时生成，按需修订。

## 页面类型

| 类型 | 目录 | 用途 |
|------|------|------|
| `source` | `wiki/sources/` | 每篇原始文档的摘要、元数据与关键声明 |
| `entity` | `wiki/entities/` | 人物、组织、工具、系统 |
| `concept` | `wiki/concepts/` | 概念、方法、定义、框架 |
| `comparison` | `wiki/comparisons/` | 跨来源对比 |
| `query` | `wiki/queries/` | 可复用的问答结果 |
| `debate` | `wiki/debates.md` | 矛盾与分歧追踪 |

## 页面格式模板

### Source 页面

```markdown
---
source: wiki/raw/<filename>
ingested: YYYY-MM-DD
type: source
---

# 来源：<Title>

## 摘要
[1-2 段总结]

## 关键声明
- [!source] 可直接引用的原文事实
- [!analysis] LLM 的综合判断
- [!unverified] 需人工确认的内容
- [!gap] 已知缺失或待补充

## 相关
- [[entities/author-name]]
- [[concepts/methodology]]
```

### Entity / Concept 页面

```markdown
---
type: entity   # 或 concept
---

# <名称>

## 定义 / 概述
[简要说明]

## 关键信息
- [!source] 事实
- [!analysis] 判断

## 相关
- [[sources/xxx]]
- [[concepts/yyy]]
```

## 链接规范

- 使用 `[[wikilink]]` 语法
- 链接用相对路径：`[[entities/foo]]` 而非 `[[wiki/wiki/entities/foo]]`
- 新增实体/概念时必须更新 `index.md`
- 修改页面后检查反链，确认引用方不出错
- 删除页面前先清空所有指向它的链接

## 信任标注体系

| 标注 | 含义 |
|------|------|
| `[!source]` | 可直接引用的原文事实 |
| `[!analysis]` | LLM 的综合判断 |
| `[!unverified]` | 需人工确认的内容 |
| `[!gap]` | 已知缺失或待补充 |

## 维护原则

- 不重复内容——用 `[[wikilink]]` 引用，不在多处写同一件事
- 保持页面短小——超过 200 行就拆分
- 每个声明标注信任级别
- `wiki/raw/` 目录下的文件绝不修改
- 每次摄入或批量修改后提交 git
"""


def generate_dashboard_md(title: str = "Wiki") -> str:
    """Return an initial dashboard.md for the given wiki title."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""---
type: dashboard
---

# {title} — 仪表盘

> 最后更新：{today}

## 概览

- 来源数量：（见 index.md）
- 实体数量：（见 index.md）
- 概念数量：（见 index.md）
- 最近摄入：（暂无）

## 最近变更

（摄入记录会出现在这里）

## 快速导航

- [索引](index.md) — 完整页面目录
- [Schema](../schema.md) — Wiki 行为契约
- [分歧](debates.md) — 矛盾与分歧追踪
"""


def generate_index_md(pages: list[PageInfo]) -> str:
    """Build index.md content from a list of PageInfo objects, grouped by type."""
    grouped: dict[str, list[PageInfo]] = {}
    for p in pages:
        grouped.setdefault(p.page_type, []).append(p)

    lines: list[str] = [
        "# Wiki 索引",
        "",
        "> 自动维护的全局目录。每次摄入或批量修改后更新。",
        "",
    ]

    type_labels = {
        "source": "📄 来源",
        "entity": "🏢 实体",
        "concept": "📖 概念",
        "comparison": "⚖️ 对比",
        "query": "🔍 查询",
    }

    for ptype, label in type_labels.items():
        items = grouped.get(ptype, [])
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        if items:
            for page in sorted(items, key=lambda x: x.title):
                lines.append(f"- [[{page.rel_path}]] — {page.title}")
        else:
            lines.append("- （暂无）")
        lines.append("")

    return "\n".join(lines)


def create_wiki_structure(root: Path, title: str = "Wiki") -> dict:
    """Create the full wiki directory structure under ``root``.

    Returns a dict summarising what was created vs what already existed.
    """
    root = root.resolve()
    _ensure_dir(root / "raw")
    wiki_dir = _ensure_dir(root / "wiki")

    # Subdirectories
    created_dirs: list[str] = []
    for sub in _WIKI_SUBDIRS:
        d = _ensure_dir(wiki_dir / sub)
        created_dirs.append(str(d.relative_to(root)))

    # Schema — always overwrite so it stays canonical
    schema_path = root / "schema.md"
    schema_existed = schema_path.exists()
    schema_path.write_text(generate_schema_md(), encoding="utf-8")

    # Dashboard
    dashboard_path = wiki_dir / "dashboard.md"
    dash_created = _write_if_missing(dashboard_path, generate_dashboard_md(title))

    # Index
    index_path = wiki_dir / "index.md"
    idx_created = _write_if_missing(index_path, generate_index_md([]))

    # Debates
    debates_path = wiki_dir / "debates.md"
    debates_created = _write_if_missing(
        debates_path,
        "---\ntype: debate\n---\n\n# 矛盾与分歧追踪\n\n"
        "> 当不同来源声称冲突的事实时，记录在此。\n\n"
        "（暂无已记录的分歧）\n",
    )

    # Git
    git_msg = _git_init(root)

    return {
        "root": str(root),
        "created_dirs": created_dirs,
        "files": {
            "schema.md": "已更新" if schema_existed else "已创建",
            "wiki/dashboard.md": "已创建" if dash_created else "已跳过（已存在）",
            "wiki/index.md": "已创建" if idx_created else "已跳过（已存在）",
            "wiki/debates.md": "已创建" if debates_created else "已跳过（已存在）",
        },
        "git": git_msg,
    }


# ── page collection ──────────────────────────────────────────────────────────


def collect_pages(wiki_dir: Path) -> list[PageInfo]:
    """Scan the ``wiki/`` subdirectory for .md pages and return PageInfo for each.

    Excludes index.md, dashboard.md, and debates.md (these are meta pages).
    """
    meta_pages = {"index.md", "dashboard.md", "debates.md"}
    pages: list[PageInfo] = []

    for md_file in sorted(wiki_dir.rglob("*.md")):
        rel = str(md_file.relative_to(wiki_dir)).replace("\\", "/")
        if rel in meta_pages:
            continue
        content = md_file.read_text(encoding="utf-8")
        fm = parse_frontmatter(content)
        title = fm.get("title", md_file.stem)
        page_type = fm.get("type", "unknown")
        links = extract_wikilinks(content)
        link_rel = _page_rel_path(md_file, wiki_dir)
        pages.append(PageInfo(rel_path=link_rel, title=title, page_type=page_type, wikilinks=links))

    return pages


# ── lint checks ──────────────────────────────────────────────────────────────


def _collect_page_files(wiki_dir: Path) -> set[str]:
    """Return the set of page-relative paths (without .md) for all .md files under wiki_dir."""
    paths: set[str] = set()
    for md_file in wiki_dir.rglob("*.md"):
        paths.add(_page_rel_path(md_file, wiki_dir))
    return paths


def check_dead_links(wiki_dir: Path) -> list[LintIssue]:
    """Find `[[wikilinks]]` that point to non-existent pages."""
    issues: list[LintIssue] = []
    page_files = _collect_page_files(wiki_dir)

    for md_file in sorted(wiki_dir.rglob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        rel = str(md_file.relative_to(wiki_dir)).replace("\\", "/")
        for match in _WIKILINK_RE.finditer(content):
            target = match.group(1).strip()
            target_key = target.removesuffix(".md")
            if target_key not in page_files:
                line_no = content[: match.start()].count("\n") + 1
                issues.append(
                    LintIssue(
                        kind="dead_link",
                        file=rel,
                        detail=f"[[{target}]] — 目标页面不存在",
                        line=line_no,
                    )
                )
    return issues


def check_orphans(wiki_dir: Path) -> list[LintIssue]:
    """Find pages that are not linked from any other *content* page.

    Meta pages (index, dashboard, debates) are excluded both as orphans
    themselves and as sources of incoming links — index.md links everything,
    so counting its links would mask true orphans.
    """
    meta_names = {"index.md", "dashboard.md", "debates.md"}

    # Collect all pages
    all_pages = _collect_page_files(wiki_dir)
    linked_from: set[str] = set()

    for md_file in wiki_dir.rglob("*.md"):
        rel = str(md_file.relative_to(wiki_dir)).replace("\\", "/")
        if rel in meta_names:
            continue  # skip meta pages — their links don't count
        content = md_file.read_text(encoding="utf-8")
        for target in extract_wikilinks(content):
            linked_from.add(target.strip().removesuffix(".md"))

    # Meta pages are never orphans
    meta_stems = {"index", "dashboard", "debates"}
    orphans = all_pages - linked_from - meta_stems

    issues: list[LintIssue] = []
    for orphan in sorted(orphans):
        issues.append(
            LintIssue(
                kind="orphan",
                file=f"{orphan}.md",
                detail="孤立页面 — 没有被任何其他页面引用",
            )
        )
    return issues


def check_index_consistency(wiki_dir: Path) -> list[LintIssue]:
    """Check whether index.md lists every page and has no stale entries."""
    issues: list[LintIssue] = []
    index_path = wiki_dir / "index.md"
    if not index_path.exists():
        return [LintIssue(kind="index_missing", file="wiki/index.md", detail="index.md 不存在")]

    index_content = index_path.read_text(encoding="utf-8")
    index_links = set(extract_wikilinks(index_content))

    # Pages in filesystem (exclude meta)
    meta = {"index", "dashboard", "debates"}
    fs_pages = {p for p in _collect_page_files(wiki_dir) if p not in meta}

    # Pages missing from index
    for page in sorted(fs_pages - index_links):
        issues.append(
            LintIssue(
                kind="index_missing",
                file="wiki/index.md",
                detail=f"索引遗漏: [[{page}]]",
            )
        )

    # Stale entries in index
    for link in sorted(index_links - fs_pages):
        issues.append(
            LintIssue(
                kind="index_extra",
                file="wiki/index.md",
                detail=f"索引指向不存在的页面: [[{link}]]",
            )
        )

    return issues


def run_all_lint_checks(wiki_dir: Path, checks: str = "all") -> LintReport:
    """Run lint checks against the wiki.

    Args:
        wiki_dir: Path to the ``wiki/`` subdirectory (e.g. ``root / "wiki"``).
        checks: Comma-separated list — ``all``, ``deadlinks``, ``orphans``, ``index``.

    Returns:
        A LintReport summarising all findings.
    """
    report = LintReport()
    check_set = {c.strip().lower() for c in checks.split(",") if c.strip()}

    if "all" in check_set or "deadlinks" in check_set:
        dead = check_dead_links(wiki_dir)
        report.issues.extend(dead)
        report.dead_links = len(dead)

    if "all" in check_set or "orphans" in check_set:
        orphans = check_orphans(wiki_dir)
        report.issues.extend(orphans)
        report.orphans = len(orphans)

    if "all" in check_set or "index" in check_set:
        idx_issues = check_index_consistency(wiki_dir)
        report.issues.extend(idx_issues)
        report.index_missing = sum(1 for i in idx_issues if i.kind == "index_missing")
        report.index_extra = sum(1 for i in idx_issues if i.kind == "index_extra")

    return report


def rebuild_index(wiki_dir: Path) -> dict:
    """Scan wiki pages and regenerate index.md. Returns a summary dict."""
    pages = collect_pages(wiki_dir)
    index_content = generate_index_md(pages)
    index_path = wiki_dir / "index.md"
    index_path.write_text(index_content, encoding="utf-8")

    grouped: dict[str, int] = {}
    for p in pages:
        grouped[p.page_type] = grouped.get(p.page_type, 0) + 1

    return {
        "total_pages": len(pages),
        "by_type": grouped,
        "index_path": str(index_path),
    }
