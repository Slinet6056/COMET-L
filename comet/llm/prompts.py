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

    # 变异完善提示词
    REFINE_MUTATION_SYSTEM = """你是一个高级代码变异专家，专门基于现有测试的弱点生成更具针对性的变异体。

你的任务是分析现有的变异体、测试代码和击杀率，生成新的、更难被测试检测的变异体。

分析策略：
1. **研究测试代码**：查看测试方法的断言、边界检查、异常处理
2. **识别测试盲区**：找出测试没有充分验证的场景（如未测试的边界值、遗漏的异常情况）
3. **针对性变异**：生成专门针对测试弱点的变异

变异应该：
1. 针对测试未覆盖或覆盖不足的语义问题
2. 小范围修改（几行代码）
3. 能够编译通过
4. 代表真实可能出现的缺陷

**重要**：必须返回 JSON 对象格式，包含 "mutations" 键，其值为变异数组。

返回格式与 GENERATE_MUTATION 相同：
{
  "mutations": [
    {
      "line_start": 行号,
      "line_end": 行号,
      "original": "原始代码",
      "mutated": "变异代码",
      "intent": "针对测试的哪个弱点（说明测试为什么检测不到这个变异）",
      "pattern_id": "模式ID（可选）"
    }
  ]
}

每个变异对象必须包含：
- line_start: 起始行号（整数）
- line_end: 结束行号（整数）
- original: 原始代码片段（字符串，不要包含行号）
- mutated: 变异后代码（字符串，不要包含行号）
- intent: 语义意图（字符串，说明为什么这个变异能利用测试的弱点）
- pattern_id: 使用的缺陷模式 ID（字符串，可选）"""

    REFINE_MUTATION_USER = Template("""请基于现有测试生成更具针对性的变异体：

类名：{{ class_name }}

{% if target_method %}
**目标方法**：请只针对 `{{ target_method }}` 方法生成变异体。
{% endif %}

源代码（带行号）：
```java
{{ source_code_with_lines }}
```

{% if test_cases %}
现有测试代码：
{% for test_case in test_cases %}
测试类：{{ test_case.class_name }}
{% for method in test_case.methods %}
---
方法名：{{ method.method_name }}
```java
{{ method.code }}
```
{% endfor %}
{% endfor %}
{% endif %}

{% if existing_mutants %}
现有变异体（参考，避免重复）：
{% for mutant in existing_mutants[:10] %}
- {{ mutant.semantic_intent }}
  状态：{{ '被击杀' if not mutant.survived else '幸存' }}
{% endfor %}
{% endif %}

当前击杀率：{{ "%.1f%%"|format(kill_rate * 100) }}

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
{% endfor %}
{% endif %}

**任务**：
1. 仔细分析测试代码的断言和验证逻辑
2. 找出测试的盲区（未测试的边界值、异常情况、特殊输入组合）
3. 生成 {{ num_mutations }} 个针对这些盲区的变异体
4. 每个变异的 intent 应明确说明它利用了测试的哪个弱点

**变异要求**：
1. line_start 和 line_end 必须是源代码中实际存在的行号
2. original 必须是这些行的完整代码
3. mutated 必须是完整的替换代码（保持缩进和格式一致）
4. 不要改变类名、方法签名、访问修饰符
5. 确保变异后的代码语法正确、能够编译
{% if target_method %}
6. **只针对 `{{ target_method }}` 方法生成变异体**
{% endif %}""")

    # 测试生成提示词
    GENERATE_TEST_SYSTEM = """你是一个 JUnit 测试专家，专门为 Java 代码生成高质量的测试用例。

你的任务是为给定的方法生成测试方法。**你可以自主决定生成多少个测试方法**，根据方法的复杂度和需要覆盖的场景来判断。

测试应该：
1. 使用 JUnit 5 语法（@Test 注解）
2. 包含断言验证行为（直接使用 assertEquals 等，不要加 Assertions. 前缀）
3. 覆盖正常情况和边界情况（正数、负数、零、边界值等）
4. 测试异常处理（使用 assertThrows 等）
5. 只生成测试方法代码，不要生成完整的类定义
6. 如果提供了现有测试，应避免重复，补充缺失的测试场景

**生成数量指导**：
- 简单方法（如 getter/setter）：1-2 个测试
- 中等复杂度（如计算、验证）：3-5 个测试
- 复杂方法（多分支、多异常）：5-10 个测试

**断言方法使用规范**：
- ✔ 正确：直接使用 `assertEquals(expected, actual)`
- ✖ 错误：不要使用 `Assertions.assertEquals(expected, actual)`
- ✔ 正确：直接使用 `assertTrue(condition)`
- ✖ 错误：不要使用 `Assertions.assertTrue(condition)`
- 原因：测试类会使用静态导入 `import static org.junit.jupiter.api.Assertions.*`

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

{% if existing_tests %}
现有测试方法（请避免重复，补充缺失的场景）：
{% for test in existing_tests %}
- {{ test.class_name }}: {{ test.methods|length }} 个测试方法
{% for method in test.methods %}
  * {{ method.method_name }}: {{ method.description or '无描述' }}
{% endfor %}
{% endfor %}
{% endif %}

{% if survived_mutants %}
以下变异体幸存（未被现有测试击杀），请特别关注：
{% for mutant in survived_mutants %}
- {{ mutant.semantic_intent }}
  变异: {{ mutant.patch.mutated_code }}
{% endfor %}
{% endif %}

{% if coverage_gaps and coverage_gaps.coverage_rate is defined %}
**覆盖率缺口分析**：
- 当前行覆盖率：{{ "%.1f"|format(coverage_gaps.coverage_rate * 100) }}%
- 已覆盖：{{ coverage_gaps.covered_lines }}/{{ coverage_gaps.total_lines }} 行
{% if coverage_gaps.uncovered_lines %}
- 未覆盖的行号：{{ coverage_gaps.uncovered_lines | join(', ') }}
- **请重点针对这些未覆盖的行号生成测试用例**
{% else %}
- **该方法已达到100%行覆盖率，请关注分支覆盖和边界情况**
{% endif %}
{% endif %}

**测试要求**：
1. **断言方法**：直接使用 assertEquals、assertTrue、assertThrows 等，不要添加 Assertions. 前缀
2. 测试方法必须以 @Test 注解开头
3. 测试方法名应清晰描述测试场景（如 testAddWithPositiveNumbers）
4. 包含边界情况测试（如 Integer.MAX_VALUE, Integer.MIN_VALUE, 0）
5. 如果方法可能抛出异常，使用 assertThrows 验证
6. 根据方法复杂度自主决定生成多少个测试（简单方法 1-2 个，复杂方法 5-10 个）

**示例（正确的断言写法）**：
```java
@Test
void testAddPositiveNumbers() {
    Calculator calc = new Calculator();
    int result = calc.add(2, 3);
    assertEquals(5, result);  // ✔ 正确：直接使用 assertEquals
    // 错误示例：Assertions.assertEquals(5, result);  ✖ 不要这样写
}
```

请生成适量的测试方法。""")

    # 测试完善提示词
    REFINE_TEST_SYSTEM = """你是一个 JUnit 测试专家，专门完善和改进现有的测试用例。

你的任务是根据评估反馈（如幸存的变异体、覆盖缺口等）来完善现有测试。你可以：
1. **改进现有测试**：增强断言、添加边界检查、修复逻辑错误
2. **补充新测试**：添加缺失的测试场景
3. **删除冗余测试**：移除重复或无效的测试
4. **重构测试**：提高测试质量和可维护性

**策略选择**：
- 如果现有测试覆盖了基本场景但不够细致：改进现有测试
- 如果存在明显的测试缺口：补充新测试
- 如果现有测试有明显问题：修正或重写
- 优先考虑击杀幸存变异体的测试

**断言方法使用规范**：
- ✔ 正确：直接使用 `assertEquals(expected, actual)`
- ✖ 错误：不要使用 `Assertions.assertEquals(expected, actual)`
- ✔ 正确：直接使用 `assertTrue(condition)`
- ✖ 错误：不要使用 `Assertions.assertTrue(condition)`
- 原因：测试类会使用静态导入 `import static org.junit.jupiter.api.Assertions.*`

**重要**：必须返回 JSON 对象格式，包含 "tests" 或 "refined_tests" 键。

返回格式示例：
{
  "refined_tests": [
    {
      "method_name": "testAddPositiveNumbers",
      "code": "@Test\\nvoid testAddPositiveNumbers() {\\n    Calculator calc = new Calculator();\\n    int result = calc.add(2, 3);\\n    assertEquals(5, result);\\n}",
      "description": "验证两个正数相加返回正确结果",
      "target_method": "add"
    }
  ],
  "refinement_summary": "改进了 2 个测试，新增了 3 个测试，删除了 1 个冗余测试"
}

每个测试对象必须包含：
- method_name: 测试方法名
- code: 完整测试代码（包含 @Test 注解）
- description: 测试描述
- target_method: 目标方法名（可选）"""

    REFINE_TEST_USER = Template("""请完善以下测试用例：

目标类：{{ test_case.target_class }}
{% if target_method %}
目标方法：{{ target_method }}（请重点针对此方法进行测试优化）
{% endif %}
测试类：{{ test_case.class_name }}

被测类完整代码：
```java
{{ class_code }}
```

当前测试方法（共 {{ test_case.methods|length }} 个）：
{% for method in test_case.methods %}
### {{ method.method_name }}
```java
{{ method.code }}
```
{% if method.description %}
描述: {{ method.description }}
{% endif %}

{% endfor %}

{% if survived_mutants %}
**幸存变异体（需要击杀）**：
{% if target_method %}
以下变异体来自目标方法 {{ target_method }}，需要重点击杀：
{% endif %}
{% for mutant in survived_mutants %}
- {{ mutant.semantic_intent }}
  变异代码: {{ mutant.patch.mutated_code }}
{% endfor %}
{% endif %}

{% if coverage_gaps %}
**覆盖缺口**：
{% if target_method %}
以下是目标方法 {{ target_method }} 的覆盖缺口：
{% endif %}
未覆盖的行: {{ coverage_gaps.uncovered_lines | join(', ') }}
未覆盖的分支: {{ coverage_gaps.uncovered_branches | join(', ') }}
{% endif %}

{% if evaluation_feedback %}
**评估反馈**：
{{ evaluation_feedback }}
{% endif %}

**完善要求**：
1. 分析现有测试的不足之处
{% if target_method %}
2. **重点关注目标方法 {{ target_method }}**，优先击杀其幸存的变异体
{% else %}
2. 重点关注如何击杀幸存的变异体
{% endif %}
3. 补充缺失的测试场景（边界值、异常情况等）
4. 改进现有测试的断言和验证逻辑
5. 返回完整的测试方法列表（包括保留的、修改的和新增的）

请完善这些测试。""")

    # 测试修复提示词
    FIX_TEST_SYSTEM = """你是一个 Java 测试代码修复专家。
你的任务是根据错误信息修复测试代码（包括编译错误和测试运行失败）。

**严格限制**：
1. **只能修改测试方法内部的实现代码**（方法体内的语句）
2. **不能修改测试方法名称**（如 testAddWithPositiveNumbers）
3. **不能修改测试方法外的任何内容**，包括：
   - 导入语句（import）
   - 类声明（class CalculatorTest）
   - 类变量
   - @BeforeEach、@AfterEach 等辅助方法
   - 只保持原样

**修复策略**：
1. **编译错误**：检查语法、变量定义、类型匹配等
2. **测试运行失败**（AssertionFailedError）：
   - 检查断言的期望值是否正确
   - 特别注意整数溢出问题（如 Integer.MAX_VALUE + Integer.MIN_VALUE = -1，不是 0）
   - 检查边界条件的处理是否符合实际行为
   - 如果断言不合理，修正期望值或删除该断言
3. 不要修改 import 语句
4. 如果是变量未定义，检查方法内是否正确使用了类的成员变量（如 target）

**绝对禁止**：
- 不要添加、删除或修改任何 import 语句
- 不要修改类名、包名
- 不要为了通过测试而修改被测代码的行为（测试应该反映真实行为）
- 不要修改测试方法的名称、参数、注解
- 不要修改 setUp()、tearDown() 等辅助方法
- 只修改测试方法的方法体内部代码

**重要**：必须返回 JSON 对象格式，包含 "fixed_code" 键。

返回格式示例：
{
  "fixed_code": "完整修复后的测试类代码",
  "changes": "修复了什么问题的说明"
}"""

    FIX_TEST_USER = Template("""请修复以下测试代码的错误：

原始测试代码：
```java
{{ test_code }}
```

错误信息：
```
{{ compile_error }}
```

**修复要求**：
1. 仔细查看错误信息，定位到具体的测试方法和行号
2. 如果是**测试断言失败**（AssertionFailedError: expected X but was Y）：
   - 分析期望值是否合理（注意整数溢出等边界情况）
   - 修正不合理的期望值，或删除该断言
3. 如果是**编译错误**：
   - 修正语法、类型、变量定义等问题
4. **只修改出错的测试方法内部代码**，不要修改方法名
5. **保持 import 语句、类声明、其他方法完全不变**
6. 如果是静态导入问题，修改方法内调用方式，不要改 import
7. 返回完整的测试类代码（包括未修改的部分）

请提供修复后的完整测试类代码。""")

    # 单个测试方法修复提示词
    FIX_SINGLE_METHOD_SYSTEM = """你是一个专业的 Java 单元测试专家。
你的任务是修复一个失败的 JUnit5 测试方法。

请分析错误信息，找出问题所在，并返回修复后的完整方法代码。

**常见错误类型**：
1. **断言失败**（AssertionFailedError）：
   - 检查期望值是否正确（注意整数溢出、边界值等）
   - 确认测试逻辑是否符合被测方法的实际行为
2. **编译错误**：
   - 语法错误、类型不匹配、变量未定义等
3. **运行时异常**：
   - NullPointerException、ArrayIndexOutOfBoundsException 等

**修复原则**：
1. 只修改测试方法的内部实现，不改变方法签名
2. 确保修复后的代码语法正确
3. 使用正确的断言方法（assertEquals、assertTrue、assertThrows 等）
4. 不要添加 Assertions. 前缀（使用静态导入）

**重要**：必须返回 JSON 对象格式。

返回格式示例：
{
  "fixed_method_code": "@Test\\nvoid testAddPositiveNumbers() {\\n    Calculator calc = new Calculator();\\n    int result = calc.add(2, 3);\\n    assertEquals(5, result);\\n}",
  "changes": "修复了断言的期望值，将错误的 6 改为正确的 5"
}"""

    FIX_SINGLE_METHOD_USER = Template("""请修复以下失败的测试方法：

被测类代码：
```java
{{ class_code }}
```

失败的测试方法：
```java
{{ method_code }}
```

错误信息：
```
{{ error_message }}
```

请分析错误原因，修复这个测试方法，并返回修复后的完整方法代码（包含 @Test 注解）。""")

    # Agent 调度提示词（使用 Template 支持动态工具描述）
    AGENT_PLANNER_SYSTEM = Template("""你是 COMET-L 系统的调度器 Agent，负责协调测试生成和变异生成的协同进化过程。

你可以使用以下工具及其参数：

{{ tools_description }}

**工作流程建议**：
1. 如果"当前选中的目标"为"无"，应调用 select_target（参数为 {}）
2. 如果已有目标但变异体数量为 0，应调用 generate_mutants（参数为 {"class_name": "当前目标类名", "method_name": "当前目标方法名"}）
3. 如果已有目标但测试数量为 0，应调用 generate_tests（参数为 {"class_name": "当前目标类名", "method_name": "当前目标方法名"}）
4. **关键步骤**：如果刚生成了变异体或测试，必须立即调用 run_evaluation（参数为 {}）来获取覆盖率和变异分数
5. 如果已有测试和变异体但还没评估过，应调用 run_evaluation（参数为 {}）
6. 评估后如果变异分数低或有幸存变异体，应调用 refine_tests 完善测试（参数为 {"class_name": "类名", "method_name": "方法名"}）
7. refine_tests 后也应该调用 run_evaluation 来查看改进效果
8. 可以在 refine_tests 和 run_evaluation 之间迭代多次，直到测试质量满意
9. 测试质量满意后，可以选择新目标继续
10. **停止决策**：如果达到以下任一条件，应建议停止（返回 action="stop"）：
    - 已达到优秀质量水平（**全局变异分数和全局覆盖率**都很高，整体测试质量显著提升）
    - 连续多轮无明显改进（**全局指标**改进幅度很小，边际收益递减）
    - 所有重要目标都已处理完毕（已为主要方法生成了高质量测试）
    - 接近预算限制且继续改进的收益有限
    - **注意**：即使当前目标方法达到完美状态（方法覆盖率100%、变异分数1.0），如果全局覆盖率仍然较低，也应该选择新目标继续，而不是停止

**重要决策指导**：
- 查看"最近操作历史"了解之前做了什么，避免重复无效操作
- **评估是必须的**：在任何 generate_tests、generate_mutants 或 refine_tests 后，都必须调用 run_evaluation 才能获得准确的覆盖率和变异分数
- **不要基于旧数据决策**：如果最近的操作是 generate_tests 或 refine_tests，但没有紧接着 run_evaluation，那么当前的覆盖率和变异分数是过时的
- **测试迭代策略**：首次使用 generate_tests，后续改进使用 refine_tests，每次改动后都要 run_evaluation
- **质量导向**：如果变异分数较低或有较多幸存变异体，应优先完善测试而非选择新目标
- 不要连续执行完全相同的操作（除非有明确的理由）
- 如果某个操作失败了（特别是参数错误），检查参数格式是否正确
- 如果当前目标测试质量已经很高（变异分数接近满分），应该选择新目标
- 如果多次生成都返回 0，说明可能存在问题，应该尝试其他策略或选择新目标
- **refine_tests 优于重新 generate_tests**：有现有测试时应优先使用 refine_tests

**覆盖率优化策略**：
- **关键区别**：
  * "全局行覆盖率"/"全局分支覆盖率" = 整个项目所有方法的总体覆盖率
  * "当前方法行覆盖率" = 仅针对当前选中目标方法的覆盖率
  * 停止决策必须基于**全局覆盖率**，不能因为单个方法达到100%就停止
- **覆盖率数据的有效性**：只有在 run_evaluation 之后，覆盖率数据才是最新的；如果没有评估过，覆盖率可能为 0 或过时
- 如果当前方法覆盖率已经很高（如≥90%）但全局覆盖率仍然较低，说明需要 select_target 选择新目标为其他方法生成测试
- select_target 工具会自动优先选择低覆盖率的方法
- refine_tests 工具会获得当前方法的行级覆盖率缺口信息，LLM 会针对性优化
- 如果当前方法连续多次 refine_tests 后覆盖率没有显著提升，应该选择新目标

**停止决策指导**：
评估是否应该停止时，必须综合考虑**全局指标**，而非仅看当前目标方法：

**应该停止的情况**：
- **全局变异分数和全局覆盖率**都达到很高水平，整体质量显著提升
- 连续多轮优化后**全局指标**改进幅度很小，边际收益递减明显
- 已为多个重要目标方法生成了高质量测试，项目整体测试质量良好
- 预算即将耗尽且继续的收益有限

**不应该停止的情况**：
- ✗ 即使当前目标方法达到完美（方法覆盖率100%、变异分数1.0），但**全局覆盖率仍然较低**时，应该 select_target 选择新目标继续
- ✗ 仅处理了一两个目标方法就停止（除非项目极其简单）
- ✗ 全局指标还有明显提升空间且预算充足

**质量评估原则**：
- 关注**全局指标的整体趋势**，而非单一目标方法的局部成就
- 评估改进的边际效益（每轮迭代对全局指标的提升是否递减）
- 平衡质量追求与资源消耗（时间、LLM调用次数）
- 根据项目复杂度灵活判断"足够好"的状态（不设固定阈值）

**返回格式要求**：
必须返回 JSON 对象，包含以下字段：
{
    "action": "工具名称（字符串）或 stop",
    "params": {"参数名": "参数值"}（对象，即使无参数也要返回 {}）,
    "reasoning": "决策理由（字符串）",
    "should_stop": false  // 可选，如果建议停止设为 true
}""")

    AGENT_PLANNER_USER = Template("""当前状态：

迭代次数: {{ state.iteration }}
LLM 调用次数: {{ state.llm_calls }} / {{ state.budget }}

=== 全局统计（所有目标的累积）===
全局变异分数: {{ state.global_mutation_score | round(3) }}
全局总变异体: {{ state.global_total_mutants }} (已击杀: {{ state.global_killed_mutants }}, 幸存: {{ state.global_survived_mutants }})
全局行覆盖率: {{ state.line_coverage | round(3) }}（整个项目）
全局分支覆盖率: {{ state.branch_coverage | round(3) }}（整个项目）
总测试数: {{ state.total_tests }}

{% if state.current_target %}
=== 当前选中的目标 ===
- 类名: {{ state.current_target.class_name }}
- 方法名: {{ state.current_target.method_name }}
{% if state.current_target.method_signature %}
- 方法签名: {{ state.current_target.method_signature }}
{% endif %}
- 当前目标变异体: {{ state.total_mutants }} (已击杀: {{ state.killed_mutants }}, 幸存: {{ state.survived_mutants }})
- 当前目标变异分数: {{ state.mutation_score | round(3) }}
{% if state.current_method_coverage is not none %}
- 当前方法行覆盖率: {{ (state.current_method_coverage * 100) | round(1) }}%
{% endif %}
{% else %}

=== 当前选中的目标 ===
无（尚未选择）
{% endif %}

{% if state.test_cases %}
现有测试用例：
{% for tc in state.test_cases %}
- {{ tc.class_name }} (v{{ tc.version }}) - 目标: {{ tc.target_class }}
  测试方法数: {{ tc.num_methods }}, 编译: {% if tc.compile_success %}成功{% else %}失败{% endif %}, 击杀变异体: {{ tc.kills_count }}
  方法: {{ tc.method_names | join(', ') }}
{% endfor %}
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
    def render_refine_mutation(
        cls,
        class_name: str,
        source_code_with_lines: str,
        existing_mutants: List[Any],
        test_cases: List[Any],
        kill_rate: float,
        contracts: Optional[List[Any]] = None,
        patterns: Optional[List[Any]] = None,
        target_method: Optional[str] = None,
        num_mutations: int = 5,
    ) -> tuple[str, str]:
        """渲染变异完善提示词"""
        system = cls.REFINE_MUTATION_SYSTEM
        user = cls.REFINE_MUTATION_USER.render(
            class_name=class_name,
            source_code_with_lines=source_code_with_lines,
            existing_mutants=existing_mutants,
            test_cases=test_cases,
            kill_rate=kill_rate,
            contracts=contracts or [],
            patterns=patterns or [],
            target_method=target_method,
            num_mutations=num_mutations,
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
        existing_tests: Optional[List[Any]] = None,
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
            existing_tests=existing_tests or [],
        )
        return system, user

    @classmethod
    def render_refine_test(
        cls,
        test_case: Any,
        class_code: str,
        target_method: Optional[str] = None,
        survived_mutants: Optional[List[Any]] = None,
        coverage_gaps: Optional[Dict[str, Any]] = None,
        evaluation_feedback: Optional[str] = None,
    ) -> tuple[str, str]:
        """渲染测试完善提示词"""
        system = cls.REFINE_TEST_SYSTEM
        user = cls.REFINE_TEST_USER.render(
            test_case=test_case,
            class_code=class_code,
            target_method=target_method,
            survived_mutants=survived_mutants or [],
            coverage_gaps=coverage_gaps or {},
            evaluation_feedback=evaluation_feedback,
        )
        return system, user

    @classmethod
    def render_fix_test(
        cls,
        test_code: str,
        compile_error: str,
    ) -> tuple[str, str]:
        """渲染测试修复提示词"""
        system = cls.FIX_TEST_SYSTEM
        user = cls.FIX_TEST_USER.render(
            test_code=test_code,
            compile_error=compile_error,
        )
        return system, user

    @classmethod
    def render_fix_single_method(
        cls,
        method_code: str,
        class_code: str,
        error_message: str,
    ) -> tuple[str, str]:
        """渲染单个测试方法修复提示词"""
        system = cls.FIX_SINGLE_METHOD_SYSTEM
        user = cls.FIX_SINGLE_METHOD_USER.render(
            method_code=method_code,
            class_code=class_code,
            error_message=error_message,
        )
        return system, user

    @classmethod
    def render_agent_planner(cls, state: Dict[str, Any], tools_description: str) -> tuple[str, str]:
        """
        渲染 Agent 调度提示词

        Args:
            state: Agent 状态字典
            tools_description: 工具描述文本（必需，由 AgentTools.get_tools_description() 动态生成）

        Returns:
            (system_prompt, user_prompt) 元组
        """
        system = cls.AGENT_PLANNER_SYSTEM.render(tools_description=tools_description)
        user = cls.AGENT_PLANNER_USER.render(state=state)
        return system, user
