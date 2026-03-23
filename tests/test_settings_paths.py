import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
