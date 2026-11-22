"""Finding the two things this bundle is built on, before anything imports them.

The stdio Capella Fabric server is a third front end in this repository,
beside the hosted ``mcp_server.py`` and the Flask ``app.py``, and all three
run over the same ``capella_service.py``. It needs two things on
``sys.path`` that are not there by default:

1. **``capella_service`` and ``git_service``** -- this repository's own root,
   one directory above this package. Importable already when something was
   launched from the root, and not when it was launched from anywhere else.
2. **``mcp_desktop``** -- the foundation library: the state root, the profile
   and credential stores, the git layer, the loopback setup shell and the page
   server. A separate repository and a separate package, with no import of
   anything above it.

### Why the library is found rather than installed

It is a **sibling checkout**, the same arrangement ``Capella_Tools`` already
has with this server (``capella_service.py`` puts it on ``sys.path`` from
``CAPELLA_TOOLS_PATH``). That is a deliberate choice rather than an oversight:
an editable install of one of these packages on this machine has already cost
one long investigation, where an interpreter imported a module from a clone
made months earlier while every path in the checkout looked right. One more
editable install is one more way for that to happen.

So three things are tried, in order, and the order is the point:

1. **Already importable.** A bundle, a wheel, a virtual environment, a ``pip
   install -e`` -- if the library is on the path, nothing below runs. That is
   what keeps a packaged build from depending on a directory layout it will
   not have, which is the whole reason this is a lookup and not a constant.
2. **``MCP_DESKTOP_HOME``**, a directory named outright. The escape hatch for
   a checkout somewhere unusual, and the thing to reach for before editing
   this file.
3. **A sibling of this checkout**, which on a developer machine holds
   ``capella_fabric_desktop`` beside ``Capella_Fabric_Generator``.

Deliberately not: walking the disk, reading a config file, or a hard-coded
absolute path.

### No Cartenza naming, and that is the reason for a second copy of this

The notebook has a module that does the same job
(``engineering_notebook/foundation.py``) and reads ``CARTENZA_MCP_DESKTOP``.
This one reads ``MCP_DESKTOP_HOME`` and shares no code with it, on purpose.
A bootstrap whose job is to *find* the shared library cannot live *in* the
shared library, so some duplication here is structural rather than careless --
and what little there is is better duplicated than reached for across the IP
boundary this separation exists to draw (``Tony_BackLog``/note-0047,
``capella_fabric``/note-0001). The two files are about forty lines each and
neither imports the other's product.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

#: A directory holding the ``mcp_desktop`` package, named outright.
#:
#: Deliberately not ``CARTENZA_MCP_DESKTOP``, which is the notebook's name for
#: the same directory. Either product may be installed without the other, and
#: a Capella bundle that read a Cartenza-named variable would be carrying the
#: other product's vocabulary in its documented interface.
HOME_ENV = "MCP_DESKTOP_HOME"

#: Checkout names to look for beside this one, best guess first.
#:
#: More than one because the library's repository is currently named after
#: *this* product while the notebook also depends on it, which is an open
#: question rather than a settled one -- so a rename should not break either
#: consumer.
SIBLINGS = ("capella_fabric_desktop", "mcp_desktop", "mcp-desktop")


class FoundationMissing(ImportError):
    """The foundation library could not be found, with the three ways in."""


def repo_root() -> Path:
    """This repository's root -- the directory holding ``capella_service.py``.

    Derived from this file rather than from the working directory, because a
    stdio server is launched by a harness from whatever directory the harness
    happens to be in. ``<root>/capella_bundle/foundation.py`` -> ``<root>``.
    """
    return Path(__file__).resolve().parents[1]


def _holds_package(directory: Path) -> bool:
    """Whether ``directory`` makes ``mcp_desktop`` importable."""
    return (directory / "mcp_desktop" / "__init__.py").is_file()


def candidates() -> list[Path]:
    """Every directory that would be tried for the library, in order.

    Returned rather than only used, so the error message below and a test can
    both say what was actually looked at instead of describing it twice.
    """
    found: list[Path] = []
    named = os.environ.get(HOME_ENV, "").strip()
    if named:
        # Both spellings: the engineer may name the directory that *holds* the
        # package or the package directory itself, and guessing wrong gives an
        # ImportError that mentions neither.
        here = Path(named).expanduser()
        found.extend([here, here.parent])
    found.extend(repo_root().parent / name for name in SIBLINGS)
    return found


def ensure_service_importable() -> Path:
    """Put this repository's root on ``sys.path`` and return it.

    **At position 0**, like the launcher and like ``capella_service``'s own
    ``Capella_Tools`` bootstrap. A different checkout of this repository
    already on the path -- a leftover ``PYTHONPATH``, an editable install --
    would otherwise win, and the bundle would run against a
    ``capella_service`` it was not pointed at while every assertion passed.
    """
    root = repo_root()
    entry = str(root)
    if entry not in sys.path:
        sys.path.insert(0, entry)
    return root


def ensure_importable() -> Optional[Path]:
    """Make ``mcp_desktop`` importable. Returns the path added, or ``None``.

    ``None`` means it was already importable and nothing was changed, which is
    the case a packaged build must hit.
    """
    try:
        import mcp_desktop  # noqa: F401
        return None
    except ImportError:
        pass

    for directory in candidates():
        if _holds_package(directory):
            entry = str(directory)
            if entry not in sys.path:
                sys.path.insert(0, entry)
            return directory

    looked = "\n".join(f"  {c}" for c in candidates())
    raise FoundationMissing(
        "The local Capella Fabric server is built on the mcp_desktop "
        "foundation library and cannot find it. It holds the state root, the "
        "profile and credential stores, the git layer, the loopback setup "
        "shell and the page server, so there is nothing useful to start "
        "without it.\n\n"
        "Three ways to fix this, cheapest first:\n"
        "  1. Clone its repository beside this one, so that "
        "<this checkout>/../capella_fabric_desktop/mcp_desktop exists.\n"
        f"  2. Set {HOME_ENV} to the directory that holds the mcp_desktop "
        "package.\n"
        "  3. Install it (`pip install <that directory>`), after which this "
        "lookup is skipped entirely.\n\n"
        f"Directories tried, in order:\n{looked}"
    )


def ensure_all() -> None:
    """Both of the above, in the order a tool call needs them.

    Called from ``capella_bundle/__init__.py``, which Python guarantees runs
    before any module in this package -- the language's own rule rather than
    an import-order assumption. So every entry point gets it for free: the
    launcher, ``python -m capella_bundle``, the setup page, each test, and the
    boundary test's ``-I`` subprocess probe.
    """
    ensure_service_importable()
    ensure_importable()
