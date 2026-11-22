# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
"""Serve rendered views and generated fabrics on loopback, so a link is a link.

The gap this closes is the same one the notebook closed, reported in the same
words: an agent hands over a Windows path and the page "has to be copy and
pasted into the browser". A path is not a link. Emitting ``file:///...``
instead does not help -- a Chromium-based chat window will not navigate from a
web page to a ``file:`` URL and mostly will not even linkify one -- so the
pages have to be reachable over http.

The hosted server answers this with a signed, expiring URL off
``mcp.innovatingwithcapella.com`` and an HMAC token
(``capella_service.view_token``). Locally there is no public base URL to sign
against, which ``render_diagram_view`` already allows for -- it returns ``url``
only when one exists, and says so in its own comment: "running over stdio,
say". This is that missing half.

### What is served, and what is emphatically not

**The views directory, never the model directory.** That is step 0's finding
and it is the whole reason the two are separate: ``git_service.clone_repo``'s
own docstring records that the credential lands in ``<dest>/.git/config`` "for
the life of the session directory", mitigated on the hosted side by
``cleanup_session`` removing that directory -- and here it is never removed,
because it is the engineer's model. So a file server rooted anywhere inside the
model directory is a server that can be walked into ``.git/config``.

Three things stand between a URL and that file, and they are deliberately
redundant:

1. ``settings.to_config`` **refuses** a views directory inside a model
   directory, so the layout cannot be configured.
2. ``stdio_server._publish`` **moves** a rendered view out of the model
   directory rather than copying it, so the unservable directory never holds a
   servable file at all.
3. ``PageSite`` resolves every request inside the registered directory,
   normalising backslashes **before** the containment check, and refuses any
   path with a ``..`` segment.

### What stays here rather than in the library

Only this product's configuration and two functions:

* The ``/v/`` prefix, the cookie name, the words a refused request is given.
  ``capella_fabric_pages`` is its own, never the notebook's ``cartenza_pages``:
  a shared cookie would let either product's pages be reached with a session
  minted by the other.
* ``VIEW_CSP``, because this product can state something about its own pages
  that the library cannot. A rendered view embeds its SVG as a base64 data URI
  and styles inline; it needs nothing else at all, so model text that somehow
  escaped escaping still could not reach the network from inside it. The
  hosted server sets the same policy on its own ``/view`` route.
* ``write_index``, which gives a model's views directory a page listing what
  has been rendered. Not decoration: the site's own front page links to
  ``/v/<model>/``, so without an index that link is a 404 handed to the
  engineer by the product itself.

### Lifetime, stated plainly

The port and the token are chosen fresh each run, so a link works for as long
as the agent that produced it is open and not after. That is the honest
behaviour for a server living inside an agent session, and it is why a link is
handed over with something to do rather than filed away. The file itself stays
on disk and is still openable with nothing running, which is what ``path`` is
for.
"""

from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Optional

from mcp_desktop.pages import PageServerError, PageSite  # noqa: F401

#: This product's own cookie. Never ``cartenza_pages``.
COOKIE = "capella_fabric_pages"

#: The policy for a page that embeds its SVG as a data URI and styles inline,
#: and needs nothing else. The same string the hosted server sets on its
#: ``/view`` route, for the same reason.
VIEW_CSP = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"

#: One site per process, serving every model in the profile. A second port per
#: model would mean a second token and a second thing to explain.
_SITE = PageSite(
    prefix="v",
    default_page="index.html",
    cookie=COOKIE,
    product="The Capella Fabric page server",
    title="Rendered views on this machine",
    noun="model",
    thread_name="capella-fabric-pages",
    refusal=(
        "This link has expired or is missing its token. Ask the agent for the "
        "link again -- a new one is issued for each run of the server."
    ),
    headers={
        "Content-Security-Policy": VIEW_CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    },
)


def site() -> PageSite:
    """The one site this process serves from.

    ``stdio_server._pages()`` imports this module, which is
    the whole seam: with this module absent the server's links degrade to file
    paths and nothing else changes.
    """
    return _SITE


def register(cfg) -> None:
    """Make one model's views servable under its nickname.

    Refuses the nested layout a second time. ``settings.to_config`` has
    already refused it, so this cannot normally fire -- which is exactly why
    it is here: a config built by some other route, or by a later caller that
    does not go through ``to_config``, must not be able to root a web server
    inside a directory holding a credential. The cheap check is at the point
    of use, not only at the point of configuration.
    """
    from mcp_desktop import state

    views = Path(cfg.views_dir).resolve()
    model = Path(cfg.model_dir).resolve()
    if state.inside(model, views) or views == model:
        raise PageServerError(
            f"Refusing to serve {views} for {cfg.name!r}: it is inside the "
            f"model directory {model}, which holds the git clone, whose "
            f".git/config holds this model's access token. Give this model a "
            f"views directory outside its model directory on the setup page."
        )
    views.mkdir(parents=True, exist_ok=True)
    _SITE.register(cfg.name, views)


def link_for(cfg, relative: str = "") -> str:
    """A clickable URL for one rendered file, or for the model's index.

    Starts the server on first use and waits for the socket, because
    ``ensure_serving`` returns as soon as the thread starts -- before uvicorn
    has bound -- and a link handed over inside that window fails once and
    works on refresh, which gets reported as flakiness rather than as a race.
    """
    register(cfg)
    _SITE.wait_until_serving()
    return _SITE.link_for(cfg.name, relative)


#: Anything else in the views directory is something this product put there.
_KINDS = (
    (".html", "rendered view"),
    (".txt", "generated fabric"),
)


def _kind_of(name: str) -> str:
    for suffix, label in _KINDS:
        if name.lower().endswith(suffix):
            return label
    return "file"


def write_index(cfg) -> Path:
    """Write the model's views directory an index page. Returns its path.

    Rewritten on every publish rather than updated, because the directory is
    the only source of truth about what is in it: a view deleted by hand, or a
    fabric regenerated under a new name, should change this page without
    anything having to remember.

    Built with ``html.escape`` rather than from a template file, like every
    other page in this product -- ``Path(__file__).parent`` does not survive
    zipimport, and a page that must work in a packaged build is one page that
    should not depend on one.
    """
    views = Path(cfg.views_dir)
    views.mkdir(parents=True, exist_ok=True)
    target = views / "index.html"

    files = sorted((p for p in views.iterdir()
                    if p.is_file() and p.name != target.name),
                   key=lambda p: p.stat().st_mtime, reverse=True)

    if files:
        rows = "".join(
            f"<tr><td><a href=\"{html.escape(p.name, quote=True)}\">"
            f"{html.escape(p.name)}</a></td>"
            f"<td>{html.escape(_kind_of(p.name))}</td>"
            f"<td>{p.stat().st_size:,} bytes</td>"
            f"<td>{html.escape(time.strftime('%Y-%m-%d %H:%M', time.localtime(p.stat().st_mtime)))}</td>"
            f"</tr>"
            for p in files
        )
        body = (f"<table><thead><tr><th>File</th><th>What it is</th>"
                f"<th>Size</th><th>Rendered</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")
    else:
        body = ("<p class=\"note\">Nothing has been rendered for this model "
                "yet. Ask the agent to render a view or generate a fabric.</p>")

    # Relative links only, and no external resource of any kind: this page is
    # served under the same Content-Security-Policy as the views themselves,
    # which allows no script, no font and no remote image.
    target.write_text(
        f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(cfg.name)} — rendered views</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin:0; padding:32px 16px; font:16px/1.55 ui-sans-serif, system-ui,
        "Segoe UI", sans-serif; }}
main {{ max-width:820px; margin:0 auto; }}
h1 {{ font-size:1.4rem; margin:0 0 4px; }}
p.sub, p.note {{ opacity:.7; font-size:.9rem; }}
table {{ width:100%; border-collapse:collapse; margin-top:12px; }}
th, td {{ text-align:left; padding:8px 6px; border-bottom:1px solid;
          border-color:color-mix(in srgb, currentColor 18%, transparent);
          font-size:.92rem; }}
th {{ opacity:.65; font-weight:600; font-size:.8rem; }}
</style></head>
<body><main>
<h1>{html.escape(cfg.name)}</h1>
<p class="sub">Rendered views and generated fabrics, served from this machine
only. Links last as long as the agent that produced them.</p>
{body}
</main></body></html>""",
        encoding="utf-8", newline="")
    return target


def base_url() -> str:
    return _SITE.base_url()


def registered() -> dict[str, Path]:
    return _SITE.registered()


def stop(timeout: float = 5.0) -> None:
    """Stop the site. Used by tests, which would otherwise leak a port each."""
    _SITE.stop(timeout)


def resolve(name: str, relative: str) -> Optional[Path]:
    """The file a request would serve, or None. Containment included."""
    return _SITE.resolve(name, relative)
