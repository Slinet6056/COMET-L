# Bug Report: 用户名重复检查竞态条件

## Bug ID
BUG-003

## 报告日期
2024-03-15

## 严重程度
中

## 描述
UserService.registerUser() 先检查用户名是否存在，再保存用户。在并发场景下可能存在竞态条件：两个请求同时检查，都通过后尝试保存相同用户名。

## 重现步骤
1. 并发调用 registerUser("sameUsername", "email1@test.com") 和 registerUser("sameUsername", "email2@test.com")
2. 两个请求可能都通过 existsByUsername 检查
3. 导致数据不一致

## 预期行为
应该使用数据库唯一约束或同步机制保证用户名唯一。

## 实际行为
先 check 再 save 存在 TOCTOU（Time-of-check to time-of-use）问题。

## 相关代码
```java
// 检查用户是否已存在 - 非原子操作
if (repository.existsByUsername(username)) {
    throw new IllegalStateException("Username already exists");
}
// 创建用户 - 可能与其他线程冲突
User savedUser = repository.save(user);
```

## 修复建议
```java
// 方案1：在 repository 层使用数据库唯一约束
// 方案2：使用乐观锁或悲观锁
// 方案3：捕获唯一约束异常并转换为业务异常
```

## 测试建议
- 虽然单元测试难以模拟真实并发，但应测试当 save 抛出唯一约束异常时的处理
- Mock existsByUsername 返回 false，但 save 抛出 DuplicateKeyException

## 相关类
UserService, UserRepository

## 标签
concurrency, race-condition, validation
