package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 库存服务类 - 管理产品库存
 */
public class InventoryService {
    private Map<String, Integer> inventory;

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
        int currentStock = inventory.getOrDefault(productId, 0);
        inventory.put(productId, currentStock + quantity);
    }

    /**
     * 减少库存
     */
    public void reduceStock(String productId, int quantity) {
        if (quantity < 0) {
            throw new IllegalArgumentException("Quantity cannot be negative");
        }
        int currentStock = getCurrentStock(productId);
        if (currentStock < quantity) {
            throw new IllegalStateException("Insufficient stock");
        }
        inventory.put(productId, currentStock - quantity);
    }

    /**
     * 获取当前库存
     */
    public int getCurrentStock(String productId) {
        return inventory.getOrDefault(productId, 0);
    }

    /**
     * 检查库存是否充足
     */
    public boolean hasEnoughStock(String productId, int requiredQuantity) {
        int currentStock = getCurrentStock(productId);
        return currentStock >= requiredQuantity;
    }

    /**
     * 获取总库存数量
     */
    public int getTotalStockCount() {
        int total = 0;
        for (int stock : inventory.values()) {
            total += stock;
        }
        return total;
    }

    /**
     * 获取低库存产品数量（库存少于10件）
     */
    public int getLowStockProductCount() {
        int count = 0;
        for (int stock : inventory.values()) {
            if (stock > 0 && stock < 10) {
                count++;
            }
        }
        return count;
    }
}
