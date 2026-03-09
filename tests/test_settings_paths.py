import tempfile
import textwrap
import unittest
from pathlib import Path

from comet.config.settings import LLMConfig, PathsConfig, Settings


class SettingsPathsTests(unittest.TestCase):
    def test_ensure_directories_creates_runtime_directories_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            workspace = root / "workspace"
            settings = Settings(
                llm=LLMConfig(api_key="test-key"),
                paths=PathsConfig(
                    cache=str(root / "cache"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
            )

            settings.ensure_directories()

            self.assertTrue((root / "cache").is_dir())
            self.assertTrue((root / "output").is_dir())
            self.assertTrue((root / "sandbox").is_dir())
            self.assertFalse(workspace.exists())

    def test_from_yaml_ignores_legacy_workspace_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.yaml"
            _ = config_path.write_text(
                textwrap.dedent(
                    f"""
                    llm:
                      api_key: test-key

                    paths:
                      workspace: {root / "workspace"}
                      cache: {root / "cache"}
                      output: {root / "output"}
                      sandbox: {root / "sandbox"}
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            settings = Settings.from_yaml(str(config_path))
            settings.ensure_directories()

            self.assertEqual(settings.paths.cache, str(root / "cache"))
            self.assertEqual(settings.paths.output, str(root / "output"))
            self.assertEqual(settings.paths.sandbox, str(root / "sandbox"))
            self.assertFalse((root / "workspace").exists())


if __name__ == "__main__":
    _ = unittest.main()
