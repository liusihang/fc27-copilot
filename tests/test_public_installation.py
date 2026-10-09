import json
import re
import sqlite3
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fc27.mcp import SERVER_VERSION
from fc27.schema import CATALOG_SCHEMA_VERSION
from scripts.validate_catalog import TABLES


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicInstallationTest(unittest.TestCase):
    def test_bundled_catalog_is_standalone_and_contains_only_catalog_tables(self):
        path = PROJECT_ROOT / "data/catalog.sqlite"
        self.assertTrue(path.is_file())
        connection = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        try:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
            }
            self.assertEqual(tables, set(TABLES) | {"catalog_meta"})
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            metadata = dict(connection.execute("SELECT key, value FROM catalog_meta"))
            self.assertEqual(metadata["schema_version"], CATALOG_SCHEMA_VERSION)
            self.assertEqual(metadata["source"], "FUT.GG")
            for table, key in (("cards", "card_count"), ("players", "player_count")):
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                self.assertGreater(count, 0)
                self.assertEqual(int(metadata[key]), count)
        finally:
            connection.close()

    def test_license_and_component_versions_match(self):
        package = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
        manifest = json.loads((PROJECT_ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(package["license"], "MIT")
        self.assertTrue((PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n"))
        self.assertEqual(package["version"], SERVER_VERSION)
        self.assertEqual(manifest["version"], SERVER_VERSION)

    def test_installation_example_is_stdio_with_explicit_placeholder_paths(self):
        for relative in ("docs/install.md", "docs/install.zh-CN.md"):
            with self.subTest(document=relative):
                guide = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
                examples = re.findall(r"```json\n(.*?)\n```", guide, flags=re.DOTALL)
                self.assertEqual(len(examples), 1)
                server = json.loads(examples[0])["mcpServers"]["FC27"]
                self.assertEqual(server["command"], "/absolute/path/fc27-copilot/.venv/bin/python")
                self.assertEqual(server["args"], ["/absolute/path/fc27-copilot/mcp_stdio.py"])
                self.assertNotIn("url", server)
                self.assertIn("240", guide)
                self.assertIn("confirmed=true", guide)

    def test_distributed_text_does_not_contain_private_machine_or_account_ids(self):
        paths = [*PROJECT_ROOT.glob("*"), *PROJECT_ROOT.joinpath("data").glob("*")]
        for directory in ("docs", "fc27", "extension", "scripts", "tests", "web", ".github"):
            paths.extend(PROJECT_ROOT.joinpath(directory).rglob("*"))
        text_suffixes = {".md", ".py", ".js", ".json", ".html", ".css", ".yml", ".yaml", ".txt"}
        private_pattern = re.compile(
            r"/(?:Users/[A-Za-z0-9._-]+|opt/homebrew)/|192\.168\.\d+\.\d+|\b[69]\d{11}\b|"
            r"accounts/\d{8,}/|Persona\s+`\d{8,}`",
            flags=re.IGNORECASE,
        )
        for path in paths:
            if not path.is_file() or (path.suffix not in text_suffixes and path.name != ".gitignore"):
                continue
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertIsNone(private_pattern.search(path.read_text(encoding="utf-8")))

    def test_update_guides_rebuild_the_extension(self):
        for relative, heading in (("docs/install.md", "## Updating"), ("docs/install.zh-CN.md", "## 更新")):
            with self.subTest(document=relative):
                guide = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
                upgrade = guide.split(heading, 1)[1].split("\n## ", 1)[0]
                self.assertIn("git pull --ff-only", upgrade)
                self.assertIn("-m pip install -r requirements.txt", upgrade)
                self.assertIn("npm run check", upgrade)
                self.assertIn("npm run build:extension", upgrade)

    def test_public_document_links_resolve(self):
        paths = [*PROJECT_ROOT.glob("*.md"), *PROJECT_ROOT.joinpath("docs").rglob("*.md"), PROJECT_ROOT / "data/README.md"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                with self.subTest(document=path.relative_to(PROJECT_ROOT), target=target):
                    self.assertTrue((path.parent / parsed.path).is_file())


if __name__ == "__main__":
    unittest.main()
