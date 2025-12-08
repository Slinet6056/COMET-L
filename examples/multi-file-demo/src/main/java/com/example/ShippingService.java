package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 物流服务类 - 精简版
 */
public class ShippingService {
    private final Map<String, ShippingInfo> shippingInfos;

    public ShippingService() {
        this.shippingInfos = new HashMap<>();
    }

    /**
     * 创建配送信息（仅校验非空）
     */
    public void createShipping(String orderId, String address, String shippingMethod) {
        if (isBlank(address)) {
            throw new IllegalArgumentException("Address cannot be empty");
        }
        if (isBlank(shippingMethod)) {
            throw new IllegalArgumentException("Shipping method cannot be empty");
        }
        shippingInfos.put(orderId, new ShippingInfo());
    }

    /**
     * 获取配送状态
     */
    public String getShippingStatus(String orderId) {
        ShippingInfo info = shippingInfos.get(orderId);
        if (info == null) {
            throw new IllegalArgumentException("Shipping info not found: " + orderId);
        }
        return info.status;
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static class ShippingInfo {
        private final String status;

        ShippingInfo() {
            this.status = "CREATED";
        }
    }
}
