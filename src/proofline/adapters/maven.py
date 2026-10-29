"""Java/Maven: surefire selectors, how a Maven run is read, and the networked dependency warm-up."""
import re
from pathlib import Path
from typing import Callable

from ..runner import RunResult, run_maven, warm_cache


def classify(r: RunResult) -> str:
    ran = [int(n) for n in re.findall(r"Tests run: (\d+), Failures", r.stdout)]
    if r.timed_out or r.exit_code in (125, 126, 127) or r.exit_code == -1:
        return "INCONCLUSIVE"
    if r.exit_code == 0:
        return "PASS" if ran and ran[-1] > 0 else "INCONCLUSIVE"  # green with zero tests proves nothing
    if r.exit_code == 1 and ("COMPILATION ERROR" in r.stdout or "FAILURE!" in r.stdout):
        return "FAIL"
    return "INCONCLUSIVE"  # e.g. dependency resolution failed: infra, not code


class MavenAdapter:
    name = "java-maven"

    def run_check(self, workdir: Path, selector: str, run: Callable[[Path, list[str]], RunResult] = run_maven) -> RunResult:
        """`run` is the container runner; the verifier passes its own module-level name so tests and the harness can wrap it."""
        return run(workdir, ["test", f"-Dtest={selector}", "-Dsurefire.failIfNoSpecifiedTests=true"])

    def classify(self, r: RunResult) -> str:
        return classify(r)

    def warm(self, project_root: Path) -> None:
        warm_cache(project_root)
