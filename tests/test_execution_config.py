import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from comet.config.settings import ExecutionConfig, Settings


class ExecutionConfigTests(unittest.TestCase):
    def test_evolution_mutation_enabled_defaults_to_true(self) -> None:
        settings = Settings.model_validate({"llm": {"api_key": "test-key"}})

        self.assertTrue(settings.evolution.mutation_enabled)

    def test_evolution_mutation_enabled_preserves_explicit_false(self) -> None:
        settings = Settings.model_validate(
            {"llm": {"api_key": "test-key"}, "evolution": {"mutation_enabled": False}}
        )

        self.assertFalse(settings.evolution.mutation_enabled)

    def test_evolution_mutation_enabled_rejects_non_boolean_values(self) -> None:
        with self.assertRaises(ValidationError) as context:
            Settings.model_validate(
                {"llm": {"api_key": "test-key"}, "evolution": {"mutation_enabled": "false"}}
            )

        self.assertIn("evolution.mutation_enabled", str(context.exception))

    def test_defaults_use_system_commands(self) -> None:
        config = ExecutionConfig()

        runtime_env = config.build_runtime_subprocess_env(base_env={})
        target_env = config.build_target_subprocess_env(base_env={})

        self.assertEqual(config.resolve_runtime_java_cmd(), "java")
        self.assertEqual(config.resolve_target_java_cmd(), "java")
        self.assertEqual(config.resolve_target_javac_cmd(), "javac")
        self.assertEqual(config.resolve_mvn_cmd(), "mvn")
        self.assertNotIn("JAVA_HOME", runtime_env)
        self.assertNotIn("JAVA_HOME", target_env)
        self.assertNotIn("MAVEN_HOME", runtime_env)
        self.assertNotIn("MAVEN_HOME", target_env)

    def test_runtime_and_target_java_home_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_home = Path(tmp_dir) / "runtime-java-home"
            runtime_bin = runtime_home / "bin"
            runtime_bin.mkdir(parents=True)
            (runtime_bin / "java").write_text("", encoding="utf-8")

            target_home = Path(tmp_dir) / "target-java-home"
            target_bin = target_home / "bin"
            target_bin.mkdir(parents=True)
            (target_bin / "java").write_text("", encoding="utf-8")
            (target_bin / "javac").write_text("", encoding="utf-8")

            config = ExecutionConfig(
                runtime_java_home=str(runtime_home),
                target_java_home=str(target_home),
            )
            runtime_env = config.build_runtime_subprocess_env(base_env={"PATH": "/usr/bin"})
            target_env = config.build_target_subprocess_env(base_env={"PATH": "/usr/bin"})

            self.assertEqual(config.resolve_runtime_java_cmd(), str(runtime_bin / "java"))
            self.assertEqual(config.resolve_target_java_cmd(), str(target_bin / "java"))
            self.assertEqual(config.resolve_target_javac_cmd(), str(target_bin / "javac"))
            self.assertEqual(runtime_env["JAVA_HOME"], str(runtime_home.resolve()))
            self.assertEqual(target_env["JAVA_HOME"], str(target_home.resolve()))
            self.assertEqual(runtime_env["PATH"], f"{runtime_bin.resolve()}:/usr/bin")
            self.assertEqual(target_env["PATH"], f"{target_bin.resolve()}:/usr/bin")

    def test_runtime_and_target_can_be_set_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime_home = Path(tmp_dir) / "runtime-java-home"
            runtime_bin = runtime_home / "bin"
            runtime_bin.mkdir(parents=True)
            (runtime_bin / "java").write_text("", encoding="utf-8")

            target_home = Path(tmp_dir) / "target-java-home"
            target_bin = target_home / "bin"
            target_bin.mkdir(parents=True)
            (target_bin / "java").write_text("", encoding="utf-8")
            (target_bin / "javac").write_text("", encoding="utf-8")

            runtime_only = ExecutionConfig(runtime_java_home=str(runtime_home))
            target_only = ExecutionConfig(target_java_home=str(target_home))

            self.assertEqual(runtime_only.resolve_runtime_java_cmd(), str(runtime_bin / "java"))
            self.assertEqual(runtime_only.resolve_target_java_cmd(), "java")
            self.assertEqual(runtime_only.resolve_target_javac_cmd(), "javac")

            self.assertEqual(target_only.resolve_runtime_java_cmd(), "java")
            self.assertEqual(target_only.resolve_target_java_cmd(), str(target_bin / "java"))
            self.assertEqual(target_only.resolve_target_javac_cmd(), str(target_bin / "javac"))

    def test_maven_home_updates_env_and_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            maven_home = Path(tmp_dir) / "maven-home"
            bin_dir = maven_home / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "mvn").write_text("", encoding="utf-8")

            config = ExecutionConfig(maven_home=str(maven_home))
            env = config.build_subprocess_env(base_env={"PATH": "/usr/bin"})

            self.assertEqual(config.resolve_mvn_cmd(), str(bin_dir / "mvn"))
            self.assertEqual(env["MAVEN_HOME"], str(maven_home.resolve()))
            self.assertEqual(env["M2_HOME"], str(maven_home.resolve()))
            self.assertEqual(env["PATH"], f"{bin_dir.resolve()}:/usr/bin")

    def test_invalid_java_home_raises_error(self) -> None:
        config = ExecutionConfig(target_java_home="/path/that/does/not/exist")

        with self.assertRaisesRegex(ValueError, "TARGET_JAVA_HOME"):
            config.resolve_target_java_cmd()

    def test_selected_java_version_maps_to_registry_target_java_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            selected_home = Path(tmp_dir) / "jdk-17"
            selected_bin = selected_home / "bin"
            selected_bin.mkdir(parents=True)
            (selected_bin / "java").write_text("", encoding="utf-8")
            (selected_bin / "javac").write_text("", encoding="utf-8")

            config = ExecutionConfig(
                selected_java_version="17",
                java_version_registry={"17": str(selected_home)},
            )

            target_env = config.build_target_subprocess_env(base_env={"PATH": "/usr/bin"})
            self.assertEqual(config.get_target_java_home(), str(selected_home.resolve()))
            self.assertEqual(config.resolve_target_java_cmd(), str(selected_bin / "java"))
            self.assertEqual(config.resolve_target_javac_cmd(), str(selected_bin / "javac"))
            self.assertEqual(target_env["JAVA_HOME"], str(selected_home.resolve()))
            self.assertEqual(target_env["PATH"], f"{selected_bin.resolve()}:/usr/bin")

    def test_selected_java_version_rejects_unsupported_version(self) -> None:
        with self.assertRaisesRegex(ValidationError, "不支持的 Java 版本: 99"):
            _ = ExecutionConfig(selected_java_version="99")


if __name__ == "__main__":
    unittest.main()
