"""Everything specific to one build ecosystem lives in one adapter; the verifier core only calls this interface."""
from pathlib import Path
from typing import Callable, Protocol

KNOWN = ("java-maven",)  # names a manifest may use for `ecosystem`


class Adapter(Protocol):
    name: str

    def run_check(self, workdir: Path, selector: str, run: Callable | None = None): ...
    def classify(self, r) -> str: ...
    def warm(self, project_root: Path) -> None: ...


def get_adapter(name: str) -> Adapter:
    if name not in KNOWN:
        raise _error(f"unknown ecosystem {name!r}; known: {', '.join(KNOWN)}")
    from .maven import MavenAdapter  # imported here: the adapter imports the container runner
    return MavenAdapter()


def _error(msg: str) -> Exception:
    from ..project import ProjectError
    return ProjectError(msg)
