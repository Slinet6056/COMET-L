# Bug Report: 除零错误

## Bug ID
BUG-001

## 报告日期
2024-01-15

## 严重程度
高

## 描述
Calculator.divide() 方法没有检查除数为零的情况，导致运行时抛出 ArithmeticException。

## 重现步骤
1. 创建 Calculator 实例
2. 调用 calculator.divide(10, 0)
3. 程序崩溃

## 预期行为
应该在除数为零时抛出有意义的异常或返回错误信息。

## 实际行为
抛出未捕获的 ArithmeticException: / by zero

## 修复前代码
```java
public int divide(int a, int b) {
    return a / b;
}
```

## 修复后代码
```java
public int divide(int a, int b) {
    if (b == 0) {
        throw new IllegalArgumentException("Division by zero");
    }
    return a / b;
}
```

## 影响
所有使用 divide 方法的代码都可能因为未检查除数而崩溃。

## 学习到的教训
在进行除法运算前，始终应该检查除数是否为零。
