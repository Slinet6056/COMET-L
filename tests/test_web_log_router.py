import io
import logging
import tempfile
import unittest
from pathlib import Path

from comet.utils.log_context import log_context
from comet.web.log_router import RunLogRouter
from comet.web.run_service import configure_logging, reset_managed_logging


class LogRouterTests(unittest.TestCase):
    def tearDown(self) -> None:
        reset_managed_logging()

    def test_worker_task_logs_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            router = RunLogRouter(max_entries_per_stream=10)
            configure_logging(
                str(Path(tmp_dir) / "run.log"),
                console_stream=io.StringIO(),
                log_router=router,
            )

            logger = logging.getLogger("test.web.log_router")
            with log_context("Worker:Calculator.add"):
                logger.info("worker only")
            with log_context("Worker:Order.submit"):
                logger.info("other worker")

            worker_messages = [
                entry["message"] for entry in router.get_logs("Worker:Calculator.add")
            ]
            other_messages = [
                entry["message"] for entry in router.get_logs("Worker:Order.submit")
            ]

            self.assertEqual(worker_messages, ["worker only"])
            self.assertEqual(other_messages, ["other worker"])

    def test_main_logs_do_not_leak_into_worker_stream(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            router = RunLogRouter(max_entries_per_stream=10)
            configure_logging(
                str(Path(tmp_dir) / "run.log"),
                console_stream=io.StringIO(),
                log_router=router,
            )

            logger = logging.getLogger("test.web.log_router")
            logger.info("main only")
            with log_context("Worker:Calculator.add"):
                logger.info("worker only")

            main_messages = [entry["message"] for entry in router.get_logs("main")]
            worker_messages = [
                entry["message"] for entry in router.get_logs("Worker:Calculator.add")
            ]

            self.assertEqual(main_messages, ["main only"])
            self.assertEqual(worker_messages, ["worker only"])
            self.assertNotIn("main only", worker_messages)

    def test_per_task_buffers_are_bounded(self) -> None:
        router = RunLogRouter(max_entries_per_stream=2)

        logger = logging.getLogger("test.web.log_router.buffer")
        logger.setLevel(logging.INFO)
        logger.addHandler(router)
        self.addCleanup(logger.removeHandler, router)

        with log_context("Worker:Calculator.add"):
            logger.info("first")
            logger.info("second")
            logger.info("third")

        messages = [
            entry["message"] for entry in router.get_logs("Worker:Calculator.add")
        ]
        self.assertEqual(messages, ["second", "third"])

        snapshot = router.snapshot()
        stream = snapshot["byTaskId"]["Worker:Calculator.add"]
        self.assertEqual(stream["bufferedEntryCount"], 2)
        self.assertEqual(stream["totalEntryCount"], 3)
        self.assertIsNotNone(stream["firstEntryAt"])
        self.assertEqual(
            stream["lastEntryAt"],
            router.get_logs("Worker:Calculator.add")[-1]["timestamp"],
        )

    def test_snapshot_uses_stream_order_instead_of_alphabetical_task_ids(self) -> None:
        router = RunLogRouter(max_entries_per_stream=5)
        router.ensure_stream(
            "task-z", status="running", started_at="2026-01-01T00:00:01+00:00"
        )
        router.ensure_stream(
            "task-a", status="running", started_at="2026-01-01T00:00:02+00:00"
        )

        snapshot = router.snapshot()

        self.assertEqual(snapshot["taskIds"], ["main", "task-z", "task-a"])

    def test_zero_log_stream_metadata_can_be_registered_and_completed(self) -> None:
        router = RunLogRouter(max_entries_per_stream=5)
        router.ensure_stream(
            "task-1",
            status="running",
            started_at="2026-01-01T00:00:00+00:00",
        )
        router.ensure_stream(
            "task-1",
            status="completed",
            started_at="2026-01-01T00:00:00+00:00",
            ended_at="2026-01-01T00:00:05+00:00",
            completed_at="2026-01-01T00:00:05+00:00",
            duration_seconds=5.0,
        )

        snapshot = router.snapshot()
        stream = snapshot["byTaskId"]["task-1"]

        self.assertEqual(stream["status"], "completed")
        self.assertEqual(stream["startedAt"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(stream["completedAt"], "2026-01-01T00:00:05+00:00")
        self.assertEqual(stream["endedAt"], "2026-01-01T00:00:05+00:00")
        self.assertEqual(stream["durationSeconds"], 5.0)
        self.assertEqual(stream["bufferedEntryCount"], 0)
        self.assertEqual(router.get_logs("task-1"), [])


if __name__ == "__main__":
    unittest.main()
