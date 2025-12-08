package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 客户服务类 - 精简版，仅负责注册与查询姓名
 */
public class CustomerService {
    private final Map<String, CustomerInfo> customers;

    public CustomerService() {
        this.customers = new HashMap<>();
    }

    /**
     * 注册客户（仅校验非空）
     */
    public void registerCustomer(String customerId, String name, String email) {
        if (isBlank(name)) {
            throw new IllegalArgumentException("Name cannot be empty");
        }
        if (isBlank(email)) {
            throw new IllegalArgumentException("Email cannot be empty");
        }
        customers.put(customerId, new CustomerInfo(name));
    }

    /**
     * 获取客户姓名
     */
    public String getCustomerName(String customerId) {
        CustomerInfo info = customers.get(customerId);
        if (info == null) {
            throw new IllegalArgumentException("Customer not found: " + customerId);
        }
        return info.name;
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static class CustomerInfo {
        private final String name;

        CustomerInfo(String name) {
            this.name = name;
        }
    }
}
