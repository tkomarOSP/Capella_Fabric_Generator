"""Finding the foundation library, and failing usefully when it is not there.

Step 4 of the stdio plan. ``capella_bundle`` is built on ``mcp_desktop``, a
*sibling checkout* rather than an installed distribution, so a machine where
that checkout is missing is a machine where this server cannot start at all --
and it fails during an import, where a harness can only report a transport
error with nothing in it. The message is therefore the product, and it is
tested like one.

Everything here runs in a subprocess with ``-I``: isolated mode ignores
``PYTHONPATH`` and the user site directory, which is the only way to be sure
the library is not reachable by some route the test did not intend. On this
machine that also sidesteps a stale ``.pth`` from an editable install that has
already caused one long investigation.

The failure case is proved by **moving the subject**, not by mocking the
lookup: ``capella_bundle`` is copied into a temp tree whose parent genuinely
holds no library. A refusal test passes for free until the target is real.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(r"c:\apps\Git_VSCode\Capella_Fabric_Generator")
sys.path.insert(0, str(REPO))

from capella_bundle import foundation

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f": {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def run(code: str, *, cwd: Path, env: dict,
        onpath: Path = None) -> subprocess.CompletedProcess:
    """``-I``: no PYTHONPATH, no user site directory, nothing inherited.

    ``onpath`` is prepended inside the child rather than through
    ``PYTHONPATH``, because ``-I`` ignores that variable -- and it also
    removes the script's own directory, so a package sitting in ``cwd`` is not
    importable at all. The first draft of this relied on ``cwd`` and so
    measured nothing: every assertion about the error message was satisfied by
    a ``ModuleNotFoundError`` for the package itself. The control below is
    what caught it.
    """
    prelude = (f"import sys; sys.path.insert(0, r'{onpath}')\n"
               if onpath is not None else "")
    return subprocess.run([sys.executable, "-I", "-c", prelude + code],
                          cwd=str(cwd), env=env, capture_output=True, text=True)


ROOT = Path(tempfile.mkdtemp(prefix="bundle_foundation_"))

# ---------------------------------------------------------------------------
print("1. The resolution order, and what it would try")
# ---------------------------------------------------------------------------

check("the repo root is where capella_service lives",
      (foundation.repo_root() / "capella_service.py").is_file(),
      str(foundation.repo_root()))
check("the environment variable is this product's, not the notebook's",
      foundation.HOME_ENV == "MCP_DESKTOP_HOME", foundation.HOME_ENV)
check("...and specifically not CARTENZA_MCP_DESKTOP",
      "CARTENZA" not in foundation.HOME_ENV)

os.environ.pop(foundation.HOME_ENV, None)
plain = foundation.candidates()
check("with no variable set, only siblings are tried",
      len(plain) == len(foundation.SIBLINGS), str(plain))
check("...of this checkout's parent",
      all(c.parent == foundation.repo_root().parent for c in plain), str(plain))
check("the library's own repository name is tried first",
      plain[0].name == "capella_fabric_desktop", plain[0].name)

os.environ[foundation.HOME_ENV] = str(ROOT / "named")
named = foundation.candidates()
check("the variable is tried before any sibling",
      named[0] == ROOT / "named", str(named[0]))
check("...in both spellings, the directory and its parent",
      named[1] == ROOT, str(named[1]))
check("...and the siblings are still there as a fallback",
      len(named) == len(plain) + 2, str(named))
os.environ.pop(foundation.HOME_ENV, None)

check("it is already importable here, so nothing is added",
      foundation.ensure_importable() is None)

# ---------------------------------------------------------------------------
print("\n2. Where the library genuinely is not: the message is the product")
# ---------------------------------------------------------------------------

# Copy the package into a tree whose parent holds no library at all. Nothing
# is mocked: foundation.repo_root() really is <stranded>/repo, and
# <stranded>/ really has no capella_fabric_desktop in it.
stranded = ROOT / "stranded" / "repo"
shutil.copytree(REPO / "capella_bundle", stranded / "capella_bundle")
check("the copy has no library beside it",
      not any((stranded.parent / name).exists() for name in foundation.SIBLINGS))

bare = {k: v for k, v in os.environ.items()
        if k.upper() in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP",
                         "LOCALAPPDATA", "APPDATA", "USERPROFILE", "COMSPEC")}

done = run("import capella_bundle", cwd=ROOT, env=bare, onpath=stranded)
check("importing it there fails", done.returncode != 0, done.stdout)
message = done.stderr
check("...with FoundationMissing, not a bare ModuleNotFoundError",
      "FoundationMissing" in message, message[-400:])
check("...naming the library", "mcp_desktop" in message)
check("...saying what it holds, so the failure is not a mystery",
      "git layer" in message, message[-400:])
for way, phrase in [("the sibling checkout", "beside this one"),
                    ("the environment variable", foundation.HOME_ENV),
                    ("installing it", "pip install")]:
    check(f"...and offers {way}", phrase in message, message[-500:])
check("...and lists every directory it actually tried",
      all(name in message for name in foundation.SIBLINGS), message[-600:])

# The control for all of the above: the same tree, with the variable set,
# imports. Without this the failure could be caused by the copy rather than
# by the missing library, and every assertion above would be about nothing.
with_var = dict(bare, MCP_DESKTOP_HOME=str(Path(
    r"c:\apps\Git_VSCode\capella_fabric_desktop")))
fixed = run("import capella_bundle, mcp_desktop, sys;"
            "print(mcp_desktop.__file__)", cwd=ROOT, env=with_var,
            onpath=stranded)
check("**the control: the same tree imports once the variable is set**",
      fixed.returncode == 0, fixed.stderr[-400:])
check("...and the library it found is the one named",
      "capella_fabric_desktop" in fixed.stdout, fixed.stdout)

# And the package directory itself, not only its parent, is accepted -- the
# engineer may reasonably name either, and guessing wrong gives an ImportError
# that mentions neither.
pkg_var = dict(bare, MCP_DESKTOP_HOME=str(Path(
    r"c:\apps\Git_VSCode\capella_fabric_desktop\mcp_desktop")))
either = run("import capella_bundle, mcp_desktop; print('ok')",
             cwd=ROOT, env=pkg_var, onpath=stranded)
check("naming the package directory works too", either.returncode == 0,
      either.stderr[-300:])

# ---------------------------------------------------------------------------
print("\n3. The import runs from the package, so every entry point gets it")
# ---------------------------------------------------------------------------

# Python guarantees a package's __init__ runs before any module in it, which
# is the language's own rule rather than an import-order assumption. So a
# submodule imported directly, from a directory that is not the repo root,
# still resolves both of the things this bundle needs.
from_anywhere = run(
    "from capella_bundle import settings;"
    "import mcp_desktop, capella_service;"
    "print(settings.SERVICE, mcp_desktop.__name__, capella_service.__name__)",
    cwd=ROOT, env=bare, onpath=REPO)
check("a submodule imported from an unrelated directory works",
      from_anywhere.returncode == 0, from_anywhere.stderr[-500:])
check("...and found both the library and capella_service",
      "capella-fabric-local mcp_desktop capella_service" in from_anywhere.stdout,
      from_anywhere.stdout)

shutil.rmtree(ROOT, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("bundle foundation: all checks passed")
