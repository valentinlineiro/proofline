package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** Protected: lives outside the editable workspace, injected by the verifier. */
class AcceptanceDiscountTest {
    private final DiscountService service = new DiscountService();

    private void assertTotal(String subtotal, String expected) {
        assertEquals(0, new BigDecimal(expected).compareTo(service.total(new BigDecimal(subtotal))));
    }

    @Test
    void shouldApplyTenPercentWhenSubtotalExceedsLimit() {
        assertTotal("120.00", "108.00");
    }

    /** Business rule: totals are rounded HALF_UP to cents. 100.05 * 0.90 = 90.045 exactly, so HALF_UP gives 90.05 (HALF_EVEN would give 90.04). */
    @Test
    void shouldRoundHalfUpToCentsWhenDiscountLeavesFractionOfCent() {
        assertEquals(new BigDecimal("90.05"), service.total(new BigDecimal("100.05")));  // equals(): scale must be 2
    }

    @Test
    void shouldNotDiscountWhenSubtotalEqualsLimit() {
        assertTotal("100.00", "100.00");
    }
}
