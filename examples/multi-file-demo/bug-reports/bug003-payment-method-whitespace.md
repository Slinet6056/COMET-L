# Bug Report: 支付方式空白字符串验证

## Bug ID
BUG-003

## 报告日期
2024-02-25

## 严重程度
中

## 描述
PaymentService.processPayment() 使用 isBlank() 检查支付方式，但只有空字符串和 null 会被拒绝，纯空白字符（如 "   "）在 trim() 后也会被拒绝。这个验证是正确的，但应该测试覆盖。

## 重现步骤
1. 调用 paymentService.processPayment("ORD001", 100.0, "   ")
2. 应该抛出 IllegalArgumentException

## 预期行为
纯空白字符的支付方式应该被拒绝。

## 实际行为
当前实现正确处理了这种情况。

## 相关代码
```java
private boolean isBlank(String value) {
    return value == null || value.trim().isEmpty();
}

public String processPayment(String orderId, double amount, String method) {
    if (isBlank(method)) {  // 正确处理空白字符串
        throw new IllegalArgumentException("Payment method is required");
    }
    // ...
}
```

## 测试建议
测试应覆盖以下边界条件：
- method = null
- method = ""
- method = "   " (纯空格)
- method = "\t\n" (制表符和换行)
- method = "credit_card" (正常值)

## 相关类
PaymentService

## 标签
validation, whitespace, boundary
