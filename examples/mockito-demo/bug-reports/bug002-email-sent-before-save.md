# Bug Report: 邮件在保存失败时仍发送

## Bug ID
BUG-002

## 报告日期
2024-03-12

## 严重程度
高

## 描述
UserService.registerUser() 方法先保存用户再发送邮件，但如果 repository.save() 抛出异常，用户未成功创建却可能已经发送了部分通知。

## 重现步骤
1. Mock repository.save() 抛出异常
2. 调用 userService.registerUser("test", "test@email.com")
3. 检查 emailService.sendWelcomeEmail 是否被调用

## 预期行为
如果用户保存失败，不应发送欢迎邮件。

## 实际行为
当前代码顺序正确（先 save 再发邮件），但应测试验证此行为。

## 相关代码
```java
public User registerUser(String username, String email) {
    // ... 验证 ...
    User savedUser = repository.save(user);  // 可能失败
    emailService.sendWelcomeEmail(email);    // 不应在 save 失败后执行
    return savedUser;
}
```

## 测试建议
- Mock repository.save() 抛出 RuntimeException
- 验证 emailService.sendWelcomeEmail 未被调用
- 使用 Mockito.verify() 确保调用顺序

## 相关类
UserService, EmailService, UserRepository

## 标签
transaction, side-effect, mock
