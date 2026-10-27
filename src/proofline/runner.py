"""Runs a Maven command inside a throwaway Docker container and records what really happened."""
import os
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = "maven:3.9-eclipse-temurin-21"


M2_VOLUME = "proofline-m2"


def warm_cache(fixture: Path) -> None:
    """Only step with network: downloads every dependency into the m2 volume. Runs rw, outside any check."""
    subprocess.run(["docker", "run", "--rm", "-v", f"{fixture.resolve()}:/src:ro", "-v", f"{M2_VOLUME}:/m2",
                    IMAGE, "sh", "-c",
                    "cp -r /src /w && cd /w && mvn -B -Dmaven.repo.local=/m2 dependency:go-offline test -DskipTests"],
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
    script = f'mvn -B -o -Dmaven.repo.local=/m2 "$@"; ec=$?; chown -R {owner} /w; exit $ec'
    cmd = ["docker", "run", "--rm", "--name", name, "--memory", "1g", "--cpus", "2", "--network", "none",
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
