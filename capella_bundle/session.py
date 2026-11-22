"""One permanent clone per model, on the engineer's own disk.

The hosted server mints a temp directory per session and clones into it, which
is right for a multi-tenant service: sessions are cheap, isolated, and nobody's
laptop is involved. A cron sweeps them after four hours. It is the wrong shape
for one engineer with one model -- a restart orphans a full copy of a
repository under ``%TEMP%`` and forces a re-clone of something already sitting
on their own disk, and a Capella model with its libraries is not small.

So this keeps **one clone per model, in a stable location, reused across
runs**, and that one change is what everything below follows from.

### The adapter: ``_session_dir``, not ``_TEMP_BASE``

``capella_service.py`` is not edited by any of this, and does not need to be.
Every path it builds for a session goes through one function::

    def _session_dir(session_id: str) -> Path:
        return _TEMP_BASE / session_id

``_TEMP_BASE`` has exactly one reader, that line, and every other caller --
including ``git_service``, which does ``import capella_service as svc`` and
calls ``svc._session_dir(...)`` -- looks the name up on the module at call
time. So replacing the *function* is the same seam as reassigning the base,
one level up, and it buys the thing reassigning the base cannot do: a model
may live anywhere, including another drive, rather than having to be a direct
child of one directory.

It is also the thread-safe version. The harness can issue parallel tool calls,
so a module global flipped per call is a race; a registry is a dict read.

### Session ids

Kept, because every tool's shape should transfer unchanged to the hosted
service -- an engineer who moves up relearns nothing, and a prompt written
against one works against the other.

The id is the model directory's **name**, not a ``uuid4``. That is forced
rather than chosen: ``_session_dir`` joins the id onto a base, so for the
session directory and the permanent model directory to be the same directory,
the id has to be that directory's name. It is also what makes a restart hand
back the same id instead of looking like data loss, which is the thing a
derived id buys.

### Nothing here reconciles, and that is the inversion worth noticing

The whole reason the git layer was extracted into ``mcp_desktop`` was that a
second copy of ``sync``/``_reconcile`` would be found out latest and cost most
*here* -- a ``.aird`` is one file, so two sides editing one model collide in a
way two knowledge entries never do. That reasoning holds, and its conclusion
is the opposite of what it sounds like: see ``RECONCILE``.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

import capella_service as svc

from mcp_desktop import gitwork
from mcp_desktop.gitwork import Remote

from capella_bundle.config import Dependency, ModelConfig, require_git

#: The commit identity written into every model clone. Without these two
#: settings git refuses to commit, and the failure surfaces much later as
#: "Committer identity unknown" on the engineer's first patch.
COMMIT_NAME = "Capella Fabric"
COMMIT_EMAIL = "capella-fabric@localhost"

#: **This server never replays commits across a divergence.** Passed to
#: ``gitwork.sync`` as ``reconcile=False``, and the one place the reason is
#: written down.
#:
#: The notebook reconciles, and is right to. Its repository is one file per
#: entry, so two sides adding entries touch different files and a rebase
#: replays them with no interaction at all; the only file that can collide is
#: a package's ``index.json``. Divergence there is an ordinary two-session
#: overlap, and asking the engineer to rebase by hand -- which is what the
#: first version did, for real, on 2026-11-20 -- is answering a routine
#: situation with homework.
#:
#: A Capella model is one file. Two sides editing the model *always* touch it,
#: so the precondition that makes an unattended rebase safe is never met here.
#: Worse than a conflict: git merges that file **textually**, and two edits in
#: different regions of model XML merge cleanly with no conflict at all. The
#: result can be a model Capella will not open, or one where a reference
#: points at an element the other side deleted -- and it arrives marked
#: "synced and reconciled". A conflict is a nuisance; a clean rebase that
#: produces an invalid model is a failure nobody is told about.
#:
#: So the parameter the library already has for this is used. ``sync`` still
#: does everything else: the fetch, the fast-forward, the ahead/behind count,
#: the four distinct outcomes, the offline note. What is shared is shared; the
#: one judgement that differs is one argument.
RECONCILE = False

#: What the generic git layer cannot know, appended to its divergence report.
#:
#: Naming the file is the difference between an agent that tells the engineer
#: which model to open and one that relays a git error.
DIVERGENCE_HINT = (
    "The file that disagrees is the Capella model itself, so this is not a "
    "merge anyone should do blind: open the clone in Capella (or a "
    "model-aware diff) and decide there. Nothing has been changed here, and "
    "every commit is still in git."
)

#: A local git call -- rev-parse, config, remote set-url -- finishes in
#: milliseconds. These are the library's numbers rather than a second pair
#: beside them, so there is one answer to "how long is a git call allowed to
#: be stuck".
GIT_LOCAL_TIMEOUT = gitwork.DEFAULT_LOCAL_TIMEOUT

#: Anything that talks to the remote. Generous, because a large Capella model
#: on a slow link is a real thing, but still bounded.
GIT_NETWORK_TIMEOUT = gitwork.DEFAULT_NETWORK_TIMEOUT


class ModelError(RuntimeError):
    """Something the engineer can act on, phrased for them."""


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

#: session_id -> the directory that session's files live in. Written before
#: any call that needs it and read by the function installed below.
_DIRS: dict[str, Path] = {}

#: ``capella_service._session_dir`` as it was before this module touched it.
#: Kept so an id this server does not know still resolves the way the hosted
#: server resolves it, rather than raising from inside somebody else's module.
_ORIGINAL_SESSION_DIR = svc._session_dir

_LOCK = threading.Lock()


def _session_dir(session_id: str) -> Path:
    """``capella_service``'s session directory, for a model this server knows.

    Installed over ``capella_service._session_dir``. A dict read, so it is
    safe under the parallel tool calls a harness can issue, and a fall-through
    for anything unregistered -- importing this module must not change how the
    hosted front end behaves if both are ever loaded in one process.
    """
    known = _DIRS.get(session_id)
    return known if known is not None else _ORIGINAL_SESSION_DIR(session_id)


def install_adapter() -> None:
    """Point ``capella_service`` at this server's directories.

    Idempotent, and deliberately an assignment on another module rather than
    an edit to it: ``capella_service.py``, ``git_service.py``, ``mcp_server.py``
    and ``app.py`` are shared with a live hosted service and stay byte for
    byte as they are.
    """
    if svc._session_dir is not _session_dir:
        svc._session_dir = _session_dir


def session_id_for(cfg: ModelConfig) -> str:
    """This model's session id: the name of its directory.

    Not a hash of the path, which is what the notebook uses -- there the id is
    opaque and nothing joins it onto anything. Here ``_session_dir`` joins the
    id onto a base, so for the session directory and the permanent model
    directory to be one directory, the id has to be that directory's name.

    Stable across restarts, which is the property that matters: an agent's
    ``session_id`` survives a server restart instead of a restart looking like
    lost work.
    """
    return cfg.model_dir.name


def _register(cfg: ModelConfig) -> str:
    """Record this model's directory against its session id, and return the id.

    **Refuses a collision**, which is the one thing a name-derived id can run
    into: two models whose directories have the same last segment in different
    parents -- ``D:\\models\\Residential_AC`` and
    ``C:\\work\\Residential_AC`` -- derive the same id, and the second would
    silently take over the first's paths. Every later call for either model
    would then read and write the other's clone. Worth a refusal naming both.
    """
    install_adapter()
    session_id = session_id_for(cfg)
    with _LOCK:
        existing = _DIRS.get(session_id)
        if existing is not None and existing != cfg.model_dir:
            raise ModelError(
                f"Two models in this profile would share the session id "
                f"{session_id!r}: {existing} and {cfg.model_dir}. The id is "
                f"the model directory's name, so two directories with the "
                f"same name in different places collide, and every call after "
                f"this one would reach the wrong clone. Give one of them a "
                f"differently named directory on the setup page."
            )
        _DIRS[session_id] = cfg.model_dir
    return session_id


def registered_dirs() -> dict[str, Path]:
    """For tests and diagnostics. Not exposed as a tool."""
    return dict(_DIRS)


# ---------------------------------------------------------------------------
# Thin delegations to the git layer
# ---------------------------------------------------------------------------


def _strip_credentials(url: str) -> str:
    return gitwork.strip_credentials(url)


def _same_repo(a: str, b: str) -> bool:
    """Whether two remotes name the same repository.

    More than a string compare, because the two sides genuinely arrive in
    different spellings: the configured URL is whatever the profile holds,
    while ``origin`` is whatever git stored -- which may carry a baked-in PAT
    (``git_service._inject_pat`` puts one there unconditionally), a ``.git``
    suffix the other lacks, a trailing slash, backslashes on Windows, or a
    bare path where the other has ``file://``.
    """
    return gitwork.same_repo(a, b)


def _git(*args: str, timeout: int = GIT_LOCAL_TIMEOUT) -> subprocess.CompletedProcess:
    """Run git, and **never wait forever**. Raises ``ModelError`` on a hang.

    The deadline, the closed stdin and the no-prompt config are the library's,
    shared with the other desktop server rather than written twice. The one
    thing added here is the exception: the library reports a timeout as return
    code 124 with the reason in ``stderr``, because it has call sites that
    already branch on ``returncode``, and this module's callers were written
    against a helper that raises.
    """
    result = gitwork.run_git(*args, timeout=timeout)
    if gitwork.timed_out(result):
        raise ModelError(result.stderr.strip())
    return result


def _is_work_tree(path: Path) -> bool:
    return gitwork.is_work_tree(path)


def _existing_origin(path: Path) -> str:
    return gitwork.existing_origin(path)


def _remote_for(cfg: ModelConfig) -> Remote:
    """This model's remote, as the git layer wants it.

    ``public_readonly`` is not passed in: the library derives it from whether
    there is a token, which makes the combination that caused the original
    defect -- no credential, and ``oauth2:@`` injected into a URL that needed
    none -- unconstructible rather than merely avoided. That is also why the
    setup page clones through ``gitwork.run_clone`` and not
    ``git_service.clone_repo``, whose ``_inject_pat`` is unconditional and so
    rewrites a ``file://`` path into a host.
    """
    return Remote(url=cfg.remote_url, branch=cfg.branch, pat=cfg.pat,
                  auth=cfg.auth)


def _remote_for_dependency(dep: Dependency, pat: str) -> Remote:
    return Remote(url=dep.remote_url, branch=dep.branch, pat=pat, auth=dep.auth)


def refresh_credential(cfg: ModelConfig) -> None:
    """Re-apply the credential the profile holds **this** run.

    The clone bakes it into ``origin`` (basic auth) or into
    ``http.extraHeader`` (the GitLab header). Either goes stale when the
    engineer rotates the token, and the symptom is a push that starts failing
    long after anything changed -- exactly the shape CLAUDE.md warns about for
    the org PAT, where an expiry reads like a revoked grant. Re-setting it on
    every open means rotating it on the setup page is all the engineer does.

    The mechanism used is the one a probe *proved* against this remote, with
    the hostname's guess only as a fallback for a model cloned before probing
    existed.
    """
    gitwork.point_origin_at(cfg.clone_dir, _remote_for(cfg),
                            user_name=COMMIT_NAME, user_email=COMMIT_EMAIL)


def fast_forward(cfg: ModelConfig) -> dict[str, Any]:
    """Fetch, then bring the clone into step, and say which happened.

    All of it is ``gitwork.sync``. Two arguments are this product's:
    ``reconcile=False`` (see ``RECONCILE`` -- the one judgement that differs
    from the notebook's) and ``DIVERGENCE_HINT``, because only this product
    knows the file that disagrees is a Capella model.
    """
    return gitwork.sync(cfg.clone_dir, _remote_for(cfg),
                        push_tool="push_model_changes",
                        reconcile=RECONCILE,
                        conflict_hint=DIVERGENCE_HINT)


# ---------------------------------------------------------------------------
# The session record capella_service reads
# ---------------------------------------------------------------------------


def find_aird(directory: Path) -> Optional[Path]:
    """The first ``.aird`` under a directory, or None.

    Sorted, so a model with more than one ``.aird`` resolves to the same file
    on every call and on every machine. ``capella_service.find_aird_file``
    takes ``rglob``'s first hit, which is filesystem order -- fine for a
    session that is cloned and discarded, and not fine for a directory that is
    opened a hundred times over a year.
    """
    if not directory.is_dir():
        return None
    return next(iter(sorted(directory.rglob("*.aird"))), None)


def write_session_record(cfg: ModelConfig, session_id: str,
                         aird_path: Path) -> dict[str, Any]:
    """Write ``session.json``, in exactly the shape the service expects.

    Field for field what the hosted ``clone_capella_repo`` writes, because
    every tool downstream reads it with ``svc.load_session`` and none of them
    should be able to tell which front end created it.

    ``oauth_connection_id`` is ``None`` and always will be: there is no
    Cartenza database here to resolve one against. It is written rather than
    omitted so that ``session.get('oauth_connection_id')`` reads as "no
    reusable authorization" rather than as a key a reader has to defend
    against.
    """
    record = {
        "session_id": session_id,
        "archive_name": cfg.name,
        "aird_path": str(aird_path),
        "resolved_uuids": [],
        "include_realized": cfg.include_realized,
        "include_realizing": cfg.include_realizing,
        "yaml_path": None,
        "resources": cfg.resources(),
        "oauth_connection_id": None,
    }
    cfg.model_dir.mkdir(parents=True, exist_ok=True)
    (cfg.model_dir / "session.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8")
    return record


# ---------------------------------------------------------------------------
# Preparing a model -- the setup page's job, never the agent's
# ---------------------------------------------------------------------------


def is_prepared(cfg: ModelConfig) -> bool:
    """Whether this model has been cloned to disk already."""
    return _is_work_tree(cfg.clone_dir)


def _probe(remote: Remote, label: str) -> Remote:
    """Find out which credential mechanism this remote accepts, once.

    Skipped when there is no credential (a public model needs none) and when a
    previous build already proved one. Asking costs one ``ls-remote`` and
    answers a question a hostname cannot: not "which platform is this" but
    "which kind of credential is this". The first real GitLab repository
    anyone set up held a token that does git over basic auth and cannot use
    the REST API at all, so the header the hostname implied was refused -- and
    a failed clone deletes its own destination, leaving a page that could only
    say "not built".
    """
    if not remote.has_credential or remote.mechanism:
        return remote
    mechanism, failures = gitwork.probe_auth(remote)
    if not mechanism:
        raise ModelError(
            gitwork.auth_refused_message(remote, failures, label=label)
            + " Replace it on the setup page to try again."
        )
    return remote.with_auth(mechanism)


def prepare_dependency(cfg: ModelConfig, dep: Dependency,
                       pat: str = "") -> dict[str, Any]:
    """Clone one library repository. **The setup page calls this.**

    Returns what the setup page needs to record: the mechanism that was
    proved, and the folder the ``.aird`` was found in -- which is the value
    ``capella_service.open_model`` wants in ``resources``.
    """
    require_git()
    target = dep.resolved_clone_dir(cfg.model_dir)

    if _is_work_tree(target):
        aird = find_aird(target)
        return {"resource_name": dep.resource_name, "first_use": False,
                "auth": dep.auth, "clone_dir": str(target),
                "model_folder": str(aird.parent) if aird else None,
                "aird_file": aird.name if aird else None}

    if target.exists() and any(target.iterdir()):
        raise ModelError(
            f"{target} already has files in it but is not a git clone. The "
            f"library {dep.resource_name!r} will not be cloned into a "
            f"directory this server does not recognise. Empty it yourself if "
            f"it is safe to, or give this library its own directory on the "
            f"setup page."
        )

    remote = _probe(_remote_for_dependency(dep, pat),
                    f"library {dep.resource_name!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    error = gitwork.run_clone(remote, target, timeout=GIT_NETWORK_TIMEOUT)
    if error:
        raise ModelError(error)
    gitwork.configure_clone(target, remote,
                            user_name=COMMIT_NAME, user_email=COMMIT_EMAIL)

    aird = find_aird(target)
    if aird is None:
        raise ModelError(
            f"The library {dep.resource_name!r} was cloned from "
            f"{_strip_credentials(dep.remote_url)} but contains no .aird "
            f"file, so it is not a Capella model and the main model's "
            f"cross-references will not resolve against it. Check the "
            f"repository URL and the branch ({dep.branch!r})."
        )
    return {"resource_name": dep.resource_name, "first_use": True,
            "auth": remote.mechanism, "clone_dir": str(target),
            "model_folder": str(aird.parent), "aird_file": aird.name}


def prepare(cfg: ModelConfig, dependency_pats: Optional[dict[str, str]] = None
            ) -> dict[str, Any]:
    """Clone the model. **The setup page calls this; the agent never does.**

    Cloning is the one genuinely slow step -- minutes on a large model over a
    slow link, and a Capella model with libraries is several of them -- so it
    belongs in a page the engineer launched and can watch, rather than inside
    a tool call that has no progress indicator and sits behind a harness
    timeout. A real session once reported an open as hanging for fifteen
    minutes when the clone took four seconds and something else had failed;
    the lesson kept is that a long operation inside a tool call is a long
    operation nobody can see.

    Idempotent: a model already on disk is simply opened.
    """
    require_git()

    if _is_work_tree(cfg.clone_dir):
        return open_model(cfg)

    if cfg.clone_dir.exists() and any(cfg.clone_dir.iterdir()):
        raise ModelError(
            f"{cfg.clone_dir} already has files in it but is not a git clone. "
            f"This server will not write into a directory it does not "
            f"recognise. Choose a different directory for {cfg.name!r} on the "
            f"setup page, or empty that one yourself if it is safe to."
        )

    remote = _probe(_remote_for(cfg), cfg.name)

    cfg.clone_dir.parent.mkdir(parents=True, exist_ok=True)
    error = gitwork.run_clone(remote, cfg.clone_dir, timeout=GIT_NETWORK_TIMEOUT)
    if error:
        raise ModelError(error)
    # Reported, not dropped. Without a commit identity git refuses to commit,
    # so an unnoticed failure here surfaces much later as "Committer identity
    # unknown" on the engineer's first patch -- a message that points nowhere
    # near the clone that caused it.
    identity_problems = gitwork.configure_clone(
        cfg.clone_dir, remote, user_name=COMMIT_NAME, user_email=COMMIT_EMAIL)

    aird_path = find_aird(cfg.clone_dir)
    if aird_path is None:
        # Deliberately NOT deleted, which is where this parts company with the
        # hosted server: there, a failed clone's temp directory is swept and
        # nothing is lost. Here the engineer may have waited ten minutes for
        # this, and the likely cause is a wrong branch rather than a wrong
        # repository -- so the clone stays and the page can offer to switch
        # branch without downloading it again.
        raise ModelError(
            f"{cfg.name!r} was cloned from {_strip_credentials(cfg.remote_url)} "
            f"but no .aird file was found in it, so there is no Capella model "
            f"to open. The clone has been kept at {cfg.clone_dir} -- check "
            f"whether branch {cfg.branch!r} is the right one, or whether the "
            f"model lives in a different repository."
        )

    dependencies = []
    for dep in cfg.dependencies:
        dependencies.append(prepare_dependency(
            cfg, dep, (dependency_pats or {}).get(dep.resource_name, "")))

    session_id = _register(cfg)
    write_session_record(cfg, session_id, aird_path)

    return {
        "session_id": session_id,
        "first_use": True,
        # So the setup page can record it on the model and every later fetch
        # uses the mechanism that was proven rather than probing again.
        "auth": remote.mechanism,
        "aird_file": aird_path.name,
        **cfg.describe(),
        **gitwork.head_summary(cfg.clone_dir),
        "synced": True,
        "sync_detail": ["cloned"],
        "identity_problems": identity_problems,
        **({"dependencies_cloned": dependencies} if dependencies else {}),
    }


# ---------------------------------------------------------------------------
# Opening a model -- what the agent calls
# ---------------------------------------------------------------------------


def open_model(cfg: ModelConfig) -> dict[str, Any]:
    """Attach to a model already on disk. **Never clones.**

    Named for the tool, not for ``capella_service.open_model``, which takes an
    ``.aird`` path and returns a ``MelodyModel``. This one returns the session
    record and the sync state; loading the model itself stays lazy, per call,
    exactly as the hosted server does it.

    Fetches and fast-forwards, so an open stays in seconds whatever the
    model's size. A model that has not been set up is *reported*, not cloned
    for: see ``prepare``.

    **Why the sync state is in the response and not just in a log.** A
    persistent clone is a permanently long-lived session, and a long-lived
    session serves the tree it was created with -- indistinguishable from a
    caching bug. That cost a real investigation once, where three "fresh"
    clones came back identical and stale against a path containing no cache at
    all. The answer is not to re-clone, which gives back everything this
    design is for, but to fetch on open and say in the response whether it
    worked. Working offline is a premise of the product, so a failed sync is
    never an error here; it is a reported fact with its reason.
    """
    require_git()

    if not _is_work_tree(cfg.clone_dir):
        raise ModelError(
            f"The model {cfg.name!r} has not been set up on this machine yet "
            f"-- there is no clone at {cfg.clone_dir}. Setting one up means "
            f"cloning a Capella model, which can take minutes, so it is done "
            f"from the setup page where it can be watched rather than from "
            f"here where it cannot. Run this server with --setup, open the "
            f"page it prints, and use Verify on {cfg.name!r}. Nothing is "
            f"wrong with this agent's configuration."
        )

    want = _strip_credentials(cfg.remote_url)
    have = _strip_credentials(_existing_origin(cfg.clone_dir))
    if have and want and not _same_repo(have, want):
        raise ModelError(
            f"{cfg.clone_dir} is a clone of {have}, but the profile says "
            f"{cfg.name!r} is {want}. Refusing to touch it -- two models "
            f"sharing one directory would overwrite each other's .aird. Give "
            f"this model its own directory on the setup page."
        )

    refresh_credential(cfg)
    sync = fast_forward(cfg)

    aird_path = find_aird(cfg.clone_dir)
    if aird_path is None:
        raise ModelError(
            f"The clone at {cfg.clone_dir} no longer contains an .aird file, "
            f"so there is no Capella model to open. If the model was moved or "
            f"renamed in the repository, this is expected and the fix is to "
            f"re-verify {cfg.name!r} on the setup page; if not, something has "
            f"removed files from the clone."
        )

    session_id = _register(cfg)
    write_session_record(cfg, session_id, aird_path)

    missing = cfg.missing_dependencies()
    return {
        "session_id": session_id,
        "first_use": False,
        "aird_file": aird_path.name,
        **cfg.describe(),
        **gitwork.head_summary(cfg.clone_dir),
        **sync,
        # Said at open, with the action, rather than discovered when
        # capellambse fails to resolve a cross-reference and reports it as a
        # problem with the main model.
        **({"dependencies_note": (
            f"{len(missing)} declared library repositor(y/ies) "
            f"({', '.join(missing)}) are not on disk, so cross-references "
            f"into them will not resolve. Open the setup page and use Verify "
            f"on {cfg.name!r} to clone them."
        )} if missing else {}),
        # The declared half of the ceiling, said at open. The measured half is
        # checked when a write is actually attempted -- see
        # ModelConfig.write_refusal, which this response is the argument to.
        **({"write_refusal": cfg.write_refusal(sync)}
           if cfg.write_refusal(sync) else {}),
    }


def get_session(session_id: str) -> dict[str, Any]:
    """The session record, or an error saying exactly what to do.

    Re-opening is free here -- the clone is already on disk and no credential
    prompt, browser trip or connect code is involved -- so an unknown id is a
    one-call inconvenience rather than the lost work it would be on a hosted
    session. The message says so, because an agent that reads "unknown
    session" and remembers the hosted rules may tell the engineer their
    session is gone and that re-cloning costs an authorization.
    """
    install_adapter()
    directory = _DIRS.get(session_id)
    if directory is None or not (directory / "session.json").is_file():
        raise ModelError(
            f"Unknown session_id {session_id!r}. This server was probably "
            f"restarted since you last called open_model. Call open_model "
            f"again -- it costs nothing and loses nothing, because the clone "
            f"is already on disk and no credential or authorization is needed."
        )
    return svc.load_session(session_id)


def forget(session_id: str) -> dict[str, Any]:
    """Drop the in-memory registration. **Never deletes the clone.**

    The hosted ``cleanup_session`` deletes its temp clone, which is correct
    there: the directory is disposable and nobody else wants it. Here the
    directory *is* the engineer's model, held deliberately so the next run
    does not re-clone it. Deleting it on cleanup would turn a tidy-up call
    into data destruction, and it would destroy uncommitted model edits.
    """
    with _LOCK:
        directory = _DIRS.pop(session_id, None)
    return {
        "status": "closed" if directory is not None else "no such session",
        "model_kept_at": str(directory) if directory is not None else None,
        "note": ("The local clone is left in place; it is your model, not a "
                 "cache."),
    }
