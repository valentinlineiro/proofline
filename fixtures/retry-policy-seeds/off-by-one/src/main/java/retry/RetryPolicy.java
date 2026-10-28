package retry;

import java.time.Duration;
import java.util.Set;

/** Decides whether a failed attempt should be retried and how long to wait. Pure computation: no sleeping, no I/O. */
public final class RetryPolicy {
    /** What to do next: retry (after delay) or give up (delay is zero). */
    public record Decision(boolean retry, Duration delay) {}

    private final int maxAttempts;
    private final Backoff backoff;
    private final Set<Class<? extends Throwable>> transientErrors;

    /** maxAttempts is the total number of attempts, the initial one included. */
    public RetryPolicy(int maxAttempts, Backoff backoff, Set<Class<? extends Throwable>> transientErrors) {
        if (maxAttempts < 1 || backoff == null || transientErrors == null) {
            throw new IllegalArgumentException("need maxAttempts >= 1, a backoff and a set of transient errors");
        }
        this.maxAttempts = maxAttempts;
        this.backoff = backoff;
        this.transientErrors = Set.copyOf(transientErrors);
    }

    /** failedAttempts: how many attempts have already been made, all failed (1 = only the initial attempt). */
    public Decision decide(int failedAttempts, Throwable error) {
        if (error == null || failedAttempts < 1 || failedAttempts > maxAttempts) {
            throw new IllegalArgumentException("invalid attempt or error");
        }
        boolean transientError = transientErrors.stream().anyMatch(t -> t.isInstance(error));
        if (transientError && failedAttempts <= maxAttempts) {
            return new Decision(true, backoff.delayAfter(failedAttempts));
        }
        return new Decision(false, Duration.ZERO);
    }
}
