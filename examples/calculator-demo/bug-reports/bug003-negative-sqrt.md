# Bug Report: 负数平方根

## Bug ID
BUG-003

## 报告日期
2024-01-17

## 严重程度
中

## 描述
Calculator.sqrt() 方法接受负数作为输入，返回 NaN 的整数转换结果（即 0），这是不正确的。

## 重现步骤
1. 调用 calculator.sqrt(-4)
2. 返回 0（Math.sqrt(-4) 返回 NaN，转换为 int 后为 0）

## 预期行为
应该对负数输入抛出异常或返回错误标识。

## 实际行为
静默失败，返回 0。

## 修复建议
```java
public int sqrt(int n) {
    if (n < 0) {
        throw new IllegalArgumentException("Cannot compute square root of negative number");
    }
    return (int) Math.sqrt(n);
}
```

## 影响
可能导致程序使用错误的计算结果继续执行。
