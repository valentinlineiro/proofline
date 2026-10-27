package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class AcceptanceE3Test {
    private final DiscountService s = new DiscountService();

    private void line(String price, int qty, String expected) {
        assertEquals(new BigDecimal(expected), s.bulkLineTotal(new BigDecimal(price), qty), price + " x " + qty);  // scale 2
    }

    @Test void shouldRoundOnceOnTheLineTotalNotPerUnit() { line("0.99", 10, "9.41"); line("19.99", 10, "189.91"); }
    @Test void shouldNotDiscountBelowTen() { line("0.99", 9, "8.91"); line("0.01", 1, "0.01"); }
    @Test void shouldRejectInvalidLines() {
        assertThrows(IllegalArgumentException.class, () -> s.bulkLineTotal(new BigDecimal("5.00"), 0));
        assertThrows(IllegalArgumentException.class, () -> s.bulkLineTotal(new BigDecimal("-1.00"), 1));
    }
}
