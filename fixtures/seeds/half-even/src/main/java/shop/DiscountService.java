package shop;

import java.math.BigDecimal;
import java.math.RoundingMode;

public class DiscountService {
    /** Returns the order total for a given subtotal. */
    public BigDecimal total(BigDecimal subtotal) {
        if (subtotal.compareTo(new BigDecimal("100")) > 0) {
            return subtotal.multiply(new BigDecimal("0.90")).setScale(2, RoundingMode.HALF_EVEN);
        }
        return subtotal;
    }
}
