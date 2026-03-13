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
  base_url: "https://api.openai.com/v1"
  api_key: "your-api-key"
  model: "gpt-4"

# RAG 知识库配置（可选）
knowledge:
  enabled: true # 启用 RAG
  embedding:
    model: "text-embedding-3-small"

execution:
  runtime_java_home: "/usr/lib/jvm/java-25-openjdk"
  target_java_home: "/usr/lib/jvm/java-8-openjdk"
```

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
```

### 当前本地限制

- Web 控制台当前按单用户本地场景设计，不包含多用户隔离与权限控制。
- 同一时刻只允许一个 active run；若已有运行中的任务，新的 `POST /api/runs` 会返回冲突错误。
- 项目路径输入必须是本机可访问的 Maven 项目绝对路径或可解析本地路径，后端不会代理远程文件系统。
- 结果页只暴露 `final_state.json` 与 `run.log` 下载入口，不提供原始数据库文件下载。

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
