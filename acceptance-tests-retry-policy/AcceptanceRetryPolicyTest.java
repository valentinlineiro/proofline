package retry;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.IOException;
import java.net.SocketTimeoutException;
import java.time.Duration;
import java.util.Set;
import org.junit.jupiter.api.Test;

/** Protected: lives outside the editable workspace, injected by the verifier. */
class AcceptanceRetryPolicyTest {
    private final Backoff backoff = new Backoff(Duration.ofMillis(100), Duration.ofSeconds(1));
    private final RetryPolicy policy = new RetryPolicy(3, backoff, Set.of(IOException.class));
    private final Throwable transientError = new IOException("connection reset");

    /** maxAttempts counts the initial attempt: with 3, a third failure must end the retries. */
    @Test
    void shouldNotRetryAfterTheLastAllowedAttempt() {
        assertTrue(policy.decide(1, transientError).retry());
        assertTrue(policy.decide(2, transientError).retry());
        RetryPolicy.Decision last = policy.decide(3, transientError);
        assertFalse(last.retry());
        assertEquals(Duration.ZERO, last.delay());
    }

    @Test
    void shouldRetryOnlyConfiguredTransientErrors() {
        assertTrue(policy.decide(1, transientError).retry());
        assertTrue(policy.decide(1, new SocketTimeoutException("slow")).retry());  // a subtype of a configured error
        assertFalse(policy.decide(1, new IllegalStateException("permanent")).retry());
        assertFalse(policy.decide(1, new Exception("unknown")).retry());
        assertEquals(Duration.ZERO, policy.decide(1, new IllegalStateException("permanent")).delay());
        assertThrows(IllegalArgumentException.class, () -> policy.decide(1, null));
        assertThrows(IllegalArgumentException.class, () -> policy.decide(0, transientError));
        assertThrows(IllegalArgumentException.class, () -> policy.decide(4, transientError));
    }

    @Test
    void shouldGrowExponentiallyUntilTheCap() {
        long[] expectedMillis = {100, 200, 400, 800, 1000, 1000};
        for (int n = 1; n <= expectedMillis.length; n++) {
            assertEquals(Duration.ofMillis(expectedMillis[n - 1]), backoff.delayAfter(n), "after " + n + " failed attempts");
        }
        assertEquals(Duration.ofMillis(200), policy.decide(2, transientError).delay());  // the decision carries the backoff delay
        assertThrows(IllegalArgumentException.class, () -> backoff.delayAfter(0));
    }

    @Test
    void shouldSaturateInsteadOfOverflowingForHugeAttemptNumbers() {
        Backoff wide = new Backoff(Duration.ofSeconds(1), Duration.ofDays(365));
        for (int n : new int[] {40, 64, 100, 1_000_000, Integer.MAX_VALUE}) {
            Duration d = wide.delayAfter(n);
            assertFalse(d.isNegative(), "negative after " + n);
            assertEquals(Duration.ofDays(365), d, "after " + n);
        }
        assertEquals(Duration.ofSeconds(1L << 20), wide.delayAfter(21));  // still exact below the cap
    }
}
