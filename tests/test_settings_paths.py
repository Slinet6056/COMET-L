import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from comet.config.settings import LLMConfig, Settings


class SettingsPathsTests(unittest.TestCase):
    def test_to_dict_includes_default_evolution_mutation_enabled(self) -> None:
        settings = Settings(llm=LLMConfig(api_key="test-key"))

        self.assertTrue(settings.to_dict()["evolution"]["mutation_enabled"])
        self.assertFalse(settings.to_dict()["preprocessing"]["exit_after_preprocessing"])

    def test_ensure_directories_creates_fixed_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            current_dir = Path.cwd()
            root = Path(tmp_dir)
            try:
                os.chdir(root)
                settings = Settings(llm=LLMConfig(api_key="test-key"))

                settings.ensure_directories()

                self.assertTrue((root / "state").is_dir())
                self.assertTrue((root / "output").is_dir())
                self.assertTrue((root / "sandbox").is_dir())
            finally:
                os.chdir(current_dir)

    def test_resolve_paths_use_fixed_runtime_locations(self) -> None:
        settings = Settings(llm=LLMConfig(api_key="test-key"))

        self.assertEqual(settings.resolve_state_root(), Path("./state"))
        self.assertEqual(settings.resolve_output_root(), Path("./output"))
        self.assertEqual(settings.resolve_sandbox_root(), Path("./sandbox"))
        self.assertEqual(settings.resolve_database_path(), Path("./state/comet.db"))
        self.assertEqual(settings.resolve_knowledge_database_path(), Path("./state/knowledge.db"))
        self.assertEqual(settings.resolve_vector_store_path(), Path("./state/chromadb"))
        self.assertEqual(
            settings.resolve_embedding_cache_path(),
            Path("./state/chromadb/embedding_cache"),
        )

    def test_from_yaml_ignores_github_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text(
                (
                    "llm:\n"
                    "  api_key: test-key\n"
                    "github:\n"
                    "  oauth_client_id: yaml-client-id\n"
                    "  oauth_client_secret: yaml-client-secret\n"
                    "  managed_clone_root: /tmp/yaml-managed-root\n"
                ),
                encoding="utf-8",
            )

            settings = Settings.from_yaml(str(config_path))

        self.assertIsNone(settings.github.oauth_client_id)
        self.assertIsNone(settings.github.oauth_client_secret)
        self.assertEqual(settings.github.managed_clone_root, "./sandbox/github-managed")

    def test_from_yaml_uses_github_env_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.yaml"
            config_path.write_text(
                ("llm:\n  api_key: test-key\ngithub:\n  oauth_client_id: yaml-client-id\n"),
                encoding="utf-8",
            )

            with patch.dict(
                "os.environ",
                {
                    "COMET_GITHUB_OAUTH_CLIENT_ID": "env-client-id",
                    "COMET_GITHUB_OAUTH_CLIENT_SECRET": "env-client-secret",
                    "COMET_GITHUB_MANAGED_CLONE_ROOT": "/tmp/env-managed-root",
                },
                clear=False,
            ):
                settings = Settings.from_yaml(str(config_path))

        self.assertEqual(settings.github.oauth_client_id, "env-client-id")
        self.assertEqual(settings.github.oauth_client_secret, "env-client-secret")
        self.assertEqual(settings.github.managed_clone_root, "/tmp/env-managed-root")


if __name__ == "__main__":
    unittest.main()
