package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class AcceptanceS1Test {
    private final DiscountService s = new DiscountService();

    private void shipping(String subtotal, String expected) {
        assertEquals(0, new BigDecimal(expected).compareTo(s.shippingCost(new BigDecimal(subtotal))), "subtotal " + subtotal);
    }

    @Test void shouldChargeShippingBelowThreshold() { shipping("49.99", "4.99"); shipping("0", "4.99"); }
    @Test void shouldBeFreeAtThreshold() { shipping("50.00", "0.00"); }
    @Test void shouldBeFreeAboveThreshold() { shipping("120.00", "0.00"); }
}
