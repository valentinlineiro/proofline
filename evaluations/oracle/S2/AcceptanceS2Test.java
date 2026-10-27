package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class AcceptanceS2Test {
    private final DiscountService s = new DiscountService();

    private void coupon(String subtotal, String code, String expected) {
        assertEquals(0, new BigDecimal(expected).compareTo(s.applyCoupon(new BigDecimal(subtotal), code)), subtotal + " " + code);
    }

    @Test void shouldSubtractFiveWhenCodeIsSave5() { coupon("20.00", "SAVE5", "15.00"); }
    @Test void shouldNeverGoBelowZero() { coupon("3.00", "SAVE5", "0.00"); coupon("5.00", "SAVE5", "0.00"); }
    @Test void shouldIgnoreOtherCodes() { coupon("20.00", "save5", "20.00"); coupon("20.00", "OTHER", "20.00"); coupon("20.00", null, "20.00"); }
}
