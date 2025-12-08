package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 库存服务类 - 精简版
 */
public class InventoryService {
    private final Map<String, Integer> inventory;

    public InventoryService() {
        this.inventory = new HashMap<>();
    }

    /**
     * 添加库存
     */
    public void addStock(String productId, int quantity) {
        if (quantity < 0) {
            throw new IllegalArgumentException("Quantity cannot be negative");
        }
        inventory.merge(productId, quantity, Integer::sum);
    }

    /**
     * 获取当前库存
     */
    public int getCurrentStock(String productId) {
        return inventory.getOrDefault(productId, 0);
    }
}
