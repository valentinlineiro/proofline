"""Reproducible identity of a change. `diff_sha256` (the text of `diff -ruN`) embeds the absolute paths of the two trees, so
it cannot be recomputed later; these hashes depend only on relative paths, contents and modes."""
import os
import shutil
import subprocess
from pathlib import Path

from .evidence import sha256
from .project import IGNORED_DIRS, is_ignored
from .workspace import changed_files

__all__ = ["IGNORED_DIRS", "change_hash", "inputs_hash", "snapshot"]


def _mode(p: Path) -> str:
    """Only the executable bit, as git records it: a different umask (664 vs 644) must not change the hash."""
    return "755" if p.stat().st_mode & 0o111 else "644"


def _entry(root: Path, rel: str) -> str:
    p = root / rel
    if p.is_symlink():
        return f"L\0{os.readlink(p)}\0-"
    if p.is_file():
        return f"F\0{sha256(p.read_bytes())}\0{_mode(p)}"
    return "D\0-\0-"  # present in the base, gone in the work tree


def _mode_only(base: Path, work: Path) -> set[str]:
    """Regular files with equal content but a different mode (changed_files compares content only)."""
    return {p.relative_to(base).as_posix() for p in base.rglob("*")
            if p.is_file() and not p.is_symlink() and not is_ignored(p.relative_to(base))
            and (work / p.relative_to(base)).is_file() and not (work / p.relative_to(base)).is_symlink()
            and _mode(work / p.relative_to(base)) != _mode(p)}


def change_hash(base: Path, work: Path) -> str:
    """Hash of what differs between `base` and `work`: sorted relative paths, each with its kind, content hash and mode."""
    lines = (f"{rel}\0{_entry(work, rel)}\n" for rel in sorted(changed_files(base, work) | _mode_only(base, work)))
    return sha256("".join(lines))


def inputs_hash(manifest: Path, protected_tests: Path) -> str:
    """Hash of the manifest and every protected-test file: what judged the change, not the change itself."""
    parts = [f"manifest\0{sha256(manifest.read_bytes())}\n"]
    parts += [f"{p.relative_to(protected_tests)}\0{sha256(p.read_bytes())}\n"
              for p in sorted(protected_tests.rglob("*")) if p.is_file()]
    return sha256("".join(parts))


def snapshot(repo: Path, into: Path) -> Path:
    """A copy of the working tree as the verifier should see it, taken once: in a git repository, tracked and untracked files
    that git does not ignore (so a git-ignored `.env` is neither verified, hashed nor written into the evidence); otherwise every
    file outside the top-level ignored directories. Verification and the gate both work on this copy."""
    into.mkdir(parents=True)
    if (repo / ".git").exists():
        out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, check=True).stdout
        rels = [Path(r.decode()) for r in out.split(b"\0") if r]
    else:
        rels = [p.relative_to(repo) for p in repo.rglob("*") if p.is_file() or p.is_symlink()]
    for rel in rels:
        src = repo / rel
        if is_ignored(rel) or not (src.is_file() or src.is_symlink()):  # deleted from the work tree but still in the index
            continue
        (into / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, into / rel, follow_symlinks=False)
    return into
