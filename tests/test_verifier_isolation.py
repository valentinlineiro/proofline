"""Adversarial: the code under test runs inside the verifier's containers. These tests run real hostile Java
(real Docker) and check it can neither change the protected checks nor survive into the next check."""
import json
import shutil
from pathlib import Path

from proofline.evidence import verify_chain
from proofline.verifier import ROOT, verify

BASE = ROOT / "fixtures" / "discount-service"
SERVICE = "src/main/java/shop/DiscountService.java"

# Defective on purpose (returns the subtotal untouched: every acceptance check must FAIL). Its static initializer
# runs inside the test JVM and tries to turn the later acceptance checks green and to leave state behind.
HOSTILE = """package shop;
import java.math.BigDecimal;
import java.nio.file.*;
public class DiscountService {
    static void attack(String what, String path, String body) {
        try { Files.writeString(Path.of(path), body); System.out.println("ATTACK " + what + " WROTE"); }
        catch (Exception e) { System.out.println("ATTACK " + what + " DENIED"); }
    }
    static {
        String green = "package shop; import org.junit.jupiter.api.Test; public class AcceptanceDiscountTest {"
            + " @Test void shouldApplyTenPercentWhenSubtotalExceedsLimit() {}"
            + " @Test void shouldRoundHalfUpToCentsWhenDiscountLeavesFractionOfCent() {}"
            + " @Test void shouldNotDiscountWhenSubtotalEqualsLimit() {} }";
        attack("test-source", "/w/src/test/java/shop/AcceptanceDiscountTest.java", green);
        attack("service-source", "/w/src/main/java/shop/DiscountService.java", "// replaced");
        attack("pom", "/w/pom.xml", "<project/>");
        attack("compiled-test", "/w/target/test-classes/shop/AcceptanceDiscountTest.class", "x");
        attack("workspace-marker", "/w/MARKER", "x");
        try {
            ProcessHandle parent = ProcessHandle.current().parent().get();
            parent.destroyForcibly(); Thread.sleep(500);
            System.out.println("ATTACK kill-parent " + (parent.isAlive() ? "DENIED" : "KILLED"));
        } catch (Exception e) { System.out.println("ATTACK kill-parent DENIED"); }
    }
    public BigDecimal total(BigDecimal subtotal) { return subtotal; }
}
"""


def hostile_run(tmp_path: Path):
    ws = tmp_path / "ws"
    shutil.copytree(BASE, ws)
    (ws / SERVICE).write_text(HOSTILE)
    ev = tmp_path / "ev.jsonl"
    result = verify(BASE, ws, ev)
    assert verify_chain(ev)
    return result, ws, ev


def test_shouldNotLetModelCodeTurnLaterChecksGreenWhenItRewritesTheProtectedTests(tmp_path):
    result, _, ev = hostile_run(tmp_path)
    statuses = {c["name"]: c["status"] for c in result["checks"]}
    assert result["verdict"] == "FAIL"
    assert statuses["acceptance_discount_applies"] == statuses["acceptance_discount_rounding"] == "FAIL"
    assert result["fully_verified"] is False


def test_shouldDenyEveryWriteAndSignalWhenModelCodeRunsInsideTheTestJvm(tmp_path):
    _, ws, ev = hostile_run(tmp_path)
    log = ev.read_text()
    out = "".join(json.loads(line).get("stdout_tail", "") for line in log.splitlines())
    attempts = [l for l in out.splitlines() if l.startswith("ATTACK ")]
    assert attempts, "the hostile initializer never ran: the test proves nothing"
    assert not [l for l in attempts if l.endswith(("WROTE", "KILLED"))], attempts
    assert not (ws / "MARKER").exists() and (ws / SERVICE).read_text() == HOSTILE  # nothing leaked back to the workspace
