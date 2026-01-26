"""
日志上下文管理模块

使用 contextvars 为多线程环境提供日志上下文支持，
使并行任务的日志可以被正确区分和追踪。
"""

import contextvars
import logging
from contextlib import contextmanager
from typing import Generator

# 任务上下文变量，存储当前任务的标识信息
task_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "task_context", default=""
)


class ContextFilter(logging.Filter):
    """
    日志过滤器，将 contextvars 中的上下文注入日志记录。

    在日志记录中添加 task_id 字段，可在日志格式中使用 %(task_id)s 引用。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """
        为日志记录添加任务上下文信息。

        Args:
            record: 日志记录对象

        Returns:
            始终返回 True，表示允许记录通过
        """
        record.task_id = task_context.get() or "main"
        return True


def set_task_context(context: str) -> contextvars.Token[str]:
    """
    设置当前任务的日志上下文。

    Args:
        context: 上下文标识字符串，如 "W1:Calculator.add"

    Returns:
        Token 对象，用于后续重置上下文
    """
    return task_context.set(context)


def reset_task_context(token: contextvars.Token[str]) -> None:
    """
    重置任务上下文到之前的值。

    Args:
        token: 由 set_task_context 返回的 Token 对象
    """
    task_context.reset(token)


def get_task_context() -> str:
    """
    获取当前任务的日志上下文。

    Returns:
        当前上下文字符串，如果未设置则返回空字符串
    """
    return task_context.get()


@contextmanager
def log_context(context: str) -> Generator[None, None, None]:
    """
    上下文管理器，用于在代码块中设置日志上下文。

    使用示例:
        with log_context("W1:Calculator.add"):
            logger.info("处理中...")  # 日志会包含 [W1:Calculator.add]

    Args:
        context: 上下文标识字符串

    Yields:
        None
    """
    token = set_task_context(context)
    try:
        yield
    finally:
        reset_task_context(token)
