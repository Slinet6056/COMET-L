import subprocess
import tempfile
import unittest
from pathlib import Path

from typing_extensions import override

from comet.generators.static_guard import StaticGuard
from comet.models import Mutant, MutationPatch


class RecordingStaticGuard(StaticGuard):
    _javac_result: subprocess.CompletedProcess[str]
    _maven_result: bool
    classpath_resolve_calls: int
    maven_compile_calls: int
    observed_original_during_maven: str | None
    original_source_file: Path | None
    last_maven_project_root: Path | None

    def __init__(
        self,
        javac_result: subprocess.CompletedProcess[str],
        maven_result: bool = False,
    ) -> None:
        super().__init__(java_runtime_jar="/tmp/nonexistent.jar")
        self._javac_result = javac_result
        self._maven_result = maven_result
        self.classpath_resolve_calls = 0
        self.maven_compile_calls = 0
        self.observed_original_during_maven = None
        self.original_source_file = None
        self.last_maven_project_root = None

    @override
    def _resolve_maven_classpath(self, project_root: Path) -> str | None:
        _ = project_root
        self.classpath_resolve_calls += 1
        return "/deps/error_prone_annotations.jar"

    @override
    def _run_javac(
        self, file_path: Path, classpath: str | None
    ) -> subprocess.CompletedProcess[str]:
        _ = (file_path, classpath)
        return self._javac_result

    @override
    def _compile_project(self, project_root: Path) -> bool:
        self.last_maven_project_root = project_root
        self.maven_compile_calls += 1
        if self.original_source_file is not None:
            self.observed_original_during_maven = self.original_source_file.read_text(
                encoding="utf-8"
            )
        return self._maven_result


class StaticGuardHybridValidationTests(unittest.TestCase):
    def _create_project(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temp_dir = tempfile.TemporaryDirectory()
        project_root = Path(temp_dir.name)
        (project_root / "src" / "main" / "java" / "com" / "example").mkdir(parents=True)
        (project_root / "target" / "classes").mkdir(parents=True)
        _ = (project_root / "pom.xml").write_text("<project />\n", encoding="utf-8")
        source_file = project_root / "src" / "main" / "java" / "com" / "example" / "Foo.java"
        _ = source_file.write_text(
            (
                "package com.example;\n\n"
                "public class Foo {\n"
                "    public int value() {\n"
                "        return 1;\n"
                "    }\n"
                "}\n"
            ),
            encoding="utf-8",
        )
        return temp_dir, project_root, source_file

    def _make_mutant(self, mutated_code: str) -> Mutant:
        return Mutant(
            id="mutant-1",
            class_name="Foo",
            method_name="value",
            patch=MutationPatch(
                file_path="src/main/java/com/example/Foo.java",
                line_start=5,
                line_end=5,
                original_code="        return 1;\n",
                mutated_code=mutated_code,
            ),
        )

    def test_retryable_javac_failure_uses_maven_fallback(self) -> None:
        temp_dir, project_root, source_file = self._create_project()
        self.addCleanup(temp_dir.cleanup)
        original_content = source_file.read_text(encoding="utf-8")
        guard = RecordingStaticGuard(
            javac_result=subprocess.CompletedProcess(
                args=["javac"],
                returncode=1,
                stdout="",
                stderr="package com.google.errorprone.annotations does not exist",
            ),
            maven_result=True,
        )
        guard.original_source_file = source_file

        mutant = self._make_mutant("        return 2;")

        result = guard.validate_mutant(mutant, str(source_file))

        self.assertTrue(result)
        self.assertEqual(mutant.status, "valid")
        self.assertIsNone(mutant.compile_error)
        self.assertEqual(guard.maven_compile_calls, 1)
        self.assertIsNotNone(guard.last_maven_project_root)
        self.assertNotEqual(guard.last_maven_project_root, project_root)
        self.assertEqual(guard.observed_original_during_maven, original_content)
        self.assertEqual(source_file.read_text(encoding="utf-8"), original_content)

    def test_syntax_error_does_not_use_maven_fallback(self) -> None:
        temp_dir, _, source_file = self._create_project()
        self.addCleanup(temp_dir.cleanup)
        guard = RecordingStaticGuard(
            javac_result=subprocess.CompletedProcess(
                args=["javac"],
                returncode=1,
                stdout="",
                stderr="error: ';' expected",
            ),
            maven_result=True,
        )

        mutant = self._make_mutant("        return ;")

        result = guard.validate_mutant(mutant, str(source_file))

        self.assertFalse(result)
        self.assertEqual(mutant.status, "invalid")
        self.assertEqual(mutant.compile_error, "error: ';' expected")
        self.assertEqual(guard.maven_compile_calls, 0)

    def test_maven_classpath_is_cached_per_project_root(self) -> None:
        temp_dir, _, source_file = self._create_project()
        self.addCleanup(temp_dir.cleanup)
        guard = RecordingStaticGuard(
            javac_result=subprocess.CompletedProcess(
                args=["javac"],
                returncode=0,
                stdout="",
                stderr="",
            )
        )

        first_mutant = self._make_mutant("        return 2;")
        second_mutant = self._make_mutant("        return 3;")
        second_mutant.id = "mutant-2"

        first_result = guard.validate_mutant(first_mutant, str(source_file))
        second_result = guard.validate_mutant(second_mutant, str(source_file))

        self.assertTrue(first_result)
        self.assertTrue(second_result)
        self.assertEqual(guard.classpath_resolve_calls, 1)
