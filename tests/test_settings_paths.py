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
                    state=str(root / "state"),
                    output=str(root / "output"),
                    sandbox=str(root / "sandbox"),
                ),
            )

            settings.ensure_directories()

            self.assertTrue((root / "state").is_dir())
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
                      state: {root / "state"}
                      output: {root / "output"}
                      sandbox: {root / "sandbox"}
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            settings = Settings.from_yaml(str(config_path))
            settings.ensure_directories()

            self.assertEqual(settings.paths.state, str(root / "state"))
            self.assertEqual(settings.paths.output, str(root / "output"))
            self.assertEqual(settings.paths.sandbox, str(root / "sandbox"))
            self.assertFalse((root / "workspace").exists())

    def test_paths_drive_database_and_vector_store_locations(self) -> None:
        settings = Settings(
            llm=LLMConfig(api_key="test-key"),
            paths=PathsConfig(
                state="/tmp/comet-state",
                output="/tmp/comet-output",
                sandbox="/tmp/comet-sandbox",
            ),
        )

        self.assertEqual(settings.resolve_database_path(), Path("/tmp/comet-state/comet.db"))
        self.assertEqual(
            settings.resolve_knowledge_database_path(),
            Path("/tmp/comet-state/knowledge.db"),
        )
        self.assertEqual(settings.resolve_vector_store_path(), Path("/tmp/comet-state/chromadb"))
        self.assertEqual(
            settings.resolve_embedding_cache_path(),
            Path("/tmp/comet-state/chromadb/embedding_cache"),
        )

    def test_from_yaml_rejects_legacy_cache_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config_path = root / "config.yaml"
            _ = config_path.write_text(
                textwrap.dedent(
                    f"""
                    llm:
                      api_key: test-key

                    paths:
                      cache: {root / "cache"}

                    knowledge:
                      vector_db:
                        type: chromadb
                        persist_directory: {root / "legacy-chromadb"}
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "paths.state"):
                Settings.from_yaml(str(config_path))


if __name__ == "__main__":
    _ = unittest.main()
