# Bug Report: 库存添加边界检查不完整

## Bug ID
BUG-002

## 报告日期
2024-02-22

## 严重程度
中

## 描述
InventoryService.addStock() 检查 quantity < 0 时抛出异常，但没有处理 quantity = 0 的情况，这是一个无意义的操作。

## 重现步骤
1. 调用 inventoryService.addStock("PROD001", 0)
2. 操作成功但没有任何效果

## 预期行为
应该拒绝 quantity <= 0 的调用，或至少记录警告。

## 实际行为
添加 0 个库存的操作静默成功。

## 相关代码
```java
public void addStock(String productId, int quantity) {
    if (quantity < 0) {  // 应该是 quantity <= 0
        throw new IllegalArgumentException("Quantity cannot be negative");
    }
    inventory.merge(productId, quantity, Integer::sum);
}
```

## 修复建议
```java
public void addStock(String productId, int quantity) {
    if (quantity <= 0) {
        throw new IllegalArgumentException("Quantity must be positive");
    }
    inventory.merge(productId, quantity, Integer::sum);
}
```

## 测试建议
- 测试 quantity = 0 时应该抛出异常
- 测试 quantity = -1 时应该抛出异常
- 测试 quantity = 1 时应该成功

## 相关类
InventoryService

## 标签
boundary, validation, zero-check
