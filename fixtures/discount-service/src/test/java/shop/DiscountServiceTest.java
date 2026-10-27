package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

class DiscountServiceTest {
    private final DiscountService service = new DiscountService();

    @Test
    void shouldChargeFullPriceWhenSubtotalIsSmall() {
        assertEquals(0, new BigDecimal("50.00").compareTo(service.total(new BigDecimal("50.00"))));
    }
}
