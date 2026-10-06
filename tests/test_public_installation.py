import json
import re
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from fc27.mcp import SERVER_VERSION


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PublicInstallationTest(unittest.TestCase):
    def test_license_and_component_versions_match(self):
        package = json.loads((PROJECT_ROOT / "package.json").read_text(encoding="utf-8"))
        manifest = json.loads((PROJECT_ROOT / "extension/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(package["license"], "MIT")
        self.assertTrue((PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License\n"))
        self.assertEqual(package["version"], SERVER_VERSION)
        self.assertEqual(manifest["version"], SERVER_VERSION)

    def test_installation_example_is_stdio_with_explicit_placeholder_paths(self):
        guide = (PROJECT_ROOT / "docs/install.md").read_text(encoding="utf-8")
        examples = re.findall(r"```json\n(.*?)\n```", guide, flags=re.DOTALL)
        self.assertEqual(len(examples), 1)
        server = json.loads(examples[0])["mcpServers"]["FC27"]
        self.assertEqual(server["command"], "/absolute/path/fc27-copilot/.venv/bin/python")
        self.assertEqual(server["args"], ["/absolute/path/fc27-copilot/mcp_stdio.py"])
        self.assertNotIn("url", server)
        self.assertIn("240", guide)
        self.assertIn("confirmed=true", guide)

    def test_public_docs_do_not_contain_private_machine_or_account_ids(self):
        paths = [*PROJECT_ROOT.glob("*.md"), *PROJECT_ROOT.joinpath("docs").rglob("*.md"), PROJECT_ROOT / "data/README.md"]
        private_pattern = re.compile(
            r"/Users/|/opt/homebrew|192\.168\.\d+\.\d+|\b[69]\d{11}\b|"
            r"accounts/\d{8,}/|Persona\s+`\d{8,}`",
            flags=re.IGNORECASE,
        )
        for path in paths:
            with self.subTest(path=path.relative_to(PROJECT_ROOT)):
                self.assertIsNone(private_pattern.search(path.read_text(encoding="utf-8")))

    def test_installation_document_links_resolve(self):
        for relative in ("README.md", "README.zh-CN.md", "docs/install.md", "docs/openclaw.md", "data/README.md"):
            path = PROJECT_ROOT / relative
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                with self.subTest(document=relative, target=target):
                    self.assertTrue((path.parent / parsed.path).is_file())


if __name__ == "__main__":
    unittest.main()
