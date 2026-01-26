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
- Java 8+
- Maven 3.6+

### 安装

```bash
# 安装 Python 依赖
pip install -r requirements.txt

# 构建 Java 运行时模块
cd java-runtime
mvn clean package
cd ..
```

### 配置

复制配置模板并填入您的设置：

```bash
cp config.example.yaml config.yaml
```

编辑 `config.yaml`，配置 LLM API：

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "your-api-key"
  model: "gpt-4"

# RAG 知识库配置（可选）
knowledge:
  enabled: true  # 启用 RAG
  embedding:
    model: "text-embedding-3-small"
```

### 运行

对任意 Maven 项目运行协同进化：

```bash
python main.py --project-path /path/to/your/java/project
```

使用示例项目测试：

```bash
python main.py --project-path examples/calculator-demo
```

## 使用示例

```bash
# 基本使用
python main.py --project-path /path/to/project

# 指定最大迭代次数
python main.py --project-path /path/to/project --max-iterations 5

# 设置 LLM 调用预算
python main.py --project-path /path/to/project --budget 500

# 使用自定义配置
python main.py --project-path /path/to/project --config my-config.yaml

# 启用调试日志
python main.py --project-path /path/to/project --debug

# 指定 Bug 报告目录（用于 RAG 知识库）
python main.py --project-path /path/to/project --bug-reports-dir /path/to/bug-reports

# 启用并行 Agent 模式（批量处理多个目标）
python main.py --project-path /path/to/project --parallel

# 指定并行目标数
python main.py --project-path /path/to/project --parallel --parallel-targets 8
```

## Bug 报告格式

系统支持多种格式的 Bug 报告用于 RAG 检索：

| 格式 | 扩展名 | 说明 |
|------|--------|------|
| Markdown | `.md` | 支持可选的 YAML front-matter |
| 纯文本 | `.txt` | 任意自然语言描述 |
| Diff | `.diff` | Git diff 输出 |
| Patch | `.patch` | 补丁文件 |

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
├── cache/             # 缓存目录（数据库、向量库）
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
- **Java 侧**：Java 8+, Maven, JUnit5, JaCoCo, JavaParser, Mockito 5

## 文档

详细文档请查看 `docs/` 目录：

- [系统架构](docs/LLM%20驱动的测试变异协同进化系统.md)
- [实现计划](docs/comet-l.plan.md)

## 许可

MIT License

## 贡献

欢迎提交 Issue 和 Pull Request！
