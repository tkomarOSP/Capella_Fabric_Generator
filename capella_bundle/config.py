"""The resolved settings for one model, and the whole write ceiling.

Read once, into a frozen object, from the profile the engineer filled in on
the setup page. Nothing here is read from the environment: the hosted server
is configured by its unit file and this one is configured by a profile, and a
third path would give two answers to "where is this model configured?".

**No value here is ever written into the repository**, the PAT especially: it
reaches git through the remote URL and ``http.extraHeader`` and nothing else,
and ``describe`` exists so that a tool response can report the model without
reporting the credential. Nor is an agent ID accepted -- there is no
registration server to check one against, and OBS-0010's addendum says plainly
that agent IDs do not belong in a bundle configuration.

### The write ceiling lives here, and it has two halves

The hosted server has no ceiling: it clones per session into a temp directory,
and the credential it was handed is the only limit on what it may do. A
permanent clone of somebody's model needs more than that, and the two halves
answer different questions.

* **Declared** -- ``access``, which the engineer set on the setup page.
  ``read_only`` is that and only that, checked at the point of a write rather
  than at push time, because a ceiling discovered at push is discovered after
  an agent has already patched a model and said so.
* **Measured** -- ``write_refusal``, which asks whether the clone is level
  with the remote. This half does not exist in the notebook and it is the
  Capella-specific one: a model is a *single file*, so patching a clone that
  is behind the remote and pushing it does not conflict, it silently reverts
  whatever landed in between.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mcp_desktop import state

#: A setting is missing or contradictory, reported with what to do.
#:
#: The foundation library's own error under this package's name. An alias
#: rather than a subclass, so that every ``except ConfigError`` here also
#: catches what the library raises -- ``StateError`` from the state root and
#: the git check, ``ProfileError`` from the profile store -- instead of letting
#: it escape as an unhandled traceback from inside a tool call.
ConfigError = state.StateError

#: A Windows drive path, a UNC path or a bare filesystem path -- not a remote.
_looks_like_local_path = state.looks_like_local_path

#: A short, stable directory name for a remote, derived from its path rather
#: than hashed, so an engineer who opens the state directory can tell which
#: model is in which folder. It is also the session id: see
#: ``session.session_id_for``.
_slug = state.slug

#: The folder name under the per-user state root.
#:
#: **Not "Cartenza".** The library takes the app name as an argument precisely
#: so that neither product decides where the other's clones live, and this
#: product is being separated from Cartenza on IP grounds
#: (``Tony_BackLog``/note-0047) -- so no shared directory, no shared profile
#: file, and no shared keychain service.
APP = "CapellaFabric"


def default_state_root() -> Path:
    """Where this server keeps its model clones and its rendered views.

    ``LOCALAPPDATA`` on Windows, ``XDG_STATE_HOME`` or ``~/.local/state``
    elsewhere -- the per-user, non-roaming location on each platform.

    Not ``Documents`` or ``Desktop``, deliberately: a corporate image
    routinely redirects those into the company tenant, and a Capella model is
    exactly the kind of thing an employer's DLP tooling will take an interest
    in. The default should not walk into that on the engineer's behalf.
    """
    # Still a function, and still called by name at each use rather than
    # captured in an object, because the tests redirect it by assigning over
    # it. A store that had captured a root would quietly write into the
    # engineer's real state directory while the test passed.
    return state.state_root(
        APP,
        remedy=("Give each model an explicit clone and views directory on the "
                "setup page."),
    )


def require_git() -> str:
    """The git executable's path, or a ConfigError naming what to install.

    Checked at start-up. Every clone, fetch and push here shells out to the
    git binary, and carrying a private one (MinGit is about 50 MB) is a
    packaging decision no bundle in this family has taken -- so this is a hard
    requirement rather than a degradation, and a hard requirement discovered
    late is discovered from a subprocess error at some depth.
    """
    return state.require_git(
        "the local Capella Fabric server",
        because=("it clones, fetches and pushes the model repository with git "
                 "directly, and there is no bundled substitute"),
    )


# ---------------------------------------------------------------------------
# Dependency library repositories
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dependency:
    """One Capella library repository this model cross-references.

    The hosted server takes these one call at a time, through
    ``add_dependency_repo`` after the session exists. Here they are
    **declared on the setup page and cloned there**, for the reason every slow
    step is: cloning a library takes as long as cloning a model, and a tool
    call has no progress indicator and sits behind a harness timeout. So this
    is a profile field rather than a tool, and ``open_model`` reports a
    dependency that has not been cloned the same way it reports a model that
    has not been set up.

    ``resource_name`` is not cosmetic and not ours to choose: it must match
    the name the main model's cross-references use, because that is the key
    ``capellambse`` resolves against. The setup page can verify it, but it
    cannot invent it.
    """

    resource_name: str
    remote_url: str
    branch: str = "main"
    clone_dir: Optional[Path] = None

    #: Which credential mechanism this remote accepts -- "basic", "header", or
    #: "" for not yet determined. Per dependency, because a model on GitHub
    #: may well reference a library on an internal GitLab.
    auth: str = ""

    def safe_name(self) -> str:
        """The directory name for this library, as the hosted server spells it.

        Deliberately the same transformation ``add_dependency_repo`` applies
        (``re.sub(r'[^\\w\\-]', '_', resource_name)``), so a model prepared
        here and a session cloned by the hosted server put a library in the
        same relative place. Reimplemented rather than imported because it is
        one expression and importing it would make this module depend on the
        hosted front end.
        """
        return "".join(
            c if (c.isalnum() or c == "_" or c == "-") else "_"
            for c in self.resource_name
        )

    def resolved_clone_dir(self, model_dir: Path) -> Path:
        """Where this library is cloned. Under the model's own directory.

        ``<model dir>/deps/<safe name>``, which is where
        ``capella_service._session_dir`` based paths already expect to find
        it. An explicit ``clone_dir`` overrides it, for a library already on
        disk that two models share.
        """
        return self.clone_dir or model_dir / "deps" / self.safe_name()

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "resource_name": self.resource_name,
            "remote_url": self.remote_url,
            "branch": self.branch,
        }
        if self.clone_dir:
            out["clone_dir"] = str(self.clone_dir)
        # Omitted while unknown rather than written as "", so an older profile
        # and a freshly probed one are not two different shapes on disk.
        if self.auth:
            out["auth"] = self.auth
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Dependency":
        resource_name = str(raw.get("resource_name") or "").strip()
        remote_url = str(raw.get("remote_url") or "").strip()
        if not resource_name or not remote_url:
            raise ConfigError(
                "A dependency library in this profile has no resource name or "
                "no repository URL. Open the setup page and complete or "
                "remove it. The resource name has to match the name the main "
                "model's cross-references use."
            )
        auth = str(raw.get("auth") or "").strip().lower()
        return cls(
            resource_name=resource_name,
            remote_url=remote_url,
            branch=str(raw.get("branch") or "main").strip() or "main",
            clone_dir=Path(raw["clone_dir"]).expanduser() if raw.get("clone_dir") else None,
            auth=auth if auth in ("basic", "header") else "",
        )


# ---------------------------------------------------------------------------
# The resolved configuration for one model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelConfig:
    """The resolved settings for one Capella model.

    The fields deliberately mirror the hosted ``UserRepo`` row the Repos page
    fills in -- nickname, git address, branch, read/write ceiling, and where
    the credential comes from -- so that an engineer who moves up to the
    hosted service is filling in a form they have already seen.
    """

    name: str
    remote_url: str
    branch: str
    pat: str
    access: str                        # "read" | "write", as UserRepo.permission

    #: The model's own directory: the session directory, in ``capella_service``
    #: terms. Holds ``session.json``, ``unpacked/`` (the clone) and ``deps/``.
    model_dir: Path

    #: Where rendered views are **copied to** before being served.
    #:
    #: A sibling of ``model_dir``, never inside it, and that is a finding
    #: rather than a preference. ``git_service.clone_repo``'s own docstring
    #: records that the credential lands in ``<dest>/.git/config`` "for the
    #: life of the session directory" -- mitigated on the hosted side by
    #: ``cleanup_session`` removing that directory. Here it is never removed,
    #: so a loopback file server pointed anywhere inside ``model_dir`` is a
    #: server that can be walked into ``.git/config``. ``to_config`` refuses
    #: the containment, and the page server checks it again.
    views_dir: Path

    #: Which credential mechanism authenticates this remote -- "basic",
    #: "header", or "" for not yet determined. Discovered by probing rather
    #: than inferred from the hostname, and recorded so every later fetch and
    #: push uses the mechanism that was actually proven to work.
    auth: str = ""

    #: Whether anyone or anything else pushes to this repository, as declared
    #: by the engineer. It does **not** lower the ceiling -- see ``read_only``
    #: -- but it is why this product never replays commits unattended, and it
    #: is reported at open so that a diverged model is not a surprise.
    shared: bool = False

    #: The realization-reference flags. Session-level on the hosted server,
    #: set as arguments to ``clone_capella_repo``; per-model here, because
    #: there is no clone call left to carry them.
    include_realized: bool = False
    include_realizing: bool = False

    dependencies: tuple[Dependency, ...] = ()

    @property
    def clone_dir(self) -> Path:
        """The git work tree: ``<model dir>/unpacked``.

        Not a stored field. ``git_service`` derives this path itself from the
        session id in four separate functions, so storing a second answer
        here would create somewhere for the two to disagree.
        """
        return self.model_dir / "unpacked"

    @property
    def has_credential(self) -> bool:
        return bool(self.pat)

    @property
    def read_only(self) -> bool:
        """Whether writing is permitted. **The declared ceiling, and only that.**

        Deliberately not "or there is no credential". A missing PAT does not
        mean a repository cannot be written to: a ``file://`` remote, an SSH
        remote and a working git credential helper all push without one.
        Folding the credential in would refuse writes to models that work
        perfectly, and would conflate what the engineer *permitted* with what
        the machine is *able* to do. Whether a push will actually succeed is
        reported separately, by ``push_risk``.

        **And deliberately not "or shared", which is where this parts company
        with the notebook.** A notebook is read-only as soon as it is marked
        shared, because a repository with a second writer diverges as a matter
        of course and a notebook's answer to divergence was to ask the
        engineer to rebase. The same rule applied here would refuse writes to
        very nearly every real model: a Capella model is the artifact a team
        collaborates on, so a single-writer model repository is the exception
        rather than the rule, and Tony's decision table includes
        ``apply_model_patch`` and ``push_model_changes`` on purpose.

        So sharing is handled where it actually bites instead. It is why this
        product passes ``reconcile=False`` to the git layer
        (``session.RECONCILE``), and it is why ``write_refusal`` requires the
        clone to be level with the remote before a patch is applied. Those two
        are a narrower and more honest answer than a blanket refusal: the
        engineer may write, and may not write *over somebody*.
        """
        return self.access != "write"

    @property
    def push_risk(self) -> Optional[str]:
        """A warning when writes are allowed but a push will probably fail.

        A warning and not a refusal: the local commits are real and safe
        either way, and plenty of remotes authenticate without a PAT.
        """
        if self.read_only or self.has_credential:
            return None
        if self.remote_url.startswith(("file://", "/")) or _looks_like_local_path(self.remote_url):
            return None
        return (
            "No access token is stored for this model. Patches will be applied "
            "and committed locally, but pushing to this remote will probably "
            "fail unless git already has a credential for it."
        )

    @property
    def shared_note(self) -> Optional[str]:
        """Said at open when the engineer declared this model shared.

        Not a warning about writing -- writing is allowed. It is a warning
        about *divergence*, said before it happens rather than reported as a
        git error afterwards, because on a shared model it will happen.
        """
        if not self.shared:
            return None
        return (
            f"{self.name!r} is marked as shared, so other people push to it. "
            f"Patches are still allowed. Two things follow from it: this "
            f"server will never replay your commits on top of theirs by "
            f"itself, because a Capella model is one file and a textual "
            f"rebase of it can succeed and still produce a model Capella "
            f"cannot open; and a patch is refused while this clone is behind "
            f"the remote, because the model is a single file and patching a "
            f"stale copy of it reverts whatever landed in between. Open the "
            f"model again to fetch, then patch."
        )

    def write_refusal(self, sync: Optional[dict[str, Any]] = None) -> Optional[str]:
        """Why a write may not happen now. ``None`` if it may.

        **The measured half of the ceiling, and the half the notebook has no
        equivalent of.** It is checked before a patch is applied rather than
        at push, for the same reason the declared half is: a refusal
        discovered at push is discovered after an agent has patched a model
        and told the engineer the change was made.

        The argument is the dict ``session.open_model`` returned. Passing
        ``None`` checks the declared half alone, which is what a caller that
        only wants to know whether this model is writable at all should do.

        Three refusals, in the order they are worth hearing:

        1. **The declared ceiling.** Read-only is read-only.
        2. **Behind the remote.** A Capella model is a single file. Git will
           not conflict on a patch applied to a stale copy of it -- it will
           take the whole file, which is what makes this different from a
           knowledge repository where one file per entry means two sides never
           touch the same one. The result is a push that reverts somebody
           else's work and reports success.
        3. **Diverged.** Commits on both sides, and ``RECONCILE`` is off here
           on purpose, so nothing has been replayed. A patch on top would
           commit onto a history that cannot fast-forward, and the push would
           be refused later with the patch already written.

        A failed *fetch* is deliberately not a refusal. Working offline is a
        premise of a desktop product, and a clone that could not reach the
        remote is not known to be stale -- it is only unverified. Refusing
        writes whenever the network is down would make the product useless on
        a train, and the warning belongs with the open, where the git layer
        already puts it (``gitwork.OFFLINE_NOTE``).
        """
        if self.read_only:
            return (
                f"{self.name!r} is open for reading only -- its access is "
                f"{self.access!r}. Nothing has been changed. To write to it, "
                f"set this model's access to read/write on the setup page. "
                f"Browsing, searching, verifying, generating a fabric and "
                f"rendering a view all work unchanged."
            )
        if not sync:
            return None

        behind = int(sync.get("behind") or 0)
        if sync.get("diverged"):
            return (
                f"{self.name!r} has local commits and so does the remote, and "
                f"the two have not been reconciled. Nothing has been changed. "
                f"A Capella model is one file, so this server does not replay "
                f"commits across a divergence by itself -- a textual rebase of "
                f"model XML can succeed and still leave a model Capella cannot "
                f"open. This one needs a person: open "
                f"{self.clone_dir} in a Capella-aware tool, or reset to the "
                f"remote if the local commits can be discarded. "
                f"{sync.get('sync_note') or ''}".strip()
            )
        if behind:
            return (
                f"{self.name!r} is {behind} commit(s) behind its remote and "
                f"was not fast-forwarded, so this clone is stale. Nothing has "
                f"been changed. A Capella model is a single file: patching a "
                f"stale copy and pushing it does not conflict, it replaces "
                f"the newer file wholesale and reverts whatever landed in "
                f"between. Call open_model again to fetch, then patch. "
                f"{sync.get('sync_error') or sync.get('sync_note') or ''}".strip()
            )
        return None

    def resources(self) -> dict[str, dict[str, str]]:
        """The ``resources`` mapping ``capella_service.open_model`` wants.

        ``{resource_name: {"path": <folder holding that library's .aird>}}``,
        which is exactly the shape ``add_dependency_repo`` builds on the
        hosted side -- so the model opens identically here, and a fabric
        generated from either resolves the same cross-references.

        A dependency with no ``.aird`` on disk yet is **left out rather than
        pointed at an empty directory**: ``capellambse`` given a resource path
        with no model in it fails in a way that reads as a problem with the
        main model, and the missing clone is reported by ``open_model``
        instead, where it can say which library and what to do.
        """
        found: dict[str, dict[str, str]] = {}
        for dep in self.dependencies:
            directory = dep.resolved_clone_dir(self.model_dir)
            aird = next(iter(sorted(directory.rglob("*.aird"))), None) \
                if directory.is_dir() else None
            if aird is not None:
                found[dep.resource_name] = {"path": str(aird.parent)}
        return found

    def missing_dependencies(self) -> list[str]:
        """Declared libraries with no ``.aird`` on disk, by resource name."""
        present = self.resources()
        return [d.resource_name for d in self.dependencies
                if d.resource_name not in present]

    def describe(self) -> dict[str, object]:
        """What the server may report about itself. **Never the PAT.**"""
        missing = self.missing_dependencies()
        return {
            "model": self.name,
            "repo": self.remote_url,
            "branch": self.branch,
            "access": "read-only" if self.read_only else "read/write",
            "model_dir": str(self.model_dir),
            "views_dir": str(self.views_dir),
            "credential": "supplied" if self.has_credential else "none (public repo or offline)",
            "include_realized": self.include_realized,
            "include_realizing": self.include_realizing,
            **({"dependencies": [d.resource_name for d in self.dependencies]}
               if self.dependencies else {}),
            **({"dependencies_not_cloned": missing} if missing else {}),
            **({"shared": True} if self.shared else {}),
            **({"push_warning": self.push_risk} if self.push_risk else {}),
            **({"shared_warning": self.shared_note} if self.shared_note else {}),
        }
