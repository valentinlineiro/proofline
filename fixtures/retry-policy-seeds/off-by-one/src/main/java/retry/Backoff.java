package retry;

import java.time.Duration;

/** Exponential backoff with a cap. Pure computation: it never sleeps. */
public final class Backoff {
    private final Duration base;
    private final Duration max;

    public Backoff(Duration base, Duration max) {
        if (base == null || max == null || base.isZero() || base.isNegative() || max.compareTo(base) < 0) {
            throw new IllegalArgumentException("need 0 < base <= max");
        }
        this.base = base;
        this.max = max;
    }

    /** Delay to wait after the given number of failed attempts (1 = the first attempt failed). */
    public Duration delayAfter(int failedAttempts) {
        if (failedAttempts < 1) {
            throw new IllegalArgumentException("failedAttempts must be >= 1");
        }
        Duration delay = base;
        for (int i = 1; i < failedAttempts; i++) {
            if (delay.compareTo(max.dividedBy(2)) > 0) {
                return max;
            }
            delay = delay.multipliedBy(2);
        }
        return delay;
    }
}
