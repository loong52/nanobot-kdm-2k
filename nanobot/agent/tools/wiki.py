"""Wiki management tools for llm-wiki knowledge bases.

Provides three deterministic operations that complement the llm-wiki Skill:

- ``wiki_init`` — Initialize wiki directory structure and contract files
- ``wiki_lint`` — Health check (dead links, orphans, index consistency)
- ``wiki_index`` — Rebuild index.md from filesystem scan

Ingest and query remain Skill-guided LLM workflows because they require
semantic understanding that deterministic code cannot provide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import ToolContext, current_request_context
from nanobot.agent.tools.schema import (
    BooleanSchema,
    StringSchema,
    tool_parameters_schema,
)
from nanobot.mcp_servers.wiki.core import (
    create_wiki_structure,
    rebuild_index,
    run_all_lint_checks,
)


def _resolve_root(raw: str) -> Path:
    """Resolve a user-supplied wiki root path against the workspace directory."""
    root = Path(raw.strip() or "wiki")
    if not root.is_absolute():
        ctx: ToolContext | None = current_request_context()
        if ctx is not None and ctx.workspace_dir:
            root = Path(ctx.workspace_dir) / root
    return root.resolve()


def _format_lint_report(report) -> str:
    """Format a LintReport as a human-readable string."""
    if report.healthy:
        return "✅ Wiki 健康检查通过，未发现问题。"

    lines: list[str] = [
        "## Wiki 健康检查报告",
        "",
        "| 检查项 | 问题数 |",
        "|--------|--------|",
        f"| 死链 | {report.dead_links} |",
        f"| 孤立页面 | {report.orphans} |",
        f"| 索引遗漏 | {report.index_missing} |",
        f"| 索引多余 | {report.index_extra} |",
        "",
        f"**共 {report.total} 个问题**",
        "",
    ]

    by_kind: dict[str, list] = {}
    for issue in report.issues:
        by_kind.setdefault(issue.kind, []).append(issue)

    kind_labels = {
        "dead_link": "🔗 死链",
        "orphan": "👻 孤立页面",
        "index_missing": "📋 索引遗漏",
        "index_extra": "📋 索引多余",
    }

    for kind, label in kind_labels.items():
        items = by_kind.get(kind, [])
        if not items:
            continue
        lines.append(f"### {label} ({len(items)})")
        lines.append("")
        for issue in items:
            loc = f":{issue.line}" if issue.line else ""
            lines.append(f"- `{issue.file}{loc}` — {issue.detail}")
        lines.append("")

    return "\n".join(lines)


# ── WikiInitTool ─────────────────────────────────────────────────────────────


@tool_parameters(
    tool_parameters_schema(
        root=StringSchema('Wiki 根目录路径（相对于工作区），默认 "wiki"'),
        force=BooleanSchema(description="如果为 true，即使 wiki 已存在也覆盖 schema.md"),
        required=[],
    )
)
class WikiInitTool(Tool):
    """Initialize an llm-wiki directory structure.

    Creates the three-layer architecture (raw/ → wiki/ → schema.md),
    generates initial dashboard and index, and initialises a git repo.
    """

    _scopes = {"core", "subagent"}

    name = "wiki_init"
    description = (
        "初始化 llm-wiki 知识库目录结构。"
        "创建三层架构（raw/ → wiki/ → schema.md）、初始仪表盘和索引，并初始化 git 仓库。"
        "如果 wiki 已存在，默认保留现有内容（使用 force=true 覆盖 schema.md）。"
    )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, root: str = "wiki", force: bool = False, **kwargs: Any) -> str:
        wiki_root = _resolve_root(root)

        if not force and (wiki_root / "schema.md").exists():
            return ToolResult.error(
                f"Wiki 已在 {wiki_root} 初始化。使用 force=true 强制重新初始化。"
            )

        try:
            result = create_wiki_structure(wiki_root)
        except OSError as e:
            return ToolResult.error(f"创建 wiki 目录失败: {e}")

        lines = [
            "## Wiki 初始化完成 ✅",
            "",
            f"**位置**: {result['root']}",
            "",
            "### 创建的目录",
        ]
        for d in result["created_dirs"]:
            lines.append(f"- `{d}/`")
        lines.append("")
        lines.append("### 文件状态")
        for path, status in result["files"].items():
            lines.append(f"- `{path}` — {status}")
        lines.append("")
        lines.append("### Git")
        lines.append(result["git"])
        lines.append("")
        lines.append(
            "### 下一步\n"
            "1. 将原始文档放入 `raw/` 目录\n"
            "2. 对 Agent 说「摄入 raw 目录中的文档」\n"
            "3. 用 `wiki_lint` 检查 wiki 健康状态"
        )
        return "\n".join(lines)


# ── WikiLintTool ─────────────────────────────────────────────────────────────


@tool_parameters(
    tool_parameters_schema(
        root=StringSchema('Wiki 根目录路径（相对于工作区），默认 "wiki"'),
        checks=StringSchema(
            '要运行的检查，逗号分隔。可选值: all, deadlinks, orphans, index。默认 "all"',
        ),
        required=[],
    )
)
class WikiLintTool(Tool):
    """Check the health of an llm-wiki knowledge base.

    Reports dead wikilinks, orphan pages, and index consistency issues.
    """

    _scopes = {"core", "subagent"}

    name = "wiki_lint"
    description = (
        "检查 llm-wiki 知识库的健康状态。"
        "报告死链（指向不存在页面的 [[wikilink]]）、孤立页面（无入链的页面）、"
        "以及索引一致性（index.md 与实际文件的差异）。"
        "用 checks 参数选择检查范围: all（全部）, deadlinks, orphans, index。"
    )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, root: str = "wiki", checks: str = "all", **kwargs: Any) -> str:
        wiki_root = _resolve_root(root)
        wiki_dir = wiki_root / "wiki"

        if not wiki_dir.is_dir():
            return ToolResult.error(
                f"Wiki 目录不存在: {wiki_dir}。请先运行 wiki_init 初始化。"
            )

        try:
            report = run_all_lint_checks(wiki_dir, checks)
        except OSError as e:
            return ToolResult.error(f"Wiki lint 检查失败: {e}")

        return _format_lint_report(report)


# ── WikiIndexTool ────────────────────────────────────────────────────────────


@tool_parameters(
    tool_parameters_schema(
        root=StringSchema('Wiki 根目录路径（相对于工作区），默认 "wiki"'),
        required=[],
    )
)
class WikiIndexTool(Tool):
    """Rebuild index.md from the filesystem.

    Scans all pages under wiki/ and regenerates a complete index grouped by type.
    """

    _scopes = {"core", "subagent"}

    name = "wiki_index"
    description = (
        "从文件系统重建 llm-wiki 的 index.md。"
        "扫描 wiki/ 下所有页面，提取 YAML frontmatter 中的 type 字段，"
        "按类型分组生成完整的索引。在批量摄入后调用以更新导航。"
    )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, root: str = "wiki", **kwargs: Any) -> str:
        wiki_root = _resolve_root(root)
        wiki_dir = wiki_root / "wiki"

        if not wiki_dir.is_dir():
            return ToolResult.error(
                f"Wiki 目录不存在: {wiki_dir}。请先运行 wiki_init 初始化。"
            )

        try:
            result = rebuild_index(wiki_dir)
        except OSError as e:
            return ToolResult.error(f"重建索引失败: {e}")

        lines = [
            "## 索引已重建 ✅",
            "",
            f"**页面总数**: {result['total_pages']}",
            "",
            "| 类型 | 数量 |",
            "|------|------|",
        ]
        type_order = ["source", "entity", "concept", "comparison", "query"]
        for t in type_order:
            count = result["by_type"].get(t, 0)
            lines.append(f"| {t} | {count} |")
        for t, count in result["by_type"].items():
            if t not in type_order:
                lines.append(f"| {t} | {count} |")
        lines.append("")
        lines.append(f"索引文件: `{result['index_path']}`")

        return "\n".join(lines)
