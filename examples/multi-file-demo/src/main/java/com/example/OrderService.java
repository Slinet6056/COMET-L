package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 订单服务类 - 精简版
 */
public class OrderService {
    private final Map<String, Double> orders;
    private int nextOrderId;

    public OrderService() {
        this.orders = new HashMap<>();
        this.nextOrderId = 1;
    }

    /**
     * 创建订单
     */
    public String createOrder(String customerId, double amount) {
        if (amount <= 0) {
            throw new IllegalArgumentException("Order amount must be positive");
        }
        String orderId = "ORD" + String.format("%04d", nextOrderId++);
        orders.put(orderId, amount);
        return orderId;
    }

    /**
     * 获取订单总金额
     */
    public double getOrderAmount(String orderId) {
        Double amount = orders.get(orderId);
        if (amount == null) {
            throw new IllegalArgumentException("Order not found: " + orderId);
        }
        return amount;
    }
}
