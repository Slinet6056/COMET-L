"""配置管理模块"""

import os
from pathlib import Path
from typing import Optional, Dict, Any
import yaml
from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    """LLM 配置"""
    base_url: str = Field(default="https://api.openai.com/v1", description="API 基础 URL")
    api_key: str = Field(description="API 密钥")
    model: str = Field(default="gpt-4", description="模型名称")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=4096, ge=1, description="最大 token 数")


class ExecutionConfig(BaseModel):
    """执行配置"""
    timeout: int = Field(default=300, ge=1, description="超时时间（秒）")
    test_timeout: int = Field(default=30, ge=1, description="测试执行超时时间（秒）")
    coverage_timeout: int = Field(default=300, ge=1, description="覆盖率收集超时时间（秒）")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    parallel_jobs: int = Field(default=4, ge=1, description="并行任务数")
    maven_home: Optional[str] = Field(default=None, description="Maven 安装路径")


class PathsConfig(BaseModel):
    """路径配置"""
    workspace: str = Field(default="./workspace", description="工作目录")
    cache: str = Field(default="./cache", description="缓存目录")
    output: str = Field(default="./output", description="输出目录")
    sandbox: str = Field(default="./sandbox", description="沙箱目录")


class EvolutionConfig(BaseModel):
    """进化配置"""
    max_iterations: int = Field(default=10, ge=1, description="最大迭代次数")
    min_improvement_threshold: float = Field(
        default=0.01, ge=0.0, le=1.0, description="最小改进阈值"
    )
    budget_llm_calls: int = Field(default=1000, ge=1, description="LLM 调用预算")
    stop_on_no_improvement_rounds: int = Field(
        default=3, ge=1, description="无改进时停止的轮数"
    )

    # 优秀水平阈值（用于提前停止）
    excellent_mutation_score: float = Field(
        default=0.95, ge=0.0, le=1.0, description="优秀变异分数阈值"
    )
    excellent_line_coverage: float = Field(
        default=0.90, ge=0.0, le=1.0, description="优秀行覆盖率阈值"
    )
    excellent_branch_coverage: float = Field(
        default=0.85, ge=0.0, le=1.0, description="优秀分支覆盖率阈值"
    )


class KnowledgeConfig(BaseModel):
    """知识库配置"""
    enable_dynamic_update: bool = Field(default=True, description="启用动态更新")
    pattern_confidence_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="模式置信度阈值"
    )
    contract_extraction_enabled: bool = Field(default=True, description="启用契约提取")


class LoggingConfig(BaseModel):
    """日志配置"""
    level: str = Field(default="INFO", description="日志级别")
    file: str = Field(default="comet.log", description="日志文件")


class Settings(BaseModel):
    """系统配置"""
    llm: LLMConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_yaml(cls, config_path: str) -> "Settings":
        """从 YAML 文件加载配置

        Args:
            config_path: 配置文件路径

        Returns:
            Settings 实例
        """
        config_file = Path(config_path)

        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")

        with open(config_file, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f)

        return cls(**config_data)

    @classmethod
    def from_yaml_or_default(cls, config_path: Optional[str] = None) -> "Settings":
        """从 YAML 文件加载配置，如果文件不存在则使用默认值

        Args:
            config_path: 配置文件路径，如果为 None 则查找默认位置

        Returns:
            Settings 实例
        """
        if config_path is None:
            # 尝试默认位置
            default_paths = ["config.yaml", "config.example.yaml"]
            for path in default_paths:
                if Path(path).exists():
                    config_path = path
                    break

        if config_path and Path(config_path).exists():
            return cls.from_yaml(config_path)

        # 使用默认配置，但需要至少设置 API key
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            raise ValueError(
                "未找到配置文件且未设置 OPENAI_API_KEY 环境变量。"
                "请创建 config.yaml 或设置环境变量。"
            )

        return cls(
            llm=LLMConfig(
                api_key=api_key,
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
            )
        )

    def ensure_directories(self) -> None:
        """确保所有配置的目录存在"""
        for path_name in ["workspace", "cache", "output", "sandbox"]:
            path = Path(getattr(self.paths, path_name))
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
