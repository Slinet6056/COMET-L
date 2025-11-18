# Calculator Demo - COMET-L 演示项目

这是一个简单的计算器项目，用于演示 COMET-L 测试变异协同进化系统。

## 项目结构

```
calculator-demo/
├── src/
│   ├── main/java/com/example/
│   │   └── Calculator.java      # 计算器实现（包含故意的缺陷）
│   └── test/java/com/example/
│       └── CalculatorTest.java  # 初始测试（覆盖率约 50%）
├── bug-reports/                 # Bug 报告（用于知识提取）
│   ├── bug001-divide-by-zero.md
│   ├── bug002-factorial-overflow.md
│   └── bug003-negative-sqrt.md
└── pom.xml                      # Maven 配置
```

## 特点

### 实现的功能
- 基本算术运算：加、减、乘、除
- 数学函数：阶乘、平方根、最大公约数
- 数论函数：质数检查、斐波那契数列

### 故意包含的缺陷
1. **除零错误** (`divide`): 没有检查除数为零
2. **阶乘溢出** (`factorial`): 没有处理负数和溢出
3. **负数平方根** (`sqrt`): 没有拒绝负数输入
4. **质数边界** (`isPrime`): 边界情况处理不完整
5. **斐波那契效率** (`fibonacci`): 效率低下的递归实现

### 初始测试覆盖率
当前测试类 `CalculatorTest.java` 提供约 50% 的代码覆盖率：
- ✅ 测试了：add, subtract, multiply, divide（部分）, isPrime
- ❌ 缺失：factorial, sqrt, gcd, fibonacci 的测试
- ❌ 缺失：边界情况和异常情况的测试

## 使用 COMET-L

在项目根目录运行：

```bash
# 对 calculator-demo 项目运行协同进化
.venv/bin/python main.py --project-path examples/calculator-demo

# 指定迭代次数和预算
.venv/bin/python main.py \
    --project-path examples/calculator-demo \
    --max-iterations 5 \
    --budget 500
```

## 预期结果

COMET-L 应该能够：

1. **提取知识**
   - 从 Bug 报告中学习除零、溢出、负数检查等缺陷模式
   - 从代码和注释中提取方法契约

2. **生成变异体**
   - 移除边界检查（如 divide 的除零检查）
   - 修改循环条件
   - 更改返回值
   - 等等...

3. **生成测试**
   - 为未测试的方法生成测试
   - 针对幸存变异体生成杀死它们的测试
   - 覆盖边界情况和异常情况

4. **提升质量**
   - 代码覆盖率：50% → 85%+
   - 变异分数：低 → 高
   - 发现并暴露所有故意留下的缺陷

## 构建和测试

```bash
# 编译项目
cd examples/calculator-demo
mvn clean compile

# 运行测试
mvn test

# 查看覆盖率报告
mvn test jacoco:report
# 报告位于: target/site/jacoco/index.html
```

## 学习资源

这个项目展示了：
- 典型的 Java 代码缺陷
- 不充分的测试覆盖
- Bug 报告的格式和内容
- 如何从 Bug 报告中提取知识

非常适合用于学习和演示 COMET-L 的能力。
