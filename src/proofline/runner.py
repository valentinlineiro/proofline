"""Runs a Maven command inside a throwaway Docker container and records what really happened."""
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .project import all_ids, load

IMAGE = "maven:3.9-eclipse-temurin-21"


M2_VOLUME = os.environ.get("PROOFLINE_M2_VOLUME", "proofline-m2")


# Maven (compile, surefire, the verdict) runs as root; the forked test JVM, the only place where model-written code
# executes, runs as nobody. Everything under /w belongs to root or the host user, so that code cannot write to the
# sources, the protected tests, the compiled classes or the reports, nor signal or trace the Maven process.
_JVM_WRAPPER = ("mkdir -p /opt/unprivileged/bin && printf '%s\\n' '#!/bin/sh' "
                "'exec setpriv --reuid=65534 --regid=65534 --clear-groups --inh-caps=-all \"$(dirname \"$(readlink -f \"$(command -v java)\")\")/java\" \"$@\"' "
                "> /opt/unprivileged/bin/java && chmod +x /opt/unprivileged/bin/java; ")
_CAPS = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "SETUID", "SETGID")  # chown of results, setpriv; nothing else


def warm_cache(fixture: Path) -> None:
    """Only step with network: downloads every dependency into the m2 volume. Runs rw, outside any check."""
    subprocess.run(["docker", "run", "--rm", "-v", f"{fixture.resolve()}:/src:ro", "-v", f"{M2_VOLUME}:/m2",
                    IMAGE, "sh", "-c",
                    "cp -r /src /w && cd /w && mvn -B -Dmaven.repo.local=/m2 dependency:go-offline test"],
                   check=True)


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    duration_s: float
    timed_out: bool = False


def run_maven(workdir: Path, args: list[str], timeout_s: int = 300) -> RunResult:
    name = f"proofline-{uuid.uuid4().hex[:8]}"
    owner = f"{os.getuid()}:{os.getgid()}"
    script = f'{_JVM_WRAPPER}mvn -B -o -Dmaven.repo.local=/m2 -Djvm=/opt/unprivileged/bin/java "$@"; ec=$?; chown -R {owner} /w; exit $ec'
    cmd = ["docker", "run", "--rm", "--name", name, "--memory", "1g", "--cpus", "2", "--network", "none",
           "--cap-drop", "ALL", *[a for c in _CAPS for a in ("--cap-add", c)],
           "--security-opt", "no-new-privileges", "--pids-limit", "512",
           "-v", f"{workdir.resolve()}:/w", "-w", "/w", "-v", f"{M2_VOLUME}:/m2:ro",
           IMAGE, "sh", "-c", script, "sh", *args]
    start = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        return RunResult(p.returncode, p.stdout + p.stderr, time.monotonic() - start)
    except subprocess.TimeoutExpired as e:
        subprocess.run(["docker", "kill", name], capture_output=True)
        out = (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return RunResult(-1, out, time.monotonic() - start, timed_out=True)


if __name__ == "__main__":  # python -m proofline.runner : one-time, networked download of the Maven dependencies
    projects = [load(i) for i in all_ids()]  # validate every manifest before the first networked step
    for project in projects:
        warm_cache(project.fixture)
