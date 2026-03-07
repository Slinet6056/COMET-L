"""Embedding 服务模块"""

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import List, Optional, Dict, Any

from openai import OpenAI

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Embedding 服务 - 可配置的 Embedding API 客户端"""

    def __init__(
        self,
        base_url: str = "https://api.openai.com/v1",
        api_key: str = "",
        model: str = "text-embedding-3-small",
        batch_size: int = 100,
        cache_dir: Optional[str] = None,
    ):
        """
        初始化 Embedding 服务

        Args:
            base_url: API 基础 URL
            api_key: API 密钥
            model: Embedding 模型名称
            batch_size: 批量 embedding 的大小
            cache_dir: 缓存目录，为 None 则不缓存
        """
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.batch_size = batch_size
        self.cache_dir = Path(cache_dir) if cache_dir else None

        # 初始化 OpenAI 客户端
        self.client = OpenAI(base_url=base_url, api_key=api_key)

        # 内存缓存
        self._cache: Dict[str, List[float]] = {}
        self._cache_lock = threading.RLock()

        # 确保缓存目录存在
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._load_cache()

    def _get_cache_key(self, text: str) -> str:
        """生成缓存键"""
        content = f"{self.model}:{text}"
        return hashlib.md5(content.encode()).hexdigest()

    def _load_cache(self) -> None:
        """从磁盘加载缓存"""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "embedding_cache.json"
        if cache_file.exists():
            try:
                with self._cache_lock:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        self._cache = json.load(f)
                logger.info(f"从缓存加载了 {len(self._cache)} 个 embedding")
            except Exception as e:
                logger.warning(f"加载 embedding 缓存失败: {e}")
                with self._cache_lock:
                    self._cache = {}
                corrupt_file = (
                    self.cache_dir / f"embedding_cache.corrupt-{int(time.time())}.json"
                )
                try:
                    cache_file.replace(corrupt_file)
                    logger.warning(f"已隔离损坏的 embedding 缓存文件: {corrupt_file}")
                except Exception as rename_error:
                    logger.warning(f"隔离损坏的 embedding 缓存文件失败: {rename_error}")

    def _save_cache(self) -> None:
        """保存缓存到磁盘"""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / "embedding_cache.json"
        temp_file = self.cache_dir / "embedding_cache.json.tmp"
        try:
            with self._cache_lock:
                cache_copy = dict(self._cache)
                with open(temp_file, "w", encoding="utf-8") as f:
                    json.dump(cache_copy, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temp_file, cache_file)
        except Exception as e:
            logger.warning(f"保存 embedding 缓存失败: {e}")
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    def embed(self, text: str) -> List[float]:
        """
        获取单个文本的 embedding

        Args:
            text: 输入文本

        Returns:
            embedding 向量
        """
        # 检查缓存
        cache_key = self._get_cache_key(text)
        with self._cache_lock:
            cached_embedding = self._cache.get(cache_key)
        if cached_embedding is not None:
            return cached_embedding

        # 调用 API
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            embedding = response.data[0].embedding

            # 更新缓存
            with self._cache_lock:
                self._cache[cache_key] = embedding
            self._save_cache()

            return embedding
        except Exception as e:
            logger.warning(f"获取 embedding 失败: {e}")
            raise

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        批量获取 embedding

        Args:
            texts: 文本列表

        Returns:
            embedding 向量列表
        """
        if not texts:
            return []

        results: List[Optional[List[float]]] = [None] * len(texts)
        texts_to_embed: List[tuple[int, str]] = []  # (index, text)

        # 检查缓存
        with self._cache_lock:
            for i, text in enumerate(texts):
                cache_key = self._get_cache_key(text)
                if cache_key in self._cache:
                    results[i] = self._cache[cache_key]
                else:
                    texts_to_embed.append((i, text))

        if not texts_to_embed:
            return [r for r in results if r is not None]

        # 分批处理
        cache_updated = False
        for batch_start in range(0, len(texts_to_embed), self.batch_size):
            batch = texts_to_embed[batch_start : batch_start + self.batch_size]
            batch_texts = [t[1] for t in batch]

            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch_texts,
                )

                # 处理响应
                for j, embedding_data in enumerate(response.data):
                    original_idx = batch[j][0]
                    original_text = batch[j][1]
                    embedding = embedding_data.embedding

                    results[original_idx] = embedding

                    # 更新缓存
                    cache_key = self._get_cache_key(original_text)
                    with self._cache_lock:
                        self._cache[cache_key] = embedding
                    cache_updated = True

            except Exception as e:
                logger.warning(f"批量获取 embedding 失败: {e}")
                raise

        # 保存缓存
        if cache_updated:
            self._save_cache()

        return [r for r in results if r is not None]

    def get_embedding_dimension(self) -> int:
        """
        获取 embedding 维度

        Returns:
            embedding 维度
        """
        # 使用一个简单的测试文本获取维度
        test_embedding = self.embed("test")
        return len(test_embedding)

    @classmethod
    def from_config(
        cls,
        embedding_config: Any,
        llm_api_key: Optional[str] = None,
        cache_dir: Optional[str] = None,
    ) -> "EmbeddingService":
        """
        从配置创建 EmbeddingService

        Args:
            embedding_config: EmbeddingConfig 对象
            llm_api_key: LLM API 密钥（如果 embedding_config.api_key 为空则使用此密钥）
            cache_dir: 缓存目录

        Returns:
            EmbeddingService 实例
        """
        api_key = embedding_config.api_key or llm_api_key
        if not api_key:
            raise ValueError("Embedding API 密钥未配置")

        return cls(
            base_url=embedding_config.base_url,
            api_key=api_key,
            model=embedding_config.model,
            batch_size=embedding_config.batch_size,
            cache_dir=cache_dir,
        )
