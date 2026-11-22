"""The local, single-engineer Capella Fabric server.

A third front end in this repository, beside the hosted ``mcp_server.py``
(streamable-http, systemd, nginx, per-session clones in ``/tmp`` swept hourly)
and the Flask ``app.py``. All three run over the same ``capella_service.py``
and ``git_service.py``, which are **not edited by any of this** -- the hosted
server keeps working, byte for byte.

What is different here is not the transport. It is the session and clone
model, inverted:

====================  =================================  ==========================
                      Hosted                             Local
====================  =================================  ==========================
Clone                 per session, under ``%TEMP%``      one permanent clone per model
Session id            ``uuid4().hex``                    derived from the clone path
Session record        ``session.json`` in the temp dir   the same file, in a kept dir
Lifetime              cron deletes it after four hours   never deleted
Credential            baked into the clone at clone      re-applied from the store on open
Realization flags     session-level, set at clone        per-model, from the setup page
====================  =================================  ==========================

The clone is **the engineer's model, not a cache**, so nothing here deletes
one. That single change is what the rest follows from: a session id has to be
derivable rather than minted, because a restart must hand back the same one; a
credential has to be re-applied rather than baked in, because rotating it
should not mean re-cloning; and rendered output has to be copied *out* of the
clone before it is served, because the credential lives in that directory's
``.git/config`` for as long as the directory does -- and the directory is now
forever.

### Modules

* ``foundation`` -- finds ``capella_service`` and ``mcp_desktop``. Runs below,
  before anything else in this package is imported.
* ``config`` -- ``ModelConfig``: the resolved settings for one model, and the
  write ceiling's declared half.
* ``settings`` -- ``ModelEntry``, the profile, the words, and the ceiling's
  measured half.
* ``session`` -- one permanent clone, the derived session id, ``open_model``
  and the ``capella_service`` adapter.
* ``instructions`` -- what a client is told at connect time.
"""

from __future__ import annotations

from capella_bundle import foundation as _foundation

# Before any module in this package is imported. Python guarantees a package's
# ``__init__`` runs first, which is what lets every entry point -- the
# launcher, ``python -m``, the setup page, each test -- get this without
# repeating it.
_foundation.ensure_all()

__all__ = ["foundation", "config", "settings", "session", "instructions"]
