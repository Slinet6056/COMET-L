package com.example;

/**
 * 用户服务示例 - 展示如何为有依赖的类生成测试
 */
public class UserService {
    private final UserRepository repository;
    private final EmailService emailService;
    private final ValidationService validator;

    public UserService(UserRepository repository, EmailService emailService, ValidationService validator) {
        this.repository = repository;
        this.emailService = emailService;
        this.validator = validator;
    }

    /**
     * 注册新用户
     */
    public User registerUser(String username, String email) {
        // 验证输入
        if (!validator.isValidUsername(username)) {
            throw new IllegalArgumentException("Invalid username");
        }
        if (!validator.isValidEmail(email)) {
            throw new IllegalArgumentException("Invalid email");
        }

        // 检查用户是否已存在
        if (repository.existsByUsername(username)) {
            throw new IllegalStateException("Username already exists");
        }

        // 创建用户
        User user = new User(username, email);
        User savedUser = repository.save(user);

        // 发送欢迎邮件
        emailService.sendWelcomeEmail(email);

        return savedUser;
    }

    /**
     * 根据用户名查找用户
     */
    public User findUserByUsername(String username) {
        return repository.findByUsername(username);
    }

    /**
     * 更新用户邮箱
     */
    public void updateUserEmail(String username, String newEmail) {
        if (!validator.isValidEmail(newEmail)) {
            throw new IllegalArgumentException("Invalid email");
        }

        User user = repository.findByUsername(username);
        if (user == null) {
            throw new IllegalArgumentException("User not found");
        }

        user.setEmail(newEmail);
        repository.save(user);

        emailService.sendEmailChangeNotification(newEmail);
    }
}

/**
 * 用户实体类
 */
class User {
    private String username;
    private String email;

    public User(String username, String email) {
        this.username = username;
        this.email = email;
    }

    public String getUsername() {
        return username;
    }

    public String getEmail() {
        return email;
    }

    public void setEmail(String email) {
        this.email = email;
    }
}

/**
 * 用户仓库接口（需要 mock）
 */
interface UserRepository {
    User save(User user);
    User findByUsername(String username);
    boolean existsByUsername(String username);
}

/**
 * 邮件服务接口（需要 mock）
 */
interface EmailService {
    void sendWelcomeEmail(String email);
    void sendEmailChangeNotification(String email);
}

/**
 * 验证服务接口（需要 mock）
 */
interface ValidationService {
    boolean isValidUsername(String username);
    boolean isValidEmail(String email);
}
