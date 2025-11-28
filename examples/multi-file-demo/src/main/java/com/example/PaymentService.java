package com.example;

import java.util.ArrayList;
import java.util.List;

/**
 * 支付服务类 - 处理支付逻辑
 */
public class PaymentService {
    private List<Payment> payments;
    private int nextPaymentId;

    public PaymentService() {
        this.payments = new ArrayList<>();
        this.nextPaymentId = 1;
    }

    /**
     * 处理支付
     */
    public String processPayment(String orderId, double amount, String method) {
        if (amount <= 0) {
            throw new IllegalArgumentException("Payment amount must be positive");
        }
        if (!isValidPaymentMethod(method)) {
            throw new IllegalArgumentException("Invalid payment method: " + method);
        }
        String paymentId = "PAY" + String.format("%04d", nextPaymentId++);
        Payment payment = new Payment(paymentId, orderId, amount, method);
        payments.add(payment);
        return paymentId;
    }

    /**
     * 获取支付金额
     */
    public double getPaymentAmount(String paymentId) {
        Payment payment = findPayment(paymentId);
        if (payment == null) {
            throw new IllegalArgumentException("Payment not found: " + paymentId);
        }
        return payment.getAmount();
    }

    /**
     * 退款
     */
    public void refund(String paymentId) {
        Payment payment = findPayment(paymentId);
        if (payment == null) {
            throw new IllegalArgumentException("Payment not found: " + paymentId);
        }
        if ("REFUNDED".equals(payment.getStatus())) {
            throw new IllegalStateException("Payment already refunded");
        }
        payment.setStatus("REFUNDED");
    }

    /**
     * 计算特定支付方式的总金额
     */
    public double calculateTotalByMethod(String method) {
        double total = 0;
        for (Payment payment : payments) {
            if (payment.getMethod().equals(method) && !"REFUNDED".equals(payment.getStatus())) {
                total += payment.getAmount();
            }
        }
        return total;
    }

    /**
     * 获取成功支付的数量
     */
    public int getSuccessfulPaymentCount() {
        int count = 0;
        for (Payment payment : payments) {
            if ("COMPLETED".equals(payment.getStatus())) {
                count++;
            }
        }
        return count;
    }

    /**
     * 应用手续费
     */
    public double applyTransactionFee(double amount, String method) {
        if (amount < 0) {
            throw new IllegalArgumentException("Amount cannot be negative");
        }
        double feeRate = 0.02; // 默认2%
        if ("CREDIT_CARD".equals(method)) {
            feeRate = 0.03; // 信用卡3%
        }
        return amount * (1 + feeRate);
    }

    private boolean isValidPaymentMethod(String method) {
        return "CREDIT_CARD".equals(method) ||
               "DEBIT_CARD".equals(method) ||
               "CASH".equals(method);
    }

    private Payment findPayment(String paymentId) {
        for (Payment payment : payments) {
            if (payment.getPaymentId().equals(paymentId)) {
                return payment;
            }
        }
        return null;
    }

    // 内部支付类
    private static class Payment {
        private String paymentId;
        private String orderId;
        private double amount;
        private String method;
        private String status;

        public Payment(String paymentId, String orderId, double amount, String method) {
            this.paymentId = paymentId;
            this.orderId = orderId;
            this.amount = amount;
            this.method = method;
            this.status = "COMPLETED";
        }

        public String getPaymentId() { return paymentId; }
        public double getAmount() { return amount; }
        public String getMethod() { return method; }
        public String getStatus() { return status; }
        public void setStatus(String status) { this.status = status; }
    }
}
