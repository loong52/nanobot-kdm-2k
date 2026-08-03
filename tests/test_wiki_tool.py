"""Tests for llm-wiki core logic and Tool classes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from nanobot.mcp_servers.wiki.core import (
    PageInfo,
    _page_rel_path,
    _resolve_wikilink_target,
    check_dead_links,
    check_index_consistency,
    check_orphans,
    collect_pages,
    create_wiki_structure,
    extract_wikilinks,
    generate_dashboard_md,
    generate_index_md,
    generate_schema_md,
    parse_frontmatter,
    rebuild_index,
    run_all_lint_checks,
)

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_wiki() -> Path:
    """Create a temporary wiki with the standard structure and a few test pages."""
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        create_wiki_structure(root)

        wiki_dir = root / "wiki"
        # A source page (links to entity only, NOT to concept — so concept is orphan)
        (wiki_dir / "sources" / "test-paper.md").write_text(
            "---\n"
            "source: raw/test-paper.pdf\n"
            "ingested: 2026-08-02\n"
            "type: source\n"
            "---\n"
            "\n"
            "# 来源：Test Paper\n"
            "\n"
            "## 摘要\n"
            "A test paper about something.\n"
            "\n"
            "## 关键声明\n"
            "- [!source] Original fact\n"
            "- [!analysis] LLM analysis\n"
            "\n"
            "## 相关\n"
            "- [[entities/test-author]]\n",
            encoding="utf-8",
        )

        # An entity page
        (wiki_dir / "entities" / "test-author.md").write_text(
            "---\n"
            "type: entity\n"
            "---\n"
            "\n"
            "# Test Author\n"
            "\n"
            "## 概述\n"
            "Author of the test paper.\n"
            "\n"
            "## 相关\n"
            "- [[sources/test-paper]]\n",
            encoding="utf-8",
        )

        # A concept page (orphan — no one links to it)
        (wiki_dir / "concepts" / "test-method.md").write_text(
            "---\n"
            "type: concept\n"
            "---\n"
            "\n"
            "# Test Method\n"
            "\n"
            "## 概述\n"
            "A methodology.\n"
            "\n"
            "## 相关\n"
            "- [[sources/test-paper]]\n",
            encoding="utf-8",
        )

        # Rebuild index so it's consistent
        rebuild_index(wiki_dir)

        yield root


# ── core: frontmatter / wikilinks ────────────────────────────────────────────


class TestParseFrontmatter:
    def test_empty(self):
        assert parse_frontmatter("# No frontmatter") == {}

    def test_basic(self):
        fm = parse_frontmatter("---\ntype: source\ntitle: Hello\n---\n\n# Body")
        assert fm == {"type": "source", "title": "Hello"}

    def test_no_frontmatter(self):
        assert parse_frontmatter("Just some text") == {}


class TestExtractWikilinks:
    def test_single(self):
        assert extract_wikilinks("See [[entities/foo]] for more.") == ["entities/foo"]

    def test_multiple(self):
        links = extract_wikilinks("- [[entities/a]]\n- [[concepts/b]]\n- [[entities/a]]")
        assert links == ["entities/a", "concepts/b"]  # deduplicated

    def test_none(self):
        assert extract_wikilinks("No links here.") == []


# ── core: structure generation ───────────────────────────────────────────────


class TestGenerateSchemaMd:
    def test_has_key_sections(self):
        text = generate_schema_md()
        assert "## 页面类型" in text
        assert "## 链接规范" in text
        assert "## 信任标注体系" in text
        assert "[!source]" in text
        assert "[!analysis]" in text


class TestGenerateDashboardMd:
    def test_has_title(self):
        text = generate_dashboard_md("My Wiki")
        assert "# My Wiki" in text
        assert "仪表盘" in text


class TestGenerateIndexMd:
    def test_groups_by_type(self):
        pages = [
            PageInfo(rel_path="entities/a", title="Entity A", page_type="entity"),
            PageInfo(rel_path="sources/b", title="Source B", page_type="source"),
            PageInfo(rel_path="entities/c", title="Entity C", page_type="entity"),
        ]
        text = generate_index_md(pages)
        assert "[[entities/a]]" in text
        assert "[[entities/c]]" in text
        assert "[[sources/b]]" in text


# ── core: create_wiki_structure ──────────────────────────────────────────────


class TestCreateWikiStructure:
    def test_creates_dirs(self, tmp_path):
        root = tmp_path / "test-wiki"
        create_wiki_structure(root)
        assert (root / "raw").is_dir()
        assert (root / "wiki").is_dir()
        assert (root / "wiki" / "sources").is_dir()
        assert (root / "wiki" / "entities").is_dir()
        assert (root / "wiki" / "concepts").is_dir()
        assert (root / "wiki" / "comparisons").is_dir()
        assert (root / "wiki" / "queries").is_dir()

    def test_creates_files(self, tmp_path):
        root = tmp_path / "test-wiki"
        create_wiki_structure(root)
        assert (root / "schema.md").exists()
        assert (root / "wiki" / "dashboard.md").exists()
        assert (root / "wiki" / "index.md").exists()
        assert (root / "wiki" / "debates.md").exists()

    def test_idempotent(self, tmp_path):
        """Re-initializing should not fail or delete content."""
        root = tmp_path / "test-wiki"
        create_wiki_structure(root)
        # Add a custom file
        (root / "wiki" / "custom.md").write_text("custom")
        create_wiki_structure(root)
        assert (root / "wiki" / "custom.md").exists()
        assert (root / "wiki" / "custom.md").read_text() == "custom"


# ── core: collect_pages ──────────────────────────────────────────────────────


class TestCollectPages:
    def test_excludes_meta_pages(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        pages = collect_pages(wiki_dir)
        rel_paths = {p.rel_path for p in pages}
        assert "index" not in rel_paths
        assert "dashboard" not in rel_paths
        assert "debates" not in rel_paths

    def test_includes_content_pages(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        pages = collect_pages(wiki_dir)
        rel_paths = {p.rel_path for p in pages}
        assert "sources/test-paper" in rel_paths
        assert "entities/test-author" in rel_paths
        assert "concepts/test-method" in rel_paths

    def test_extracts_type_and_title(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        pages = collect_pages(wiki_dir)
        by_rel = {p.rel_path: p for p in pages}
        assert by_rel["sources/test-paper"].page_type == "source"
        assert by_rel["entities/test-author"].page_type == "entity"
        assert by_rel["concepts/test-method"].page_type == "concept"


# ── core: lint — dead links ──────────────────────────────────────────────────


class TestDeadLinks:
    def test_no_dead_links(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        issues = check_dead_links(wiki_dir)
        assert len(issues) == 0

    def test_finds_dead_link(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        # Add a dead link to the source page
        page = wiki_dir / "sources" / "test-paper.md"
        content = page.read_text(encoding="utf-8") + "\n- [[entities/nonexistent]]\n"
        page.write_text(content, encoding="utf-8")
        issues = check_dead_links(wiki_dir)
        assert len(issues) >= 1
        dead = [i for i in issues if i.kind == "dead_link"]
        assert any("nonexistent" in d.detail for d in dead)


# ── core: lint — orphans ─────────────────────────────────────────────────────


class TestOrphans:
    def test_finds_orphan(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        issues = check_orphans(wiki_dir)
        # test-method is an orphan — no one links to it
        orphan_files = [i.file for i in issues]
        assert "concepts/test-method.md" in orphan_files

    def test_linked_pages_not_orphans(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        issues = check_orphans(wiki_dir)
        orphan_files = [i.file for i in issues]
        assert "entities/test-author.md" not in orphan_files  # linked from test-paper
        assert "sources/test-paper.md" not in orphan_files  # linked from test-author, test-method


# ── core: lint — index consistency ───────────────────────────────────────────


class TestIndexConsistency:
    def test_consistent_index(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        # rebuild_index was called in the fixture, so index should be consistent
        issues = check_index_consistency(wiki_dir)
        assert len(issues) == 0

    def test_missing_from_index(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        # Add a page that isn't in the index
        (wiki_dir / "entities" / "undiscovered.md").write_text(
            "---\ntype: entity\n---\n\n# Undiscovered\n", encoding="utf-8"
        )
        issues = check_index_consistency(wiki_dir)
        missing = [i for i in issues if i.kind == "index_missing"]
        assert any("undiscovered" in m.detail for m in missing)

    def test_stale_index_entry(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        # Manually write a stale entry into index.md
        index = wiki_dir / "index.md"
        index.write_text(
            index.read_text(encoding="utf-8") + "\n- [[entities/stale-page]]\n",
            encoding="utf-8",
        )
        issues = check_index_consistency(wiki_dir)
        extra = [i for i in issues if i.kind == "index_extra"]
        assert any("stale-page" in e.detail for e in extra)


# ── core: run_all_lint_checks ────────────────────────────────────────────────


class TestRunAllLintChecks:
    def test_filter_by_check_type(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        # Add a dead link
        page = wiki_dir / "sources" / "test-paper.md"
        page.write_text(
            page.read_text(encoding="utf-8") + "\n- [[entities/nonexistent]]\n",
            encoding="utf-8",
        )
        # Run only orphans check
        report = run_all_lint_checks(wiki_dir, checks="orphans")
        assert report.dead_links == 0  # not checked
        assert report.orphans > 0

    def test_all_runs_everything(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        report = run_all_lint_checks(wiki_dir, checks="all")
        assert isinstance(report.orphans, int)
        assert isinstance(report.dead_links, int)


# ── core: rebuild_index ──────────────────────────────────────────────────────


class TestRebuildIndex:
    def test_counts_pages(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        result = rebuild_index(wiki_dir)
        assert result["total_pages"] == 3
        assert result["by_type"]["source"] == 1
        assert result["by_type"]["entity"] == 1
        assert result["by_type"]["concept"] == 1

    def test_index_file_updated(self, tmp_wiki):
        wiki_dir = tmp_wiki / "wiki"
        rebuild_index(wiki_dir)
        content = (wiki_dir / "index.md").read_text(encoding="utf-8")
        assert "[[sources/test-paper]]" in content
        assert "[[entities/test-author]]" in content
        assert "[[concepts/test-method]]" in content


# ── helpers ──────────────────────────────────────────────────────────────────


class TestPageRelPath:
    def test_simple(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True)
        f = wiki_dir / "entities" / "foo.md"
        f.parent.mkdir(parents=True)
        f.write_text("")
        assert _page_rel_path(f, wiki_dir) == "entities/foo"


class TestResolveWikilinkTarget:
    def test_resolves(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "entities").mkdir()
        (wiki_dir / "entities" / "foo.md").write_text("")
        result = _resolve_wikilink_target("entities/foo", wiki_dir)
        assert result is not None

    def test_returns_none_for_missing(self, tmp_path):
        wiki_dir = tmp_path / "wiki"
        wiki_dir.mkdir(parents=True)
        result = _resolve_wikilink_target("entities/nope", wiki_dir)
        assert result is None
