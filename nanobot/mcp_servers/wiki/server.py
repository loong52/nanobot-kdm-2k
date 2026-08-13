"""MCP server entry point for llm-wiki.

Run with::

    python -m nanobot.mcp_servers.wiki

This starts a stdio MCP server that exposes wiki file-system operations as tools.
Configure in nanobot's ``config.json``:

.. code-block:: json

    {
      "tools": {
        "mcp_servers": {
          "llm-wiki": {
            "type": "stdio",
            "command": "python",
            "args": ["-m", "nanobot.mcp_servers.wiki"],
            "enabled_tools": ["*"]
          }
        }
      }
    }
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from nanobot.mcp_servers.wiki.core import (
    create_wiki_structure,
    rebuild_index,
    run_all_lint_checks,
)

mcp = FastMCP("llm-wiki")


# ── helpers ──────────────────────────────────────────────────────────────────


def _resolve_root(raw: str) -> Path:
    """Resolve a wiki root path, defaulting to ``./wiki`` in the CWD."""
    root = Path(raw.strip() or "wiki").resolve()
    return root


def _format_lint_report(report) -> str:
    """Format a LintReport as a human-readable string."""
    if report.healthy:
        return "✅ Wiki health check passed — no issues found."

    lines: list[str] = [
        "## Wiki Health Report",
        "",
        "| Check | Issues |",
        "|-------|--------|",
        f"| Dead links | {report.dead_links} |",
        f"| Orphan pages | {report.orphans} |",
        f"| Missing from index | {report.index_missing} |",
        f"| Stale index entries | {report.index_extra} |",
        "",
        f"**{report.total} total issue(s)**",
        "",
    ]

    kind_labels = {
        "dead_link": "🔗 Dead Links",
        "orphan": "👻 Orphan Pages",
        "index_missing": "📋 Missing from Index",
        "index_extra": "📋 Stale Index Entries",
    }

    for kind, label in kind_labels.items():
        items = [i for i in report.issues if i.kind == kind]
        if not items:
            continue
        lines.append(f"### {label} ({len(items)})")
        for issue in items:
            loc = f":{issue.line}" if issue.line else ""
            lines.append(f"- `{issue.file}{loc}` — {issue.detail}")
        lines.append("")

    return "\n".join(lines)


# ── tools ────────────────────────────────────────────────────────────────────


@mcp.tool()
def wiki_init(root: str = "wiki") -> str:
    """Initialize an llm-wiki knowledge base directory structure.

    Creates the three-layer architecture (raw/ → wiki/ → schema.md),
    generates initial dashboard and index, and initializes a git repo.
    """
    wiki_root = _resolve_root(root)
    result = create_wiki_structure(wiki_root)

    lines = [
        "## Wiki Initialized ✅",
        "",
        f"**Location**: {result['root']}",
        "",
        "### Directories created",
    ]
    for d in result["created_dirs"]:
        lines.append(f"- `{d}/`")
    lines.append("")
    lines.append("### File status")
    for path, status in result["files"].items():
        lines.append(f"- `{path}` — {status}")
    lines.append("")
    lines.append(f"### Git\n{result['git']}")
    lines.append("")
    lines.append(
        "### Next steps\n"
        "1. Place source documents in the `raw/` directory\n"
        "2. Ask your agent to ingest the documents\n"
        "3. Run `wiki_lint` to check wiki health"
    )
    return "\n".join(lines)


@mcp.tool()
def wiki_lint(root: str = "wiki", checks: str = "all") -> str:
    """Check the health of an llm-wiki knowledge base.

    Args:
        root: Wiki root directory path.
        checks: Comma-separated checks — 'all', 'deadlinks', 'orphans', 'index'.
    """
    wiki_root = _resolve_root(root)
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return f"Error: wiki directory not found at {wiki_dir}. Run wiki_init first."
    report = run_all_lint_checks(wiki_dir, checks)
    return _format_lint_report(report)


@mcp.tool()
def wiki_index(root: str = "wiki") -> str:
    """Rebuild index.md from the current wiki pages on disk.

    Scans all pages under wiki/ and regenerates a complete index grouped by type.
    """
    wiki_root = _resolve_root(root)
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return f"Error: wiki directory not found at {wiki_dir}. Run wiki_init first."
    result = rebuild_index(wiki_dir)

    lines = [
        "## Index Rebuilt ✅",
        "",
        f"**Total pages**: {result['total_pages']}",
        "",
        "| Type | Count |",
        "|------|-------|",
    ]
    for t, count in sorted(result["by_type"].items()):
        lines.append(f"| {t} | {count} |")
    lines.append("")
    lines.append(f"Index file: `{result['index_path']}`")
    return "\n".join(lines)


@mcp.tool()
def wiki_list_pages(root: str = "wiki", page_type: str = "") -> str:
    """List all wiki pages, optionally filtered by type.

    Args:
        root: Wiki root directory path.
        page_type: Filter by page type (source, entity, concept, comparison, query).
                   Leave empty to list all pages.
    """
    from nanobot.mcp_servers.wiki.core import collect_pages

    wiki_root = _resolve_root(root)
    wiki_dir = wiki_root / "wiki"
    if not wiki_dir.is_dir():
        return f"Error: wiki directory not found at {wiki_dir}. Run wiki_init first."

    pages = collect_pages(wiki_dir)
    if page_type:
        pages = [p for p in pages if p.page_type == page_type]

    if not pages:
        return "(no pages found)"

    lines = [f"## Wiki Pages ({len(pages)})", ""]
    for p in pages:
        lines.append(f"- [[{p.rel_path}]] — {p.title} ({p.page_type})")
    return "\n".join(lines)


@mcp.tool()
def wiki_get_page(root: str, path: str) -> str:
    """Read a single wiki page by its relative path.

    Args:
        root: Wiki root directory path.
        path: Page path relative to wiki/ (e.g. 'entities/foo', 'sources/bar').
    """
    wiki_root = _resolve_root(root)
    wiki_dir = wiki_root / "wiki"

    clean = path.strip().removesuffix(".md")
    full = wiki_dir / f"{clean}.md"
    if not full.exists():
        return f"Error: page not found: {clean}.md"

    content = full.read_text(encoding="utf-8")
    return content


def main() -> None:
    """Entry point for ``python -m nanobot.mcp_servers.wiki``."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
