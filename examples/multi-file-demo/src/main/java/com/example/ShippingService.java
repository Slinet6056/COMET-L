package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 物流服务类 - 管理订单配送
 */
public class ShippingService {
    private Map<String, ShippingInfo> shippingInfos;

    public ShippingService() {
        this.shippingInfos = new HashMap<>();
    }

    /**
     * 创建配送信息
     */
    public void createShipping(String orderId, String address, String shippingMethod) {
        if (address == null || address.trim().isEmpty()) {
            throw new IllegalArgumentException("Address cannot be empty");
        }
        if (!isValidShippingMethod(shippingMethod)) {
            throw new IllegalArgumentException("Invalid shipping method: " + shippingMethod);
        }
        shippingInfos.put(orderId, new ShippingInfo(orderId, address, shippingMethod));
    }

    /**
     * 计算配送费用
     */
    public double calculateShippingFee(String shippingMethod, double weight) {
        if (weight < 0) {
            throw new IllegalArgumentException("Weight cannot be negative");
        }
        double baseFee = 10.0;
        if ("EXPRESS".equals(shippingMethod)) {
            baseFee = 20.0;
        } else if ("STANDARD".equals(shippingMethod)) {
            baseFee = 10.0;
        } else if ("ECONOMY".equals(shippingMethod)) {
            baseFee = 5.0;
        }
        return baseFee + (weight * 2.0);
    }

    /**
     * 更新配送状态
     */
    public void updateStatus(String orderId, String status) {
        ShippingInfo info = shippingInfos.get(orderId);
        if (info == null) {
            throw new IllegalArgumentException("Shipping info not found: " + orderId);
        }
        info.setStatus(status);
    }

    /**
     * 获取配送状态
     */
    public String getShippingStatus(String orderId) {
        ShippingInfo info = shippingInfos.get(orderId);
        if (info == null) {
            throw new IllegalArgumentException("Shipping info not found: " + orderId);
        }
        return info.getStatus();
    }

    /**
     * 计算预计送达天数
     */
    public int estimateDeliveryDays(String shippingMethod) {
        if ("EXPRESS".equals(shippingMethod)) {
            return 1;
        } else if ("STANDARD".equals(shippingMethod)) {
            return 3;
        } else if ("ECONOMY".equals(shippingMethod)) {
            return 7;
        }
        return 5; // 默认
    }

    /**
     * 检查是否已发货
     */
    public boolean isShipped(String orderId) {
        ShippingInfo info = shippingInfos.get(orderId);
        if (info == null) {
            return false;
        }
        return "SHIPPED".equals(info.getStatus()) || "DELIVERED".equals(info.getStatus());
    }

    private boolean isValidShippingMethod(String method) {
        return "EXPRESS".equals(method) ||
               "STANDARD".equals(method) ||
               "ECONOMY".equals(method);
    }

    // 内部配送信息类
    private static class ShippingInfo {
        private String orderId;
        private String address;
        private String shippingMethod;
        private String status;

        public ShippingInfo(String orderId, String address, String shippingMethod) {
            this.orderId = orderId;
            this.address = address;
            this.shippingMethod = shippingMethod;
            this.status = "PENDING";
        }

        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
    }
}
