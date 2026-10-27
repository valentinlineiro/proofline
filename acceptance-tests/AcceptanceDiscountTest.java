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

    @Test
    void shouldNotDiscountWhenSubtotalEqualsLimit() {
        assertTotal("100.00", "100.00");
    }
}
