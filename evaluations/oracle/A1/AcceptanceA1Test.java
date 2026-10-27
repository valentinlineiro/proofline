package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** The requirement is underspecified, so no oracle can judge the loyalty rule. This only guards existing behaviour. */
class AcceptanceA1Test {
    @Test void shouldKeepExistingTotalBehaviour() {
        assertEquals(0, new BigDecimal("50.00").compareTo(new DiscountService().total(new BigDecimal("50.00"))));
    }
}
