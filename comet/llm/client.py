"""LLM 客户端封装"""

import logging
import queue
import threading
import time
from math import ceil
from typing import Any, Dict, List, Optional, cast

import httpx
import tiktoken
from openai import APITimeoutError, OpenAI
from openai.types.chat import ChatCompletion

logger = logging.getLogger(__name__)

_TOKEN_HEADROOM = 8


class LLMClient:
    """LLM 客户端 - 封装 OpenAI 兼容 API"""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        max_retries: int = 3,
        supports_json_mode: bool = True,
        timeout: float = 600.0,
        reasoning_effort: Optional[str] = None,
        reasoning_enabled: Optional[bool] = None,
        verbosity: Optional[str] = None,
    ):
        """
        初始化 LLM 客户端

        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            model: 模型名称
            temperature: 温度参数
            max_tokens: 单次请求的总 token 预算
            max_retries: 最大重试次数
            supports_json_mode: 是否支持 JSON 模式
            timeout: 请求超时时间（秒），默认 600 秒
            reasoning_effort: 推理努力程度，可选值: 'none', 'low', 'medium', 'high'
            reasoning_enabled: 是否启用推理，None 表示不下发该配置
            verbosity: 响应详细程度，可选值: 'low', 'medium', 'high'
        """
        self.api_key = api_key
        self.base_url = base_url
        self.client = self._build_client()
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.supports_json_mode = supports_json_mode
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.reasoning_enabled = reasoning_enabled
        self.verbosity = verbosity

        # 统计信息
        self._client_lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self.total_calls = 0
        self.total_tokens = 0
        self.total_cost = 0.0

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        调用聊天 API

        Args:
            messages: 消息列表
            temperature: 温度参数（覆盖默认值）
            max_tokens: 输出 token 上限（覆盖默认值，但仍受总预算约束）
            response_format: 响应格式（如 {"type": "json_object"}）

        Returns:
            模型响应内容
        """
        temp = temperature if temperature is not None else self.temperature
        configured_budget = self.max_tokens
        prompt_tokens = self._estimate_prompt_tokens(messages)

        if prompt_tokens >= configured_budget:
            raise ValueError(
                f"提示词 token 数已达到或超过预算上限: prompt={prompt_tokens}, budget={configured_budget}"
            )

        remaining_budget = configured_budget - prompt_tokens
        available_completion_budget = remaining_budget - _TOKEN_HEADROOM

        if available_completion_budget < 1:
            raise ValueError("提示词已接近预算上限，扣除请求开销后没有可用的输出 token 预算")

        output_cap = max_tokens if max_tokens is not None else available_completion_budget
        outbound_max_tokens = min(output_cap, available_completion_budget)

        for attempt in range(self.max_retries):
            start_time = time.time()
            try:
                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": outbound_max_tokens,
                    "stream": False,  # 明确禁用流式响应
                    "timeout": self.timeout,
                }

                if response_format and self.supports_json_mode:
                    kwargs["response_format"] = response_format

                # 添加 reasoning effort 配置（Chat Completions API 使用顶级参数）
                if self.reasoning_effort is not None:
                    kwargs["reasoning_effort"] = self.reasoning_effort

                if self.reasoning_enabled is not None:
                    kwargs["extra_body"] = {"reasoning": {"enabled": self.reasoning_enabled}}

                # 添加 verbosity 配置（如果模型支持）
                if self.verbosity is not None:
                    kwargs["verbosity"] = self.verbosity

                logger.debug(
                    f"LLM 调用参数: model={self.model}, max_tokens={outbound_max_tokens}, "
                    f"temperature={temp}, timeout={self.timeout}s, "
                    f"prompt_tokens={prompt_tokens}, remaining_budget={remaining_budget}, "
                    f"available_completion_budget={available_completion_budget}"
                )
                logger.debug(f"开始请求 LLM，超时设置: {self.timeout}s")

                response = self._create_with_hard_timeout(kwargs)

                elapsed = time.time() - start_time
                logger.debug(f"LLM 请求完成，耗时: {elapsed:.2f}s")

                with self._stats_lock:
                    self.total_calls += 1
                    if response.usage:
                        self.total_tokens += response.usage.total_tokens

                content = response.choices[0].message.content
                finish_reason = response.choices[0].finish_reason

                # 记录详细的响应信息
                if response.usage:
                    logger.debug(
                        f"LLM 响应: finish_reason={finish_reason}, "
                        f"prompt_tokens={response.usage.prompt_tokens}, "
                        f"completion_tokens={response.usage.completion_tokens}, "
                        f"total_tokens={response.usage.total_tokens}, "
                        f"content_length={len(content) if content else 0}"
                    )

                if content is None or content == "":
                    error_msg = f"模型返回空内容 (finish_reason: {finish_reason}"
                    if response.usage:
                        error_msg += f", completion_tokens: {response.usage.completion_tokens}"
                    error_msg += ")"
                    raise ValueError(error_msg)

                logger.debug(
                    f"LLM 调用成功，使用 {response.usage.total_tokens if response.usage else '?'} tokens"
                )
                return content

            except (APITimeoutError, httpx.TimeoutException) as e:
                elapsed = time.time() - start_time
                logger.warning(
                    f"LLM 请求超时 (尝试 {attempt + 1}/{self.max_retries}): "
                    f"耗时 {elapsed:.2f}s, 错误: {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"LLM 请求超时，已重试 {self.max_retries} 次: {e}")
                time.sleep(2**attempt)  # 指数退避

            except Exception as e:
                elapsed = time.time() - start_time
                logger.warning(
                    f"LLM 调用失败 (尝试 {attempt + 1}/{self.max_retries}): "
                    f"耗时 {elapsed:.2f}s, 错误: {e}"
                )
                if attempt == self.max_retries - 1:
                    raise
                time.sleep(2**attempt)  # 指数退避

        raise RuntimeError("LLM 调用失败，已达最大重试次数")

    def _build_client(self) -> OpenAI:
        return OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=0,
        )

    def _reset_client(self) -> None:
        with self._client_lock:
            old_client = self.client
            self.client = self._build_client()

        close = getattr(old_client, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                logger.warning(f"关闭 LLM 客户端失败，将继续重建客户端: {exc}")

    def _create_with_hard_timeout(self, kwargs: Dict[str, Any]) -> ChatCompletion:
        result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)

        with self._client_lock:
            client = self.client

        def _worker() -> None:
            try:
                response = client.chat.completions.create(**kwargs)
                result_queue.put(("response", response))
            except Exception as exc:
                result_queue.put(("exception", exc))

        worker = threading.Thread(target=_worker, daemon=True)
        worker.start()
        worker.join(timeout=self.timeout)

        if worker.is_alive():
            self._reset_client()
            raise self._hard_timeout_error()

        try:
            result_type, payload = result_queue.get_nowait()
        except queue.Empty as exc:
            raise RuntimeError("LLM 请求线程已结束，但未返回结果") from exc

        if result_type == "exception":
            if isinstance(payload, Exception):
                raise payload
            raise RuntimeError("LLM 请求线程返回了非异常错误对象")

        return cast(ChatCompletion, payload)

    def _hard_timeout_error(self) -> httpx.ReadTimeout:
        return httpx.ReadTimeout(f"LLM 请求超过硬超时限制 {self.timeout}s")

    def _estimate_prompt_tokens(self, messages: List[Dict[str, str]]) -> int:
        text_parts: List[str] = []
        for message in messages:
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            text_parts.append(f"{role}:{content}")

        prompt_text = "\n".join(text_parts)

        try:
            encoding = tiktoken.encoding_for_model(self.model)
            return len(encoding.encode(prompt_text))
        except KeyError:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                return len(encoding.encode(prompt_text))
            except Exception:
                return max(1, ceil(len(prompt_text) / 2))
        except Exception:
            return max(1, ceil(len(prompt_text) / 2))

    def chat_with_system(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        使用系统提示词和用户提示词调用 API

        Args:
            system_prompt: 系统提示词
            user_prompt: 用户提示词
            temperature: 温度参数
            max_tokens: 输出 token 上限
            response_format: 响应格式

        Returns:
            模型响应内容
        """
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self.chat(messages, temperature, max_tokens, response_format)

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        with self._stats_lock:
            total_calls = self.total_calls
            total_tokens = self.total_tokens
            total_cost = self.total_cost

        return {
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "avg_tokens_per_call": (total_tokens / total_calls if total_calls > 0 else 0),
        }

    def get_total_calls(self) -> int:
        with self._stats_lock:
            return self.total_calls

    def reset_stats(self) -> None:
        """重置统计信息"""
        with self._stats_lock:
            self.total_calls = 0
            self.total_tokens = 0
            self.total_cost = 0.0
