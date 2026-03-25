import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from comet.executor.coverage_parser import MethodCoverage
from comet.utils.method_keys import build_method_key
from comet.web.study_sampling import (
    ClassMappingRecord,
    MethodRecord,
    collect_partially_covered_target_ids,
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
    coverage_map: dict[tuple[str, str, str | None], MethodCoverage]

    def __init__(
        self,
        mappings: list[ClassMappingRecord],
        coverage_map: dict[tuple[str, str, str | None], MethodCoverage] | None = None,
    ) -> None:
        self.mappings = mappings
        self.file_map = {mapping["simple_name"]: mapping["file_path"] for mapping in mappings}
        self.coverage_map = coverage_map or {}

    def get_all_class_mappings(self) -> list[ClassMappingRecord]:
        return self.mappings

    def get_class_file_path(self, class_name: str) -> str | None:
        return self.file_map.get(class_name)

    def get_method_coverage(
        self,
        class_name: str,
        method_name: str,
        method_signature: str | None = None,
    ) -> MethodCoverage | None:
        return self.coverage_map.get((class_name, method_name, method_signature))


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

    def test_collect_partially_covered_target_ids_only_returns_open_interval_coverage(self) -> None:
        partial_method = build_method_key("Alpha", "partial", "public void partial()")
        zero_method = build_method_key("Alpha", "zero", "public void zero()")
        full_method = build_method_key("Alpha", "full", "public void full()")
        discovered = [
            _build_sampled_method("Alpha", "partial", "public void partial()"),
            _build_sampled_method("Alpha", "zero", "public void zero()"),
            _build_sampled_method("Alpha", "full", "public void full()"),
            _build_sampled_method("Alpha", "missing", "public void missing()"),
        ]
        db = _FakeDatabase(
            mappings=[],
            coverage_map={
                ("Alpha", "partial", "public void partial()"): _build_coverage(
                    "Alpha", "partial", "public void partial()", 0.4
                ),
                ("Alpha", "zero", "public void zero()"): _build_coverage(
                    "Alpha", "zero", "public void zero()", 0.0
                ),
                ("Alpha", "full", "public void full()"): _build_coverage(
                    "Alpha", "full", "public void full()", 1.0
                ),
            },
        )

        preferred_target_ids = collect_partially_covered_target_ids(discovered, db)

        self.assertEqual(preferred_target_ids, {partial_method})
        self.assertNotIn(zero_method, preferred_target_ids)
        self.assertNotIn(full_method, preferred_target_ids)

    def test_sampling_prioritizes_partially_covered_methods_before_fallback(self) -> None:
        discovered = [
            _build_sampled_method("Alpha", "method00", "public void method00()"),
            _build_sampled_method("Alpha", "method01", "public void method01()"),
            _build_sampled_method("Alpha", "method02", "public void method02()"),
            _build_sampled_method("Alpha", "method03", "public void method03()"),
            _build_sampled_method("Alpha", "method04", "public void method04()"),
        ]
        preferred_target_ids = {
            discovered[1].target_id,
            discovered[3].target_id,
        }

        sampled = sample_cold_start_methods(
            discovered,
            sample_size=3,
            seed=42,
            preferred_target_ids=preferred_target_ids,
        )

        self.assertEqual(
            [item.target_id for item in sampled[:2]],
            [discovered[1].target_id, discovered[3].target_id],
        )
        self.assertEqual(len(sampled), 3)
        self.assertEqual([item.order for item in sampled], [0, 1, 2])


def _build_sampled_method(
    class_name: str,
    method_name: str,
    method_signature: str,
):
    from comet.web.study_protocol import StudySampledMethodSchema

    return StudySampledMethodSchema(
        target_id=build_method_key(class_name, method_name, method_signature),
        class_name=class_name,
        method_name=method_name,
        method_signature=method_signature,
        order=0,
    )


def _build_coverage(
    class_name: str,
    method_name: str,
    method_signature: str,
    line_coverage_rate: float,
) -> MethodCoverage:
    return MethodCoverage(
        class_name=class_name,
        method_name=method_name,
        method_signature=method_signature,
        covered_lines=[1] if line_coverage_rate > 0 else [],
        missed_lines=[] if line_coverage_rate >= 1.0 else [2],
        total_lines=2,
        covered_branches=0,
        missed_branches=0,
        total_branches=0,
        line_coverage_rate=line_coverage_rate,
        branch_coverage_rate=0.0,
    )


if __name__ == "__main__":
    _ = unittest.main()
