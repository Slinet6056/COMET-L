# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

COMET-L（LLM 驱动的测试变异协同进化系统）是一个创新的自动化测试生成系统，通过测试生成器和变异生成器的对抗式协同进化来自动提升 Java 项目的测试质量。系统包含 Python 主控制器和 Java 运行时两部分。

## 开发环境

### 环境要求

- Python 3.13+ (项目使用 Python 3.14)
- Java 25+ (使用 direnv 自动管理，默认 Java 25；用于运行 COMET-L 与 Java Runtime)
- Maven 3.6+
- 目标项目可为 Java 8+（测试生成默认使用 Java 8 语法，兼容 JDK8 项目）
- 推荐使用 direnv + uv 自动同步 Python 依赖并激活 Python/Java 环境

### 环境设置

```bash
# direnv 会自动执行 uv sync 并设置 Java 25
# 首次使用需允许: direnv allow

# 使用 uv 同步 Python 依赖（包括 ChromaDB、sentence-transformers 等）
uv sync

# 构建 Java 运行时模块（必须先完成此步骤）
cd java-runtime
mvn clean package
cd ..
```

**注意**：首次运行时，系统会自动下载嵌入模型（sentence-transformers），可能需要一些时间。

## 常用命令

### 构建和测试

```bash
# 构建 Java 运行时模块
cd java-runtime && mvn clean package && cd ..

# 运行系统（使用示例项目）
uv run python main.py --project-path examples/calculator-demo

# 运行系统（带调试日志）
uv run python main.py --project-path /path/to/project --debug

# 使用自定义配置
uv run python main.py --project-path /path/to/project --config my-config.yaml

# 设置迭代次数和预算
uv run python main.py --project-path /path/to/project --max-iterations 5 --budget 500

# 从中断状态恢复
uv run python main.py --project-path /path/to/project --resume output/interrupted_state.json
```

### 配置文件

配置文件使用 YAML 格式，模板为 `config.example.yaml`。首次使用需复制并修改：

```bash
cp config.example.yaml config.yaml
# 编辑 config.yaml，设置 LLM API 密钥和其他参数
```

**主要配置项**：

- `llm` - LLM 相关配置（API 密钥、模型、温度等）
- `knowledge` - RAG 知识库配置（启用开关、检索参数、嵌入模型）
- `preprocessing` - 并行预处理配置（启用开关、工作线程数）
- `formatting` - 代码格式化配置（Java 代码风格）

## 系统架构

### 核心流程

1. **并行预处理阶段**（可选，默认启用）
   - 扫描项目所有公共方法
   - 并行为每个方法生成初始测试和变异体
   - 配置：`config.yaml` 中的 `preprocessing.enabled` 和 `preprocessing.max_workers`

2. **Agent 主循环**（PlannerAgent 驱动）
   - 选择目标方法（select_target）
   - 生成/完善测试或变异体（generate_tests/refine_tests/generate_mutants/refine_mutants）
   - 运行评估获取覆盖率和变异分数（run_evaluation）
   - 根据全局指标检查改进和停止条件

3. **沙箱机制**
   - workspace 沙箱：主工作目录，所有测试文件都在此生成
   - target 沙箱：为每个变异体创建临时沙箱，评估后自动清理
   - 最终导出：测试文件从 workspace 沙箱复制回原项目

### 关键组件映射

**Python 侧（主控制器）**

- `comet/agent/` - Agent 调度器，协调整个进化过程
  - `planner.py` - 核心调度逻辑，决策下一步操作
  - `tools.py` - Agent 工具集，封装所有操作为标准接口
  - `state.py` - 全局状态管理
  - `target_selector.py` - 目标方法选择策略

- `comet/generators/` - 测试和变异生成器
  - `test_generator.py` - 使用 LLM 生成 JUnit5 测试
  - `mutant_generator.py` - 使用 LLM 生成语义变异体
  - `static_guard.py` - 静态验证（调用 Java 模块）

- `comet/executor/` - 执行和评估
  - `java_executor.py` - Python 侧 Java 调用接口
  - `mutation_evaluator.py` - 变异体评估流程
  - `coverage_parser.py` - 解析 JaCoCo 覆盖率报告
  - `surefire_parser.py` - 解析 Maven Surefire 测试报告

- `comet/knowledge/` - RAG 知识库系统（最新集成）
  - `knowledge_base.py` - 知识库主类，统一管理源代码契约和 Bug 模式
  - `vector_store.py` - ChromaDB 向量存储封装，支持语义检索
  - `chunker.py` - 智能文本分块器，针对代码和 Bug 报告优化
  - `embedding.py` - 嵌入生成器，支持 OpenAI embeddings
  - `retriever.py` - 检索器，实现混合检索策略（语义+关键词）
  - `bug_parser.py` - Bug 报告解析器，从 Markdown 提取缺陷模式
  - 从源代码提取契约（前置条件、后置条件、不变量）
  - 从 Bug 报告学习缺陷模式，为测试生成提供上下文

- `comet/llm/` - LLM 客户端和提示词管理
  - `client.py` - OpenAI API 客户端
  - `prompts.py` - Jinja2 模板管理器

- `comet/utils/` - 工具类
  - `sandbox.py` - 沙箱管理器
  - `project_scanner.py` - 项目扫描，建立类到文件的映射
  - `java_formatter.py` - Java 代码格式化（调用 Java 模块）

**Java 侧（运行时模块）**

- `java-runtime/src/main/java/com/comet/`
  - `analyzer/` - 代码分析（JavaParser）
    - `DeepAnalyzer.java` - 深度代码分析，提取方法签名、依赖关系、控制流信息
  - `executor/` - 测试执行和覆盖率收集
  - `mutator/` - 变异体应用
  - `formatter/` - 代码格式化（使用 google-java-format）
  - `models/` - 数据模型

### 重要实现细节

**代码格式化**

- 系统使用 `google-java-format` 格式化生成的 Java 代码
- 配置位于 `config.yaml` 的 `formatting` 部分
- 格式化在测试代码写入文件前自动调用

**行号系统**

- 最新改动：为类代码添加行号以提高 LLM 定位准确性
- 变异体生成时使用带行号的代码，LLM 返回具体行范围

**数据库结构**

- SQLite 存储变异体、测试用例、覆盖率数据
- 变异体状态：pending（待验证）→ valid/invalid（静态验证后）→ 评估后更新 survived 字段
- 测试用例关联目标方法，支持增量生成

**并行预处理**

- 使用 ThreadPoolExecutor 并发处理方法
- 每个方法在独立的 target 沙箱中生成和验证
- 预处理完成后清理所有临时沙箱，释放资源

**Mockito 支持**

- 系统自动识别外部依赖并使用 Mockito 创建隔离的单元测试（面向 JDK8 目标项目建议 4.x，示例项目锁定 4.11.0）
- 生成的测试包含必要的 @ExtendWith(MockitoExtension.class) 和 @Mock 注解

**RAG 知识库系统**（最新集成）

- 使用 ChromaDB 作为向量数据库，支持语义检索
- 智能文本分块：代码分块保留方法完整性，Bug 报告按语义段落分块
- 混合检索策略：结合语义相似度和关键词匹配，提高检索准确性
- 知识来源：
  - 源代码契约：从 JavaDoc、异常处理、断言提取前置/后置条件
  - Bug 报告：从 `examples/*/bug-reports/*.md` 学习历史缺陷模式
- 配置位于 `config.yaml` 的 `knowledge` 部分
- 为测试生成和变异体生成提供领域知识上下文，提高生成质量

## 开发注意事项

### LLM 提示词修改

提示词模板位于 `comet/llm/prompts/` 目录，使用 Jinja2 格式。修改后无需重新构建，直接运行即可生效。

### Java 运行时修改

修改 `java-runtime/` 下的 Java 代码后，必须重新构建：

```bash
cd java-runtime && mvn clean package && cd ..
```

### 调试技巧

- 使用 `--debug` 标志启用详细日志
- 日志文件：`comet.log`
- 沙箱目录：`./sandbox/` - 保留了所有中间文件，便于调试
- 数据库文件：
  - `./cache/comet.db` - 变异体、测试用例、覆盖率数据
  - `./cache/knowledge.db` - RAG 知识库（契约和 Bug 模式）
  - 可用 SQLite 工具查看
- ChromaDB 向量数据：`./cache/chroma/` - 语义检索索引

### 测试输出

生成的测试文件最终会导出到原项目的 `src/test/java/` 目录，命名规则：`{ClassName}_{methodName}Test.java`

### 停止条件

系统会在以下情况自动停止：

- 达到最大迭代次数（`max_iterations`）
- 达到 LLM 调用预算（`budget_llm_calls`）
- 连续 N 轮无改进（`stop_on_no_improvement_rounds`，默认 3）
- 达到优秀质量水平（变异分数 ≥ 95%，行覆盖率 ≥ 90%，分支覆盖率 ≥ 85%）
- 没有更多可选目标方法

### 代码注释语言

遵循现有代码的注释语言：

- Python 代码：主要使用中文注释
- Java 代码：主要使用英文注释
- 新文件：根据上下文决定，优先保持一致性
