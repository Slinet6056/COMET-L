"""LLM 客户端封装"""

import time
import logging
from typing import Optional, List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from openai import OpenAI
from openai.types.chat import ChatCompletion
import httpx

logger = logging.getLogger(__name__)


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
    ):
        """
        初始化 LLM 客户端

        Args:
            api_key: API 密钥
            base_url: API 基础 URL
            model: 模型名称
            temperature: 温度参数
            max_tokens: 最大 token 数
            max_retries: 最大重试次数
            supports_json_mode: 是否支持 JSON 模式
            timeout: 请求超时时间（秒），默认 600 秒
        """
        # 使用简单的超时配置
        # OpenAI SDK 会自动处理超时，我们在每次请求时传入
        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,  # 禁用SDK自动重试，我们自己处理重试逻辑
        )
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.supports_json_mode = supports_json_mode
        self.timeout = timeout

        # 统计信息
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
            max_tokens: 最大 token 数（覆盖默认值）
            response_format: 响应格式（如 {"type": "json_object"}）

        Returns:
            模型响应内容
        """
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        for attempt in range(self.max_retries):
            start_time = time.time()
            try:
                kwargs = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temp,
                    "max_tokens": max_tok,
                    "stream": False,  # 明确禁用流式响应
                }

                if response_format and self.supports_json_mode:
                    kwargs["response_format"] = response_format

                logger.debug(
                    f"LLM 调用参数: model={self.model}, max_tokens={max_tok}, temperature={temp}, timeout={self.timeout}s"
                )
                logger.debug(f"开始请求 LLM，超时设置: {self.timeout}s")

                # 为每次请求创建独立的线程池执行器，避免并发场景下的共享状态问题
                def _make_request():
                    return self.client.chat.completions.create(**kwargs)

                # 使用 with 语句确保 executor 被正确清理
                with ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(_make_request)

                    try:
                        response: ChatCompletion = future.result(timeout=self.timeout)
                    except FutureTimeoutError:
                        elapsed = time.time() - start_time
                        raise httpx.TimeoutException(
                            f"请求总超时 ({self.timeout}s)，实际耗时: {elapsed:.2f}s"
                        )

                elapsed = time.time() - start_time
                logger.debug(f"LLM 请求完成，耗时: {elapsed:.2f}s")

                # 更新统计
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
                        error_msg += (
                            f", completion_tokens: {response.usage.completion_tokens}"
                        )
                    error_msg += ")"
                    raise ValueError(error_msg)

                logger.debug(
                    f"LLM 调用成功，使用 {response.usage.total_tokens if response.usage else '?'} tokens"
                )
                return content

            except httpx.TimeoutException as e:
                elapsed = time.time() - start_time
                logger.warning(
                    f"LLM 请求超时 (尝试 {attempt + 1}/{self.max_retries}): "
                    f"耗时 {elapsed:.2f}s, 错误: {e}"
                )
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"LLM 请求超时，已重试 {self.max_retries} 次: {e}"
                    )
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
            max_tokens: 最大 token 数
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
        return {
            "total_calls": self.total_calls,
            "total_tokens": self.total_tokens,
            "total_cost": self.total_cost,
            "avg_tokens_per_call": (
                self.total_tokens / self.total_calls if self.total_calls > 0 else 0
            ),
        }

    def reset_stats(self) -> None:
        """重置统计信息"""
        self.total_calls = 0
        self.total_tokens = 0
        self.total_cost = 0.0
