package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class AcceptanceE2Test {
    private final DiscountService s = new DiscountService();

    private void total(String subtotal, String expected) {
        assertEquals(new BigDecimal(expected), s.checkedTotal(new BigDecimal(subtotal)), "subtotal " + subtotal);  // scale 2
    }

    @Test void shouldCompareWithExactSubtotalBeforeRounding() { total("100.005", "90.00"); total("99.995", "100.00"); }
    @Test void shouldNotDiscountAtLimit() { total("100.00", "100.00"); }
    @Test void shouldDiscountJustAboveLimit() { total("100.01", "90.01"); }
    @Test void shouldHandleZeroAndHugeValues() { total("0", "0.00"); total("1000000000000.00", "900000000000.00"); }
    @Test void shouldRejectInvalidInput() {
        assertThrows(IllegalArgumentException.class, () -> s.checkedTotal(new BigDecimal("-0.01")));
        assertThrows(IllegalArgumentException.class, () -> s.checkedTotal(null));
    }
}
