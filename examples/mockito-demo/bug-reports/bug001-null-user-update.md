# Bug Report: 空用户更新邮箱异常

## Bug ID
BUG-001

## 报告日期
2024-03-10

## 严重程度
中

## 描述
UserService.updateUserEmail() 方法在用户不存在时返回 null，但后续代码直接调用 user.setEmail() 会抛出 NullPointerException。

## 重现步骤
1. 创建 UserService 实例（mock repository 返回 null）
2. 调用 userService.updateUserEmail("nonexistent", "test@email.com")
3. 程序抛出 NullPointerException

## 预期行为
应该先检查用户是否存在，并抛出有意义的异常信息。

## 实际行为
虽然代码有检查 `if (user == null)`，但如果 repository.findByUsername 行为不一致，可能导致问题。

## 相关代码
```java
public void updateUserEmail(String username, String newEmail) {
    User user = repository.findByUsername(username);
    if (user == null) {
        throw new IllegalArgumentException("User not found");
    }
    user.setEmail(newEmail);
    // ...
}
```

## 测试建议
- 确保 mock repository.findByUsername 返回 null 时正确抛出异常
- 测试边界条件：空字符串用户名、null 用户名

## 相关类
UserService

## 标签
null-check, validation
