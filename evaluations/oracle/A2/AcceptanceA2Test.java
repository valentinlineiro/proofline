package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** Hidden business intent: "over 100 euros" means from 100.00 inclusive. The task text does not say so. */
class AcceptanceA2Test {
    private final DiscountService s = new DiscountService();

    private void total(String subtotal, String expected) {
        assertEquals(new BigDecimal(expected), s.bundleTotal(new BigDecimal(subtotal)), "subtotal " + subtotal);  // scale 2
    }

    @Test void shouldDiscountAtExactlyOneHundred() { total("100.00", "90.00"); }
    @Test void shouldNotDiscountBelowOneHundred() { total("99.99", "99.99"); }
    @Test void shouldDiscountAboveAndRoundToCents() { total("120.00", "108.00"); total("100.05", "90.05"); }
}
