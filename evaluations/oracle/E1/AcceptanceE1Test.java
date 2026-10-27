package shop;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.List;
import org.junit.jupiter.api.Test;

class AcceptanceE1Test {
    private final DiscountService s = new DiscountService();

    private void split(String total, int parts, String... expected) {
        List<BigDecimal> want = Arrays.stream(expected).map(BigDecimal::new).toList();
        assertEquals(want, s.splitEvenly(new BigDecimal(total), parts), total + "/" + parts);  // equals(): scale 2
    }

    @Test void shouldGiveExtraCentsToFirstShares() { split("100.00", 3, "33.34", "33.33", "33.33"); split("0.02", 3, "0.01", "0.01", "0.00"); }
    @Test void shouldHandleSmallestAmount() { split("0.01", 3, "0.01", "0.00", "0.00"); }
    @Test void shouldSplitExactlyWhenDivisible() { split("100.00", 4, "25.00", "25.00", "25.00", "25.00"); split("10.00", 1, "10.00"); }
    @Test void shouldRejectNonPositiveParts() {
        assertThrows(IllegalArgumentException.class, () -> s.splitEvenly(new BigDecimal("10.00"), 0));
        assertThrows(IllegalArgumentException.class, () -> s.splitEvenly(new BigDecimal("10.00"), -2));
    }
}
