package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 客户服务类 - 管理客户信息
 */
public class CustomerService {
    private Map<String, Customer> customers;

    public CustomerService() {
        this.customers = new HashMap<>();
    }

    /**
     * 注册客户
     */
    public void registerCustomer(String customerId, String name, String email) {
        if (name == null || name.trim().isEmpty()) {
            throw new IllegalArgumentException("Name cannot be empty");
        }
        if (!isValidEmail(email)) {
            throw new IllegalArgumentException("Invalid email format");
        }
        customers.put(customerId, new Customer(customerId, name, email));
    }

    /**
     * 获取客户姓名
     */
    public String getCustomerName(String customerId) {
        Customer customer = customers.get(customerId);
        if (customer == null) {
            throw new IllegalArgumentException("Customer not found: " + customerId);
        }
        return customer.getName();
    }

    /**
     * 更新客户邮箱
     */
    public void updateEmail(String customerId, String newEmail) {
        if (!isValidEmail(newEmail)) {
            throw new IllegalArgumentException("Invalid email format");
        }
        Customer customer = customers.get(customerId);
        if (customer == null) {
            throw new IllegalArgumentException("Customer not found: " + customerId);
        }
        customer.setEmail(newEmail);
    }

    /**
     * 增加积分
     */
    public void addPoints(String customerId, int points) {
        if (points < 0) {
            throw new IllegalArgumentException("Points cannot be negative");
        }
        Customer customer = customers.get(customerId);
        if (customer == null) {
            throw new IllegalArgumentException("Customer not found: " + customerId);
        }
        customer.addPoints(points);
    }

    /**
     * 获取客户积分
     */
    public int getCustomerPoints(String customerId) {
        Customer customer = customers.get(customerId);
        if (customer == null) {
            throw new IllegalArgumentException("Customer not found: " + customerId);
        }
        return customer.getPoints();
    }

    /**
     * 检查客户是否为VIP（积分大于1000）
     */
    public boolean isVipCustomer(String customerId) {
        Customer customer = customers.get(customerId);
        if (customer == null) {
            return false;
        }
        return customer.getPoints() >= 1000;
    }

    private boolean isValidEmail(String email) {
        return email != null && email.contains("@") && email.contains(".");
    }

    // 内部客户类
    private static class Customer {
        private String customerId;
        private String name;
        private String email;
        private int points;

        public Customer(String customerId, String name, String email) {
            this.customerId = customerId;
            this.name = name;
            this.email = email;
            this.points = 0;
        }

        public String getName() { return name; }
        public String getEmail() { return email; }
        public void setEmail(String email) { this.email = email; }
        public int getPoints() { return points; }
        public void addPoints(int points) { this.points += points; }
    }
}
