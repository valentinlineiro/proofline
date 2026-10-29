package inventory;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class StockTest {
    @Test
    void shouldStartWithTheGivenQuantity() {
        assertEquals(4, new Stock(4).available());
    }

    @Test
    void shouldRejectANegativeInitialQuantity() {
        assertThrows(IllegalArgumentException.class, () -> new Stock(-1));
    }
}
