# COMET-L: 基于 LLM 的测试变异协同进化系统

COMET-L 是一个创新的测试生成系统，通过测试生成器和变异生成器的对抗式协同进化来自动提升测试质量。

## 核心特性

- **双 LLM 协同**：测试生成器和变异生成器相互对抗，持续提升测试覆盖率和变异检测能力
- **RAG 知识增强**：使用向量数据库检索相关知识，为 LLM 提供上下文
- **知识库驱动**：从源代码提取契约，从 Bug 报告学习缺陷模式
- **语义变异**：基于 LLM 生成有意义的语义变异，而非简单的语法变异
- **Agent 调度**：智能 Agent 自动选择目标、分配预算、调整策略
- **并行处理**：支持预处理和主循环的并行模式，显著提升处理效率
- **沙箱隔离**：独立的执行环境确保测试和变异互不干扰
- **Mockito 支持**：自动识别依赖并使用 Mockito 创建隔离的单元测试

## 系统架构

系统包含以下核心组件：

1. **知识提取层**：从源代码和 Bug 报告中提取知识
2. **RAG 检索层**：使用 ChromaDB 存储向量，语义检索相关上下文
3. **变异生成管线**：生成语义变异体暴露测试不足
4. **测试生成管线**：针对幸存变异生成新测试
5. **执行与评估**：编译运行并收集覆盖率和击杀率数据
6. **Agent 调度器**：协调整个进化过程

## 快速开始

### 环境要求

- Python 3.11+
- Java 25+（用于运行 COMET-L 与 Java Runtime）
- Maven 3.6+
- just（推荐，作为统一开发入口）
- 目标项目可为 Java 8+（系统默认按 Java 8 语法生成测试，以兼容 JDK8 项目）

### 安装

```bash
# 推荐：一键完成 Python 依赖同步、Web 依赖安装和 Java Runtime 构建
just setup

# 或者按需分步执行
just sync
just web-install
just runtime-build
```

底层命令仍然可用：`uv sync`、`pnpm --dir web install`、`mvn clean package -f java-runtime/pom.xml`。

### 配置

复制配置模板并填入您的设置：

```bash
just config-init
```

如果目标文件已存在，`just config-init` 不会覆盖；你也可以继续手动执行 `cp config.example.yaml config.yaml`。

编辑 `config.yaml`，配置 LLM API：

```yaml
llm:
  base_url: 'https://api.openai.com/v1'
  api_key: 'your-api-key'
  model: 'gpt-4'

# RAG 知识库配置（可选）
knowledge:
  enabled: true # 启用 RAG
  embedding:
    model: 'text-embedding-3-small'

execution:
  runtime_java_home: '/usr/lib/jvm/java-25-openjdk'
  target_java_home: '/usr/lib/jvm/java-8-openjdk'
```

`config.yaml` 只承载运行参数（例如 LLM、执行、演化和知识库设置）。如果你要使用 GitHub 仓库模式，请通过 Docker/进程环境变量提供 GitHub OAuth 和相关部署级设置。

`execution.runtime_java_home` 用于指定 COMET-L 的 `java-runtime` 与格式化器所使用的 JDK，`execution.target_java_home` 用于指定被测项目的 Maven、测试和 `javac` 所使用的 JDK。

### 运行

对任意 Maven 项目运行协同进化：

```bash
just run /path/to/your/java/project
```

使用示例项目测试：

```bash
just run
```

更多常用运行方式：

```bash
# 调试模式
just run-debug examples/calculator-demo

# 指定配置文件
just run-config examples/calculator-demo config.yaml

# 指定 LLM 调用预算
just run-budget examples/calculator-demo 500

# 并行模式
just run-parallel examples/calculator-demo 8
```

### 研究命令（study）

`study` 是终端专用的稳定入口，用于一次性跑完冷启动抽样、共享 baseline 与 `M0/M2/M3` 三臂研究，不会进入默认 `PlannerAgent` 主循环。

```bash
uv run python main.py study --project-path examples/calculator-demo --sample-size 12 --seed 42 --output-dir .artifacts/study-demo --bug-reports-dir /path/to/bug-reports
```

- 冷启动：即使目标项目一开始没有测试用例，`study` 也会先为每个候选方法建立共享 baseline，再继续三臂对比。
- 抽样：`--sample-size` 表示目标成功数，不再表示预先截断的固定样本数；运行会按冻结候选顺序持续补位，前面失败或部分失败的方法会由后续候选补位，直到攒够该成功数，或候选耗尽。
- 输出：`--output-dir` 会生成 `summary.json`、`per_method.csv`、`per_mutant.jsonl`、`sampled_methods.json`，并在 `artifacts/<target-method>/{baseline,M0,M2,M3}/` 下归档测试文件；其中 `sampled_methods.json` 记录全部已尝试目标，`summary.json` 的 `sample_size`、`method_count` 与 `project_averages` 统计只基于 `M0/M2/M3` 三臂全部成功的方法。
- 配额缺口：若候选方法耗尽后仍未达到目标成功数，`summary.json` 会保留 `requested_sample_size`、`attempted_method_count` 与 `successful_sample_shortfall`，显式展示本次研究还差多少个成功方法。
- Bug reports：可通过 `--bug-reports-dir` 指定缺陷报告目录，`M3` 会在研究执行时读取并索引这些报告用于 RAG 检索；未提供时保持原有无 bug reports 输入行为。
- 运行目录：日志写入 `--output-dir/study.log`，隔离运行状态和沙箱分别写入 `--output-dir/.study-state/` 与 `--output-dir/.study-sandbox/`，不会回写目标项目。

退出码约定：`0` 表示研究执行完成；`1` 表示运行期失败（如配置、构建、PIT 或 LLM 调用失败）；`2` 表示命令行参数错误（例如缺少 `--project-path` 或 `--output-dir`）。

### Defects4J 固定测试回放

如果你已经准备好一批固定测试，想批量把它们回放到 Defects4J 的 `buggy` / `fixed` 版本中并统计结果，可以使用：

```bash
uv run python main.py replay-defects4j --manifest /path/to/manifest.jsonl --output-dir .artifacts/defects4j-replay
```

如果回放的是 EvoSuite 测试，建议额外加上 `--use-xvfb`，让 Maven 通过 `xvfb-run -a` 执行；如果它还依赖专用 `pom.xml`，可以在 manifest 中为该记录设置 `pom_override_path`。当前 `docker` 模式会直接使用镜像内 Defects4J，而不是宿主机 `defects4j` 目录。

完整说明、manifest 格式、路径约定、CLI 示例和结果文件解释见 `docs/defects4j-replay.md:1`。

常见失败原因：

- 目标路径不存在、不是 Maven 项目，或缺少 `pom.xml`。
- 未提供可用的 `config.yaml` / `OPENAI_API_KEY`，导致 LLM 配置无法初始化。
- `java-runtime` 未构建、Java/Maven 环境不完整，或目标项目自身无法完成 `test-compile` / PIT 执行。
- LLM 接口不可达、超时，或返回内容无法生成可验证测试。

## Web 控制台

### 本地启动方式

Web 控制台当前采用“前端先构建、后端托管静态资源”的本地工作流。请在仓库根目录执行：

```bash
just web-install
just web-build
just web-serve
```

启动后访问 `http://127.0.0.1:8000/`，后端会在生产形态下自动挂载 `web/dist`，并同时继续提供 `/api/*` 接口。

如果只想单独调试前端组件，也可以运行 `just web-dev`；但当前仓库默认没有为 Vite dev server 配置 API 代理，因此完整联调仍建议以上述“build + backend”方式进行。

### Web 测试命令

```bash
# 结果页定向回归
just test-web ResultsPage.test.tsx

# 前端生产构建
just web-build
```

如需运行后端 API 测试，可使用：

```bash
just test-web-api
```

## 常用 just 命令

仓库已经内置 `justfile`，日常开发优先使用 `just`：

```bash
# 查看可用命令
just

# 环境与构建
just setup
just sync
just web-install
just runtime-build

# 运行 COMET-L
just run
just run-debug examples/calculator-demo
just run-config examples/calculator-demo config.yaml
just run-budget examples/calculator-demo 500
just run-parallel examples/calculator-demo 8

# Web 开发
just web-build
just web-dev
just web-serve
just test-web ResultsPage.test.tsx
just test-web-api

# 格式化与检查
just format
just check
just install-hooks

# Docker 多 JDK 运行时
just docker-build
just docker-self-check
just docker-verify
```

### Docker 单镜像（多 JDK）

仓库根目录提供了单镜像多 JDK Docker 构建产物，镜像内固定包含以下稳定路径：

- `/opt/jdks/jdk-8`
- `/opt/jdks/jdk-11`
- `/opt/jdks/jdk-17`
- `/opt/jdks/jdk-21`
- `/opt/jdks/jdk-25`

构建与自检示例：

```bash
just docker-build
just docker-self-check
just docker-verify
```

镜像内 `JAVA_HOME` 映射规则如下：

- 若容器启动时显式传入 `JAVA_HOME`，入口脚本会保留该值，并把对应 `bin` 目录加入 `PATH`
- 若未显式传入，则默认 `JAVA_HOME=/opt/jdks/jdk-25`
- 入口脚本始终导出 `COMET_JAVA_HOME_8`、`COMET_JAVA_HOME_11`、`COMET_JAVA_HOME_17`、`COMET_JAVA_HOME_21`、`COMET_JAVA_HOME_25`

也可以直接运行镜像内自检命令：

```bash
docker build -t comet-l:multi-jdk .
docker run --rm comet-l:multi-jdk comet-docker-self-check
```

#### Docker Web 控制台运行

推荐把状态、输出、沙箱和日志挂载到宿主机目录。这样 GitHub OAuth token、clone 出来的仓库、运行报告和日志在容器重启后不会丢失。运行配置建议在 Web 控制台里按需上传，不需要在容器启动时固定挂载 `config.yaml`。

```bash
# 第一次运行前准备宿主机数据目录
mkdir -p .docker-data/{state,output,sandbox,logs}

docker run --rm -it \
  --name comet-l \
  -p 8000:8000 \
  -v "$PWD/.docker-data/state:/opt/comet-l/state" \
  -v "$PWD/.docker-data/output:/opt/comet-l/output" \
  -v "$PWD/.docker-data/sandbox:/opt/comet-l/sandbox" \
  -v "$PWD/.docker-data/logs:/opt/comet-l/logs" \
  -e COMET_GITHUB_OAUTH_CLIENT_ID='你的 Client ID' \
  -e COMET_GITHUB_OAUTH_CLIENT_SECRET='你的 Client Secret' \
  comet-l:multi-jdk
```

镜像默认会自动启动 Web 控制台；启动后访问 `http://127.0.0.1:8000`。如果你需要进入容器 shell 或执行其他命令，可以在镜像名后显式追加命令，例如 `docker run --rm -it comet-l:multi-jdk bash`。

推荐的使用方式是：

1. 用 `-e COMET_GITHUB_...` 提供 GitHub 部署级配置。
2. 打开 Web 控制台后，再上传一份只包含运行参数的 YAML。
3. 让运行参数和 GitHub 部署级设置分别管理，不要混在同一份启动输入里。

如果你还希望把 OAuth 回调地址、scope 或本地存储路径也做成部署级配置，可以额外传入：

```bash
-e COMET_GITHUB_OAUTH_REDIRECT_URI='http://127.0.0.1:8000/api/github/auth/callback' \
-e COMET_GITHUB_OAUTH_SCOPE='repo' \
-e COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH='./state/github/auth/token.enc' \
-e COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH='./state/github/auth/token.key' \
-e COMET_GITHUB_MANAGED_CLONE_ROOT='./sandbox/github-managed'
```

必须挂载的路径取决于使用方式：

| 使用方式               | 需要挂载                                                | 说明                                                                                                              |
| ---------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 只跑本地路径模式       | 本地 Maven 项目目录（如需跑本地项目）                   | 容器内必须能看到目标项目，例如把宿主机项目挂到 `/workspace/project` 后，在 UI 填 `/workspace/project`。           |
| 使用 GitHub 仓库模式   | `state/`、`sandbox/`、`output/`、`logs/`                | GitHub token 存在 `state/`，受管 clone 默认在 `sandbox/github-managed`，报告在 `output/runs/{run_id}/report.md`。 |
| 希望重启后保留历史记录 | `state/`、`output/`、`logs/`                            | `state/` 保存运行会话与数据库，`output/` 保存 artifact，`logs/` 保存运行日志。                                    |

如果要让容器处理宿主机上的本地 Maven 项目，再额外挂载项目目录：

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v "$PWD/.docker-data/state:/opt/comet-l/state" \
  -v "$PWD/.docker-data/output:/opt/comet-l/output" \
  -v "$PWD/.docker-data/sandbox:/opt/comet-l/sandbox" \
  -v "$PWD/.docker-data/logs:/opt/comet-l/logs" \
  -v "/absolute/path/to/maven-project:/workspace/project" \
  comet-l:multi-jdk
```

然后在 Web 首页选择“本地路径”，项目路径填写 `/workspace/project`。

#### GitHub OAuth 配置

只使用本地路径模式时，不需要 GitHub 配置。使用 GitHub 仓库导入、自动 push 和创建 PR 时，需要在 GitHub 创建 OAuth App：

1. 打开 GitHub `Settings` → `Developer settings` → `OAuth Apps` → `New OAuth App`。
2. `Homepage URL` 可以填 `http://127.0.0.1:8000`。
3. `Authorization callback URL` 必须与 `COMET_GITHUB_OAUTH_REDIRECT_URI` 一致；如果没有显式传环境变量，默认是 `http://127.0.0.1:8000/api/github/auth/callback`。
4. 创建后在启动容器时传入 `Client ID` 和 `Client Secret`：

```bash
docker run --rm -it \
  -p 8000:8000 \
  -v "$PWD/.docker-data/state:/opt/comet-l/state" \
  -v "$PWD/.docker-data/output:/opt/comet-l/output" \
  -v "$PWD/.docker-data/sandbox:/opt/comet-l/sandbox" \
  -v "$PWD/.docker-data/logs:/opt/comet-l/logs" \
  -e COMET_GITHUB_OAUTH_CLIENT_ID='你的 Client ID' \
  -e COMET_GITHUB_OAUTH_CLIENT_SECRET='你的 Client Secret' \
  -e COMET_GITHUB_OAUTH_REDIRECT_URI='http://127.0.0.1:8000/api/github/auth/callback' \
  -e COMET_GITHUB_OAUTH_SCOPE='repo' \
  -e COMET_GITHUB_ENCRYPTED_TOKEN_STORE_PATH='./state/github/auth/token.enc' \
  -e COMET_GITHUB_ENCRYPTED_KEY_STORE_PATH='./state/github/auth/token.key' \
  -e COMET_GITHUB_MANAGED_CLONE_ROOT='./sandbox/github-managed' \
  comet-l:multi-jdk
```

运行配置（例如 `llm.api_key`、预算、开关等）建议在 Web 控制台中通过上传 YAML 或表单填写，避免把一次性运行参数和容器部署参数混在同一个 `config.yaml` 里。GitHub OAuth、token 存储路径和受管 clone 目录始终以部署时环境变量或服务端默认值为准。

`oauth_scope: "repo"` 用于访问私有仓库、push 同仓分支并创建 PR。如果只处理公开仓库，可以按你的安全策略收窄 scope，但同仓 push/PR 仍需要 GitHub 授权具备写权限。

token 不会写入 `config.yaml` 或浏览器本地存储。系统会优先使用系统 keyring；容器里通常没有可用 keyring，因此会退回到本地加密文件：

- 加密 token：`state/github/auth/token.enc`
- fallback 密钥：`state/github/auth/token.key`

因此 Docker 运行时建议持久化挂载 `/opt/comet-l/state`。如果删除该目录，前端会显示未连接，需要重新授权 GitHub。

### 当前本地限制

- Web 控制台当前按单用户本地场景设计，不包含多用户隔离与权限控制。
- 同一时刻只允许一个 active run；若已有运行中的任务，新的 `POST /api/runs` 会返回冲突错误。
- 本地路径模式下，项目路径必须是容器或本机可访问的 Maven 项目路径；GitHub 仓库模式下只支持 `https://github.com/{owner}/{repo}` 或 `.git` 结尾形式。
- 结果页只暴露 `final_state.json`、`run.log` 与 `report.md` 下载入口，不提供原始数据库文件下载。

## 使用示例

```bash
# 基本使用
just run /path/to/project

# 指定最大迭代次数
uv run python main.py --project-path /path/to/project --max-iterations 5

# 设置 LLM 调用预算
just run-budget /path/to/project 500

# 使用自定义配置
just run-config /path/to/project my-config.yaml

# 启用调试日志
just run-debug /path/to/project

# 指定 Bug 报告目录（用于 RAG 知识库）
uv run python main.py --project-path /path/to/project --bug-reports-dir /path/to/bug-reports

# 启用并行 Agent 模式（批量处理多个目标）
uv run python main.py --project-path /path/to/project --parallel

# 指定并行目标数
just run-parallel /path/to/project 8
```

## Bug 报告格式

系统支持多种格式的 Bug 报告用于 RAG 检索：

| 格式     | 扩展名   | 说明                         |
| -------- | -------- | ---------------------------- |
| Markdown | `.md`    | 支持可选的 YAML front-matter |
| 纯文本   | `.txt`   | 任意自然语言描述             |
| Diff     | `.diff`  | Git diff 输出                |
| Patch    | `.patch` | 补丁文件                     |

示例 Bug 报告：

```markdown
# 空指针异常

用户名为 null 时调用 getUserName() 会抛出 NullPointerException。
建议在方法入口添加空值检查。
```

## 项目结构

```
COMET-L/
├── comet/              # Python 主模块
│   ├── config/        # 配置管理
│   ├── llm/           # LLM 客户端
│   ├── knowledge/     # 知识库（含 RAG 组件）
│   ├── extractors/    # 知识提取器
│   ├── generators/    # 测试和变异生成器
│   ├── executor/      # 执行器
│   ├── agent/         # Agent 调度器
│   └── store/         # 数据存储
├── java-runtime/      # Java 执行模块
├── examples/          # 示例项目
├── state/             # 状态目录（数据库、向量库）
└── sandbox/           # 沙箱工作目录
```

## 工作原理

1. **初始化**：
   - 扫描源代码，提取方法契约
   - 深度分析代码模式（null检查、边界检查、异常处理等）
   - 索引 Bug 报告到向量数据库

2. **并行预处理**（可选）：
   - 为每个公共方法生成初始测试和变异体
   - 提取契约并索引到 RAG 知识库

3. **迭代循环**：
   - **标准模式**：顺序处理单个目标方法
   - **并行模式**：批量并行处理多个目标，提高吞吐量
   - 变异生成器创建语义变异体（RAG 提供相关 Bug 模式）
   - 执行测试识别幸存变异体
   - 测试生成器针对幸存变异生成新测试（RAG 提供契约和分析上下文）
   - Agent 调度器根据结果调整策略

4. **输出**：生成的测试类输出到项目的 `src/test/java/` 目录

## 技术栈

- **Python 侧**：Python 3.11+, OpenAI API, SQLite, Pydantic, ChromaDB, tiktoken
- **Java 侧**：Java Runtime（Java 25+）, Maven, JUnit5, JaCoCo, JavaParser, Mockito（测试生成默认 Java 8 语法；示例项目使用 JUnit 5.10.3 与 Mockito 4.11.0）

## 许可

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
