"""配置管理模块"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

STATE_ROOT = Path("./state")
OUTPUT_ROOT = Path("./output")
SANDBOX_ROOT = Path("./sandbox")


class LLMConfig(BaseModel):
    """LLM 配置"""

    base_url: str = Field(default="https://api.openai.com/v1", description="API 基础 URL")
    api_key: str = Field(description="API 密钥")
    model: str = Field(default="gpt-4", description="模型名称")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="温度参数")
    max_tokens: int = Field(default=4096, ge=1, description="单次请求的总 token 预算")
    supports_json_mode: bool = Field(default=True, description="是否支持 JSON 模式")
    timeout: float = Field(default=600.0, ge=1.0, description="API 请求超时时间（秒）")
    reasoning_effort: Optional[str] = Field(
        default=None,
        description="推理努力程度，可选值: 'none', 'low', 'medium', 'high'",
    )
    reasoning_enabled: Optional[bool] = Field(
        default=None,
        description="是否启用推理，null 表示不下发该配置",
    )
    verbosity: Optional[str] = Field(
        default=None, description="响应详细程度，可选值: 'low', 'medium', 'high'"
    )


class FormattingConfig(BaseModel):
    """代码格式化配置"""

    enabled: bool = Field(default=True, description="是否启用代码格式化")
    style: str = Field(default="GOOGLE", description="格式化风格 (GOOGLE 或 AOSP)")


class ExecutionConfig(BaseModel):
    """执行配置"""

    timeout: int = Field(default=300, ge=1, description="超时时间（秒）")
    test_timeout: int = Field(default=30, ge=1, description="测试执行超时时间（秒）")
    coverage_timeout: int = Field(default=300, ge=1, description="覆盖率收集超时时间（秒）")
    max_retries: int = Field(default=3, ge=0, description="最大重试次数")
    runtime_java_home: Optional[str] = Field(
        default=None, description="运行 COMET-L Java Runtime 使用的 Java 安装路径"
    )
    target_java_home: Optional[str] = Field(
        default=None, description="运行被测项目构建、测试与编译使用的 Java 安装路径"
    )
    maven_home: Optional[str] = Field(default=None, description="Maven 安装路径")

    def _resolve_home(self, home: Optional[str], name: str) -> Optional[Path]:
        if not home:
            return None

        resolved = Path(home).expanduser().resolve()
        if not resolved.is_dir():
            raise ValueError(f"{name} 不存在或不是目录: {resolved}")
        return resolved

    def _resolve_bin_command(self, home: Optional[Path], command: str) -> str:
        if home is None:
            return command

        command_path = home / "bin" / command
        if not command_path.is_file():
            raise ValueError(f"未找到 {command} 可执行文件: {command_path}")
        return str(command_path)

    def _resolve_runtime_java_home(self) -> Optional[Path]:
        return self._resolve_home(self.runtime_java_home, "RUNTIME_JAVA_HOME")

    def _resolve_target_java_home(self) -> Optional[Path]:
        return self._resolve_home(self.target_java_home, "TARGET_JAVA_HOME")

    def _build_subprocess_env(
        self,
        java_home: Optional[Path],
        base_env: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        env = dict(base_env) if base_env is not None else os.environ.copy()
        path_entries: list[str] = []

        if java_home is not None:
            env["JAVA_HOME"] = str(java_home)
            path_entries.append(str(java_home / "bin"))

        maven_home = self._resolve_home(self.maven_home, "MAVEN_HOME")
        if maven_home is not None:
            env["MAVEN_HOME"] = str(maven_home)
            env["M2_HOME"] = str(maven_home)
            path_entries.append(str(maven_home / "bin"))

        current_path = env.get("PATH", "")
        if path_entries:
            deduped_entries: list[str] = []
            for entry in path_entries:
                if entry not in deduped_entries:
                    deduped_entries.append(entry)

            if current_path:
                deduped_entries.append(current_path)
            env["PATH"] = os.pathsep.join(deduped_entries)

        return env

    def build_runtime_subprocess_env(
        self, base_env: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        return self._build_subprocess_env(self._resolve_runtime_java_home(), base_env)

    def build_target_subprocess_env(
        self, base_env: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        return self._build_subprocess_env(self._resolve_target_java_home(), base_env)

    def build_subprocess_env(self, base_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return self.build_target_subprocess_env(base_env)

    def resolve_runtime_java_cmd(self) -> str:
        return self._resolve_bin_command(self._resolve_runtime_java_home(), "java")

    def resolve_target_java_cmd(self) -> str:
        return self._resolve_bin_command(self._resolve_target_java_home(), "java")

    def resolve_java_cmd(self) -> str:
        return self.resolve_runtime_java_cmd()

    def resolve_javac_cmd(self) -> str:
        return self.resolve_target_javac_cmd()

    def resolve_target_javac_cmd(self) -> str:
        return self._resolve_bin_command(self._resolve_target_java_home(), "javac")

    def get_runtime_java_home(self) -> Optional[str]:
        java_home = self._resolve_runtime_java_home()
        return str(java_home) if java_home is not None else None

    def get_target_java_home(self) -> Optional[str]:
        java_home = self._resolve_target_java_home()
        return str(java_home) if java_home is not None else None

    def resolve_mvn_cmd(self) -> str:
        maven_home = self._resolve_home(self.maven_home, "MAVEN_HOME")
        return self._resolve_bin_command(maven_home, "mvn")


class EvolutionConfig(BaseModel):
    """进化配置"""

    mutation_enabled: bool = Field(default=True, strict=True, description="是否启用变异分析")
    max_iterations: int = Field(default=10, ge=1, description="最大迭代次数")
    min_improvement_threshold: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="最小改进绝对阈值（比例值，0.01 表示提升 1 个百分点）",
    )
    budget_llm_calls: int = Field(default=1000, ge=1, description="LLM 调用预算")
    stop_on_no_improvement_rounds: int = Field(default=3, ge=1, description="无改进时停止的轮数")

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

    # 目标方法选择策略
    min_method_lines: int = Field(
        default=1, ge=1, description="目标方法的最小行数，小于此值的方法将被跳过"
    )


class EmbeddingConfig(BaseModel):
    """Embedding 配置"""

    base_url: str = Field(default="https://api.openai.com/v1", description="Embedding API 基础 URL")
    api_key: Optional[str] = Field(default=None, description="API 密钥，留空则使用 llm.api_key")
    model: str = Field(default="text-embedding-3-small", description="Embedding 模型名称")
    batch_size: int = Field(default=100, ge=1, description="批量 embedding 的大小")


class RetrievalConfig(BaseModel):
    """检索配置"""

    top_k: int = Field(default=5, ge=1, description="每次检索返回的文档数")
    score_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="相似度阈值")


class KnowledgeConfig(BaseModel):
    """知识库配置"""

    enabled: bool = Field(default=True, description="是否启用 RAG 知识库")
    enable_dynamic_update: bool = Field(default=True, description="启用动态更新")
    pattern_confidence_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="模式置信度阈值"
    )
    contract_extraction_enabled: bool = Field(default=True, description="启用契约提取")
    embedding: EmbeddingConfig = Field(
        default_factory=EmbeddingConfig, description="Embedding 配置"
    )
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig, description="检索配置")


class LoggingConfig(BaseModel):
    """日志配置"""

    level: str = Field(default="INFO", description="日志级别")
    file: str = Field(default="comet.log", description="日志文件")


class PreprocessingConfig(BaseModel):
    """并行预处理配置"""

    enabled: bool = Field(default=True, description="是否启用并行预处理")
    max_workers: Optional[int] = Field(
        default=None, description="最大并发数，null表示自动（cpu_count）"
    )
    timeout_per_method: int = Field(default=300, ge=1, description="单个方法的超时时间（秒）")


class AgentParallelConfig(BaseModel):
    """Agent 并行配置"""

    enabled: bool = Field(default=False, description="是否启用并行 Agent 模式")
    max_parallel_targets: int = Field(default=4, ge=1, description="最大并行目标数")
    max_eval_workers: int = Field(default=4, ge=1, description="变异体评估并行度")
    timeout_per_target: int = Field(default=300, ge=1, description="单个目标的超时时间（秒）")


class AgentConfig(BaseModel):
    """Agent 配置"""

    parallel: AgentParallelConfig = Field(
        default_factory=AgentParallelConfig, description="并行配置"
    )


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    """系统配置"""

    llm: LLMConfig
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    preprocessing: PreprocessingConfig = Field(default_factory=PreprocessingConfig)
    formatting: FormattingConfig = Field(default_factory=FormattingConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    _state_root: Path = PrivateAttr(default=STATE_ROOT)
    _output_root: Path = PrivateAttr(default=OUTPUT_ROOT)
    _sandbox_root: Path = PrivateAttr(default=SANDBOX_ROOT)

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
                base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            )
        )

    def ensure_directories(self) -> None:
        for path in (
            self.resolve_state_root(),
            self.resolve_output_root(),
            self.resolve_sandbox_root(),
        ):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_state_root(self) -> Path:
        return self._state_root

    def resolve_output_root(self) -> Path:
        return self._output_root

    def resolve_sandbox_root(self) -> Path:
        return self._sandbox_root

    def set_runtime_roots(self, *, state: Path, output: Path, sandbox: Path) -> None:
        self._state_root = state
        self._output_root = output
        self._sandbox_root = sandbox

    def resolve_database_path(self) -> Path:
        return self.resolve_state_root() / "comet.db"

    def resolve_knowledge_database_path(self) -> Path:
        return self.resolve_state_root() / "knowledge.db"

    def resolve_vector_store_path(self) -> Path:
        return self.resolve_state_root() / "chromadb"

    def resolve_embedding_cache_path(self) -> Path:
        return self.resolve_vector_store_path() / "embedding_cache"

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self.model_dump()
