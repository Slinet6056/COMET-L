# COMET-L

[![Wakapi](https://wakapi.dev/api/badge/Slinet6056/interval:any/project:COMET-L)](https://wakapi.dev)
[![Docker](https://github.com/slinet6056/COMET-L/actions/workflows/docker.yml/badge.svg)](https://github.com/slinet6056/COMET-L/actions/workflows/docker.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](pyproject.toml)
[![Java 25](https://img.shields.io/badge/Java-25-orange.svg)](java-runtime/pom.xml)
[![TypeScript](https://img.shields.io/badge/TypeScript-5.x-blue.svg)](web/package.json)

COMET-L 是一个面向 Java Maven 项目的 LLM 驱动测试生成与变异分析系统。它会扫描目标项目、选择值得改进的方法、生成语义变异体，再根据幸存变异体补充测试，用覆盖率和变异击杀率持续评估测试质量。

仓库同时包含三部分：

- Python 编排层：CLI、配置、RAG 知识库、LLM 调用、沙箱、执行调度和 FastAPI 后端。
- Java Runtime：负责 Java 源码分析、格式化、Maven 执行、覆盖率和变异相关运行时能力。
- Web 控制台：React/Vite 前端，用于本地发起运行、查看日志和下载报告，也支持 GitHub 仓库导入与 PR 流程。

## 当前能力

- 基于 LLM 生成测试和语义变异体，而不是只做语法级变异。
- 使用 RAG 索引源码契约、代码模式和 Bug 报告，为生成过程补充上下文。
- 支持标准单目标循环和批量并行目标处理。
- 使用沙箱隔离目标项目，默认不会直接在原项目中执行破坏性操作。
- 支持 Mockito 场景、JUnit 测试生成、JaCoCo 覆盖率收集和 Maven 项目执行。
- 提供终端 CLI、研究实验命令、Defects4J 回放工具和本地 Web 控制台。
- 提供多 JDK Docker 镜像，便于同时处理 Java 8/11/17/21/25 目标项目。

## 环境要求

- Python 3.11+
- uv
- Maven
- pnpm
- just，推荐作为统一入口
- 本机直接执行 `just runtime-build` 时，Java Runtime 构建需要满足 `java-runtime/pom.xml` 中的 Maven 编译 release 配置。目标项目可通过 `execution.target_java_home` 或 `--selected-java-version` 使用 Java 8/11/17/21/25 等运行环境。

> 说明：Docker 镜像内置 `/opt/jdks/jdk-8`、`/opt/jdks/jdk-11`、`/opt/jdks/jdk-17`、`/opt/jdks/jdk-21`、`/opt/jdks/jdk-25`。本机运行时，请把 `java-runtime` 的构建 JDK 与目标项目 JDK 分开配置，避免用目标项目的旧 JDK 构建 Runtime。

## 快速开始

### 1. 安装依赖并构建 Runtime

```bash
just setup
```

等价的分步命令：

```bash
just sync
just web-install
just runtime-build
```

底层命令分别是 `uv sync`、`pnpm --dir web install` 和 `mvn clean package -f java-runtime/pom.xml`。

### 2. 创建配置文件

```bash
just config-init
```

该命令会从 `config.example.yaml` 创建面向单次运行的 `config.yaml`，如果目标文件已存在则不会覆盖。这个配置用于 CLI 运行，也可作为 Web 控制台上传运行配置的格式参考，只提供 LLM、execution、knowledge、evolution 等运行参数。最小配置通常只需要先填 LLM：

```yaml
llm:
  base_url: 'https://api.openai.com/v1'
  api_key: 'your-api-key-here'
  model: 'gpt-4'
  temperature: 0.7
  max_tokens: 4096
  supports_json_mode: true
  timeout: 120
  reasoning_effort: null
  reasoning_enabled: null
  verbosity: null

execution:
  runtime_java_home: null
  target_java_home: null
  maven_home: null
```

常用配置说明：

- `llm.*`：LLM API、模型、超时、JSON 模式和推理相关参数。
- `execution.runtime_java_home`：运行 COMET-L Java Runtime 使用的 JDK。
- `execution.target_java_home`：运行被测 Maven 项目、测试和 `javac` 使用的 JDK。
- `knowledge.*`：RAG、embedding、检索阈值和动态更新配置。
- `preprocessing.*`：并行预处理开关、并发数和单方法超时。
- `agent.parallel.*`：并行 Agent 模式的目标数、评估并发和超时。

Web 控制台上传的运行配置只影响当次运行，不会成为服务器基础配置，也不能填充 GitHub OAuth、部署策略或服务器固定执行路径。服务端策略仍可限制或覆盖运行字段，例如预算、目标 Java 版本、固定 JDK 或 Maven 路径。GitHub OAuth、token 存储路径和受管 clone 根目录属于部署级配置，优先通过环境变量或服务器启动配置提供，不建议写入用户侧运行配置。

### 3. 运行示例项目

```bash
just run
```

默认目标是 `examples/calculator-demo`。运行任意 Maven 项目：

```bash
just run /absolute/path/to/maven-project
```

## CLI 用法

`main.py` 当前有四个子命令：`run`、`study`、`analyze-study`、`replay-defects4j`。如果省略子命令，默认按 `run` 处理。

### 默认协同进化流程

```bash
uv run python main.py run --project-path examples/calculator-demo
```

常用 just 封装：

```bash
# 默认示例项目
just run

# 调试日志
just run-debug examples/calculator-demo

# 指定配置文件
just run-config examples/calculator-demo config.yaml

# 覆盖 LLM 调用预算
just run-budget examples/calculator-demo 500

# 启用并行 Agent 模式
just run-parallel examples/calculator-demo 8
```

常用原生命令参数：

```bash
uv run python main.py run \
  --project-path /path/to/project \
  --config config.yaml \
  --max-iterations 5 \
  --budget 500 \
  --bug-reports-dir /path/to/bug-reports \
  --selected-java-version 17
```

### 研究流程 study

`study` 是终端专用研究入口，用于冷启动抽样、共享 baseline 与 `M0/M2/M3` 三臂对比，不进入默认 `PlannerAgent` 主循环。

```bash
uv run python main.py study \
  --project-path examples/calculator-demo \
  --sample-size 12 \
  --seed 42 \
  --output-dir .artifacts/study-demo \
  --bug-reports-dir /path/to/bug-reports
```

输出目录会包含 `summary.json`、`per_method.csv`、`per_mutant.jsonl`、`sampled_methods.json`，并在 `artifacts/<target-method>/{baseline,M0,M2,M3}/` 下归档测试文件。`--sample-size` 表示目标成功方法数；如果候选方法耗尽仍不足，`summary.json` 会记录缺口。

### 分析 study 结果

```bash
uv run python main.py analyze-study \
  --project-path examples/calculator-demo \
  --study-results-path .artifacts/study-demo \
  --output-csv .artifacts/study-demo/analysis_metrics.csv
```

该命令会回放 study 归档测试，并导出详细统计 CSV。

### Defects4J 固定测试回放

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir .artifacts/defects4j-replay
```

如果回放 EvoSuite 测试，可以加入 `--use-xvfb`。如需通过 Defects4J 自动 checkout，可使用 `--checkout-mode local` 或 `--checkout-mode docker` 并补充对应的 checkout 参数。完整 manifest 格式和结果说明见 `docs/defects4j-replay.md`。

## Web 控制台

本地完整联调推荐采用“前端构建，后端托管静态资源”的方式：

```bash
just web-install
just web-build
just web-serve
```

启动后访问 `http://127.0.0.1:8000/`。后端同时提供 `/api/*` 接口，并在存在 `web/dist/index.html` 时挂载前端静态资源。

如果只调试前端组件，可以运行：

```bash
just web-dev
```

当前默认没有为 Vite dev server 配置 API 代理，所以完整运行仍建议使用 `just web-build && just web-serve`。

### 多用户生产部署

COMET-L 支持多用户 Web 控制台部署，适用于小型团队或半可信环境。详细部署指南请参见 [docs/production-multi-user-deployment.md](docs/production-multi-user-deployment.md)。该模式只面向 fresh deployment，不提供旧单用户数据导入、迁移或兼容流程，migration unsupported。

**关键要点：**

- **单进程限制：** 必须使用 `uvicorn --workers 1` 启动，不支持多 worker、多进程、多节点或公共 SaaS 级水平扩展。
- **全新部署：** 当前版本不支持旧单用户版本数据迁移、导入或兼容读取。
- **代码执行警告：** 用户上传的 Maven 项目会在服务器上执行 Maven 构建、测试和项目代码。本 MVP 仅适合小型内部或半可信用户，不适合直接开放给不可信公网用户。
- **安全配置：** 生产环境必须启用 HTTPS、`secure_auth_cookies: true`、可信 `allowed_origins`、上传大小限制、队列限制和数据保留策略。
- **状态与会话：** Web SQLite 默认位于 `state/web/comet-web.sqlite3`，登录 Cookie 名为 `comet_session`。普通用户使用上传 ID 创建运行，管理员本地路径模式默认关闭，只有启用 `allow_local_path_mode` 且配置 allowlist 后才可使用。

快速初始化管理员账户：

```bash
uv run python -m comet.web.admin create-admin --username admin --password "your-password"
```

## Docker 多 JDK 镜像

构建和自检：

```bash
just docker-build
just docker-self-check
just docker-verify
```

镜像内固定 JDK 路径：

| 版本 | 路径               |
| ---- | ------------------ |
| 8    | `/opt/jdks/jdk-8`  |
| 11   | `/opt/jdks/jdk-11` |
| 17   | `/opt/jdks/jdk-17` |
| 21   | `/opt/jdks/jdk-21` |
| 25   | `/opt/jdks/jdk-25` |

容器启动时如果未显式传入 `JAVA_HOME`，默认使用 `/opt/jdks/jdk-25`。入口脚本还会导出 `COMET_JAVA_HOME_8`、`COMET_JAVA_HOME_11`、`COMET_JAVA_HOME_17`、`COMET_JAVA_HOME_21`、`COMET_JAVA_HOME_25`。

### 运行 Web 控制台容器

服务器部署请阅读 [Docker 服务器部署指南](docs/docker-server-deployment.md)。该文档使用发布镜像 `slinet6056/comet-l:latest`，按使用者视角说明服务器目录、配置文件、启动容器、创建管理员、GitHub 可选配置和用户运行流程。

本 README 只保留本地构建与自检命令；正式在服务器上部署时，以 Docker 服务器部署指南为准。多用户部署的安全边界和实现限制见 [docs/production-multi-user-deployment.md](docs/production-multi-user-deployment.md)。

## Bug 报告输入

`--bug-reports-dir` 可指向一个包含缺陷报告的目录，供 RAG 检索使用。当前支持：

| 格式     | 扩展名   | 说明                       |
| -------- | -------- | -------------------------- |
| Markdown | `.md`    | 支持可选 YAML front matter |
| 纯文本   | `.txt`   | 任意自然语言描述           |
| Diff     | `.diff`  | Git diff 输出              |
| Patch    | `.patch` | 补丁文件                   |

示例：

```markdown
# 空指针异常

用户名为 null 时调用 getUserName() 会抛出 NullPointerException。
建议在方法入口添加空值检查。
```

## 开发与验证

优先使用 just：

```bash
# 查看所有命令
just

# 全量检查
just check

# 全量格式化
just format

# Python 检查
just check-python

# Web 格式检查
just check-web

# Java Spotless 检查
just check-java

# Python 测试，可传入具体测试路径
just test-python tests/test_web_api.py

# Web 单文件测试
just test-web ResultsPage.test.tsx

# 后端 API 测试
just test-web-api
```

直接命令：

```bash
uv run ruff check .
uv run ruff format --check .
uv run python -m pytest
pnpm --dir web run format:check
pnpm --dir web exec vitest run src/pages/ResultsPage.test.tsx
mvn -q -f java-runtime/pom.xml spotless:check
```

如果修改 `java-runtime/`，请重新执行：

```bash
just runtime-build
```

## 项目结构

```text
COMET-L/
├── main.py              # CLI 入口：run/study/analyze-study/replay-defects4j
├── comet/               # Python 主模块
│   ├── agent/           # PlannerAgent、ParallelPlannerAgent 与目标选择
│   ├── config/          # Pydantic 配置与运行路径解析
│   ├── executor/        # Java 执行、覆盖率解析和变异评估
│   ├── extractors/      # 契约和代码模式提取
│   ├── generators/      # 测试、变异体和静态校验生成逻辑
│   ├── knowledge/       # RAG、embedding、向量库和知识存储
│   ├── llm/             # LLM 客户端
│   ├── store/           # SQLite 数据存储
│   ├── utils/           # 项目扫描、沙箱和代码工具
│   └── web/             # FastAPI app、路由和 Web 运行服务
├── java-runtime/        # Java 分析、格式化和 Maven 执行 Runtime
├── web/                 # React + TypeScript + Vite Web 控制台
├── examples/            # Maven 示例项目和 Java 格式检查样本
├── tests/               # Python 测试
├── docs/                # 补充文档
├── state/               # 本地运行状态、SQLite、ChromaDB，通常不提交
├── output/              # 运行产物，通常不提交
└── sandbox/             # 沙箱工作目录，通常不提交
```

## 运行产物与限制

- 标准本地路径模式只供管理员在显式开启 allowlist 后使用，会在沙箱中运行，完成后把生成测试导出回原项目的 `src/test/java/`。普通用户使用 ZIP 上传模式。
- GitHub 受管仓库模式会直接使用受管 clone 工作目录，不从 sandbox 回写。
- Web 控制台的多用户生产模式要求单 FastAPI 进程运行，状态存储在 SQLite，并按用户隔离运行根目录。
- Web 调度器是单进程 SQLite-backed FIFO 队列，默认全局同时运行 2 个任务、每用户同时运行 1 个任务。
- 结果页主要提供 `final_state.json`、`run.log` 和 `report.md` 下载入口。
- 没有可用 `config.yaml` 且没有 `OPENAI_API_KEY` 环境变量时，CLI 会拒绝启动。

## 技术栈

- Python：FastAPI、Pydantic、SQLite、ChromaDB、OpenAI SDK、tiktoken、pytest、ruff。
- Java：Maven、JavaParser、JUnit、JaCoCo、Gson、google-java-format、Spotless。
- Web：React 19、TypeScript、Vite、Vitest、Prettier、Tailwind 4、Base UI、Radix Slot。

## 许可

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request。提交前建议至少运行与改动范围对应的 `just check-*` 和测试命令。
