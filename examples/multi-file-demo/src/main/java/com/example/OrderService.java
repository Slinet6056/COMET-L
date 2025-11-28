package com.example;

import java.util.ArrayList;
import java.util.List;

/**
 * 订单服务类 - 管理订单
 */
public class OrderService {
    private List<Order> orders;
    private int nextOrderId;

    public OrderService() {
        this.orders = new ArrayList<>();
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
        Order order = new Order(orderId, customerId, amount);
        orders.add(order);
        return orderId;
    }

    /**
     * 获取订单总金额
     */
    public double getOrderAmount(String orderId) {
        Order order = findOrder(orderId);
        if (order == null) {
            throw new IllegalArgumentException("Order not found: " + orderId);
        }
        return order.getAmount();
    }

    /**
     * 取消订单
     */
    public void cancelOrder(String orderId) {
        Order order = findOrder(orderId);
        if (order == null) {
            throw new IllegalArgumentException("Order not found: " + orderId);
        }
        order.setStatus("CANCELLED");
    }

    /**
     * 获取客户的订单数量
     */
    public int getCustomerOrderCount(String customerId) {
        int count = 0;
        for (Order order : orders) {
            if (order.getCustomerId().equals(customerId)) {
                count++;
            }
        }
        return count;
    }

    /**
     * 计算总收入
     */
    public double calculateTotalRevenue() {
        double total = 0;
        for (Order order : orders) {
            if (!"CANCELLED".equals(order.getStatus())) {
                total += order.getAmount();
            }
        }
        return total;
    }

    private Order findOrder(String orderId) {
        for (Order order : orders) {
            if (order.getOrderId().equals(orderId)) {
                return order;
            }
        }
        return null;
    }

    // 内部订单类
    private static class Order {
        private String orderId;
        private String customerId;
        private double amount;
        private String status;

        public Order(String orderId, String customerId, double amount) {
            this.orderId = orderId;
            this.customerId = customerId;
            this.amount = amount;
            this.status = "ACTIVE";
        }

        public String getOrderId() { return orderId; }
        public String getCustomerId() { return customerId; }
        public double getAmount() { return amount; }
        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
    }
}
