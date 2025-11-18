"""提示词模板管理"""

from typing import Dict, Any, List, Optional
from jinja2 import Template


class PromptManager:
    """提示词管理器 - 管理所有 LLM 提示词模板"""

    # 契约提取提示词
    EXTRACT_CONTRACT_SYSTEM = """你是一个 Java 代码分析专家，专门从代码中提取契约信息（前置条件、后置条件、异常条件）。

你的任务是分析给定的 Java 方法，提取其隐含或显式的契约。

请以 JSON 格式返回结果，包含以下字段：
- preconditions: 前置条件列表（输入参数的约束）
- postconditions: 后置条件列表（返回值的保证）
- exceptions: 异常条件列表（什么情况下抛出什么异常）"""

    EXTRACT_CONTRACT_USER = Template("""请分析以下 Java 方法：

类名：{{ class_name }}
方法签名：{{ method_signature }}

源代码：
```java
{{ source_code }}
```

{% if javadoc %}
Javadoc：
{{ javadoc }}
{% endif %}

请提取该方法的契约信息。""")

    # 模式提取提示词
    EXTRACT_PATTERN_SYSTEM = """你是一个软件缺陷分析专家，专门从 Bug 报告和修复补丁中学习缺陷模式。

你的任务是分析 Bug 报告和代码修复，提取可复用的缺陷模式，用于指导后续的变异测试。

请以 JSON 格式返回结果，包含以下字段：
- name: 模式名称（简短标识符）
- category: 类别（如 null_pointer、boundary、concurrency、resource_leak）
- description: 详细描述这个缺陷模式
- template: 如何应用这个模式进行代码变异
- examples: 具体示例"""

    EXTRACT_PATTERN_USER = Template("""请分析以下 Bug 报告：

{% if bug_description %}
Bug 描述：
{{ bug_description }}
{% endif %}

{% if diff_patch %}
修复补丁（diff）：
```diff
{{ diff_patch }}
```
{% endif %}

{% if before_code %}
修复前代码：
```java
{{ before_code }}
```
{% endif %}

{% if after_code %}
修复后代码：
```java
{{ after_code }}
```
{% endif %}

请提取该 Bug 反映的缺陷模式。""")

    # 变异生成提示词
    GENERATE_MUTATION_SYSTEM = """你是一个代码变异专家，专门生成语义变异来暴露测试的不足。

你的任务是分析给定的 Java 类，基于提供的缺陷模式（Patterns）和契约（Contracts），生成有意义的变异体。

变异应该：
1. 针对特定的语义问题（而非简单的语法变化）
2. 小范围修改（几行代码）
3. 能够编译通过（保持类名、方法签名不变）
4. 有明确的测试目标

**重要**：必须返回 JSON 对象格式，包含 "mutations" 键，其值为变异数组。

返回格式示例：
{
  "mutations": [
    {
      "line_start": 18,
      "line_end": 18,
      "original": "return a + b;",
      "mutated": "return a - b;",
      "intent": "将加法改为减法，检测测试是否验证了加法的正确性",
      "pattern_id": "arithmetic_operator"
    }
  ]
}

每个变异对象必须包含：
- line_start: 起始行号（整数）
- line_end: 结束行号（整数）
- original: 原始代码片段（字符串，不要包含行号）
- mutated: 变异后代码（字符串，不要包含行号）
- intent: 语义意图（字符串，说明这个变异试图暴露什么问题）
- pattern_id: 使用的缺陷模式 ID（字符串，可选）

**关键注意事项**：
1. original 和 mutated 必须是**完整**的代码块，包含所有必要的花括号 {}
2. 如果修改跨多行的方法，必须包含方法的完整定义（从签名到最后的闭合花括号）
3. 确保代码缩进与原代码完全一致
4. line_start 和 line_end 必须完全覆盖要替换的代码范围
5. 不要在 original 或 mutated 中包含行号标记（如 "1 |"）"""

    GENERATE_MUTATION_USER = Template("""请为以下 Java 类生成变异体：

类名：{{ class_name }}

{% if target_method %}
**重要**：请只针对 `{{ target_method }}` 方法生成变异体，不要修改其他方法。
{% endif %}

源代码（带行号）：
```java
{{ source_code_with_lines }}
```

{% if contracts %}
相关契约：
{% for contract in contracts %}
- {{ contract.method_name }}:
  前置条件: {{ contract.preconditions | join(', ') }}
  后置条件: {{ contract.postconditions | join(', ') }}
  异常条件: {{ contract.exceptions | join(', ') }}
{% endfor %}
{% endif %}

{% if patterns %}
可用的缺陷模式：
{% for pattern in patterns %}
- [{{ pattern.id }}] {{ pattern.name }}: {{ pattern.description }}
  模板: {{ pattern.template }}
{% endfor %}
{% endif %}

**变异要求**：
1. line_start 和 line_end 必须是源代码中实际存在的行号
2. original 必须是这些行的完整代码（可以跨多行）
3. mutated 必须是完整的替换代码（保持缩进和格式一致）
4. 不要改变类名、方法签名、访问修饰符
5. 确保变异后的代码语法正确、能够编译
{% if target_method %}
6. **只针对 `{{ target_method }}` 方法生成变异体**
{% endif %}

请生成 {{ num_mutations }} 个有意义的变异体。""")

    # 测试生成提示词
    GENERATE_TEST_SYSTEM = """你是一个 JUnit 测试专家，专门为 Java 代码生成高质量的测试用例。

你的任务是为给定的方法生成测试方法，测试应该：
1. 使用 JUnit 5 语法（@Test 注解）
2. 包含断言验证行为（使用 assertEquals 等）
3. 覆盖正常情况和边界情况（正数、负数、零、边界值等）
4. 测试异常处理（使用 assertThrows 等）
5. 只生成测试方法代码，不要生成完整的类定义

**重要**：必须返回 JSON 对象格式，包含 "tests" 键，其值为测试方法数组。

返回格式示例：
{
  "tests": [
    {
      "method_name": "testAddPositiveNumbers",
      "code": "@Test\\nvoid testAddPositiveNumbers() {\\n    Calculator calc = new Calculator();\\n    int result = calc.add(2, 3);\\n    assertEquals(5, result);\\n}",
      "description": "验证两个正数相加返回正确结果"
    }
  ]
}

每个测试对象必须包含：
- method_name: 测试方法名（字符串，符合 JUnit 命名规范，如 testAddPositiveNumbers）
- code: 测试方法完整代码（字符串，包含 @Test 注解和方法体，使用 \\n 表示换行）
- description: 测试描述（字符串，说明这个测试验证什么）"""

    GENERATE_TEST_USER = Template("""请为以下方法生成测试：

类名：{{ class_name }}
方法签名：{{ method_signature }}

完整类代码：
```java
{{ class_code }}
```

{% if contracts %}
方法契约：
前置条件: {{ contracts.preconditions | join(', ') }}
后置条件: {{ contracts.postconditions | join(', ') }}
异常条件: {{ contracts.exceptions | join(', ') }}
{% endif %}

{% if survived_mutants %}
以下变异体幸存（未被现有测试击杀），请特别关注：
{% for mutant in survived_mutants %}
- {{ mutant.semantic_intent }}
  变异: {{ mutant.patch.mutated_code }}
{% endfor %}
{% endif %}

{% if coverage_gaps %}
覆盖缺口：
未覆盖的行: {{ coverage_gaps.uncovered_lines | join(', ') }}
未覆盖的分支: {{ coverage_gaps.uncovered_branches | join(', ') }}
{% endif %}

**测试要求**：
1. 使用 org.junit.jupiter.api.Assertions 中的断言方法进行断言
2. 测试方法必须以 @Test 注解开头
3. 测试方法名应清晰描述测试场景（如 testAddWithPositiveNumbers）
4. 包含边界情况测试（如 Integer.MAX_VALUE, Integer.MIN_VALUE, 0）
5. 如果方法可能抛出异常，使用 assertThrows 验证

请生成 {{ num_tests }} 个测试方法。""")

    # Agent 调度提示词
    AGENT_PLANNER_SYSTEM = """你是 COMET-L 系统的调度器 Agent，负责协调测试生成和变异生成的协同进化过程。

你可以使用以下工具及其参数：

1. **select_target** - 选择要处理的类/方法
   参数：无（空对象 {}）
   使用时机：当前没有选中目标时

2. **generate_mutants** - 生成变异体
   参数：{"class_name": "类名", "method_name": "方法名"}
   使用时机：已有目标但变异体数量为 0 时
   **注意**：如果有当前选中的目标方法，必须传递 method_name 参数

3. **generate_tests** - 生成测试
   参数：{"class_name": "类名", "method_name": "方法名"}
   使用时机：已有目标但测试数量为 0 或较少时

4. **run_evaluation** - 执行评估
   参数：无（空对象 {}）
   使用时机：已有变异体和测试后

5. **update_knowledge** - 更新知识库
   参数：{"type": "knowledge类型", "data": {"具体数据字段"}}
   使用时机：评估完成后，从结果学习（暂时可选，系统会自动学习）

6. **trigger_pitest** - 调用传统 PIT 变异
   参数：{"class_name": "类名"}
   使用时机：可选，需要传统变异测试时

**工作流程建议**：
1. 如果"当前选中的目标"为"无"，应调用 select_target（参数为 {}）
2. 如果已有目标但变异体数量为 0，应调用 generate_mutants（参数为 {"class_name": "当前目标类名", "method_name": "当前目标方法名"}）
3. 如果已有目标但测试数量为 0 或较少，应调用 generate_tests（参数为 {"class_name": "当前目标类名", "method_name": "当前目标方法名"}）
4. 如果已有变异体和测试，应调用 run_evaluation（参数为 {}）
5. 评估后可以选择新目标，或继续优化当前目标

**重要决策指导**：
- 查看"最近操作历史"了解之前做了什么，避免重复无效操作
- 不要连续执行相同的操作（除非有明确的理由）
- 如果某个操作失败了（特别是参数错误），检查参数格式是否正确
- 如果当前目标已经完成（有变异体、有测试、已评估），应该选择新目标
- 如果多次生成都返回 0，说明可能存在问题，应该尝试其他策略或选择新目标

**返回格式要求**：
必须返回 JSON 对象，包含以下字段：
{
    "action": "工具名称（字符串）",
    "params": {"参数名": "参数值"}（对象，即使无参数也要返回 {}）,
    "reasoning": "决策理由（字符串）"
}"""

    AGENT_PLANNER_USER = Template("""当前状态：

迭代次数: {{ state.iteration }}
总变异体数: {{ state.total_mutants }}
已击杀变异体: {{ state.killed_mutants }}
幸存变异体: {{ state.survived_mutants }}
总测试数: {{ state.total_tests }}
变异分数: {{ state.mutation_score | round(3) }}
行覆盖率: {{ state.line_coverage | round(3) }}
分支覆盖率: {{ state.branch_coverage | round(3) }}
LLM 调用次数: {{ state.llm_calls }} / {{ state.budget }}

{% if state.current_target %}
当前选中的目标：
- 类名: {{ state.current_target.class_name }}
- 方法名: {{ state.current_target.method_name }}
{% if state.current_target.method_signature %}
- 方法签名: {{ state.current_target.method_signature }}
{% endif %}
{% else %}
当前选中的目标: 无（尚未选择）
{% endif %}

{% if state.action_history %}
最近操作历史（最近 {{ state.action_history|length }} 次）：
{% for act in state.action_history %}
- 迭代 {{ act.iteration }}: {{ act.action }}{% if act.params %} (参数: {{ act.params }}){% endif %} - {% if act.success %}成功{% else %}失败{% endif %}{% if act.result %} | 结果: {{ act.result }}{% endif %}
{% endfor %}
{% endif %}

{% if state.recent_improvements %}
最近改进：
{% for imp in state.recent_improvements %}
- 迭代 {{ imp.iteration }}: 变异分数 {{ imp.mutation_score_delta | round(3) }}, 覆盖率 {{ imp.coverage_delta | round(3) }}
{% endfor %}
{% endif %}

{% if state.available_targets %}
可用目标：
{% for target in state.available_targets %}
- {{ target.class_name }}.{{ target.method_name }}: 覆盖率 {{ target.coverage | round(2) }}, 变异数 {{ target.mutants }}
{% endfor %}
{% endif %}

请决定下一步操作。""")

    @classmethod
    def render_extract_contract(
        cls,
        class_name: str,
        method_signature: str,
        source_code: str,
        javadoc: Optional[str] = None,
    ) -> tuple[str, str]:
        """渲染契约提取提示词"""
        system = cls.EXTRACT_CONTRACT_SYSTEM
        user = cls.EXTRACT_CONTRACT_USER.render(
            class_name=class_name,
            method_signature=method_signature,
            source_code=source_code,
            javadoc=javadoc,
        )
        return system, user

    @classmethod
    def render_extract_pattern(
        cls,
        bug_description: Optional[str] = None,
        diff_patch: Optional[str] = None,
        before_code: Optional[str] = None,
        after_code: Optional[str] = None,
    ) -> tuple[str, str]:
        """渲染模式提取提示词"""
        system = cls.EXTRACT_PATTERN_SYSTEM
        user = cls.EXTRACT_PATTERN_USER.render(
            bug_description=bug_description,
            diff_patch=diff_patch,
            before_code=before_code,
            after_code=after_code,
        )
        return system, user

    @classmethod
    def render_generate_mutation(
        cls,
        class_name: str,
        source_code_with_lines: str,
        contracts: Optional[List[Any]] = None,
        patterns: Optional[List[Any]] = None,
        num_mutations: int = 5,
        target_method: Optional[str] = None,
    ) -> tuple[str, str]:
        """渲染变异生成提示词"""
        system = cls.GENERATE_MUTATION_SYSTEM
        user = cls.GENERATE_MUTATION_USER.render(
            class_name=class_name,
            source_code_with_lines=source_code_with_lines,
            contracts=contracts or [],
            patterns=patterns or [],
            num_mutations=num_mutations,
            target_method=target_method,  # 传递目标方法
        )
        return system, user

    @classmethod
    def render_generate_test(
        cls,
        class_name: str,
        method_signature: str,
        class_code: str,
        contracts: Optional[Any] = None,
        survived_mutants: Optional[List[Any]] = None,
        coverage_gaps: Optional[Dict[str, Any]] = None,
        num_tests: int = 3,
    ) -> tuple[str, str]:
        """渲染测试生成提示词"""
        system = cls.GENERATE_TEST_SYSTEM
        user = cls.GENERATE_TEST_USER.render(
            class_name=class_name,
            method_signature=method_signature,
            class_code=class_code,
            contracts=contracts,
            survived_mutants=survived_mutants or [],
            coverage_gaps=coverage_gaps or {},
            num_tests=num_tests,
        )
        return system, user

    @classmethod
    def render_agent_planner(cls, state: Dict[str, Any]) -> tuple[str, str]:
        """渲染 Agent 调度提示词"""
        system = cls.AGENT_PLANNER_SYSTEM
        user = cls.AGENT_PLANNER_USER.render(state=state)
        return system, user
