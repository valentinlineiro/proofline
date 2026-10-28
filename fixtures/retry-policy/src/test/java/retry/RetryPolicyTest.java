package retry;

import static org.junit.jupiter.api.Assertions.assertThrows;

import java.io.IOException;
import java.time.Duration;
import java.util.Set;
import org.junit.jupiter.api.Test;

class RetryPolicyTest {
    private final Backoff backoff = new Backoff(Duration.ofMillis(100), Duration.ofSeconds(1));

    @Test
    void shouldRejectZeroMaxAttempts() {
        assertThrows(IllegalArgumentException.class, () -> new RetryPolicy(0, backoff, Set.of(IOException.class)));
    }

    @Test
    void shouldRejectCapSmallerThanBase() {
        assertThrows(IllegalArgumentException.class, () -> new Backoff(Duration.ofSeconds(2), Duration.ofSeconds(1)));
    }
}
