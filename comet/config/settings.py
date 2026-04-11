"""配置管理模块"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

STATE_ROOT = Path("./state")
OUTPUT_ROOT = Path("./output")
SANDBOX_ROOT = Path("./sandbox")


def _default_java_version_registry() -> dict[str, str | None]:
    return {
        "8": "/opt/jdks/jdk-8",
        "11": "/opt/jdks/jdk-11",
        "17": "/opt/jdks/jdk-17",
        "21": "/opt/jdks/jdk-21",
        "25": "/opt/jdks/jdk-25",
    }


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
    selected_java_version: str | None = Field(
        default=None,
        description="运行请求选择的目标 Java 版本（仅记录契约，不直接切换运行时）",
    )
    java_version_registry: dict[str, str | None] = Field(
        default_factory=_default_java_version_registry,
        description="JDK 版本注册表，键通常为 8/11/17/21/25，值为对应 JAVA_HOME",
    )

    @model_validator(mode="after")
    def _validate_selected_java_version(self) -> "ExecutionConfig":
        if self.selected_java_version is None:
            return self

        normalized_version = self.selected_java_version.strip()
        if not normalized_version:
            self.selected_java_version = None
            return self

        if normalized_version not in self.java_version_registry:
            raise ValueError(
                f"不支持的 Java 版本: {normalized_version}。"
                f"可选值: {', '.join(self.java_version_registry.keys())}"
            )

        self.selected_java_version = normalized_version
        return self

    def _resolve_selected_target_java_home(self) -> Optional[Path]:
        if self.selected_java_version is None:
            return None

        version = self.selected_java_version
        mapped_home = self.java_version_registry.get(version)
        if mapped_home is None:
            raise ValueError(f"Java 版本 {version} 未配置对应 JAVA_HOME")

        return self._resolve_home(mapped_home, f"JAVA_VERSION_{version}_HOME")

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
        selected_home = self._resolve_selected_target_java_home()
        if selected_home is not None:
            return selected_home
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
    exit_after_preprocessing: bool = Field(
        default=False,
        description="预处理完成后导出测试并退出，不进入主循环",
    )
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


class GitHubConfig(BaseModel):
    """GitHub 集成契约配置"""

    encrypted_token_store_path: str = Field(
        default="./state/github/auth/token.enc",
        description="本地加密后的 GitHub Token 存储路径",
    )
    managed_clone_root: str = Field(
        default="./sandbox/github-managed",
        description="受管 Git 仓库 clone 根目录",
    )
    oauth_client_id: str | None = Field(default=None, description="GitHub OAuth App Client ID")
    oauth_client_secret: str | None = Field(
        default=None,
        description="GitHub OAuth App Client Secret",
    )
    oauth_redirect_uri: str = Field(
        default="http://127.0.0.1:8000/api/github/auth/callback",
        description="GitHub OAuth App 回调地址",
    )
    oauth_scope: str = Field(
        default="repo",
        description="GitHub OAuth App 授权 scope",
    )
    oauth_state_ttl_seconds: int = Field(
        default=600,
        ge=60,
        description="OAuth state 有效期（秒）",
    )
    oauth_authorize_url: str = Field(
        default="https://github.com/login/oauth/authorize",
        description="GitHub OAuth 授权地址",
    )
    oauth_token_exchange_url: str = Field(
        default="https://github.com/login/oauth/access_token",
        description="GitHub OAuth code 交换 token 地址",
    )
    oauth_api_base_url: str = Field(
        default="https://api.github.com",
        description="GitHub API 基础地址",
    )
    encrypted_key_store_path: str = Field(
        default="./state/github/auth/token.key",
        description="本地加密 token 的密钥文件路径（fallback）",
    )
    repo_url: str | None = Field(default=None, description="本次运行的 GitHub 仓库地址")
    base_branch: str | None = Field(default=None, description="本次运行目标基线分支")

    @classmethod
    def apply_env_overrides(cls, config_data: dict[str, Any] | None) -> dict[str, Any]:
        resolved_config = dict(config_data or {})
        github_config: dict[str, Any] = {}

        oauth_client_id = os.getenv("COMET_GITHUB_OAUTH_CLIENT_ID")
        if oauth_client_id is not None:
            github_config["oauth_client_id"] = oauth_client_id

        oauth_client_secret = os.getenv("COMET_GITHUB_OAUTH_CLIENT_SECRET")
        if oauth_client_secret is not None:
            github_config["oauth_client_secret"] = oauth_client_secret

        oauth_redirect_uri = os.getenv("COMET_GITHUB_OAUTH_REDIRECT_URI")
        if oauth_redirect_uri is not None:
            github_config["oauth_redirect_uri"] = oauth_redirect_uri

        oauth_scope = os.getenv("COMET_GITHUB_OAUTH_SCOPE")
        if oauth_scope is not None:
            github_config["oauth_scope"] = oauth_scope

        oauth_state_ttl_seconds = os.getenv("COMET_GITHUB_OAUTH_STATE_TTL_SECONDS")
        if oauth_state_ttl_seconds is not None:
            github_config["oauth_state_ttl_seconds"] = int(oauth_state_ttl_seconds)

        oauth_authorize_url = os.getenv("COMET_GITHUB_OAUTH_AUTHORIZE_URL")
        if oauth_authorize_url is not None:
            github_config["oauth_authorize_url"] = oauth_authorize_url

        oauth_token_exchange_url = os.getenv("COMET_GITHUB_OAUTH_TOKEN_EXCHANGE_URL")
        if oauth_token_exchange_url is not None:
            github_config["oauth_token_exchange_url"] = oauth_token_exchange_url

        oauth_api_base_url = os.getenv("COMET_GITHUB_OAUTH_API_BASE_URL")
        if oauth_api_base_url is not None:
            github_config["oauth_api_base_url"] = oauth_api_base_url

        encrypted_token_store_path = os.getenv("COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH")
        if encrypted_token_store_path is not None:
            github_config["encrypted_token_store_path"] = encrypted_token_store_path

        encrypted_key_store_path = os.getenv("COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH")
        if encrypted_key_store_path is not None:
            github_config["encrypted_key_store_path"] = encrypted_key_store_path

        managed_clone_root = os.getenv("COMET_GITHUB_MANAGED_CLONE_ROOT")
        if managed_clone_root is not None:
            github_config["managed_clone_root"] = managed_clone_root

        if github_config:
            resolved_config["github"] = github_config

        return resolved_config

    @classmethod
    def strip_yaml_config(cls, config_data: dict[str, Any] | None) -> dict[str, Any]:
        resolved_config = dict(config_data or {})
        resolved_config.pop("github", None)
        return resolved_config


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
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    _state_root: Path = PrivateAttr(default=STATE_ROOT)
    _output_root: Path = PrivateAttr(default=OUTPUT_ROOT)
    _sandbox_root: Path = PrivateAttr(default=SANDBOX_ROOT)
    _bug_reports_dir: Optional[Path] = PrivateAttr(default=None)

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

        return cls(**GitHubConfig.apply_env_overrides(GitHubConfig.strip_yaml_config(config_data)))

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
            ),
            github=GitHubConfig.model_validate(
                GitHubConfig.apply_env_overrides({}).get("github") or {}
            ),
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

    def set_bug_reports_dir(self, bug_reports_dir: Optional[str | Path]) -> None:
        if bug_reports_dir is None:
            self._bug_reports_dir = None
            return

        self._bug_reports_dir = Path(bug_reports_dir).expanduser().resolve()

    def resolve_bug_reports_dir(self) -> Optional[Path]:
        return self._bug_reports_dir

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
