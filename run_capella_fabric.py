# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
r"""Launch the local Capella Fabric server, or its setup page.

    "C:\apps\Python_3_12\python.exe" c:\path\to\Capella_Fabric_Generator\run_capella_fabric.py
    ... --setup            the setup page in a browser
    ... --profile NAME     a specific profile, when more than one exists

Point the harness's command at **this** file rather than at
``stdio_server.py``, for two reasons that only show up when something is
already wrong.

**The import path.** ``sys.path.insert(0, ...)``, not append. An editable
install elsewhere on this machine can put a *different* checkout's directory
on the path ahead of this one -- that has genuinely happened here, with a
module served from a clone several months old -- and the failure is not an
ImportError but a wrong version imported silently. Inserting at position 0
means this checkout wins.

**Somewhere to say what went wrong.** ``capella_bundle`` finds two things
during its own import: this repository's root, for ``capella_service``, and the
``mcp_desktop`` foundation library, as a sibling checkout. A machine missing
either fails *during an import*, and all a harness can report of that is a
transport error -- which reads as a broken install and is not. This file is
the outermost place that can still print a sentence naming the real cause.
``FoundationMissing`` is an ImportError whose message already lists the three
ways to point at the library, so printing it to stderr and exiting 1 is the
same contract ``main()`` uses for a missing git.

**Nothing is printed on the happy path**: under stdio, stdout is the JSON-RPC
channel, and a single stray line corrupts the session's first message.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from stdio_server import main  # noqa: E402  (after the path fix)
except ImportError as exc:
    print(f"Capella Fabric cannot start: {exc}", file=sys.stderr)
    raise SystemExit(1) from exc

if __name__ == "__main__":
    raise SystemExit(main())
