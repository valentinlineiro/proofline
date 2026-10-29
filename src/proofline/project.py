"""Project manifest: the only per-project facts the verifier needs (what the agent may edit, which checks judge it, where the
protected tests live). Validated strictly: an invalid manifest is an explicit error, never a permissive run."""
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[2]
PROJECTS = ROOT / "projects"
DEFAULT_ID = "discount-service"
ID_RE = r"[a-z0-9][a-z0-9-]*"
IGNORED_DIRS = ("target", ".git", ".proofline")  # build output, VCS and Proofline's own records are never part of a change


class ProjectError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Check(_Strict):
    name: str = Field(pattern=r"^[a-z0-9_]+$")
    selector: str = Field(min_length=1)  # surefire -Dtest selector


class _Manifest(_Strict):
    id: str = Field(pattern=f"^{ID_RE}$")
    fixture: str
    seeds: str | None = None
    acceptance: str
    acceptance_dest: str
    editable: list[str] = Field(min_length=1)
    checks: list[_Check] = Field(min_length=1)
    default_task: str = Field(min_length=1)
    ecosystem: str = "java-maven"


@dataclass(frozen=True)
class Project:
    id: str
    fixture: Path                        # pristine project; also the base every diff is taken against
    seeds: Path | None                   # prepared-defect overlays for the controlled demo
    acceptance: Path                     # protected tests + requirements.json, outside the fixture the agent works on
    acceptance_dest: str                 # where the protected tests are injected inside the sandbox
    editable: tuple[str, ...]            # the only files the agent may change, relative to the fixture
    checks: tuple[tuple[str, str], ...]  # (check name, surefire selector)
    default_task: str
    ecosystem: str = "java-maven"
    root: Path | None = None             # what every manifest path is relative to (the Proofline repo, or the external repo)
    manifest: Path | None = None


def _inside(base: Path, rel: str, what: str) -> Path:
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts:
        raise ProjectError(f"{what}: must be a relative path without '..': {rel!r}")
    target = (base / str(p)).resolve()  # resolved once: the path that was validated is the path that gets used
    if not target.is_relative_to(base.resolve()):
        raise ProjectError(f"{what}: escapes {base.name!r} (symlink?): {rel!r}")
    return target


def load(project_id: str = DEFAULT_ID, projects_dir: Path = PROJECTS, root: Path = ROOT) -> Project:
    if not re.fullmatch(ID_RE, project_id):
        raise ProjectError(f"invalid project id: {project_id!r}")
    f = projects_dir / f"{project_id}.json"
    try:
        m = _Manifest.model_validate_json(f.read_text())
    except FileNotFoundError:
        raise ProjectError(f"unknown project: {project_id!r}") from None
    except ValueError as e:  # malformed JSON, unknown keys, wrong types
        raise ProjectError(f"invalid manifest {f.name}: {e}") from e
    from .adapters import get_adapter
    get_adapter(m.ecosystem)  # an unknown ecosystem is an explicit error at load time
    if m.id != project_id:
        raise ProjectError(f"manifest {f.name} declares id {m.id!r}")
    return _build(m, root, f)


class _ExternalManifest(_Strict):
    """`.proofline/project.json` of a repository: the repository itself is the fixture, so there is no fixture or seeds key."""
    id: str = Field(pattern=f"^{ID_RE}$")
    acceptance: str = ".proofline/acceptance"
    acceptance_dest: str
    editable: list[str] = Field(min_length=1)
    checks: list[_Check] = Field(min_length=1)
    default_task: str = "Verify the change."
    ecosystem: str = "java-maven"


def load_external(repo: Path) -> Project:
    """A project that lives in someone else's repository: same strict validation, with `repo` as the root."""
    repo = repo.resolve()
    f = repo / ".proofline" / "project.json"
    try:
        e = _ExternalManifest.model_validate_json(f.read_text())
    except FileNotFoundError:
        raise ProjectError(f"no {f.relative_to(repo)} in {repo.name!r}: run `proofline init` first") from None
    except ValueError as err:
        raise ProjectError(f"invalid manifest {f.name}: {err}") from err
    m = _Manifest(id=e.id, fixture=".", acceptance=e.acceptance, acceptance_dest=e.acceptance_dest, editable=e.editable,
                  checks=e.checks, default_task=e.default_task, ecosystem=e.ecosystem)
    return _build(m, repo, f)


def _build(m: _Manifest, root: Path, manifest: Path) -> Project:
    from .adapters import get_adapter
    get_adapter(m.ecosystem)  # an unknown ecosystem is an explicit error at load time
    fixture = _inside(root, m.fixture, "fixture")
    acceptance = _inside(root, m.acceptance, "acceptance")
    seeds = _inside(root, m.seeds, "seeds") if m.seeds else None
    for d, what in ((fixture, "fixture"), (acceptance, "acceptance"), *(((seeds, "seeds"),) if seeds else ())):
        if not d.is_dir():
            raise ProjectError(f"{what}: not a directory: {d.name!r}")
    _inside(Path("/sandbox"), m.acceptance_dest, "acceptance_dest")
    for e in m.editable:
        t = fixture / e
        if t.is_symlink() or not _inside(fixture, e, "editable").is_file():
            raise ProjectError(f"editable: not a regular file in the fixture: {e!r}")
    if len(set(m.editable)) != len(m.editable):
        raise ProjectError("editable: duplicate entries")
    for d, what in ((fixture, "fixture"), (acceptance, "acceptance"), *(((seeds, "seeds"),) if seeds else ())):
        if any(t.is_symlink() for t in d.rglob("*") if not set(IGNORED_DIRS) & set(t.relative_to(d).parts)):  # copytree would follow them and pull outside content in
            raise ProjectError(f"{what}: symlinks are not allowed")
    names = [c.name for c in m.checks]
    if len(set(names)) != len(names):
        raise ProjectError("checks: duplicate names")
    for c in m.checks:  # a Class#method selector must name a test that exists, or the check could never run
        if "#" in c.selector:
            cls, method = c.selector.split("#", 1)
            files = [*acceptance.rglob(f"{cls}.java"), *fixture.rglob(f"{cls}.java")]
            if not any(re.search(rf"\b{re.escape(method)}\s*\(", f.read_text()) for f in files):
                raise ProjectError(f"check {c.name!r}: no test {c.selector!r} in the acceptance tests or the fixture")
    req = acceptance / "requirements.json"
    unknown = sorted({c for r in (json.loads(req.read_text()) if req.exists() else []) for c in r["checks"]} - set(names))
    if unknown:
        raise ProjectError(f"requirements.json references checks the manifest does not define: {unknown}")
    return Project(m.id, fixture, seeds, acceptance, m.acceptance_dest, tuple(m.editable),
                   tuple((c.name, c.selector) for c in m.checks), m.default_task, m.ecosystem, root.resolve(), manifest.resolve())


def all_ids(projects_dir: Path = PROJECTS) -> list[str]:
    return sorted(p.stem for p in projects_dir.glob("*.json"))
