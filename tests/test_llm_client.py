import unittest
from unittest.mock import patch

from comet.llm.client import LLMClient


class LLMClientReasoningEnabledTest(unittest.TestCase):
    def _make_client(self, reasoning_enabled: bool | None) -> LLMClient:
        return LLMClient(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="test-model",
            max_retries=1,
            reasoning_enabled=reasoning_enabled,
        )

    def _run_chat_and_capture_kwargs(
        self, client: LLMClient
    ) -> tuple[str, dict[str, object]]:
        captured_kwargs: dict[str, object] = {}

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1
            total_tokens = 2

        class _Message:
            content = "ok"

        class _Choice:
            message = _Message()
            finish_reason = "stop"

        class _Response:
            usage = _Usage()
            choices = [_Choice()]

        def _fake_create(**kwargs: object) -> _Response:
            captured_kwargs.update(kwargs)
            return _Response()

        with patch.object(
            client.client.chat.completions, "create", side_effect=_fake_create
        ):
            content = client.chat([{"role": "user", "content": "hello"}])
        return content, captured_kwargs

    def test_reasoning_enabled_none_does_not_send_extra_body(self) -> None:
        client = self._make_client(None)

        content, captured_kwargs = self._run_chat_and_capture_kwargs(client)

        self.assertEqual(content, "ok")
        self.assertNotIn("extra_body", captured_kwargs)

    def test_reasoning_enabled_true_sends_extra_body(self) -> None:
        client = self._make_client(True)

        content, captured_kwargs = self._run_chat_and_capture_kwargs(client)

        self.assertEqual(content, "ok")
        self.assertEqual(
            captured_kwargs["extra_body"],
            {"reasoning": {"enabled": True}},
        )

    def test_reasoning_enabled_false_sends_extra_body(self) -> None:
        client = self._make_client(False)

        content, captured_kwargs = self._run_chat_and_capture_kwargs(client)

        self.assertEqual(content, "ok")
        self.assertEqual(
            captured_kwargs["extra_body"],
            {"reasoning": {"enabled": False}},
        )


if __name__ == "__main__":
    unittest.main()
