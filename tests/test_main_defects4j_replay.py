import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main
from comet.config.settings import LLMConfig, Settings


class MainDefects4JReplayCommandTests(unittest.TestCase):
    def test_parse_args_supports_replay_defects4j_defaults(self) -> None:
        args = main.parse_args(
            [
                "replay-defects4j",
                "--manifest",
                "benchmark.jsonl",
                "--output-dir",
                ".artifacts/defects4j",
            ]
        )

        self.assertEqual(args.command, "replay-defects4j")
        self.assertEqual(args.manifest, "benchmark.jsonl")
        self.assertEqual(args.output_dir, ".artifacts/defects4j")
        self.assertEqual(args.checkout_mode, "none")
        self.assertFalse(args.refresh_checkouts)
        self.assertFalse(args.use_xvfb)

    def test_run_replay_defects4j_command_invokes_runner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            manifest_path = root / "manifest.jsonl"
            manifest_path.write_text("{}\n", encoding="utf-8")
            output_root = root / "out"
            settings = Settings(llm=LLMConfig(api_key="test-key"))

            args = SimpleNamespace(
                manifest=str(manifest_path),
                output_dir=str(output_root),
                checkout_mode="docker",
                defects4j_root=None,
                checkout_root=str(root / "checkouts"),
                docker_image="defects4j-local",
                max_workers=4,
                refresh_checkouts=True,
                use_xvfb=True,
                config="config.yaml",
                debug=True,
                command="replay-defects4j",
            )

            with patch.object(main, "configure_logging") as logging_mock:
                captured: dict[str, object] = {}

                def fake_runner(**kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(
                        summary_path=output_root / "summary.json",
                        per_bug_path=output_root / "per_bug.csv",
                        per_test_path=output_root / "per_test.csv",
                    )

                exit_code = main.run_replay_defects4j_command(
                    args,
                    settings_loader=lambda _: settings,
                    replay_runner=fake_runner,
                )

        self.assertEqual(exit_code, 0)
        logging_mock.assert_called_once_with(
            str((output_root / "defects4j-replay.log").resolve()), level="DEBUG"
        )
        self.assertEqual(captured["manifest_path"], manifest_path.resolve())
        self.assertEqual(captured["output_dir"], output_root.resolve())
        self.assertIs(captured["settings"], settings)
        self.assertEqual(captured["checkout_mode"], "docker")
        self.assertIsNone(captured["defects4j_root"])
        self.assertEqual(captured["checkout_root"], (root / "checkouts").resolve())
        self.assertEqual(captured["docker_image"], "defects4j-local")
        self.assertEqual(captured["max_workers"], 4)
        self.assertTrue(captured["refresh_checkouts"])
        self.assertTrue(captured["use_xvfb"])


if __name__ == "__main__":
    unittest.main()
