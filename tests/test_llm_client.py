import unittest
from unittest.mock import patch

import httpx
from openai import APITimeoutError

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

    def _run_chat_and_capture_kwargs(self, client: LLMClient) -> tuple[str, dict[str, object]]:
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

        with patch.object(client.client.chat.completions, "create", side_effect=_fake_create):
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


class LLMClientTimeoutTest(unittest.TestCase):
    def test_chat_passes_timeout_to_openai_sdk(self) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="test-model",
            max_retries=1,
            timeout=3.5,
        )
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

        with patch.object(client.client.chat.completions, "create", side_effect=_fake_create):
            result = client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "ok")
        self.assertEqual(captured_kwargs["timeout"], 3.5)

    def test_chat_retries_on_openai_timeout(self) -> None:
        client = LLMClient(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="test-model",
            max_retries=2,
            timeout=1.0,
        )
        attempts = 0

        def _timeout_then_fail(**_: object) -> object:
            nonlocal attempts
            attempts += 1
            request = httpx.Request("POST", "https://example.com/v1/chat/completions")
            raise APITimeoutError(request=request)

        with (
            patch.object(client.client.chat.completions, "create", side_effect=_timeout_then_fail),
            patch("comet.llm.client.time.sleep") as mock_sleep,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(attempts, 2)
        mock_sleep.assert_called_once_with(1)
        self.assertIn("LLM 请求超时", str(ctx.exception))


class LLMClientTokenBudgetTest(unittest.TestCase):
    def _make_client(self, *, configured_budget: int) -> LLMClient:
        return LLMClient(
            api_key="test-key",
            base_url="https://example.com/v1",
            model="test-model",
            max_retries=1,
            max_tokens=configured_budget,
        )

    def _make_response(self) -> object:
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

        return _Response()

    def test_chat_reduces_outbound_max_tokens_by_prompt_token_usage(self) -> None:
        client = self._make_client(configured_budget=100)
        captured_kwargs: dict[str, object] = {}

        def _fake_create(**kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            return self._make_response()

        with (
            patch.object(LLMClient, "_estimate_prompt_tokens", return_value=30, create=True),
            patch.object(client.client.chat.completions, "create", side_effect=_fake_create),
        ):
            result = client.chat([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "ok")
        self.assertEqual(captured_kwargs["max_tokens"], 62)

    def test_chat_clamps_per_call_max_tokens_by_remaining_budget(self) -> None:
        client = self._make_client(configured_budget=100)
        captured_kwargs: dict[str, object] = {}

        def _fake_create(**kwargs: object) -> object:
            captured_kwargs.update(kwargs)
            return self._make_response()

        with (
            patch.object(LLMClient, "_estimate_prompt_tokens", return_value=30, create=True),
            patch.object(client.client.chat.completions, "create", side_effect=_fake_create),
        ):
            result = client.chat([{"role": "user", "content": "hello"}], max_tokens=90)

        self.assertEqual(result, "ok")
        self.assertEqual(captured_kwargs["max_tokens"], 62)

    def test_chat_raises_when_prompt_tokens_use_all_remaining_budget_after_headroom(self) -> None:
        client = self._make_client(configured_budget=100)

        with (
            patch.object(LLMClient, "_estimate_prompt_tokens", return_value=92, create=True),
            patch.object(client.client.chat.completions, "create") as mock_create,
        ):
            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "hello"}])

        mock_create.assert_not_called()
        self.assertIn("输出 token", str(ctx.exception))

    def test_chat_raises_when_prompt_tokens_exceed_total_budget(self) -> None:
        client = self._make_client(configured_budget=100)

        with (
            patch.object(LLMClient, "_estimate_prompt_tokens", return_value=120, create=True),
            patch.object(client.client.chat.completions, "create") as mock_create,
        ):
            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "hello"}])

        mock_create.assert_not_called()
        self.assertIn("prompt", str(ctx.exception).lower())
        self.assertIn("budget", str(ctx.exception).lower())

    def test_chat_raises_when_prompt_tokens_equal_total_budget(self) -> None:
        client = self._make_client(configured_budget=100)

        with (
            patch.object(LLMClient, "_estimate_prompt_tokens", return_value=100, create=True),
            patch.object(client.client.chat.completions, "create") as mock_create,
        ):
            with self.assertRaises(ValueError) as ctx:
                client.chat([{"role": "user", "content": "hello"}])

        mock_create.assert_not_called()
        self.assertIn("prompt", str(ctx.exception).lower())
        self.assertIn("budget", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
