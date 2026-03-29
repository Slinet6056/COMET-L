# Defects4J 固定测试回放指南

`replay-defects4j` 用于批量将一组**已经准备好的固定测试**回放到 Defects4J 的 `buggy` / `fixed` 版本，并导出按方法、按 bug、按测试粒度的执行结果。

它不负责生成测试，也不负责修复测试。这个命令只做三件事：

- 读取 manifest 清单，定位每条记录对应的 Defects4J 缺陷与测试目录
- 将测试目录注入到 `buggy` 和 `fixed` 工作树的 `src/test/` 下进行回放
- 收集 Maven Surefire 报告，判断哪些测试仅在 `buggy` 失败且在 `fixed` 通过

## 适用场景

适合以下情况：

- 你已经准备好了 B1 / B3 / B4 等方法对应的固定测试目录
- 你想批量评估这些测试是否真的能够区分 Defects4J 的 `buggy` / `fixed` 版本
- 你希望导出可进一步统计的结果文件，而不是手工逐个项目运行

不适合以下情况：

- 自动生成测试
- 替代默认 `run` 或 `study` 流程
- 直接处理非 Maven 项目

## 运行前准备

### 环境要求

- COMET-L 依赖已安装
- Maven 可用
- Java 环境可用；必要时可通过 `config.yaml` 中的 `execution.runtime_java_home`、`execution.target_java_home`、`execution.maven_home` 指定
- 目标工作树必须是 Maven 项目，根目录下需要存在 `pom.xml`
- 如果启用 `--checkout-mode local` 或 `--checkout-mode docker`，还需要可用的 Defects4J 环境

说明：这个命令会读取 `config.yaml`，但主要目的是复用 Java / Maven 执行环境配置，不依赖 LLM 流程。

### 输入准备

运行前需要准备两类输入：

- 一个 manifest 文件，定义要回放的任务列表
- 每条记录对应的测试目录

## Manifest 格式

manifest 是一份批量任务清单。每条记录至少说明：

- 这是哪个 Defects4J 项目和 bug
- 这条记录属于哪个方法标签，例如 `B1`、`B3`、`B4`
- 要注入哪一份测试目录
- 是否已经有现成的 `buggy` / `fixed` 工作树路径

### 支持的文件格式

- `.jsonl`：每行一个 JSON 对象
- `.json`：顶层必须是数组
- `.csv`：使用表头映射字段名

### 字段说明

| 字段         | 是否必填 | 说明                                       |
| ------------ | -------- | ------------------------------------------ |
| `project_id` | 是       | Defects4J 项目标识，例如 `Lang`、`Chart`   |
| `bug_id`     | 是       | 缺陷编号，按字符串读取                     |
| `method`     | 是       | 该测试集所属的方法标签，汇总统计按它聚合   |
| `test_path`  | 是       | 测试目录路径，不是单个文件路径             |
| `buggy_path` | 条件必填 | `buggy` 版本工作树根目录，需包含 `pom.xml` |
| `fixed_path` | 条件必填 | `fixed` 版本工作树根目录，需包含 `pom.xml` |
| `pom_override_path` | 否 | 仅对当前记录生效的 `pom.xml` 覆盖文件路径，适合 EvoSuite 等特殊测试依赖 |

规则：

- 如果 `--checkout-mode none`，manifest 中每条记录都必须同时提供 `buggy_path` 和 `fixed_path`
- 如果不提供 `buggy_path` / `fixed_path`，就必须启用 `--checkout-mode local` 或 `--checkout-mode docker`

### JSONL 示例

```jsonl
{"project_id":"Lang","bug_id":"1","method":"B1","test_path":"/data/tests/Lang-1/B1","buggy_path":"/data/d4j/Lang-1/buggy","fixed_path":"/data/d4j/Lang-1/fixed"}
{"project_id":"Lang","bug_id":"1","method":"B3","test_path":"/data/tests/Lang-1/B3","buggy_path":"/data/d4j/Lang-1/buggy","fixed_path":"/data/d4j/Lang-1/fixed"}
{"project_id":"Lang","bug_id":"1","method":"B4","test_path":"/data/tests/Lang-1/B4","buggy_path":"/data/d4j/Lang-1/buggy","fixed_path":"/data/d4j/Lang-1/fixed"}
```

如果某条记录需要专用 `pom.xml`，可以额外提供 `pom_override_path`。例如 EvoSuite：

```jsonl
{"project_id":"Lang","bug_id":"1","method":"B1","test_path":"/data/tests/Lang-1/B1","buggy_path":"/data/d4j/Lang-1/buggy","fixed_path":"/data/d4j/Lang-1/fixed","pom_override_path":"/data/poms/evosuite-pom.xml"}
```

### JSON 示例

```json
[
  {
    "project_id": "Chart",
    "bug_id": "2",
    "method": "B1",
    "test_path": "/data/tests/Chart-2/B1",
    "buggy_path": "/data/d4j/Chart-2/buggy",
    "fixed_path": "/data/d4j/Chart-2/fixed"
  }
]
```

### CSV 示例

```csv
project_id,bug_id,method,test_path,buggy_path,fixed_path
Lang,1,B4,/data/tests/Lang-1/B4,/data/d4j/Lang-1/buggy,/data/d4j/Lang-1/fixed
```

## 测试目录约定

`test_path` 必须是一个目录，而不是单个测试文件。

回放时，工具会把 `test_path` 下的内容复制到 sandbox 中目标项目的 `src/test/` 目录下。因此，推荐你的测试 bundle 直接从 `java/` 这一层开始组织。

例如，如果你的 `test_path` 是：

```text
/data/tests/Lang-1/B4/
└── java/
    └── org/
        └── example/
            └── ReplayTest.java
```

那么回放到 sandbox 后，对应文件会出现在：

```text
<workspace>/src/test/java/org/example/ReplayTest.java
```

### 现有测试目录的处理方式

回放发生在 sandbox 中，不会直接修改 manifest 指向的原始工作树。

在 sandbox 的 `src/test/java` 中：

- 现有的 `*Test.java` 会先被删除
- 回放时会逐个保留一个候选 `*Test.java`，并把它与 bundle 中的非 `*Test.java` 辅助文件一起复制到 `src/test/` 下
- 原项目中非 `*Test.java` 的测试辅助类会被保留

如果 manifest 提供了 `pom_override_path`，回放器会在 sandbox 中先用该文件覆盖 `pom.xml`，然后再执行 Maven。这个覆盖只发生在 sandbox 内，不会修改原始 `buggy_path` / `fixed_path` 工作树。

这意味着当前实现更适合“借用项目现有测试支架 + 替换测试类”的场景。如果某些项目的测试命名不止 `*Test.java` 一种，需要额外注意结果解释。

## CLI 用法

### 基本命令

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out
```

默认 `--checkout-mode` 为 `none`，因此默认模式要求 manifest 中已经提供 `buggy_path` 和 `fixed_path`。

### 使用已有 `buggy` / `fixed` 工作树

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out \
  --checkout-mode none
```

适合：你已经提前准备好了每条记录对应的 `buggy` / `fixed` 路径。

### 使用本地 Defects4J 自动 checkout

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out \
  --checkout-mode local \
  --defects4j-root /path/to/defects4j \
  --checkout-root /path/to/checkouts
```

说明：

- `--defects4j-root` 指向 Defects4J 源码根目录
- `--checkout-root` 是缓存 `buggy` / `fixed` 工作树的目录
- checkout 结果会缓存到类似 `<checkout-root>/<project_id>-<bug_id>/buggy` 和 `.../fixed`

### 使用 Docker 自动 checkout

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out \
  --checkout-mode docker \
  --checkout-root /path/to/checkouts \
  --docker-image defects4j-local
```

当前 Docker 模式使用镜像内的 `/defects4j` 执行 checkout。

当前 Docker 模式要求提供：

- `--checkout-root`
- `--docker-image`

说明：

- `--checkout-root` 仍用于把 checkout 出来的 `buggy` / `fixed` 工作树持久化回宿主机
- 如果传入 `--defects4j-root`，docker 模式会忽略它

### 并行回放与缓存刷新

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out \
  --checkout-mode local \
  --defects4j-root /path/to/defects4j \
  --checkout-root /path/to/checkouts \
  --max-workers 4 \
  --refresh-checkouts
```

- `--max-workers` 控制并行回放任务数
- 如果未显式指定，会优先尝试使用 `preprocessing.max_workers`，否则串行执行
- `--refresh-checkouts` 会忽略已有 checkout 缓存并重新拉取工作树

### EvoSuite 测试与 `xvfb-run`

如果你的固定测试中包含 EvoSuite 生成测试，通常建议启用：

```bash
uv run python main.py replay-defects4j \
  --manifest /path/to/manifest.jsonl \
  --output-dir /path/to/out \
  --use-xvfb
```

启用后，回放器会使用 `xvfb-run -a mvn ...` 执行 `test-compile` 和 `test`。

适用场景：

- 测试依赖 AWT / 图形环境
- EvoSuite 测试在无显示环境下运行会报错

如果不启用，某些 EvoSuite 测试可能会因为图形环境缺失而失败，这类失败不应直接解释为“成功触发了 bug”。

如果 EvoSuite 测试还依赖专用 `pom.xml`，请同时在 manifest 中设置 `pom_override_path`。

## 执行逻辑

对 manifest 中的每条记录，工具会按以下顺序执行：

1. 定位或 checkout 对应的 `buggy` / `fixed` 工作树
2. 枚举 `test_path` 下的 `*Test.java` 候选文件
3. 对每个候选文件创建 `fixed` sandbox，并注入“当前测试文件 + bundle 中的辅助文件”
4. 在 `fixed` 上先执行 `mvn test-compile`，保留 `compile_fixed=true` 的文件
5. 对这些文件继续在 `fixed` 上执行 `mvn test -Dmaven.test.failure.ignore=true`，保留 `pass_fixed=true` 的文件
6. 仅对 `fixed-pass` 的文件创建 `buggy` sandbox 并执行同样的测试命令
7. 统计哪些文件满足 `fail_buggy=true`
8. 按 defect 聚合结果与指标，再导出明细文件

## 结果文件

成功执行后，`--output-dir` 下会生成：

- `summary.json`
- `per_bug.csv`
- `per_test.csv`
- `defects4j-replay.log`

同时还会创建运行时目录：

- `.defects4j-replay-state/`
- `.defects4j-replay-sandbox/`

### `summary.json`

`summary.json` 是汇总视图，包含：

- `manifest_path`
- `output_dir`
- `total_entries`
- `checkout_mode`
- `per_method`

其中 `per_method` 按 manifest 中的 `method` 字段聚合，并按 defect 去重统计，当前包含：

- `total_defects`
- `compatible_defects`
- `compatibility_rate`
- `valid_regression_defects`
- `valid_regression_test_rate`
- `end_to_end_success_defects`
- `end_to_end_success_rate`

这 3 个核心指标的定义是：

- `compatibility_rate = compatible_defects / total_defects`
- `valid_regression_test_rate = valid_regression_defects / compatible_defects`
- `end_to_end_success_rate = end_to_end_success_defects / total_defects`

其中：

- `compatible_defect` 表示该 defect 至少有一个测试文件满足 `compile_fixed=true`
- `valid_regression_defect` / `end_to_end_success_defect` 表示该 defect 至少有一个测试文件满足 `fixed-pass` 且 `buggy-fail`

注意：这里的 `method` 是你在 manifest 中提供的分组标签，不是工具自动从源码中提取的方法签名。

### `per_bug.csv`

`per_bug.csv` 每行对应一条 manifest 记录，主要字段包括：

- 基本信息：`method`, `project_id`, `bug_id`, `test_path`
- 版本路径：`buggy_source_path`, `fixed_source_path`
- defect 级漏斗：`compile_fixed`, `pass_fixed`, `fail_buggy`
- 文件计数：`compatible_test_file_count`, `pass_fixed_test_file_count`, `fail_buggy_test_file_count`
- 文件列表：`compatible_test_files`, `pass_fixed_test_files`, `fail_buggy_test_files`
- 兼容字段：`compile_valid`, `buggy_compile_success`, `fixed_compile_success`, `triggered`
- 测试统计：`buggy_total_tests`, `buggy_failed_tests`, `buggy_error_tests`, `fixed_total_tests`, `fixed_failed_tests`, `fixed_error_tests`
- 触发统计：`triggered_test_count`, `triggered_test_names`
- 失败详情：`buggy_failed_test_names`, `fixed_failed_test_names`
- 编译错误：`buggy_compile_error`, `fixed_compile_error`
- 一致性：`consistency_ratio`

判定规则：

- `compile_fixed=true` 表示该 defect 至少有一个测试文件能在 `fixed` 上完成编译
- `pass_fixed=true` 表示该 defect 至少有一个测试文件在 `fixed` 上编译成功且执行通过
- `fail_buggy=true` 表示该 defect 至少有一个 `fixed-pass` 文件在 `buggy` 上执行失败或报错
- `triggered` 当前与 `fail_buggy` 等价，用于兼容旧下游
- `consistency_ratio = fail_buggy_test_file_count / pass_fixed_test_file_count`

### `per_test.csv`

`per_test.csv` 现在每行对应一个测试文件，主要字段包括：

- `method`, `project_id`, `bug_id`, `test_file`
- `compile_fixed`, `pass_fixed`, `fail_buggy`
- `fixed_compile_error`, `buggy_compile_error`
- `fixed_total_tests`, `fixed_failed_tests`, `fixed_error_tests`
- `buggy_total_tests`, `buggy_failed_tests`, `buggy_error_tests`

这里的 `pass_fixed` 要求该测试文件在 `fixed` 上实际执行到至少一个测试用例，并且没有失败或报错；只有满足这一条件的文件，才会继续进入 `buggy` 阶段。

## 当前限制

这版实现适合作为受控条件下的 MVP，当前限制如下：

- 仅支持 Maven 项目，项目根目录必须存在 `pom.xml`
- manifest 仅支持 `.jsonl`、`.json`、`.csv`
- `test_path` 必须是目录，不支持单个测试文件
- 默认 `--checkout-mode none` 不会自动 checkout
- `local` 模式依赖宿主机可用的 Defects4J 环境
- `docker` 模式依赖镜像内已准备好的 Defects4J 环境，以及宿主机可写的 `checkout_root`
- 对包含 EvoSuite 测试的样本，通常需要显式启用 `--use-xvfb`
- 对需要特殊依赖的测试集，可以通过 manifest 的 `pom_override_path` 为单条记录切换 `pom.xml`
- 当前结果解析依赖 Maven Surefire XML 报告
- 该命令只负责回放与统计，不负责生成、筛选或修复测试
- 由于当前会保留原项目中的非 `*Test.java` 辅助类，这更适合“借用项目测试支架”的场景

## 常见出错原因

- `manifest` 路径不存在
- manifest 为空
- JSON manifest 顶层不是数组
- manifest 后缀不是 `.jsonl`、`.json`、`.csv`
- `test_path` 不是目录或目录不存在
- `buggy_path` / `fixed_path` 不是有效 Maven 项目，缺少 `pom.xml`
- `checkout-mode=none` 时缺少 `buggy_path` 或 `fixed_path`
- `checkout-mode=local` 时缺少 `defects4j_root` 或 `checkout_root`
- `checkout-mode=docker` 时缺少 `checkout_root` 或 `docker_image`
- 某个测试文件在 `fixed` 上 `mvn test-compile` 失败，导致它无法进入后续漏斗

## 最小成功示例

目录结构：

```text
manifest.jsonl
tests/
└── lang-1/
    └── B4/
        └── java/
            └── org/
                └── example/
                    └── ReplayTest.java
```

命令：

```bash
uv run python main.py replay-defects4j \
  --manifest manifest.jsonl \
  --output-dir .artifacts/defects4j-replay
```

运行完成后建议优先查看：

- `summary.json`：看 defect 级兼容率、有效回归测试率和端到端成功率
- `per_bug.csv`：看每个 defect 在 fixed-first 漏斗中的聚合结果
- `per_test.csv`：看每个测试文件落在哪个漏斗阶段
