"""The local bundle's profile, configuration and write ceiling.

Step 4 of the stdio plan. Nothing here touches the network or the engineer's
real keychain: the state root is redirected into a temp directory and the
credential store is forced to the file backend, because a test that wrote into
``%LOCALAPPDATA%\\CapellaFabric`` or into the OS keychain would be a test that
edits the machine it runs on.

What is pinned, and why each one:

1. **Its own namespace.** Separate state directory, separate profiles
   directory, separate keychain service from the notebook's. That is Tony's
   decision and it is the IP separation in practice -- no shared profile file,
   no shared credential service, no Cartenza naming on disk. A shared service
   name would mean either product could read the other's tokens.
2. **The entry round-trips**, including the two realization flags and the
   library repositories, with "absent means false" preserved -- so a profile
   written before a flag existed and one written now are not two shapes.
3. **A profile file never holds a credential**, proved by putting a
   token-shaped string in the store and reading the file back, not by trusting
   the writer.
4. **The views directory is never inside the model directory.** The credential
   lands in ``<model dir>/unpacked/.git/config`` at clone and stays there,
   because this directory is never deleted -- so a loopback server rooted
   anywhere inside it is a server that can be walked into a token.
5. **The write ceiling, both halves**, including the two cases that must
   *not* refuse: a model that is level with its remote, and a clone that
   could not reach the remote at all. A product that refused writes whenever
   the network was down would be useless on a train.
6. **Dependencies resolve to the folder holding the ``.aird``**, which is the
   shape ``capella_service.open_model`` wants, and a declared library with
   nothing on disk is left out rather than pointed at an empty directory.
"""

import json
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, r"c:\apps\Git_VSCode\Capella_Fabric_Generator")

from capella_bundle import config, settings
from capella_bundle.config import Dependency, ModelConfig

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def raises(label: str, fn, *, saying: str = "") -> None:
    try:
        fn()
    except Exception as exc:
        if saying and saying.lower() not in str(exc).lower():
            check(label, False, f"raised but did not say {saying!r}: {exc}")
        else:
            check(label, True)
        return
    check(label, False, "did not raise")


ROOT = Path(tempfile.mkdtemp(prefix="bundle_profile_"))

# Redirect the state root. Assigned on ``settings`` rather than on ``config``
# because ``settings`` imported the name, so the binding a profile actually
# consults is this one -- and ``_Store`` overrides the library's location
# methods to call it by name at call time for exactly this reason.
settings.default_state_root = lambda: ROOT
config.default_state_root = lambda: ROOT


class Recording(settings.FileStore):
    """The file backend, forced, and recording which accounts were asked for.

    Forced because the real ``credential_store()`` prefers the OS keychain,
    and a test must not write the engineer's keychain. Recording because one
    assertion below is about *how many* accounts are consulted, not just the
    answer: a credential fallback that iterated over candidates looking for
    one that works would be credential probing, which CLAUDE.md blocks.
    """

    def __init__(self) -> None:
        super().__init__(ROOT / "credentials")
        self.asked: list[str] = []

    def get(self, account: str) -> str:
        self.asked.append(account)
        return super().get(account)


STORE = Recording()
settings.credential_store = lambda prefer_file=False: STORE


# ---------------------------------------------------------------------------
print("1. Its own namespace, separate from the notebook's")
# ---------------------------------------------------------------------------

check("the app folder is not Cartenza's", config.APP == "CapellaFabric", config.APP)
check("the keychain service is its own",
      settings.SERVICE == "capella-fabric-local", settings.SERVICE)
check("...and is not the notebook's",
      settings.SERVICE != "cartenza-engineering-notebook")
check("the real state root is named after this product",
      config.state.state_root(config.APP).name == "CapellaFabric",
      str(config.state.state_root(config.APP)))
check("profiles live under the redirected root",
      settings.profiles_dir() == ROOT / "profiles", str(settings.profiles_dir()))
check("the file credential store defaults under it too",
      settings.FileStore()._path("x").parent == ROOT / "credentials",
      str(settings.FileStore()._path("x")))

# No Cartenza anywhere in this package's own source. The notebook's foundation
# module reads CARTENZA_MCP_DESKTOP; this one must not, because either product
# may be installed without the other and a Capella bundle that read a
# Cartenza-named variable would carry the other product's vocabulary in its
# documented interface.
#
# Deliberately about what the code *reads*, not about what the prose mentions:
# ``foundation.py``'s docstring names CARTENZA_MCP_DESKTOP precisely to say it
# is not read here, and a check that failed on the explanation would push the
# next person to delete the explanation. So: environment access and the
# documented constant.
pkg = Path(r"c:\apps\Git_VSCode\Capella_Fabric_Generator\capella_bundle")
for source in sorted(pkg.glob("*.py")):
    text = source.read_text(encoding="utf-8")
    offending = [line.strip() for line in text.splitlines()
                 if "CARTENZA_" in line
                 and ("os.environ" in line or "getenv" in line
                      or line.lstrip().startswith(("HOME_ENV", "ENV")))]
    check(f"{source.name} reads no CARTENZA_ environment variable",
          not offending, "; ".join(offending[:2]))


# ---------------------------------------------------------------------------
print("\n2. ModelEntry round-trips, with absent meaning false")
# ---------------------------------------------------------------------------

plain = settings.ModelEntry(nickname="ac", remote_url="https://example.com/a/b.git")
raw = plain.to_json()
check("a default entry writes no shared key", "shared" not in raw, str(raw))
check("...no realization keys", "include_realized" not in raw
      and "include_realizing" not in raw, str(raw))
check("...no auth key while unknown", "auth" not in raw, str(raw))
check("...and no dependencies key", "dependencies" not in raw, str(raw))
check("it reads back identical", settings.ModelEntry.from_json(raw) == plain)

full = settings.ModelEntry(
    nickname="brakes", remote_url="https://example.com/a/c.git", branch="dev",
    access="read", auth="header", shared=True,
    include_realized=True, include_realizing=True,
    model_dir=Path(r"D:\m\ac"), views_dir=Path(r"D:\v\ac"),
    dependencies=(Dependency(resource_name="Brake Library",
                             remote_url="https://example.com/a/lib.git",
                             branch="main", auth="basic"),))
check("a full entry round-trips",
      settings.ModelEntry.from_json(full.to_json()) == full,
      str(settings.ModelEntry.from_json(full.to_json())))
check("the library's directory name matches the hosted spelling",
      full.dependencies[0].safe_name() == "Brake_Library",
      full.dependencies[0].safe_name())

raises("an entry with no nickname is refused",
       lambda: settings.ModelEntry.from_json({"remote_url": "x"}),
       saying="nickname")
raises("an entry with a nonsense access is refused",
       lambda: settings.ModelEntry.from_json(
           {"nickname": "a", "remote_url": "x", "access": "maybe"}),
       saying="read")
raises("dependencies that are not a list are refused",
       lambda: settings.ModelEntry.from_json(
           {"nickname": "a", "remote_url": "x", "dependencies": {"a": 1}}),
       saying="list")
raises("a library with no resource name is refused",
       lambda: Dependency.from_json({"remote_url": "x"}),
       saying="resource name")
check("an unrecognised auth mechanism is dropped, not refused",
      settings.ModelEntry.from_json(
          {"nickname": "a", "remote_url": "x", "auth": "magic"}).auth == "")


# ---------------------------------------------------------------------------
print("\n3. The profile keeps this product's word, and no credential")
# ---------------------------------------------------------------------------

profile = settings.Profile(name="agent-one", models=(plain,))
check("the entries are reachable as .models", profile.models == (plain,))
check("...and as the library's .entries", profile.entries == (plain,))
check("on disk the key is 'models'", "models" in profile.to_json(), str(profile.to_json()))
check("...and not the library's 'entries'", "entries" not in profile.to_json())

# dataclasses.replace constructs type(obj)(**fields), so a subclass that did
# not accept ``entries=`` would come apart here -- and ``upsert`` is built on
# replace, so it would come apart at the first model added.
grown = profile.upsert(full)
check("upsert returns this product's Profile, not the library's",
      type(grown) is settings.Profile, type(grown).__name__)
check("...with both models", len(grown.models) == 2, str(len(grown.models)))
check("replace round-trips through the subclass",
      type(replace(profile, name="other")) is settings.Profile)
# Matched on nickname, case-insensitively, so "AC" replaces "ac" rather than
# adding a second model with a near-identical name -- which is what the setup
# page's "save" depends on, and the only thing stopping two entries fighting
# over one clone directory.
restated = grown.upsert(replace(plain, nickname="AC", branch="dev"))
check("upsert matches on nickname case-insensitively",
      len(restated.models) == 2, str([m.nickname for m in restated.models]))
check("...replacing in place, order preserved",
      [m.nickname for m in restated.models] == ["AC", "brakes"],
      str([m.nickname for m in restated.models]))
check("...with the new values", restated.models[0].branch == "dev")

STORE.set(settings.credential_account("agent-one", "ac"),
          "ghp_EXAMPLETOKENVALUE0000000000000000")
path = settings.save_profile(grown)
text = path.read_text(encoding="utf-8")
check("the profile file was written", path.is_file(), str(path))
check("**it contains no credential**", "ghp_EXAMPLETOKENVALUE0000000000000000" not in text)
for field in ("pat", "token", "password", "secret"):
    check(f"...and no {field!r} field", f'"{field}"' not in text)
reloaded = settings.load_profile("agent-one")
check("it reads back as this product's Profile", type(reloaded) is settings.Profile)
check("...with the models intact", len(reloaded.models) == 2, str(len(reloaded.models)))
check("...as ModelEntry, not a dict",
      isinstance(reloaded.models[0], settings.ModelEntry),
      type(reloaded.models[0]).__name__)

raises("a profile that was never written is not invented",
       lambda: settings.load_profile("no-such-agent"))

# The library refuses to guess even when there is exactly one, and that is the
# behaviour, not an accident: picking the only profile is the same leak as a
# "default" fallback, just narrower. Proved by asking with no argument in both
# situations, because the one-profile case is the one that looks harmless.
check("one profile exists at this point", settings.list_profiles() == ["agent-one"],
      str(settings.list_profiles()))
raises("**with exactly one profile, no argument is still refused**",
       lambda: settings.load_profile(), saying="use_profile")
settings.save_profile(settings.Profile(name="agent-two"))
check("now there are two", settings.list_profiles() == ["agent-one", "agent-two"],
      str(settings.list_profiles()))
raises("...and with two, likewise", lambda: settings.load_profile(),
       saying="use_profile")
check("but naming one works", settings.load_profile("agent-two").name == "agent-two")


# ---------------------------------------------------------------------------
print("\n4. Views are never served from inside the model directory")
# ---------------------------------------------------------------------------

check("the defaults are siblings, not nested",
      not config.state.inside(plain.resolved_model_dir(), plain.resolved_views_dir()),
      f"{plain.resolved_model_dir()} / {plain.resolved_views_dir()}")

bad = settings.ModelEntry(nickname="nested", remote_url="https://e.com/a/b.git",
                          model_dir=ROOT / "m", views_dir=ROOT / "m" / "views")
nested = settings.Profile(name="agent-one", models=(bad,))
raises("a views directory inside the model directory is refused",
       lambda: settings.to_config(nested, "nested"), saying=".git/config")
# The same layout spelled with .. must be refused too, since that is how it
# would actually arrive -- state.inside resolves before comparing.
sneaky = settings.ModelEntry(nickname="sneaky", remote_url="https://e.com/a/b.git",
                             model_dir=ROOT / "m",
                             views_dir=ROOT / "m" / "x" / ".." / "v")
raises("...including by way of ..",
       lambda: settings.to_config(settings.Profile(name="agent-one",
                                                   models=(sneaky,)), "sneaky"),
       saying="views directory")


# ---------------------------------------------------------------------------
print("\n5. The write ceiling, both halves")
# ---------------------------------------------------------------------------


def cfg(access="write", **kw) -> ModelConfig:
    return ModelConfig(name="ac", remote_url="https://example.com/a/b.git",
                       branch="main", pat=kw.pop("pat", ""), access=access,
                       model_dir=ROOT / "mm", views_dir=ROOT / "vv", **kw)


check("read-only is read-only", cfg("read").read_only is True)
check("read/write is not", cfg("write").read_only is False)
check("**a shared model is still writable**, unlike a shared notebook",
      cfg("write", shared=True).read_only is False)
check("...and says why that is safe here", "never replay" in
      (cfg("write", shared=True).shared_note or ""),
      str(cfg("write", shared=True).shared_note))
check("an unshared model says nothing about sharing",
      cfg("write").shared_note is None)

refusal = cfg("read").write_refusal()
check("the declared half refuses with no sync state at all", bool(refusal))
check("...and names the way out", "setup page" in (refusal or ""), refusal or "")
check("...and says nothing was changed", "Nothing has been changed" in (refusal or ""))
check("read-only still allows everything else", "Browsing" in (refusal or ""))

check("a level clone is not refused",
      cfg("write").write_refusal({"synced": True, "sync_detail": ["up to date"]}) is None)
behind = cfg("write").write_refusal({"synced": False, "behind": 3,
                                     "sync_error": "could not fast-forward"})
check("a clone behind the remote is refused", bool(behind))
# Corrected 2026-11-22, measured against the real Residential_AC. This used to
# pin "single file" and "revert", from a claim that a stale push "replaces the
# newer file wholesale and reverts whatever landed in between, reporting
# success". Staged for real, git rejects such a push as a non-fast-forward and
# the remote is untouched -- so the refusal was right and its reason was not.
check("...and says the patch would be built on content already replaced",
      "already replaced" in (behind or ""), behind or "")
check("...and that the push would be rejected, not silently accepted",
      "rejected" in (behind or "") and "wholesale" not in (behind or ""),
      behind or "")
check("...and names the call that fixes it", "open_model" in (behind or ""))
diverged = cfg("write").write_refusal({"synced": False, "diverged": True,
                                       "behind": 1, "ahead": 1,
                                       "sync_note": "..."})
check("a diverged clone is refused", bool(diverged))
check("...and says a person is needed", "needs a person" in (diverged or ""), diverged or "")

# The deliberate non-refusal. A failed fetch means the clone is unverified,
# not known to be stale, and working offline is a premise of the product.
offline = cfg("write").write_refusal(
    {"synced": False, "sync_error": "could not resolve host",
     "sync_note": "Working from the clone already on disk."})
check("**a failed fetch is not a refusal**", offline is None, str(offline))

check("no credential, remote repo, writable -> a push warning",
      "probably" in (cfg("write").push_risk or ""), str(cfg("write").push_risk))
check("...but not for a local path",
      ModelConfig(name="l", remote_url="file:///tmp/x", branch="main", pat="",
                  access="write", model_dir=ROOT / "a",
                  views_dir=ROOT / "b").push_risk is None)
check("...and not when there is a credential",
      cfg("write", pat="ghp_x").push_risk is None)
check("...and not for a read-only model", cfg("read").push_risk is None)

secret = "ghp_DESCRIBEMUSTNOTLEAKTHIS000000000000"
described = json.dumps(cfg("write", pat=secret).describe())
check("**describe() never reports the PAT**", secret not in described)
check("...and says a credential is held", '"supplied"' in described, described)
check("the clone dir is the service's own 'unpacked'",
      cfg().clone_dir.name == "unpacked", str(cfg().clone_dir))


# ---------------------------------------------------------------------------
print("\n6. Dependency libraries resolve to the folder holding the .aird")
# ---------------------------------------------------------------------------

dep = Dependency(resource_name="Brake Library", remote_url="https://e.com/a/lib.git")
withdep = cfg("write", dependencies=(dep,))
check("a library with nothing on disk is left out of resources",
      withdep.resources() == {}, str(withdep.resources()))
check("...and is reported as missing instead",
      withdep.missing_dependencies() == ["Brake Library"],
      str(withdep.missing_dependencies()))
check("...and describe says so", "dependencies_not_cloned" in withdep.describe())

planted = dep.resolved_clone_dir(withdep.model_dir) / "model" / "deep"
planted.mkdir(parents=True, exist_ok=True)
(planted / "Lib.aird").write_text("<aird/>", encoding="utf-8")
check("once the .aird is there it resolves",
      withdep.resources() == {"Brake Library": {"path": str(planted)}},
      str(withdep.resources()))
check("...to the folder, not the file itself",
      Path(withdep.resources()["Brake Library"]["path"]).is_dir())
check("...and nothing is missing any more", withdep.missing_dependencies() == [])
check("the default directory is under the model, where the service looks",
      dep.resolved_clone_dir(withdep.model_dir)
      == withdep.model_dir / "deps" / "Brake_Library",
      str(dep.resolved_clone_dir(withdep.model_dir)))

twice = settings.ModelEntry(
    nickname="dup", remote_url="https://e.com/a/b.git",
    dependencies=(dep, replace(dep, remote_url="https://e.com/a/other.git")))
raises("two libraries with one resource name are refused",
       lambda: settings.upsert_model(settings.Profile(name="agent-one"), twice),
       saying="more than once")
check("one of each is accepted",
      len(settings.upsert_model(settings.Profile(name="agent-one"),
                                replace(twice, dependencies=(dep,))).models) == 1)


# ---------------------------------------------------------------------------
print("\n7. A library's credential falls back once, and never probes")
# ---------------------------------------------------------------------------

prof = settings.Profile(name="agent-one", models=(plain,))
STORE.asked.clear()
got = settings.dependency_secret(prof, "ac", "Brake Library")
check("with no library token, the model's own is used",
      got == "ghp_EXAMPLETOKENVALUE0000000000000000", got[:8])
check("...after consulting exactly two accounts, not a list of candidates",
      len(STORE.asked) == 2, str(STORE.asked))

STORE.set(settings.credential_account(
    "agent-one", settings.dependency_account("ac", "Brake Library")), "ghp_LIBOWN")
STORE.asked.clear()
check("a library's own token wins",
      settings.dependency_secret(prof, "ac", "Brake Library") == "ghp_LIBOWN")
check("...and the model's is not even consulted then",
      len(STORE.asked) == 1, str(STORE.asked))

# remove_model has to take the per-dependency accounts with it. The library's
# remove_entry knows only about the entry's own account, so without this the
# library's token outlives the model that justified it.
entry = settings.ModelEntry(nickname="ac", remote_url="https://e.com/a/b.git",
                            dependencies=(dep,))
after = settings.remove_model(settings.Profile(name="agent-one", models=(entry,)), "ac")
check("removing a model drops it", after.models == ())
check("...its own credential", not STORE.get(
    settings.credential_account("agent-one", "ac")))
check("**...and its libraries' credentials**", not STORE.get(
    settings.credential_account(
        "agent-one", settings.dependency_account("ac", "Brake Library"))))


# ---------------------------------------------------------------------------
print("\n8. to_config attaches the credential and the resolved directories")
# ---------------------------------------------------------------------------

STORE.set(settings.credential_account("agent-one", "ac"), "ghp_REATTACHED")
resolved = settings.to_config(
    settings.Profile(name="agent-one", models=(
        settings.ModelEntry(nickname="ac", remote_url="https://e.com/a/b.git",
                            access="read", include_realized=True,
                            dependencies=(dep,)),)), "ac")
check("the credential comes from the store", resolved.pat == "ghp_REATTACHED")
check("the access ceiling comes from the entry", resolved.read_only is True)
check("the realization flag is per model now", resolved.include_realized is True)
check("the libraries came across", len(resolved.dependencies) == 1)
check("the model directory defaults under the state root",
      resolved.model_dir == ROOT / "models" / config._slug("https://e.com/a/b.git"),
      str(resolved.model_dir))
check("the views directory is its sibling",
      resolved.views_dir == ROOT / "views" / config._slug("https://e.com/a/b.git"),
      str(resolved.views_dir))

raises("asking for a model that is not in the profile lists the real ones",
       lambda: settings.to_config(
           settings.Profile(name="agent-one", models=(plain,)), "nope"),
       saying="'ac'")
raises("asking for nothing, with several, is refused rather than guessed",
       lambda: settings.to_config(
           settings.Profile(name="agent-one", models=(plain, full)), ""),
       saying="list_my_scopes")


shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("bundle profile: all checks passed")
