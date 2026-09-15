"""Guard agent guidance against drift, oversized context, and broken local links.

These standard-library-only checks are included in normal unittest discovery.
They read documentation without modifying the user-owned hard-rule sections.
"""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
ROOT_DOCS = (ROOT / "AGENTS.md", ROOT / "CLAUDE.md")
DOCS = ROOT_DOCS + (ROOT / "docs" / "implemented-features.md",)
EDITABLE_HEADING = "## Development instructions\n"
MAX_ROOT_BYTES = 12 * 1024


def _heading_anchors(markdown):
    """Collect GitHub-style anchors for the ATX headings used in these guides."""
    anchors = set()
    counts = {}
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", markdown, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(f"{slug}-{count}" if count else slug)
    return anchors


class AgentDocumentationTests(unittest.TestCase):
    """Keep editable instructions usable while leaving hard rules independent."""

    def test_editable_sections_match(self):
        """Compare only the shared content after the protected hard-rule section."""
        sections = []
        for path in ROOT_DOCS:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count(EDITABLE_HEADING), 1, str(path))
            sections.append(text.split(EDITABLE_HEADING, 1)[1])
        self.assertEqual(
            sections[0], sections[1],
            "Synchronize AGENTS.md and CLAUDE.md from Development instructions onward; "
            "do not copy or alter their hard rules.",
        )

    def test_root_files_fit_context_budget(self):
        """Leave room for other instructions by capping each root guide at 12 KiB."""
        for path in ROOT_DOCS:
            with self.subTest(path=path.name):
                self.assertLessEqual(
                    len(path.read_bytes()), MAX_ROOT_BYTES,
                    f"{path.name} exceeds 12 KiB; shorten editable prose or move "
                    "details to docs/implemented-features.md.",
                )

    def test_local_links_and_section_anchors_resolve(self):
        """Validate inline local Markdown links in both root guides and the reference."""
        for source in DOCS:
            markdown = source.read_text(encoding="utf-8")
            for raw_target in re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", markdown):
                target = urlsplit(raw_target.strip().strip("<>"))
                if target.scheme or target.netloc:
                    continue
                path = (source.parent / unquote(target.path)).resolve() if target.path else source
                with self.subTest(source=source.name, target=raw_target):
                    self.assertTrue(path.exists(), f"Missing local link: {raw_target}")
                    if target.fragment:
                        self.assertEqual(path.suffix.lower(), ".md")
                        self.assertIn(
                            unquote(target.fragment),
                            _heading_anchors(path.read_text(encoding="utf-8")),
                            f"Missing heading in {path.name}: {target.fragment}",
                        )
