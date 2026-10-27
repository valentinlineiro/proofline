package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class AcceptanceS3Test {
    private final DiscountService s = new DiscountService();

    private void vat(String net, String expected) {
        assertEquals(new BigDecimal(expected), s.totalWithVat(new BigDecimal(net)), "net " + net);  // equals(): scale must be 2
    }

    @Test void shouldAddTwentyOnePercent() { vat("100.00", "121.00"); vat("10.00", "12.10"); }
    @Test void shouldRoundHalfUpToCents() { vat("0.05", "0.06"); vat("1.15", "1.39"); }
    @Test void shouldHandleZero() { vat("0.00", "0.00"); }
}
