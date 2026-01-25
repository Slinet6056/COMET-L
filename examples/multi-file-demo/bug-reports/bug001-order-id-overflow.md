# Bug Report: 订单 ID 溢出

## Bug ID
BUG-001

## 报告日期
2024-02-20

## 严重程度
低

## 描述
OrderService.createOrder() 使用 int 类型的 nextOrderId 计数器，在极端情况下可能溢出，导致订单 ID 重复或负数。

## 重现步骤
1. 创建大量订单直到 nextOrderId 接近 Integer.MAX_VALUE
2. 继续创建订单
3. nextOrderId 溢出变为负数

## 预期行为
订单 ID 应该始终唯一且为正数。

## 实际行为
nextOrderId++ 溢出后变为负数，生成的订单 ID 为 "ORD-2147483648" 等负数格式。

## 相关代码
```java
private int nextOrderId;

public String createOrder(String customerId, double amount) {
    String orderId = "ORD" + String.format("%04d", nextOrderId++);
    // nextOrderId 可能溢出
    // ...
}
```

## 修复建议
```java
// 使用 AtomicLong 或 UUID
private final AtomicLong nextOrderId = new AtomicLong(1);

public String createOrder(String customerId, double amount) {
    String orderId = "ORD" + String.format("%08d", nextOrderId.getAndIncrement());
    // ...
}
```

## 测试建议
- 测试 nextOrderId 为最大值时的行为
- 验证生成的订单 ID 格式正确

## 相关类
OrderService

## 标签
overflow, integer, id-generation
