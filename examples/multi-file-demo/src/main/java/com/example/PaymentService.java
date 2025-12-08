package com.example;

import java.util.HashMap;
import java.util.Map;

/**
 * 支付服务类 - 精简版
 */
public class PaymentService {
    private final Map<String, Payment> payments;
    private int nextPaymentId;

    public PaymentService() {
        this.payments = new HashMap<>();
        this.nextPaymentId = 1;
    }

    /**
     * 处理支付（仅校验金额与支付方式非空）
     */
    public String processPayment(String orderId, double amount, String method) {
        if (amount <= 0) {
            throw new IllegalArgumentException("Payment amount must be positive");
        }
        if (isBlank(method)) {
            throw new IllegalArgumentException("Payment method is required");
        }
        String paymentId = "PAY" + String.format("%04d", nextPaymentId++);
        payments.put(paymentId, new Payment(amount));
        return paymentId;
    }

    /**
     * 获取支付金额
     */
    public double getPaymentAmount(String paymentId) {
        Payment payment = payments.get(paymentId);
        if (payment == null) {
            throw new IllegalArgumentException("Payment not found: " + paymentId);
        }
        return payment.amount;
    }

    private boolean isBlank(String value) {
        return value == null || value.trim().isEmpty();
    }

    private static class Payment {
        private final double amount;

        Payment(double amount) {
            this.amount = amount;
        }
    }
}
