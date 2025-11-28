package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 产品服务类 - 管理产品信息
 */
public class ProductService {
    private Map<String, Double> products;

    public ProductService() {
        this.products = new HashMap<>();
    }

    /**
     * 添加产品
     */
    public void addProduct(String productId, double price) {
        if (price < 0) {
            throw new IllegalArgumentException("Price cannot be negative");
        }
        products.put(productId, price);
    }

    /**
     * 获取产品价格
     */
    public double getPrice(String productId) {
        Double price = products.get(productId);
        if (price == null) {
            throw new IllegalArgumentException("Product not found: " + productId);
        }
        return price;
    }

    /**
     * 计算折扣价格
     */
    public double calculateDiscountPrice(String productId, double discountPercent) {
        if (discountPercent < 0 || discountPercent > 100) {
            throw new IllegalArgumentException("Invalid discount percentage");
        }
        double originalPrice = getPrice(productId);
        return originalPrice * (1 - discountPercent / 100);
    }

    /**
     * 检查产品是否存在
     */
    public boolean productExists(String productId) {
        return products.containsKey(productId);
    }

    /**
     * 获取产品数量
     */
    public int getProductCount() {
        return products.size();
    }
}
