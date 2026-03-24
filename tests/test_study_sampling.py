import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from comet.web.study_sampling import (
    ClassMappingRecord,
    MethodRecord,
    discover_cold_start_methods,
    freeze_sampled_methods,
    sample_cold_start_methods,
)


class _FakeJavaExecutor:
    methods_by_file: dict[str, list[MethodRecord]]

    def __init__(self, methods_by_file: dict[str, list[MethodRecord]]) -> None:
        self.methods_by_file = methods_by_file

    def get_public_methods(self, file_path: str) -> list[MethodRecord]:
        return self.methods_by_file.get(file_path, [])


class _FakeDatabase:
    mappings: list[ClassMappingRecord]
    file_map: dict[str, str]

    def __init__(self, mappings: list[ClassMappingRecord]) -> None:
        self.mappings = mappings
        self.file_map = {mapping["simple_name"]: mapping["file_path"] for mapping in mappings}

    def get_all_class_mappings(self) -> list[ClassMappingRecord]:
        return self.mappings

    def get_class_file_path(self, class_name: str) -> str | None:
        return self.file_map.get(class_name)


class StudySamplingTest(unittest.TestCase):
    def test_discover_cold_start_methods_only_reads_src_main_java(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_dir = Path(tmp_dir) / "src" / "main" / "java" / "pkg"
            evosuite_dir = Path(tmp_dir) / ".evosuite" / "best-tests" / "pkg"
            source_dir.mkdir(parents=True, exist_ok=True)
            evosuite_dir.mkdir(parents=True, exist_ok=True)

            main_file_path = source_dir / "Alpha.java"
            ignored_file_path = evosuite_dir / "Alpha_ESTest.java"
            _ = main_file_path.write_text("public class Alpha {}\n", encoding="utf-8")
            _ = ignored_file_path.write_text("public class Alpha_ESTest {}\n", encoding="utf-8")

            executor = _FakeJavaExecutor(
                cast(
                    dict[str, list[MethodRecord]],
                    {
                        str(main_file_path): [
                            {
                                "className": "Alpha",
                                "name": "run",
                                "signature": "public void run()",
                                "range": {"begin": 1, "end": 3},
                            }
                        ],
                        str(ignored_file_path): [
                            {
                                "className": "Alpha_ESTest",
                                "name": "test00",
                                "signature": "public void test00()",
                                "range": {"begin": 1, "end": 3},
                            }
                        ],
                    },
                )
            )

            discovered = discover_cold_start_methods(
                project_path=tmp_dir,
                java_executor=executor,
                db=None,
                min_method_lines=1,
            )

            self.assertEqual([item.class_name for item in discovered], ["Alpha"])
            self.assertEqual([item.method_name for item in discovered], ["run"])

    def test_discover_cold_start_methods_excludes_interface_methods(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_dir = f"{tmp_dir}/src/main/java/pkg"
            interface_file_path = f"{source_dir}/Worker.java"
            class_file_path = f"{source_dir}/WorkerImpl.java"
            Path(source_dir).mkdir(parents=True, exist_ok=True)
            _ = Path(interface_file_path).write_text(
                "public interface Worker {\n    void execute();\n}\n",
                encoding="utf-8",
            )
            _ = Path(class_file_path).write_text(
                "public class WorkerImpl {\n    public void execute() {}\n}\n",
                encoding="utf-8",
            )
            db = _FakeDatabase(
                [
                    ClassMappingRecord(simple_name="Worker", file_path=interface_file_path),
                    ClassMappingRecord(simple_name="WorkerImpl", file_path=class_file_path),
                ]
            )
            executor = _FakeJavaExecutor(
                cast(
                    dict[str, list[MethodRecord]],
                    {
                        interface_file_path: [
                            {
                                "className": "Worker",
                                "name": "execute",
                                "signature": "public abstract void execute()",
                                "range": {"begin": 1, "end": 2},
                            }
                        ],
                        class_file_path: [
                            {
                                "className": "WorkerImpl",
                                "name": "execute",
                                "signature": "public void execute()",
                                "range": {"begin": 1, "end": 3},
                            }
                        ],
                    },
                )
            )

            discovered = discover_cold_start_methods(
                project_path=tmp_dir,
                java_executor=executor,
                db=db,
                min_method_lines=1,
            )

            self.assertEqual([item.class_name for item in discovered], ["WorkerImpl"])
            self.assertEqual([item.method_name for item in discovered], ["execute"])

    def test_sampling_is_deterministic_with_seed(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_dir = f"{tmp_dir}/src/main/java/pkg"
            file_path = f"{source_dir}/Alpha.java"
            Path(source_dir).mkdir(parents=True, exist_ok=True)
            _ = Path(file_path).write_text("class Alpha {}", encoding="utf-8")
            db = _FakeDatabase([ClassMappingRecord(simple_name="Alpha", file_path=file_path)])
            executor = _FakeJavaExecutor(
                cast(
                    dict[str, list[MethodRecord]],
                    {
                        file_path: [
                            {
                                "className": "Alpha",
                                "name": f"method{index:02d}",
                                "signature": f"public void method{index:02d}()",
                                "range": {"begin": 1, "end": 4},
                            }
                            for index in range(15)
                        ]
                        + [
                            {
                                "className": "OtherClass",
                                "name": "ignored",
                                "signature": "public void ignored()",
                                "range": {"begin": 1, "end": 4},
                            },
                            {
                                "className": "Alpha",
                                "name": "tooShort",
                                "signature": "public void tooShort()",
                                "range": {"begin": 10, "end": 10},
                            },
                        ]
                    },
                )
            )

            discovered = discover_cold_start_methods(
                project_path=tmp_dir,
                java_executor=executor,
                db=db,
                min_method_lines=2,
            )

            first_sample = sample_cold_start_methods(discovered, sample_size=12, seed=42)
            second_sample = sample_cold_start_methods(discovered, sample_size=12, seed=42)

            self.assertEqual(
                [item.target_id for item in first_sample],
                [item.target_id for item in second_sample],
            )
            self.assertEqual([item.order for item in first_sample], list(range(12)))
            self.assertEqual(len(first_sample), 12)
            self.assertTrue(all(item.method_name != "tooShort" for item in discovered))
            self.assertTrue(all(item.method_name != "ignored" for item in discovered))

    def test_sampling_uses_available_methods_when_under_limit(self) -> None:
        with TemporaryDirectory() as tmp_dir:
            source_dir = f"{tmp_dir}/src/main/java/pkg"
            file_path = f"{source_dir}/Beta.java"
            Path(source_dir).mkdir(parents=True, exist_ok=True)
            _ = Path(file_path).write_text("class Beta {}", encoding="utf-8")
            db = _FakeDatabase([ClassMappingRecord(simple_name="Beta", file_path=file_path)])
            executor = _FakeJavaExecutor(
                cast(
                    dict[str, list[MethodRecord]],
                    {
                        file_path: [
                            {
                                "className": "Beta",
                                "name": "alpha",
                                "signature": "public void alpha()",
                                "range": {"begin": 1, "end": 3},
                            },
                            {
                                "className": "Beta",
                                "name": "beta",
                                "signature": "public void beta()",
                                "range": {"begin": 5, "end": 7},
                            },
                        ]
                    },
                )
            )

            discovered = discover_cold_start_methods(
                project_path=tmp_dir,
                java_executor=executor,
                db=db,
                min_method_lines=2,
            )
            sampled = sample_cold_start_methods(discovered, sample_size=12, seed=42)

            self.assertEqual([item.method_name for item in sampled], ["alpha", "beta"])
            self.assertEqual([item.order for item in sampled], [0, 1])

            output_path = freeze_sampled_methods(tmp_dir, sampled)
            payload = cast(
                list[dict[str, object]], json.loads(output_path.read_text(encoding="utf-8"))
            )
            self.assertEqual(output_path.name, "sampled_methods.json")
            self.assertEqual(payload[0]["class_name"], "Beta")
            self.assertEqual(payload[1]["order"], 1)


if __name__ == "__main__":
    _ = unittest.main()
