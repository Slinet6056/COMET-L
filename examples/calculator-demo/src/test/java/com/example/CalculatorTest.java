package com.example;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import static org.junit.jupiter.api.Assertions.*;

public class CalculatorTest {

    private Calculator target;

    @BeforeEach
    public void setUp() {
        target = new Calculator();
    }

    @Test
    void testAddPositiveNumbers() {
        Calculator calculator = new Calculator();
        int result = calculator.add(2, 3);
        assertEquals(5, result);
    }

    @Test
    void testAddWithNegativeAndZero() {
        Calculator calculator = new Calculator();
        assertEquals(-15, calculator.add(-5, -10));
        assertEquals(0, calculator.add(-5, 5));
        assertEquals(-5, calculator.add(-5, 0));
    }

    @Test
    void testAddWithIntegerOverflow() {
        Calculator calculator = new Calculator();
        int result = calculator.add(Integer.MAX_VALUE, 1);
        assertEquals(Integer.MIN_VALUE, result);
    }

}