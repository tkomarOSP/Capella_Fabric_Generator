"""What the engineer configured once, instead of the agent being told it.

The hosted server is handed a repository URL and a credential per call, or a
connect code redeemed against the Cartenza database. Neither exists on a
desktop: there is no database, no website, and nothing should be putting a
token into a model's context. So this server is pointed at a **profile** --
one agent's identity and the models it may reach -- written by a local setup
page the engineer launched.

Two stores, deliberately separate:

* **The profile file** -- models, dependency libraries and identity. Plain
  JSON the engineer can read, diff and back up. **It never holds a
  credential**, and a test asserts that rather than trusting this sentence.
* **The credential store** -- one secret per model, in the operating system's
  own keychain, under this product's **own** service name. Protected by the
  engineer's login rather than by a key sitting beside the file it protects.

### What is this product's, and what is the library's

Everything generic is ``mcp_desktop.profiles``: the credential store protocol,
the keychain and file backends, ``Identity``, the generic profile and every
read and write of a profile file. What stays here is what a *model* is:

1. **``ModelEntry``** -- the typed entry. One product's entry is a notebook
   with a pages directory and a shared flag; this one is a Capella model with
   realization flags and a list of library repositories. Flattening them into
   one shape would give each product the other's fields.
2. **The words.** ``VOCABULARY``. The messages a missing or ambiguous profile
   raises are read by a model deciding what to do next, so "there is no
   profile" versus "call ``use_profile()`` with no argument, ask the engineer,
   then call it with their answer" is the difference between a resolved
   exchange and a blocked tool.
3. **The ceiling**, in ``config.ModelConfig``. The library holds no concept of
   read-only, because one product's ceiling is a declaration about
   repositories and this one's is also a fact about a single file.

### Its own namespace, on purpose

``APP`` is ``CapellaFabric`` and ``SERVICE`` is ``capella-fabric-local``:
a separate state directory, a separate profiles directory, a separate keychain
service. Tony's decision, and it reinforces the IP separation this whole piece
of work serves -- no shared profile file, no shared credential service, no
Cartenza naming on disk. The capability to read the other product's tokens is
not something a shared library should hand out for free, which is why the
library refuses to default a service name at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from mcp_desktop import profiles, state
from mcp_desktop.profiles import (CredentialStore, CredentialUnavailable,
                                  Identity, Vocabulary, credential_account)

from capella_bundle.config import (
    APP,
    ConfigError,
    Dependency,
    ModelConfig,
    _slug,
    default_state_root,
)

#: This product's keychain namespace. Never the notebook's.
SERVICE = "capella-fabric-local"

#: Profile file format. Bumped only for a change old readers cannot survive.
FORMAT = profiles.FORMAT

#: A profile is missing, malformed, or contradicts itself.
#:
#: The library's ``ProfileError`` under this package's name, and an alias
#: rather than a subclass for the same reason ``ConfigError`` is one: every
#: ``except SettingsError`` here also catches what the library's profile store
#: raises. It remains a subclass of ``ConfigError``, because ``ProfileError``
#: subclasses ``StateError``.
SettingsError = profiles.ProfileError

#: This product's own words, for the messages an agent will act on.
#:
#: The tool names are the local surface's, not the hosted one's. An agent told
#: to call ``clone_capella_repo`` on this server would be told to do the one
#: thing it must never do: the agent never clones here, because cloning a
#: Capella model takes minutes and belongs on a page the engineer can watch.
VOCABULARY = Vocabulary(
    noun="model",
    plural="models",
    choose_profile=(
        "Call use_profile() with no argument to see what each one holds, ask "
        "the engineer which of them they want to work in, then call it with "
        "their answer. Choosing needs no restart and no change to how this "
        "server was launched. Do not guess, do not choose for them, and do "
        "not pick one because it is the only one."
    ),
    nothing_set_up=(
        "Call setup_model() to get a link for the engineer to open; they name "
        "this agent and add their model repositories there, and nothing has "
        "to be configured before that. (Outside an agent: run the server with "
        "--setup.)"
    ),
    choose_entry="Call list_my_scopes() to see what each one is.",
)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------
#
# All three backends are the library's. Two keep a thin subclass, and only to
# supply a default the library deliberately refuses to have: it will not
# default a keychain service name, because two products sharing one would let
# either read the other's tokens.


class KeyringStore(profiles.KeyringStore):
    """The operating system's own keychain, under this product's service."""

    def __init__(self, service: str = SERVICE) -> None:
        super().__init__(service)


class FileStore(profiles.FileStore):
    """The fallback, named honestly: a protected file, **not encryption.**

    Used where the OS keychain cannot be reached -- a service account, a
    container, headless Linux with no Secret Service. Deliberately not called
    encrypted: encrypting with a key kept beside the file is obfuscation, and
    the label would tell the engineer they are safer than they are. The setup
    page says exactly that before anyone chooses it.

    The subclass exists for the default directory, which has to be resolved
    when the store is built rather than when the module is imported, because
    ``default_state_root`` is redirected by the tests.
    """

    def __init__(self, directory: Optional[Path] = None) -> None:
        super().__init__(Path(directory) if directory
                         else default_state_root() / "credentials")


def credential_store(prefer_file: bool = False) -> CredentialStore:
    """The keychain if it is there, otherwise the file store.

    Never silently downgrades: a caller that wants to know which one it got
    reads ``.label``, and the setup page shows it.
    """
    if not prefer_file and KeyringStore.available():
        return KeyringStore()
    return FileStore()


def dependency_account(model_nickname: str, resource_name: str) -> str:
    """The credential account for one library repository of one model.

    ``<model>/<resource>`` as the nickname half, so a library lives under the
    model that declares it. A library shared by two models is stored twice,
    which is the right trade: deduplicating would mean one model's credential
    change silently altering another model's access.
    """
    return f"{model_nickname}/{resource_name}"


# ---------------------------------------------------------------------------
# The typed entry -- this product's, and not the library's
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelEntry:
    """One Capella model repository this agent may reach.

    The first four fields are the hosted ``UserRepo`` row's fields under the
    same names -- nickname, git address, branch, read/write ceiling -- because
    an engineer who moves up to the hosted service should be filling in a form
    they have already seen, and an imported model should line up field for
    field instead of needing a mapping nobody wrote down.

    The rest are this product's, and each one is here because there is no
    longer a call to carry it. The hosted ``clone_capella_repo`` takes
    ``include_realized`` and ``include_realizing`` as arguments and
    ``add_dependency_repo`` adds libraries one at a time; the local server has
    no clone call at all, so both become configuration.
    """

    nickname: str
    remote_url: str
    branch: str = "main"
    access: str = "write"

    #: The model's directory -- the session directory, in ``capella_service``
    #: terms. Blank for the default, ``<state root>/models/<slug>``.
    model_dir: Optional[Path] = None

    #: Where rendered views are copied to before being served. Blank for the
    #: default, ``<state root>/views/<slug>`` -- a sibling of the model
    #: directory and never inside it. See ``ModelConfig.views_dir``.
    views_dir: Optional[Path] = None

    #: Which credential mechanism authenticates this remote: "basic",
    #: "header", or "" for not yet determined. Written by the setup page after
    #: a probe proved it, and cleared when the token is replaced, because a
    #: different kind of credential may want a different mechanism.
    auth: str = ""

    #: Whether anyone or anything else pushes to this repository.
    #:
    #: Declared by the engineer, because no code can know it: a URL does not
    #: say who else holds a credential for it, and a clone that is in sync now
    #: says nothing about the next five minutes.
    #:
    #: Unlike the notebook's flag of the same name, this one does **not** make
    #: the model read-only -- a shared model repository is the normal case for
    #: a Capella team, and refusing writes to it would remove the write path
    #: on purpose. See ``ModelConfig.read_only`` for what it does instead.
    shared: bool = False

    include_realized: bool = False
    include_realizing: bool = False

    dependencies: tuple[Dependency, ...] = ()

    def resolved_model_dir(self) -> Path:
        return self.model_dir or default_state_root() / "models" / _slug(self.remote_url)

    def resolved_views_dir(self) -> Path:
        return self.views_dir or default_state_root() / "views" / _slug(self.remote_url)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "nickname": self.nickname,
            "remote_url": self.remote_url,
            "branch": self.branch,
            "access": self.access,
        }
        if self.model_dir:
            out["model_dir"] = str(self.model_dir)
        if self.views_dir:
            out["views_dir"] = str(self.views_dir)
        # Omitted while unknown rather than written as "", so an older profile
        # and a freshly probed one are not two different shapes on disk. Same
        # convention for the booleans: absent means false, which is what every
        # profile written before a given flag existed meant.
        if self.auth:
            out["auth"] = self.auth
        if self.shared:
            out["shared"] = True
        if self.include_realized:
            out["include_realized"] = True
        if self.include_realizing:
            out["include_realizing"] = True
        if self.dependencies:
            out["dependencies"] = [d.to_json() for d in self.dependencies]
        return out

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "ModelEntry":
        nickname = str(raw.get("nickname") or "").strip()
        remote_url = str(raw.get("remote_url") or "").strip()
        if not nickname or not remote_url:
            raise SettingsError(
                "A model in this profile has no nickname or no repository "
                "URL. Open the setup page and complete or remove it."
            )
        access = str(raw.get("access") or "write").strip().lower()
        if access not in ("read", "write"):
            raise SettingsError(
                f"Model {nickname!r} has access {access!r}; it must be "
                f"\"read\" or \"write\"."
            )
        auth = str(raw.get("auth") or "").strip().lower()
        declared = raw.get("dependencies") or []
        if not isinstance(declared, list):
            raise SettingsError(
                f"Model {nickname!r} has a \"dependencies\" value that is not "
                f"a list. Open the setup page and re-add its library "
                f"repositories."
            )
        return cls(
            nickname=nickname,
            remote_url=remote_url,
            branch=str(raw.get("branch") or "main").strip() or "main",
            access=access,
            model_dir=Path(raw["model_dir"]).expanduser() if raw.get("model_dir") else None,
            views_dir=Path(raw["views_dir"]).expanduser() if raw.get("views_dir") else None,
            # An unrecognised mechanism is dropped rather than rejected: it
            # only means the next build probes again, which is cheap and
            # correct, whereas refusing to load the profile over it would lock
            # the engineer out of the page that could fix it.
            auth=auth if auth in ("basic", "header") else "",
            shared=bool(raw.get("shared")),
            include_realized=bool(raw.get("include_realized")),
            include_realizing=bool(raw.get("include_realizing")),
            dependencies=tuple(Dependency.from_json(d) for d in declared),
        )


# ---------------------------------------------------------------------------
# The profile
# ---------------------------------------------------------------------------


class Profile(profiles.Profile):
    """One agent's identity and the models it may reach.

    The library's ``Profile`` under this product's word for its entries. The
    library calls them ``entries`` because a notebook is not a model; here
    they are ``models``, in the constructor, on the instance and on disk.

    A subclass rather than a copy: ``find``, ``upsert``, ``without`` and the
    duplicate-nickname check are the library's one implementation of each.
    ``dataclasses.replace`` keeps working through this because it constructs
    ``type(obj)(**fields)``, and ``entries`` is accepted here for exactly that
    reason -- which is what the notebook's equivalent found out the hard way
    and is worth not rediscovering.
    """

    def __init__(self, name: str,
                 identity: Optional[Identity] = None,
                 models: tuple[Any, ...] = (),
                 entries: Optional[tuple[Any, ...]] = None) -> None:
        super().__init__(
            name=name,
            identity=identity if identity is not None else Identity(
                agent_name="", user_name="", handle=""),
            entries=tuple(entries if entries is not None else models),
        )

    @property
    def models(self) -> tuple[Any, ...]:
        return self.entries

    def find(self, nickname: str = "", *,
             vocabulary: Vocabulary = VOCABULARY) -> ModelEntry:
        """The model by nickname, or an error that lists the real ones."""
        return super().find(nickname, vocabulary=vocabulary)

    def to_json(self) -> dict[str, Any]:
        """The profile as written to disk, with ``models`` as the key.

        Overridden rather than left to the library, which writes ``entries``.
        Unlike the notebook, no profile with this key exists yet anywhere, so
        this is a choice rather than a compatibility obligation -- and the
        choice is for a file an engineer can read. ``"models"`` is what the
        setup page calls them and what the error messages call them; a file
        that said ``"entries"`` would be the one place using the library's
        word for them.
        """
        return {
            "format": FORMAT,
            "name": self.name,
            "identity": self.identity.to_json(),
            "models": [m.to_json() for m in self.entries],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any], *, name: str) -> "Profile":
        """Kept because the setup page and the tests call it on the class."""
        return _store().from_json(raw, name=name)


# ---------------------------------------------------------------------------
# Reading and writing profiles
# ---------------------------------------------------------------------------


def profiles_dir() -> Path:
    return default_state_root() / "profiles"


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{_slug(name)}.json"


class _Store(profiles.ProfileStore):
    """The library's profile store, pointed at this product's locations.

    Every location is overridden to call the module-level function rather than
    to derive it from a root captured at construction. That is not indirection
    for its own sake: the tests redirect ``default_state_root``,
    ``profiles_dir`` and ``credential_store`` by assigning over them, and a
    store that had captured a root would quietly write into the engineer's
    real profile directory while the test passed. That exact failure is
    recorded against the notebook's equivalent.
    """

    @property
    def root(self) -> Path:
        return default_state_root()

    def dir(self) -> Path:
        return profiles_dir()

    def path(self, name: str) -> Path:
        return profile_path(name)

    def credentials(self, prefer_file: bool = False) -> CredentialStore:
        return credential_store(prefer_file)

    def from_json(self, raw: dict[str, Any], *, name: str) -> Profile:
        """The library's reader, returning this product's Profile subclass."""
        generic = super().from_json(raw, name=name)
        return Profile(name=generic.name, identity=generic.identity,
                       entries=generic.entries)


def _store() -> _Store:
    """The profile store. Built per call, because its locations are redirected."""
    return _Store(
        app=APP,
        service=SERVICE,
        entry_from_json=ModelEntry.from_json,
        vocabulary=VOCABULARY,
        entries_key="models",
    )


def list_profiles() -> list[str]:
    return _store().list()


def load_profile(name: str = "") -> Profile:
    """The named profile, or the only one, with a message if neither holds.

    **No auto-selection, not even when there is exactly one.** Picking the
    only profile looks harmless and is the same leak as a "default" fallback,
    just narrower: any agent that starts gets whatever is configured, without
    the engineer choosing. A profile here is write access to somebody's
    Capella model, which is the version of that leak with the most to lose.
    """
    return _store().load(name)


def save_profile(profile: Profile) -> Path:
    """Write the profile. **Refuses to write a credential into it.**

    The refusal is the library's, and it inspects the serialised text rather
    than a field list, so it catches a token written under a name nothing has
    heard of.
    """
    return _store().save(profile)


def rename_profile(old: str, new: str,
                   store: Optional[CredentialStore] = None) -> Profile:
    """Move a profile, credentials and all, and return it under its new name.

    Credentials move with it. A rename that left the secrets filed under the
    old account would silently break every model it touched, and the engineer
    would find out at the next push.
    """
    return _store().rename(old, new, store or credential_store())


def delete_profile(name: str, store: Optional[CredentialStore] = None) -> None:
    """Remove a profile and every credential it owns.

    Both, together. A profile deleted on its own leaves secrets in the
    keychain under names nothing refers to any more, which is how a credential
    outlives the thing that justified it.
    """
    _store().delete(name, store or credential_store())


def secret_for(profile: Profile, nickname: str,
               store: Optional[CredentialStore] = None) -> str:
    """One model's credential, or ``""`` when there is none."""
    return _store().secret_for(profile, nickname, store or credential_store())


def dependency_secret(profile: Profile, nickname: str, resource_name: str,
                      store: Optional[CredentialStore] = None) -> str:
    """One library repository's credential, falling back to the model's own.

    **The fallback is deliberate, and it is not credential probing.** A model
    and its libraries are nearly always in one organisation behind one token
    -- CLAUDE.md's own list of PATs is a list of exactly that -- so requiring
    the engineer to paste the same token once per library would be busywork
    that teaches them to paste tokens. One documented fallback, in one
    direction, tried once: the library's own stored secret if there is one,
    otherwise the model's. Nothing iterates over candidates looking for one
    that works.
    """
    store = store or credential_store()
    own = store.get(credential_account(
        profile.name, dependency_account(nickname, resource_name)))
    return own or store.get(credential_account(profile.name, nickname))


# ---------------------------------------------------------------------------
# Turning a profile entry into the config the server understands
# ---------------------------------------------------------------------------


def to_config(profile: Profile, nickname: str = "",
              store: Optional[CredentialStore] = None) -> ModelConfig:
    """The ``ModelConfig`` for one model, with its credential attached."""
    entry = profile.find(nickname)
    pat = secret_for(profile, entry.nickname, store)

    model_dir = entry.resolved_model_dir()
    views_dir = entry.resolved_views_dir()

    # The one refusal this function makes, and it is the step-0 finding rather
    # than tidiness. The credential lands in <model dir>/unpacked/.git/config
    # at clone and stays there for as long as the directory does -- and here
    # the directory is never deleted. A views directory inside the model
    # directory means a loopback file server rooted at a path from which
    # `.git/config` is reachable, and path containment is the only thing
    # standing between a URL with enough `..` in it and a token. Refusing the
    # layout outright is cheaper than trusting the check, and the page server
    # checks it again anyway.
    if state.inside(model_dir, views_dir):
        raise SettingsError(
            f"Model {entry.nickname!r} has its views directory ({views_dir}) "
            f"inside its model directory ({model_dir}). That is refused: the "
            f"model directory holds the git clone, the clone's .git/config "
            f"holds this model's access token, and rendered views are served "
            f"over a local HTTP port. Choose a directory outside the model "
            f"directory on the setup page, or leave it blank for the default."
        )

    return ModelConfig(
        name=entry.nickname,
        remote_url=entry.remote_url,
        branch=entry.branch,
        pat=pat,
        access=entry.access,
        model_dir=model_dir,
        views_dir=views_dir,
        auth=entry.auth,
        shared=entry.shared,
        include_realized=entry.include_realized,
        include_realizing=entry.include_realizing,
        dependencies=entry.dependencies,
    )


def upsert_model(profile: Profile, entry: ModelEntry) -> Profile:
    """Add or replace one model, matched on nickname, order preserved.

    Refuses a duplicate ``resource_name`` among the model's libraries. Here
    rather than only in the page handler, so a second caller cannot save what
    the page declines -- and the duplicate matters because ``resources`` is a
    mapping: the second declaration would silently win and the first library
    would be absent with nothing saying so.
    """
    seen: dict[str, int] = {}
    for dep in entry.dependencies:
        key = dep.resource_name.strip().lower()
        seen[key] = seen.get(key, 0) + 1
    repeated = sorted(k for k, n in seen.items() if n > 1)
    if repeated:
        raise SettingsError(
            f"Model {entry.nickname!r} declares the library resource name(s) "
            f"{', '.join(repr(r) for r in repeated)} more than once. A "
            f"resource name is the key the model's cross-references resolve "
            f"against, so only one of each can be used -- the others would be "
            f"cloned and then ignored. Remove the duplicates on the setup page."
        )
    return profile.upsert(entry)


def remove_model(profile: Profile, nickname: str,
                 store: Optional[CredentialStore] = None) -> Profile:
    """Drop a model, its credential and its libraries' credentials together.

    The libraries matter: the library's ``remove_entry`` deletes the entry's
    own account and knows nothing about the per-dependency accounts this
    product files beside it, so without this they would outlive the model that
    justified them.
    """
    store = store or credential_store()
    try:
        entry = profile.find(nickname)
    except SettingsError:
        entry = None
    if entry is not None:
        for dep in entry.dependencies:
            store.delete(credential_account(
                profile.name,
                dependency_account(entry.nickname, dep.resource_name)))
    return _store().remove_entry(profile, nickname, store)
