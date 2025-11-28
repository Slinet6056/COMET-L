package com.example;

/**
 * 简单的计算器类 - 用于演示 COMET-L 系统
 *
 * 注意：此类故意包含一些缺陷，用于演示变异测试
 */
public class Calculator {

    /**
     * 加法运算
     *
     * @param a 第一个加数
     * @param b 第二个加数
     * @return 两数之和
     */
    public int add(int a, int b) {
        return a + b;
    }

    // /**
    //  * 减法运算
    //  *
    //  * @param a 被减数
    //  * @param b 减数
    //  * @return 两数之差
    //  */
    // public int subtract(int a, int b) {
    //     return a - b;
    // }

    // /**
    //  * 乘法运算
    //  *
    //  * @param a 第一个乘数
    //  * @param b 第二个乘数
    //  * @return 两数之积
    //  */
    // public int multiply(int a, int b) {
    //     return a * b;
    // }

    // /**
    //  * 除法运算
    //  * 注意：缺陷 1 - 没有检查除数为零的情况
    //  *
    //  * @param a 被除数
    //  * @param b 除数
    //  * @return 两数之商
    //  * @throws ArithmeticException 当除数为零时
    //  */
    // public int divide(int a, int b) {
    //     // BUG: 应该先检查 b == 0
    //     return a / b;
    // }

    // /**
    //  * 计算阶乘
    //  * 注意：缺陷 2 - 没有处理负数和溢出
    //  *
    //  * @param n 非负整数
    //  * @return n 的阶乘
    //  */
    // public long factorial(int n) {
    //     // BUG: 没有检查负数
    //     if (n == 0 || n == 1) {
    //         return 1;
    //     }
    //     // BUG: 没有处理溢出
    //     return n * factorial(n - 1);
    // }

    // /**
    //  * 计算平方根（整数部分）
    //  * 注意：缺陷 3 - 没有处理负数
    //  *
    //  * @param n 非负整数
    //  * @return n 的平方根的整数部分
    //  */
    // public int sqrt(int n) {
    //     // BUG: 没有检查负数
    //     return (int) Math.sqrt(n);
    // }

    // /**
    //  * 计算两个数的最大公约数
    //  *
    //  * @param a 第一个数
    //  * @param b 第二个数
    //  * @return 最大公约数
    //  */
    // public int gcd(int a, int b) {
    //     // 欧几里得算法
    //     if (b == 0) {
    //         return a;
    //     }
    //     return gcd(b, a % b);
    // }

    /**
     * 检查是否为质数
     * 注意：缺陷 4 - 边界情况处理不完整
     *
     * @param n 要检查的数
     * @return 是否为质数
     */
    public boolean isPrime(int n) {
        // BUG: 没有正确处理 n <= 1 的情况
        if (n <= 1) {
            return false;
        }
        if (n == 2) {
            return true;
        }
        if (n % 2 == 0) {
            return false;
        }

        // 只检查到 sqrt(n)
        for (int i = 3; i * i <= n; i += 2) {
            if (n % i == 0) {
                return false;
            }
        }
        return true;
    }

    /**
     * 计算斐波那契数列第 n 项
     * 注意：缺陷 5 - 效率低下且没有处理大数
     *
     * @param n 项数（从 0 开始）
     * @return 第 n 项的值
     */
    public long fibonacci(int n) {
        // BUG: 效率低下的递归实现，没有缓存
        if (n < 0) {
            throw new IllegalArgumentException("n must be non-negative");
        }
        if (n == 0) {
            return 0;
        }
        if (n == 1) {
            return 1;
        }
        return fibonacci(n - 1) + fibonacci(n - 2);
    }
}
