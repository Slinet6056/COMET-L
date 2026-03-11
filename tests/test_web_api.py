import unittest
from datetime import datetime
from io import BytesIO
import json
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from comet.agent.state import AgentState, ParallelAgentState, WorkerResult
from comet.config.settings import Settings
from comet.executor.coverage_parser import MethodCoverage
from comet.models import MutationPatch, Mutant, TestCase, TestMethod
from comet.store.database import Database
from comet.utils.log_context import log_context
from comet.web.app import app, create_app
from comet.web.log_router import RunLogRouter
from comet.web.run_service import RunLifecycleService, RunRequest
from comet.web.runtime_protocol import RuntimeEventBus, build_run_snapshot


class HealthApiTests(unittest.TestCase):
    def test_health_endpoint_returns_ok(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "activeRunId": None})


class ConfigApiTests(unittest.TestCase):
    def test_app_is_importable(self) -> None:
        self.assertEqual(app.title, "COMET-L Web API")

    def test_defaults_endpoint_returns_normalized_config(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.get("/api/config/defaults")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("config", payload)
        self.assertEqual(payload["config"]["llm"]["model"], "gpt-4")
        self.assertEqual(payload["config"]["paths"]["cache"], "./cache")

    def test_parse_valid_yaml_returns_normalized_config(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: test-key\n"
                            "  model: gpt-4o-mini\n"
                            "execution:\n"
                            "  timeout: 123\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    enabled: true\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["config"]["llm"]["api_key"], "test-key")
        self.assertEqual(payload["config"]["llm"]["model"], "gpt-4o-mini")
        self.assertEqual(payload["config"]["execution"]["timeout"], 123)
        self.assertTrue(payload["config"]["agent"]["parallel"]["enabled"])
        self.assertEqual(payload["config"]["paths"]["output"], "./output")

    def test_parse_invalid_yaml_returns_field_errors(self) -> None:
        client = TestClient(create_app(run_service=RunLifecycleService()))

        response = client.post(
            "/api/config/parse",
            files={
                "file": (
                    "config.yaml",
                    BytesIO(
                        ("llm:\n  temperature: 3.5\nexecution:\n  timeout: 0\n").encode(
                            "utf-8"
                        )
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_config")
        field_errors = payload["error"]["fieldErrors"]
        error_map = {tuple(item["path"]): item["code"] for item in field_errors}
        self.assertEqual(error_map[("llm", "api_key")], "missing")
        self.assertEqual(error_map[("llm", "temperature")], "less_than_equal")
        self.assertEqual(error_map[("execution", "timeout")], "greater_than_equal")


class SnapshotTests(unittest.TestCase):
    def test_standard_snapshot_includes_decision_reasoning(self) -> None:
        state = AgentState()
        state.current_target = {"class_name": "Calculator", "method_name": "add"}
        state.set_decision_reasoning("Need more assertions for Calculator.add")
        state.add_improvement(
            {
                "iteration": 3,
                "mutation_score_delta": 0.1,
                "coverage_delta": 0.05,
            }
        )

        snapshot = build_run_snapshot("run-001", "running", state)

        self.assertEqual(snapshot["mode"], "standard")
        self.assertEqual(
            snapshot["decisionReasoning"], "Need more assertions for Calculator.add"
        )
        self.assertEqual(
            snapshot["currentTarget"],
            {"class_name": "Calculator", "method_name": "add"},
        )
        self.assertEqual(snapshot["improvementSummary"]["count"], 1)
        self.assertEqual(
            snapshot["improvementSummary"]["latest"]["mutation_score_delta"], 0.1
        )

    def test_parallel_snapshot_includes_worker_cards(self) -> None:
        state = ParallelAgentState()
        acquired = state.acquire_target(
            "Calculator",
            "add",
            metadata={"method_coverage": 0.4, "source": "coverage"},
        )
        self.assertTrue(acquired)
        state.add_batch_result(
            [
                WorkerResult(
                    target_id="Calculator.add",
                    class_name="Calculator",
                    method_name="add",
                    success=True,
                    tests_generated=2,
                    mutants_generated=3,
                    mutants_evaluated=3,
                    mutants_killed=2,
                    local_mutation_score=2 / 3,
                    processing_time=1.5,
                )
            ]
        )

        snapshot = build_run_snapshot("run-002", "running", state)

        self.assertEqual(snapshot["mode"], "parallel")
        self.assertEqual(snapshot["currentBatch"], 1)
        self.assertEqual(snapshot["parallelStats"]["total_batches"], 1)
        self.assertEqual(len(snapshot["workerCards"]), 1)
        self.assertEqual(snapshot["workerCards"][0]["targetId"], "Calculator.add")
        self.assertEqual(len(snapshot["activeTargets"]), 1)
        self.assertEqual(snapshot["activeTargets"][0]["targetId"], "Calculator.add")
        self.assertEqual(snapshot["activeTargets"][0]["method_coverage"], 0.4)
        self.assertEqual(len(snapshot["batchResults"]), 1)
        self.assertEqual(snapshot["batchResults"][0][0]["targetId"], "Calculator.add")


class EventBusTests(unittest.TestCase):
    def test_event_bus_keeps_ordered_snapshot_events(self) -> None:
        state = AgentState()
        state.set_decision_reasoning("reasoning")
        bus = RuntimeEventBus(max_events=2)

        first = bus.publish_snapshot("run-003", "running", state)
        second = bus.publish("run.completed", runId="run-003")

        events = bus.list_events()
        self.assertEqual(
            [event["type"] for event in events], ["run.snapshot", "run.completed"]
        )
        self.assertLess(first["sequence"], second["sequence"])


class StreamingApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    default_config_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            "llm:\n  api_key: default-key\n  model: gpt-4\n",
            encoding="utf-8",
        )
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
            )
        )

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def _parse_sse(self, payload: str) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for block in payload.strip().split("\n\n"):
            if not block.strip():
                continue
            event: dict[str, Any] = {}
            for line in block.splitlines():
                key, value = line.split(": ", 1)
                if key == "data":
                    event[key] = json.loads(value)
                else:
                    event[key] = value
            events.append(event)
        return events

    def test_events_endpoint_streams_snapshot_then_ordered_terminal_events(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        bus = self.run_service.get_event_bus(run_id)
        self.run_service.mark_running(run_id)
        bus.publish("run.started", runId=run_id, projectPath="/tmp/project")
        bus.publish(
            "run.phase",
            runId=run_id,
            phase={"key": "running", "label": "Running"},
        )
        bus.publish("run.completed", runId=run_id)
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/events")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["content-type"], "text/event-stream; charset=utf-8"
        )
        events = self._parse_sse(response.text)
        self.assertGreaterEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "run.snapshot")
        self.assertEqual(events[0]["data"]["snapshot"]["status"], "completed")
        self.assertEqual(
            [event["event"] for event in events[1:]],
            ["run.started", "run.phase", "run.completed"],
        )
        self.assertEqual(events[-1]["data"]["type"], "run.completed")

    def test_log_endpoints_list_streams_and_return_bounded_entries(self) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        router = RunLogRouter(max_entries_per_stream=2)
        self.run_service._log_routers[run_id] = router

        logger = logging.getLogger("test.web.api.streaming")
        logger.setLevel(logging.INFO)
        logger.addHandler(router)
        self.addCleanup(logger.removeHandler, router)

        logger.info("main-1")
        logger.info("main-2")
        logger.info("main-3")
        with log_context("task-1"):
            logger.info("worker-1")
            logger.info("worker-2")
            logger.info("worker-3")

        summary = self.client.get(f"/api/runs/{run_id}/logs")
        self.assertEqual(summary.status_code, 200)
        summary_payload = summary.json()
        self.assertEqual(summary_payload["runId"], run_id)
        self.assertEqual(summary_payload["streams"]["taskIds"], ["main", "task-1"])
        self.assertEqual(summary_payload["streams"]["counts"], {"main": 2, "task-1": 2})
        self.assertEqual(summary_payload["streams"]["maxEntriesPerStream"], 2)

        main_logs = self.client.get(f"/api/runs/{run_id}/logs/main")
        self.assertEqual(main_logs.status_code, 200)
        self.assertEqual(
            [entry["message"] for entry in main_logs.json()["entries"]],
            ["main-2", "main-3"],
        )

        worker_logs = self.client.get(f"/api/runs/{run_id}/logs/task-1")
        self.assertEqual(worker_logs.status_code, 200)
        worker_payload = worker_logs.json()
        self.assertEqual(worker_payload["availableTaskIds"], ["main", "task-1"])
        self.assertEqual(
            [entry["message"] for entry in worker_payload["entries"]],
            ["worker-2", "worker-3"],
        )


class RunApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    non_maven_path: Path | None = None
    default_config_path: Path | None = None
    release_run: threading.Event | None = None
    run_started: threading.Event | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.non_maven_path = self.root / "not-maven"
        self.non_maven_path.mkdir()
        self.default_config_path = self.root / "config.example.yaml"
        self.default_config_path.write_text(
            "llm:\n  api_key: default-key\n  model: gpt-4\n",
            encoding="utf-8",
        )
        self.release_run = threading.Event()
        self.run_started = threading.Event()
        self.run_service = RunLifecycleService(workspace_root=self.root)

        def fake_initialize(
            config: Settings,
            bug_reports_dir: str | None = None,
            parallel_mode: bool = False,
        ) -> dict[str, object]:
            return {
                "config": config,
                "bug_reports_dir": bug_reports_dir,
                "parallel_mode": parallel_mode,
            }

        def fake_run(
            project_path: str,
            components: dict[str, object],
            resume_state: str | None = None,
        ) -> None:
            del project_path, resume_state
            assert self.run_started is not None
            assert self.release_run is not None
            self.run_started.set()
            released = self.release_run.wait(timeout=5)
            if not released:
                raise TimeoutError("run release timeout")

            config = components["config"]
            assert isinstance(config, Settings)
            state = (
                ParallelAgentState() if components["parallel_mode"] else AgentState()
            )
            state.iteration = 2
            state.llm_calls = 9
            state.budget = config.evolution.budget_llm_calls
            state.total_tests = 4
            state.total_mutants = 6
            state.killed_mutants = 5
            state.survived_mutants = 1
            state.mutation_score = 5 / 6
            state.line_coverage = 0.8
            state.branch_coverage = 0.6
            output_path = Path(config.paths.output)
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "final_state.json").write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        self.client = TestClient(
            create_app(
                run_service=self.run_service,
                default_config_path=self.default_config_path,
                system_initializer=fake_initialize,
                evolution_runner=fake_run,
            )
        )

    def tearDown(self) -> None:
        if self.release_run is not None:
            self.release_run.set()
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _wait_for_status(
        self, run_id: str, expected: str, timeout: float = 5.0
    ) -> None:
        assert self.run_service is not None
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.run_service.get_session(run_id).status == expected:
                return
            time.sleep(0.01)
        self.fail(f"run {run_id} did not reach status {expected}")

    def test_post_runs_creates_background_run_and_returns_stable_snapshot(self) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_started is not None
        assert self.run_service is not None
        assert self.release_run is not None
        response = self.client.post(
            "/api/runs",
            data={
                "projectPath": str(self.project_path),
                "maxIterations": "7",
                "budget": "42",
                "parallel": "true",
            },
            files={
                "configFile": (
                    "config.yaml",
                    BytesIO(
                        (
                            "llm:\n"
                            "  api_key: yaml-key\n"
                            "agent:\n"
                            "  parallel:\n"
                            "    enabled: false\n"
                        ).encode("utf-8")
                    ),
                    "application/x-yaml",
                )
            },
        )

        self.assertEqual(response.status_code, 201)
        created = response.json()
        self.assertEqual(created["status"], "created")
        self.assertEqual(created["mode"], "parallel")
        run_id = created["runId"]

        self.assertTrue(self.run_started.wait(timeout=5))
        self._wait_for_status(run_id, "running")

        current_response = self.client.get("/api/runs/current")
        self.assertEqual(current_response.status_code, 200)
        current_payload = current_response.json()
        self.assertEqual(current_payload["runId"], run_id)
        self.assertEqual(current_payload["status"], "running")
        self.assertEqual(current_payload["mode"], "parallel")
        self.assertEqual(current_payload["phase"]["key"], "running")
        self.assertIn("metrics", current_payload)
        self.assertIn("mutationScore", current_payload["metrics"])
        self.assertTrue(current_payload["artifacts"]["resolvedConfig"]["exists"])

        by_id_response = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(by_id_response.status_code, 200)
        by_id_payload = by_id_response.json()
        self.assertEqual(by_id_payload["runId"], run_id)
        self.assertEqual(by_id_payload["artifacts"]["log"]["exists"], True)
        self.assertNotIn("path", by_id_payload["artifacts"]["log"])

        session = self.run_service.get_session(run_id)
        resolved_config = json.loads(
            Path(session.paths["resolved_config"]).read_text(encoding="utf-8")
        )
        self.assertEqual(resolved_config["llm"]["api_key"], "yaml-key")
        self.assertEqual(resolved_config["evolution"]["max_iterations"], 7)
        self.assertEqual(resolved_config["evolution"]["budget_llm_calls"], 42)
        self.assertTrue(resolved_config["agent"]["parallel"]["enabled"])

        conflict = self.client.post(
            "/api/runs", data={"projectPath": str(self.project_path)}
        )
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "active_run_conflict")

        self.release_run.set()
        self._wait_for_status(run_id, "completed")

        completed_response = self.client.get(f"/api/runs/{run_id}")
        self.assertEqual(completed_response.status_code, 200)
        completed_payload = completed_response.json()
        self.assertEqual(completed_payload["status"], "completed")
        self.assertEqual(completed_payload["phase"]["key"], "completed")
        self.assertEqual(completed_payload["iteration"], 2)
        self.assertEqual(completed_payload["metrics"]["totalTests"], 4)

        no_current = self.client.get("/api/runs/current")
        self.assertEqual(no_current.status_code, 404)
        self.assertEqual(no_current.json()["error"]["code"], "no_active_run")

    def test_post_runs_allows_only_one_active_run_when_requests_are_simultaneous(
        self,
    ) -> None:
        assert self.client is not None
        assert self.project_path is not None
        assert self.run_service is not None
        client = self.client
        run_service = self.run_service

        original_new_run_id = run_service._new_run_id

        def delayed_new_run_id() -> str:
            time.sleep(0.05)
            return original_new_run_id()

        run_service._new_run_id = delayed_new_run_id
        self.addCleanup(setattr, run_service, "_new_run_id", original_new_run_id)

        barrier = threading.Barrier(2)
        status_codes: list[int] = []

        def post_run() -> None:
            barrier.wait(timeout=5)
            response = client.post(
                "/api/runs",
                data={"projectPath": str(self.project_path)},
            )
            status_codes.append(response.status_code)

        first = threading.Thread(target=post_run)
        second = threading.Thread(target=post_run)
        first.start()
        second.start()
        first.join(timeout=5)
        second.join(timeout=5)

        self.assertCountEqual(status_codes, [201, 409])
        self.assertEqual(len(self.run_service._sessions), 1)

    def test_post_runs_rejects_missing_project_path(self) -> None:
        assert self.client is not None
        assert self.root is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.root / "missing-project")},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_project_path")
        self.assertEqual(payload["error"]["fieldErrors"][0]["code"], "path_not_found")

    def test_post_runs_rejects_non_maven_project(self) -> None:
        assert self.client is not None
        assert self.non_maven_path is not None
        response = self.client.post(
            "/api/runs",
            data={"projectPath": str(self.non_maven_path)},
        )

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["error"]["code"], "invalid_project_path")
        self.assertEqual(
            payload["error"]["fieldErrors"][0]["code"], "not_maven_project"
        )


class ResultsApiTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None
    project_path: Path | None = None
    run_service: RunLifecycleService | None = None
    client: TestClient | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project_path = self.root / "project"
        self.project_path.mkdir()
        (self.project_path / "pom.xml").write_text("<project/>", encoding="utf-8")
        self.run_service = RunLifecycleService(workspace_root=self.root)
        self.client = TestClient(create_app(run_service=self.run_service))

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def _create_run(self) -> str:
        assert self.run_service is not None
        assert self.project_path is not None
        session = self.run_service.create_run(
            RunRequest(project_path=str(self.project_path)),
            settings_loader=lambda _config_path: Settings.model_validate(
                {"llm": {"api_key": "default-key", "model": "gpt-4"}}
            ),
        )
        return session.run_id

    def _write_completed_run_artifacts(self, run_id: str) -> None:
        assert self.run_service is not None
        session = self.run_service.get_session(run_id)

        state = AgentState()
        state.iteration = 4
        state.llm_calls = 13
        state.budget = 88
        state.total_tests = 7
        state.total_mutants = 2
        state.global_total_mutants = 5
        state.killed_mutants = 1
        state.global_killed_mutants = 4
        state.survived_mutants = 1
        state.global_survived_mutants = 1
        state.mutation_score = 0.5
        state.global_mutation_score = 0.8
        state.line_coverage = 0.9
        state.branch_coverage = 0.75
        state.current_method_coverage = 0.75
        state.current_target = {"class_name": "Calculator", "method_name": "add"}
        Path(session.paths["final_state"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["final_state"]).write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text(
            "run started\nrun completed\n", encoding="utf-8"
        )

        database = Database(session.paths["database"])
        try:
            database.save_test_case(
                TestCase(
                    id="tc-1",
                    class_name="CalculatorAddTest",
                    target_class="Calculator",
                    methods=[
                        TestMethod(
                            method_name="testAddPositive",
                            code="assertEquals(3, calculator.add(1, 2));",
                            target_method="add",
                        ),
                        TestMethod(
                            method_name="testAddNegative",
                            code="assertEquals(-1, calculator.add(1, -2));",
                            target_method="add",
                        ),
                    ],
                    compile_success=True,
                )
            )
            database.save_mutant(
                Mutant(
                    id="mut-1",
                    class_name="Calculator",
                    method_name="add",
                    patch=MutationPatch(
                        file_path="src/main/java/Calculator.java",
                        line_start=10,
                        line_end=10,
                        original_code="return a + b;",
                        mutated_code="return a - b;",
                    ),
                    status="killed",
                    killed_by=["CalculatorAddTest.testAddPositive"],
                    survived=False,
                    evaluated_at=datetime.now(),
                )
            )
            database.save_mutant(
                Mutant(
                    id="mut-2",
                    class_name="Calculator",
                    method_name="add",
                    patch=MutationPatch(
                        file_path="src/main/java/Calculator.java",
                        line_start=11,
                        line_end=11,
                        original_code="return a + b;",
                        mutated_code="return a + 0;",
                    ),
                    status="valid",
                    survived=True,
                    evaluated_at=datetime.now(),
                )
            )
            database.save_method_coverage(
                MethodCoverage(
                    class_name="Calculator",
                    method_name="add",
                    covered_lines=[10, 11, 12],
                    missed_lines=[13],
                    total_lines=4,
                    covered_branches=1,
                    missed_branches=1,
                    total_branches=2,
                    line_coverage_rate=0.75,
                    branch_coverage_rate=0.5,
                ),
                iteration=3,
            )
        finally:
            database.close()

        self.run_service.mark_completed(run_id)

    def test_results_endpoint_returns_aggregated_summary_and_artifact_metadata(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        self._write_completed_run_artifacts(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["runId"], run_id)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["summary"]["metrics"]["totalTests"], 7)
        self.assertEqual(payload["summary"]["metrics"]["mutationScore"], 0.5)
        self.assertEqual(payload["summary"]["tests"]["totalCases"], 1)
        self.assertEqual(payload["summary"]["tests"]["compiledCases"], 1)
        self.assertEqual(payload["summary"]["tests"]["totalMethods"], 2)
        self.assertEqual(payload["summary"]["tests"]["targetMethods"], 1)
        self.assertEqual(payload["summary"]["mutants"]["total"], 2)
        self.assertEqual(payload["summary"]["mutants"]["evaluated"], 2)
        self.assertEqual(payload["summary"]["mutants"]["killed"], 1)
        self.assertEqual(payload["summary"]["mutants"]["survived"], 1)
        self.assertEqual(payload["summary"]["coverage"]["latestIteration"], 3)
        self.assertEqual(payload["summary"]["coverage"]["methodsTracked"], 1)
        self.assertEqual(payload["summary"]["coverage"]["averageLineCoverage"], 0.75)
        self.assertEqual(payload["summary"]["coverage"]["averageBranchCoverage"], 0.5)
        self.assertTrue(payload["summary"]["sources"]["finalState"])
        self.assertTrue(payload["summary"]["sources"]["database"])
        self.assertTrue(payload["summary"]["sources"]["runLog"])
        self.assertEqual(
            payload["artifacts"]["finalState"]["downloadUrl"],
            f"/api/runs/{run_id}/artifacts/final-state",
        )
        self.assertEqual(
            payload["artifacts"]["runLog"]["downloadUrl"],
            f"/api/runs/{run_id}/artifacts/run-log",
        )
        self.assertNotIn("path", payload["artifacts"]["finalState"])
        self.assertNotIn("path", payload["artifacts"]["runLog"])
        self.assertGreater(payload["artifacts"]["finalState"]["sizeBytes"], 0)
        self.assertGreater(payload["artifacts"]["runLog"]["sizeBytes"], 0)

        final_state_response = self.client.get(
            f"/api/runs/{run_id}/artifacts/final-state"
        )
        self.assertEqual(final_state_response.status_code, 200)
        self.assertEqual(
            final_state_response.headers["content-type"], "application/json"
        )
        self.assertIn('"total_tests": 7', final_state_response.text)

        run_log_response = self.client.get(f"/api/runs/{run_id}/artifacts/run-log")
        self.assertEqual(run_log_response.status_code, 200)
        self.assertEqual(
            run_log_response.headers["content-type"], "text/plain; charset=utf-8"
        )
        self.assertIn("run completed", run_log_response.text)

    def test_results_endpoint_gracefully_degrades_when_database_is_missing(
        self,
    ) -> None:
        assert self.client is not None
        assert self.run_service is not None
        run_id = self._create_run()
        session = self.run_service.get_session(run_id)
        Path(session.paths["log"]).parent.mkdir(parents=True, exist_ok=True)
        Path(session.paths["log"]).write_text("run completed\n", encoding="utf-8")
        self.run_service.mark_completed(run_id)

        response = self.client.get(f"/api/runs/{run_id}/results")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["summary"]["tests"]["totalCases"], 0)
        self.assertEqual(payload["summary"]["mutants"]["total"], 0)
        self.assertEqual(payload["summary"]["coverage"]["latestIteration"], None)
        self.assertFalse(payload["summary"]["sources"]["finalState"])
        self.assertFalse(payload["summary"]["sources"]["database"])
        self.assertTrue(payload["summary"]["sources"]["runLog"])
        self.assertEqual(payload["artifacts"]["finalState"]["exists"], False)


class StaticFrontendMountTests(unittest.TestCase):
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    root: Path | None = None

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        if self.temp_dir is not None:
            self.temp_dir.cleanup()

    def test_create_app_mounts_built_frontend_without_breaking_api_routes(self) -> None:
        assert self.root is not None
        dist_path = self.root / "web" / "dist"
        dist_path.mkdir(parents=True)
        (dist_path / "index.html").write_text(
            "<html><body><div id='root'>COMET-L Web</div></body></html>",
            encoding="utf-8",
        )

        client = TestClient(
            create_app(
                run_service=RunLifecycleService(workspace_root=self.root),
                frontend_dist_path=dist_path,
            )
        )

        root_response = client.get("/")
        self.assertEqual(root_response.status_code, 200)
        self.assertIn("COMET-L Web", root_response.text)

        nested_response = client.get("/runs/run-42/results")
        self.assertEqual(nested_response.status_code, 200)
        self.assertIn("COMET-L Web", nested_response.text)

        health_response = client.get("/api/health")
        self.assertEqual(health_response.status_code, 200)
        self.assertEqual(health_response.json()["status"], "ok")


if __name__ == "__main__":
    unittest.main()
