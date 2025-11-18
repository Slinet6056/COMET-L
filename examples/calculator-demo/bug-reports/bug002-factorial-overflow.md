# Bug Report: 阶乘溢出

## Bug ID
BUG-002

## 报告日期
2024-01-16

## 严重程度
中

## 描述
Calculator.factorial() 方法没有处理负数输入和整数溢出，导致错误结果。

## 重现步骤
1. 调用 calculator.factorial(-5) - 导致无限递归
2. 调用 calculator.factorial(25) - 导致溢出，返回错误结果

## 预期行为
- 负数应该抛出 IllegalArgumentException
- 大数应该抛出溢出异常或使用 BigInteger

## 实际行为
- 负数导致 StackOverflowError
- 大数返回错误的负值（溢出）

## 修复建议
```java
public long factorial(int n) {
    if (n < 0) {
        throw new IllegalArgumentException("Factorial not defined for negative numbers");
    }
    if (n == 0 || n == 1) {
        return 1;
    }
    if (n > 20) {
        throw new ArithmeticException("Factorial overflow for n > 20");
    }
    return n * factorial(n - 1);
}
```

## 影响
可能导致程序崩溃或返回错误的计算结果。
