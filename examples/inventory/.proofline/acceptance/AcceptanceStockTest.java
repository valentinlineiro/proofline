package inventory;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class AcceptanceStockTest {
    @Test
    void shouldReturnRemainingAfterReserving() {
        Stock s = new Stock(10);
        assertEquals(7, s.reserve(3));
        assertEquals(0, s.reserve(7));
    }

    @Test
    void shouldRejectReservingMoreThanAvailable() {
        Stock s = new Stock(5);
        assertThrows(IllegalStateException.class, () -> s.reserve(6));
        assertEquals(5, s.available());
    }

    @Test
    void shouldRejectNonPositiveQuantities() {
        Stock s = new Stock(5);
        assertThrows(IllegalArgumentException.class, () -> s.reserve(0));
        assertThrows(IllegalArgumentException.class, () -> s.reserve(-1));
        assertEquals(5, s.available());
    }
}
