package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 产品服务类 - 精简版
 */
public class ProductService {
    private final Map<String, Double> products;

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
}
