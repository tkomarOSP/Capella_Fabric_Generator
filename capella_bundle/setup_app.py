# Copyright 2024-2026 Open Sun Power, LLC
# SPDX-License-Identifier: Apache-2.0
"""The local setup page: where an engineer configures this agent, once.

What the page is *for*, because it is not merely a form over a JSON file:

* **It is the only place a credential is entered**, and the only place one is
  changed. Nothing here ever renders a stored token back, so a configured
  model shows "stored" and offers replace or remove -- never a value to be
  shoulder-read, copied into a chat, or captured in a screenshot.
* **It is where the slow work happens.** Cloning a Capella model with its
  library repositories is minutes, not seconds. A tool call has no progress
  indicator and sits behind a harness timeout; a browser tab has both. This is
  why ``open_model`` never clones.
* **It is where the libraries are declared**, which is why
  ``add_dependency_repo`` is absent from the tool surface rather than renamed.
* **It is the local counterpart of the hosted Repos page**, limited to one
  agent -- which is what lets an engineer who moves up to the hosted service
  fill in a form they have already seen.

### The clone goes through the library, never ``git_service.clone_repo``

Step 0's third finding, and the one thing this page could get wrong silently.
``git_service._inject_pat`` is **unconditional**: an empty PAT still produces
``oauth2:@``, which a public https remote shrugs off but which rewrites a
``file://`` path into a host that does not exist. So every clone here goes
through ``capella_bundle.session.prepare``, which uses ``gitwork.run_clone``
and derives "no credential" from the absence of a token rather than injecting
an empty one.

### Starlette, not FastAPI

Starlette and uvicorn are already in the server's import closure through
``mcp.server.fastmcp``, so this page adds no new runtime dependency. It is
reached only from ``run_capella_fabric.py --setup`` and from ``setup_model()``
-- never from ``stdio_server``'s module scope -- so none of it enters the MCP
server's import graph, and a test pins that.

The HTML is built here with ``html.escape`` rather than from template files.
Template loading is a known packaging gap: ``Path(__file__).parent`` does not
survive zipimport and no ``.html`` is installed by a wheel, so a page that must
work in a packaged build is one page that should not depend on one.

### What locks it down

* bound to ``127.0.0.1`` only, never ``0.0.0.0``;
* a random high port, so nothing can guess where it is;
* a one-time token in the URL, exchanged on first load for a session cookie
  and then gone from the address bar and from browser history;
* a ``Host`` header check, so a hostile page cannot reach it by pointing a
  name it controls at ``127.0.0.1`` and having the browser send the request
  for it. Checked **before** the cookie, because a valid cookie arriving on a
  forged host is exactly the attack;
* ``SameSite=Strict`` on the cookie, so another site cannot have the browser
  submit these forms.

All five are the shared library's ``Guard``, with this product's own cookie
name -- its own, deliberately: two products sharing a cookie would let either
one's page be reached with the other's session.
"""

from __future__ import annotations

import html
import os
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from mcp_desktop import setup_shell
from mcp_desktop.setup_shell import Guard

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from mcp_desktop.profiles import Identity

from capella_bundle import session as bsession
from capella_bundle import settings as bsettings
from capella_bundle.config import Dependency, ModelConfig
from capella_bundle.settings import (
    ModelEntry,
    Profile,
    SettingsError,
    credential_account,
    dependency_account,
)

#: This product's own cookie. Never ``cartenza_setup``: the notebook's page
#: uses that name, and a shared cookie would mean either product's setup page
#: could be reached with a session minted by the other.
COOKIE = "capella_fabric_setup"

free_port = setup_shell.free_port


def launcher_path() -> Path:
    """Where this product's launcher lives.

    One function rather than the expression inlined into ``launch_command``,
    because this is the line a packaged build has to change:
    ``Path(__file__).parent`` does not survive zipimport. The shared library
    takes the launcher as a *parameter* for exactly that reason, so the single
    place that resolves it is here, in the product that knows its own layout.
    """
    return Path(__file__).resolve().parents[1] / "run_capella_fabric.py"


def launch_command(profile_name: str = "") -> str:
    """The exact command line to paste into a harness.

    With no profile name this is **the same line for every agent**, which is
    the point: a profile written into a recipe configures an agent
    *definition*, while the model someone is working in belongs to a
    *session*, and the session chooses by calling ``use_profile``. So there is
    one line to paste, once, and onboarding never involves a per-agent value.

    ``profile_name`` is still honoured and still skips the question, which is
    worth having for an agent that only ever works in one model.
    """
    return setup_shell.launch_command(launcher_path(), argument=profile_name)


def load_or_start(profile_name: str) -> Optional[Profile]:
    """The named profile, an empty one to fill in, or **None** if none is open.

    ``None`` is a real state, not a failure. Until the engineer has chosen or
    created a profile there is nothing to edit, and the page shows the chooser
    and nothing else -- which is what stops someone wandering into a model
    they did not mean to change.
    """
    if not (profile_name or "").strip():
        return None
    try:
        return bsettings.load_profile(profile_name)
    except SettingsError:
        return Profile(name=profile_name,
                       identity=Identity(agent_name="", user_name="", handle=""))


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------

_CSS = """
:root { color-scheme: light dark; --bg:#fbfaf7; --fg:#1b1b1a; --muted:#6b6a66;
        --line:#dedcd5; --card:#ffffff; --accent:#1f4f6f; --warn:#8a5a00;
        --bad:#9a2f2f; --good:#1f6f52; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16171a; --fg:#e9e8e4; --muted:#9a9a95; --line:#2e3034;
          --card:#1e2024; --accent:#7fb6cf; --warn:#e0a94a;
          --bad:#e38b8b; --good:#7fcfb8; }
}
* { box-sizing: border-box; }
body { margin:0; padding:32px 16px 64px; background:var(--bg); color:var(--fg);
       font:16px/1.55 ui-sans-serif, system-ui, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; }
h1 { font-size:1.6rem; margin:0 0 4px; }
h2 { font-size:1.1rem; margin:32px 0 8px; }
h3 { font-size:.95rem; margin:18px 0 4px; }
p.sub { color:var(--muted); margin:0 0 24px; }
section { background:var(--card); border:1px solid var(--line); border-radius:10px;
          padding:18px 20px; margin-bottom:18px; }
label { display:block; font-size:.85rem; color:var(--muted); margin:10px 0 3px; }
input[type=text], input[type=password], select {
  width:100%; padding:8px 10px; border:1px solid var(--line); border-radius:6px;
  background:var(--bg); color:var(--fg); font:inherit; }
label.check { display:flex; align-items:flex-start; gap:8px; color:var(--fg);
  font-size:.9rem; margin:14px 0 3px; cursor:pointer; }
label.check input[type=checkbox] { width:auto; margin:2px 0 0; flex:none; }
button { font:inherit; padding:8px 14px; border-radius:6px; border:1px solid var(--accent);
         background:var(--accent); color:var(--bg); cursor:pointer; }
button.secondary, a.button.secondary { background:transparent; color:var(--fg);
       border-color:var(--line); }
a.button { display:inline-block; text-decoration:none; font:inherit; padding:8px 14px;
       border-radius:6px; border:1px solid var(--accent); background:var(--accent);
       color:var(--bg); }
td.actions { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
td.actions form { margin:0; }
details summary { cursor:pointer; }
details p, details pre, details table { margin-top:10px; }
.row { display:flex; gap:12px; flex-wrap:wrap; }
.row > div { flex:1 1 200px; }
table { width:100%; border-collapse:collapse; margin-top:6px; }
th, td { text-align:left; padding:8px 6px; border-bottom:1px solid var(--line);
         font-size:.92rem; vertical-align:top; }
th { color:var(--muted); font-weight:600; font-size:.8rem; }
code { background:var(--bg); border:1px solid var(--line); border-radius:4px;
       padding:2px 5px; font-size:.86rem; word-break:break-all; }
pre { background:var(--bg); border:1px solid var(--line); border-radius:6px;
      padding:12px; overflow-x:auto; font-size:.84rem; white-space:pre-wrap; }
.note { color:var(--muted); font-size:.86rem; }
.warn { color:var(--warn); }
.flash { border-left:3px solid var(--accent); padding:8px 12px; margin-bottom:18px;
         background:var(--card); }
.yes { color:var(--good); font-weight:600; }
.no { color:var(--bad); font-weight:600; }
.maybe { color:var(--muted); font-weight:600; }
"""


def _esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _config_without_credential(entry: ModelEntry) -> ModelConfig:
    """A config good enough to ask "is this on disk yet?" without the secret.

    ``to_config`` reads the credential store and can also refuse the whole
    entry -- the views-inside-model layout -- and neither belongs in drawing a
    table row. ``is_prepared`` only looks at the filesystem, so it is given a
    config with ``pat=""``: the row renders for a model whose credential is
    missing or whose directories are wrong, and says so, instead of the table
    failing.

    Whether a credential *exists* is asked separately, once per row, as a
    boolean -- never as a value, which is what keeps a token out of the HTML.
    """
    return ModelConfig(
        name=entry.nickname, remote_url=entry.remote_url, branch=entry.branch,
        pat="", access=entry.access,
        model_dir=entry.resolved_model_dir(),
        views_dir=entry.resolved_views_dir(),
        auth=entry.auth, shared=entry.shared,
        include_realized=entry.include_realized,
        include_realizing=entry.include_realizing,
        dependencies=entry.dependencies,
    )


def _profile_chooser(current_name: str, flash_html: str) -> str:
    """Pick a saved profile, or start a new one. Always visible."""
    saved = bsettings.list_profiles()
    options = "".join(
        f'<option value="{_esc(name)}"'
        f'{" selected" if name == current_name else ""}>{_esc(name)}</option>'
        for name in saved
    )
    open_now = (f"Working in <strong>{_esc(current_name)}</strong>."
                if current_name else
                "No profile is open yet. Choose one, or start a new one.")
    existing = f"""
  <form method="post" action="/profile/use">
    <div class="row">
      <div><select name="name">{options}</select></div>
      <div><button class="secondary" type="submit" style="margin-top:0">Open</button></div>
    </div>
  </form>""" if saved else ""

    return f"""
<h1>Capella Fabric setup</h1>
<p class="sub">One agent, its models, and the credentials they need. Nothing
here leaves this machine.</p>
{flash_html}
<h2>Profile</h2>
<section>
  <p class="note">{open_now} A profile is a named set of Capella models. An
  agent asks which one you want to work in, so most engineers need exactly
  one.</p>
  {existing}
  <form method="post" action="/profile/new">
    <div class="row">
      <div><input type="text" name="name" placeholder="models"></div>
      <div><button class="secondary" type="submit" style="margin-top:0">Start a new one</button></div>
    </div>
  </form>
</section>"""


def _verdict(ok: Optional[bool]) -> str:
    if ok is True:
        return '<span class="yes">yes</span>'
    if ok is False:
        return '<span class="no">no</span>'
    return '<span class="maybe">not checked</span>'


def _verify_section(nickname: str, steps: list[dict[str, Any]]) -> str:
    """The four questions and their answers, in the order they bite."""
    if not steps:
        return ""
    rows = "".join(
        f"<tr><th style='white-space:nowrap'>{_esc(s['step'])}</th>"
        f"<td style='white-space:nowrap'>{_verdict(s['ok'])}</td>"
        f"<td>{_esc(s['detail'])}</td></tr>"
        for s in steps
    )
    return f"""
<h2>Verified {_esc(nickname)}</h2>
<section>
  <table><tbody>{rows}</tbody></table>
  <p class="note">Each answer makes the next question meaningful, so a "no"
  high up leaves what is below it unchecked rather than failing. "Not checked"
  is a real answer: two of these cannot be settled without a clone.</p>
</section>"""


def _model_rows(profile: Profile, store) -> str:
    if not profile.models:
        return ('<tr><td colspan="7" class="note">No models yet. Add one below.'
                '</td></tr>')
    rows = []
    for entry in profile.models:
        has_cred = bool(store.get(credential_account(profile.name, entry.nickname)))
        cfg = _config_without_credential(entry)
        built = bsession.is_prepared(cfg)
        access = _esc(entry.access)
        if entry.shared:
            # Shared does NOT lower the ceiling here, unlike the notebook: a
            # Capella model is what a team collaborates on, so a single-writer
            # model repository is the exception. It changes what happens on a
            # divergence, and the row says so rather than overriding the
            # declaration the engineer made.
            access += ('<div class="note">shared &mdash; still writable; a '
                       'divergence is reported for you to settle</div>')
        flags = [name for name, on in (("realized", entry.include_realized),
                                       ("realizing", entry.include_realizing))
                 if on]
        libraries = (f'{len(entry.dependencies)}'
                     if entry.dependencies else '<span class="note">none</span>')
        rows.append(f"""
        <tr>
          <td><strong>{_esc(entry.nickname)}</strong>
              {'<div class="note">' + _esc(', '.join(flags)) + '</div>' if flags else ''}</td>
          <td><code>{_esc(entry.remote_url)}</code>
              <div class="note">branch {_esc(entry.branch)}</div></td>
          <td>{access}</td>
          <td>{'&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;' if has_cred else '<span class="note">none</span>'}</td>
          <td>{libraries}</td>
          <td>{'cloned' if built else '<span class="note">not cloned</span>'}</td>
          <td class="actions">
            <a class="button secondary" href="/?edit={_esc(entry.nickname)}">Edit</a>
            <form method="post" action="/model/verify">
              <input type="hidden" name="nickname" value="{_esc(entry.nickname)}">
              <button class="secondary" type="submit">Verify</button>
            </form>
            <form method="post" action="/model/delete">
              <input type="hidden" name="nickname" value="{_esc(entry.nickname)}">
              <button class="secondary" type="submit">Remove</button>
            </form>
          </td>
        </tr>""")
    return "".join(rows)


def _library_section(profile: Profile, editing: Optional[ModelEntry], store) -> str:
    """The model's library repositories, declared here and nowhere else.

    This section is why ``add_dependency_repo`` is absent from the tool
    surface rather than renamed: a library is another clone, which is slow and
    may need its own credential, and both belong where the engineer is
    watching.
    """
    if editing is None:
        return ""

    if editing.dependencies:
        rows = "".join(f"""
        <tr>
          <td><strong>{_esc(dep.resource_name)}</strong></td>
          <td><code>{_esc(dep.remote_url)}</code>
              <div class="note">branch {_esc(dep.branch)}</div></td>
          <td>{'&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;' if store.get(credential_account(
              profile.name, dependency_account(editing.nickname, dep.resource_name)))
              else '<span class="note">the model&rsquo;s own</span>'}</td>
          <td>{'cloned' if (dep.resolved_clone_dir(editing.resolved_model_dir()) / '.git').exists()
               else '<span class="note">not cloned</span>'}</td>
          <td class="actions">
            <form method="post" action="/library/delete">
              <input type="hidden" name="nickname" value="{_esc(editing.nickname)}">
              <input type="hidden" name="resource_name" value="{_esc(dep.resource_name)}">
              <button class="secondary" type="submit">Remove</button>
            </form>
          </td>
        </tr>""" for dep in editing.dependencies)
        table = f"""
  <table>
    <thead><tr><th>Resource name</th><th>Repository</th><th>Token</th>
    <th>On disk</th><th></th></tr></thead>
    <tbody>{rows}</tbody>
  </table>"""
    else:
        table = ('<p class="note">No libraries declared. Most models need '
                 'none.</p>')

    return f"""
<h2>Library repositories for {_esc(editing.nickname)}</h2>
<section>
  {table}
  <h3>Add a library</h3>
  <form method="post" action="/library">
    <input type="hidden" name="nickname" value="{_esc(editing.nickname)}">
    <label for="resource_name">Resource name</label>
    <input type="text" id="resource_name" name="resource_name" required
           placeholder="Bike BrakeSystem Library">
    <p class="note"><strong>This has to match exactly</strong> the name the
    main model's cross-references use &mdash; it is the key they resolve
    against, not a label. Open the model in Capella and read it off the
    referenced library, rather than guessing from the repository name.</p>
    <label for="lib_url">Repository URL</label>
    <input type="text" id="lib_url" name="remote_url" required
           placeholder="https://github.com/you/brake-library.git">
    <div class="row">
      <div>
        <label for="lib_branch">Branch</label>
        <input type="text" id="lib_branch" name="branch" value="main">
      </div>
      <div>
        <label for="lib_pat">Access token (optional)</label>
        <input type="password" id="lib_pat" name="pat" autocomplete="off"
               placeholder="leave blank to use {_esc(editing.nickname)}&rsquo;s own token">
      </div>
    </div>
    <p class="note">Leaving the token blank is the normal case: a model and
    its libraries are usually in one organisation behind one credential, so
    the model's own is used. A library in a different organisation needs its
    own.</p>
    <button type="submit">Add and clone library</button>
  </form>
</section>"""


def _diagnostics(profile: Profile) -> str:
    """What this server was actually launched with.

    Names of environment variables only, never values: a harness may well
    have put a credential in one, and this page is read over shoulders and on
    screen shares. The git variables are the exception and are shown with
    values, because they are paths and switches rather than secrets, and
    because ``HOME`` or ``GIT_CONFIG_GLOBAL`` pointing somewhere slow or
    absent is the one remaining difference between a git call that hangs
    inside this process and the identical call that returns instantly from a
    shell.
    """
    harness_vars = sorted(
        name for name in os.environ
        if name.startswith(("MCP_DESKTOP", "SC_AGENTS_", "SIMCENTER_",
                            "CAPELLA_", "GOOSE_"))
    )
    git_names = ("HOME", "USERPROFILE", "GIT_DIR", "GIT_WORK_TREE",
                 "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_COUNT",
                 "GIT_TERMINAL_PROMPT", "GCM_INTERACTIVE", "GIT_ASKPASS",
                 "GIT_SSH_COMMAND", "GIT_EXEC_PATH", "GIT_TRACE")
    git_env = [(name, os.environ[name]) for name in git_names if name in os.environ]

    rows = [
        ("Profile in use", profile.name),
        ("Interpreter", sys.executable),
        ("Command line", " ".join(sys.argv) or "(none)"),
        ("Profile file", str(bsettings.profile_path(profile.name))),
        ("Credential store",
         getattr(bsettings.credential_store(), "label", "unknown")),
        ("git", shutil.which("git") or "not found on PATH"),
        ("Capella_Tools", os.environ.get("CAPELLA_TOOLS_PATH",
                                         r"C:\apps\.metadata\Capella_Tools")),
        ("Harness variables set", ", ".join(harness_vars) if harness_vars
         else "none — the harness passed nothing of its own"),
    ]
    rows += [(f"env {name}", value) for name, value in git_env]
    cells = "".join(
        f"<tr><th style='white-space:nowrap'>{_esc(label)}</th>"
        f"<td><code>{_esc(value)}</code></td></tr>"
        for label, value in rows
    )
    return (f"<table>{cells}</table>"
            f"<p class='note'>Variable names only, never their values, except "
            f"git's own paths and switches.</p>")


def render_page(profile: Optional[Profile], store, flash: str = "",
                edit: str = "", flash_detail: str = "",
                verified: Optional[tuple[str, list[dict[str, Any]]]] = None) -> str:
    flash_html = ""
    if flash:
        detail = f'<pre>{_esc(flash_detail)}</pre>' if flash_detail else ""
        flash_html = f'<div class="flash">{_esc(flash)}{detail}</div>'

    chooser = _profile_chooser(profile.name if profile else "", flash_html)

    if profile is None:
        # Nothing open: the chooser is the whole page. An identity form and a
        # model list with no profile behind them would invite someone to fill
        # them in and wonder where it went.
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capella Fabric setup</title><style>{_CSS}</style></head>
<body><main>{chooser}
<section>
  <p class="note">Open a profile above, or start a new one, to add models.</p>
</section>
</main></body></html>"""

    return _render_open_profile(profile, store, chooser, edit, verified)


def _render_open_profile(profile: Profile, store, chooser: str, edit: str,
                         verified: Optional[tuple[str, list[dict[str, Any]]]]
                         ) -> str:
    ident = profile.identity

    editing: Optional[ModelEntry] = None
    for entry in profile.models:
        if entry.nickname.lower() == (edit or "").strip().lower():
            editing = entry
            break

    has_cred = bool(editing and store.get(
        credential_account(profile.name, editing.nickname)))
    pat_placeholder = ("stored — leave blank to keep it" if has_cred
                       else "none stored; blank is fine for a public repository")
    pat_note = ("&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull; already stored "
                "for this model. Type a new one only to replace it."
                if has_cred else "")
    verify_html = _verify_section(*verified) if verified else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Capella Fabric setup</title><style>{_CSS}</style></head>
<body><main>
{chooser}

<h2>Rename this profile</h2>
<section>
  <form method="post" action="/profile/rename">
    <div class="row">
      <div><input type="text" name="name" value="{_esc(profile.name)}" required></div>
      <div><button class="secondary" type="submit" style="margin-top:0">Rename</button></div>
    </div>
    <p class="note">Its models and their stored tokens move with it. The clones
    on disk are not moved or renamed.</p>
  </form>
</section>

<h2>This agent &mdash; in profile {_esc(profile.name)}</h2>
<section>
  <form method="post" action="/identity">
    <div class="row">
      <div>
        <label for="agent_name">Full name</label>
        <input type="text" id="agent_name" name="agent_name"
               value="{_esc(ident.agent_name)}"
               placeholder="Thermal Architecture Agent (Simcenter AI Agents)">
      </div>
      <div>
        <label for="display_name">Name commits are signed with</label>
        <input type="text" id="display_name" name="display_name"
               value="{_esc(ident.display_name)}" placeholder="Thermal Architecture">
      </div>
    </div>
    <div class="row">
      <div>
        <label for="user_name">Engineer it works for</label>
        <input type="text" id="user_name" name="user_name" value="{_esc(ident.user_name)}">
      </div>
      <div>
        <label for="role">What it is for, in a line</label>
        <input type="text" id="role" name="role" value="{_esc(ident.role)}">
      </div>
    </div>
    <p class="note">The full name tells agents apart in a list; the signing
    name is what appears in the model repository's git history beside each
    patch.</p>
    <button type="submit">Save</button>
  </form>
</section>

{verify_html}

<h2>Models</h2>
<section>
  <table>
    <thead><tr><th>Nickname</th><th>Repository</th><th>Access</th><th>Token</th>
    <th>Libraries</th><th>On disk</th><th></th></tr></thead>
    <tbody>{_model_rows(profile, store)}</tbody>
  </table>
  <p class="note">A read model is one this agent browses, searches, renders and
  generates fabrics from but never changes. A write model is one it may patch
  and push. <strong>Verify</strong> asks the remote four questions without
  cloning anything.</p>
</section>

<h2>{'Change ' + _esc(editing.nickname) if editing else 'Add a model'}</h2>
<section>
  <form method="post" action="/model">
    <div class="row">
      <div>
        <label for="nickname">Nickname</label>
        <input type="text" id="nickname" name="nickname" required
               value="{_esc(editing.nickname) if editing else 'model'}">
      </div>
      <div>
        <p class="note" style="margin-top:28px">What your agent calls this
        model, and what everything here is keyed on. Reusing a nickname changes
        that model and keeps its stored token and its libraries.</p>
      </div>
    </div>

    <label for="remote_url">Repository URL</label>
    <input type="text" id="remote_url" name="remote_url" required
           value="{_esc(editing.remote_url) if editing else ''}"
           placeholder="https://github.com/you/your-capella-model.git">

    <div class="row">
      <div>
        <label for="branch">Branch</label>
        <input type="text" id="branch" name="branch"
               value="{_esc(editing.branch) if editing else 'main'}">
      </div>
      <div>
        <label for="access">Access</label>
        <select id="access" name="access">
          <option value="write"{' selected' if not editing or editing.access == 'write' else ''}>write &mdash; this agent may patch and push</option>
          <option value="read"{' selected' if editing and editing.access == 'read' else ''}>read &mdash; browse, render and generate only</option>
        </select>
      </div>
    </div>

    <label class="check" for="shared">
      <input type="checkbox" id="shared" name="shared" value="1"
             {'checked' if editing and editing.shared else ''}>
      Other people or services also push to this repository
    </label>
    <p class="note">A Capella model is normally shared, so this is the usual
    case and it does <strong>not</strong> make the model read-only. What it
    changes is what happens when both sides have moved: a model is a single
    file, and git merges it textually &mdash; so a rebase can succeed with no
    conflict at all and still produce a model Capella will not open. This
    server therefore never reconciles a divergence by itself. It reports it and
    leaves the decision to you, in Capella.</p>

    <div class="row">
      <div>
        <label class="check" for="include_realized">
          <input type="checkbox" id="include_realized" name="include_realized"
                 value="1" {'checked' if editing and editing.include_realized else ''}>
          Include realized references in generated fabrics
        </label>
      </div>
      <div>
        <label class="check" for="include_realizing">
          <input type="checkbox" id="include_realizing" name="include_realizing"
                 value="1" {'checked' if editing and editing.include_realizing else ''}>
          Include realizing references
        </label>
      </div>
    </div>
    <p class="note">Set per model here. On the hosted server these are
    arguments to the clone call; there is no clone call left to carry them.</p>

    <label for="pat">Access token</label>
    <input type="password" id="pat" name="pat" autocomplete="off"
           placeholder="{_esc(pat_placeholder)}">
    <p class="note">{pat_note} Kept in {_esc(getattr(store, 'label', 'the credential store'))},
    never in a file this tool writes and never in anything your agent can
    read.</p>

    <details>
      <summary class="note">Where this model is kept on disk (optional)</summary>
      <div class="row">
        <div>
          <label for="model_dir">Model directory</label>
          <input type="text" id="model_dir" name="model_dir"
                 value="{_esc(editing.model_dir) if editing and editing.model_dir else ''}"
                 placeholder="leave blank for the default">
        </div>
        <div>
          <label for="views_dir">Views directory</label>
          <input type="text" id="views_dir" name="views_dir"
                 value="{_esc(editing.views_dir) if editing and editing.views_dir else ''}"
                 placeholder="leave blank for the default">
        </div>
      </div>
      <p class="note">The views directory <strong>cannot be inside the model
      directory</strong>, and that is refused rather than discouraged. The
      model directory holds the git clone; the clone's <code>.git/config</code>
      holds this model's access token for as long as the directory exists, and
      here it is never deleted. Rendered views are served over a local HTTP
      port, so nothing inside the model directory may ever be servable. Leave
      both blank and they are placed correctly for you.</p>
    </details>

    <p class="note">Saving clones the repository and its libraries, which for a
    Capella model can take minutes. Anything wrong with the URL, the branch or
    the token shows up here rather than later in your agent.</p>
    <button type="submit">Save and clone model</button>
    {'<a class="button secondary" href="/">Cancel</a>' if editing else ''}
  </form>
</section>

{_library_section(profile, editing, store)}

<h2>Back to your agent</h2>
<section>
  <p>Setup is finished when the models above say <strong>cloned</strong>.
  Return to your agent and ask it to open your model &mdash; it will see
  everything configured here.</p>
  <details>
    <summary class="note">Running the server outside an agent that offers setup</summary>
    <p class="note">A harness that cannot start this page for you needs the MCP
    server's command line directly. In Simcenter AI Agents it goes in the
    agent's own copy of the server configuration, not only the global one
    &mdash; an agent created earlier keeps the copy it was made with.</p>
    <pre>{_esc(launch_command())}</pre>
    <p class="note"><strong>This line is the same for every agent</strong> and
    carries no profile name. Which model a session works in is settled in the
    conversation: the agent asks, you answer.</p>
    <p class="note">To reopen this page later:</p>
    <pre>{_esc(launch_command() + ' --setup')}</pre>
  </details>
  <details>
    <summary class="note">Diagnostics &mdash; what this server was launched with</summary>
    {_diagnostics(profile)}
  </details>
</section>
</main></body></html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def build_app(profile_name: str = "", *, token: str = "",
              store=None, port: int = 0) -> Starlette:
    """The setup application, with its own token and credential store.

    ``store`` is injected so tests never touch the engineer's real keychain.
    """
    token = token or setup_shell.new_token()
    store = store or bsettings.credential_store()

    # Which profile this page is editing. Mutable, because opening or creating
    # one moves the page to it.
    current = {"name": profile_name}

    page_guard = Guard(token, cookie=COOKIE, product="This page",
                       query_param="token")

    def guard(request: Request):
        """None when the request may proceed, else the response to send.

        Also performs the token exchange: a request carrying the valid
        one-time token is redirected to its own path with the cookie set, so
        the token does not sit in the address bar, in history, or in a
        referrer header on any link the page carries.
        """
        return page_guard.guard(request)

    def page(request: Request, flash: str = "", flash_detail: str = "",
             edit: str = "", verified=None) -> HTMLResponse:
        return HTMLResponse(render_page(load_or_start(current["name"]), store,
                                        flash, edit, flash_detail, verified))

    def _needs_profile(request: Request):
        """Refuse any change while no profile is open, rather than inventing one."""
        if not current["name"]:
            return page(request, "No profile is open. Choose one above, or "
                                 "start a new one, before making changes.")
        return None

    def _named_model(profile: Profile, nickname: str) -> Optional[ModelEntry]:
        for entry in profile.models:
            if entry.nickname.lower() == (nickname or "").strip().lower():
                return entry
        return None

    async def index(request: Request):
        denied = guard(request)
        return denied or page(request, edit=request.query_params.get("edit", ""))

    async def use_profile(request: Request):
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        wanted = str(form.get("name") or "").strip()
        if wanted not in bsettings.list_profiles():
            return page(request, f"There is no saved profile called {wanted!r}.")
        current["name"] = wanted
        held = len(bsettings.load_profile(wanted).models)
        return page(request, f"Opened {wanted} — {held} model"
                             f"{'' if held == 1 else 's'}.")

    async def new_profile(request: Request):
        denied = guard(request)
        if denied:
            return denied
        form = await request.form()
        wanted = str(form.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", wanted or ""):
            return page(request, f"{wanted!r} cannot be used as a profile name. "
                                 f"Use letters, digits, dots, dashes or "
                                 f"underscores.")
        if wanted in bsettings.list_profiles():
            return page(request, f"A profile called {wanted!r} already exists. "
                                 f"Open it above, or choose another name.")
        bsettings.save_profile(Profile(
            name=wanted,
            identity=Identity(agent_name="", user_name="", handle="")))
        current["name"] = wanted
        return page(request, f"Created {wanted}. Fill in this agent's identity, "
                             f"then add its models.")

    async def rename_open_profile(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        wanted = str(form.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", wanted or ""):
            return page(request, f"{wanted!r} cannot be used as a profile name. "
                                 f"Use letters, digits, dots, dashes or "
                                 f"underscores.")
        was = current["name"]
        if wanted == was:
            return page(request, f"It is already called {wanted}.")
        try:
            bsettings.rename_profile(was, wanted, store=store)
        except SettingsError as exc:
            return page(request, "Not renamed.", str(exc))
        current["name"] = wanted
        return page(request, f"Renamed {was} to {wanted}.")

    async def save_identity(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        profile = load_or_start(current["name"])
        assert profile is not None                  # _needs_profile ran first
        updated = Identity(
            agent_name=str(form.get("agent_name") or "").strip(),
            display_name=str(form.get("display_name") or "").strip(),
            role=str(form.get("role") or "").strip(),
            user_name=str(form.get("user_name") or "").strip(),
            handle=profile.identity.handle,
            harness=profile.identity.harness,
            model=profile.identity.model,
        )
        bsettings.save_profile(Profile(name=profile.name, identity=updated,
                                       models=profile.models))
        return page(request, "Saved this agent's identity.")

    async def save_model(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        nickname = str(form.get("nickname") or "").strip()
        profile = load_or_start(current["name"])
        assert profile is not None

        existing = _named_model(profile, nickname)
        supplied = str(form.get("pat") or "").strip()

        raw: dict[str, Any] = {
            "nickname": nickname,
            "remote_url": str(form.get("remote_url") or "").strip(),
            "branch": str(form.get("branch") or "main").strip() or "main",
            "access": str(form.get("access") or "write").strip(),
            # An unticked checkbox sends nothing at all, so absence is the
            # "no" and there is no hidden companion field to keep in step.
            "shared": bool(form.get("shared")),
            "include_realized": bool(form.get("include_realized")),
            "include_realizing": bool(form.get("include_realizing")),
        }
        for field in ("model_dir", "views_dir"):
            typed = str(form.get(field) or "").strip()
            if typed:
                raw[field] = typed
            elif existing is not None and getattr(existing, field):
                # Blank means "leave it where it is" for a model that already
                # has one. Silently resetting it to the default would move a
                # clone out from under the engineer.
                raw[field] = str(getattr(existing, field))

        if existing is not None:
            raw["dependencies"] = [d.to_json() for d in existing.dependencies]
            # Carried over, except when the token is being replaced or the
            # model is being pointed somewhere else: a different credential or
            # a different host may want a different mechanism, and a stale
            # answer would be applied with confidence it has not earned.
            if not supplied and existing.remote_url == raw["remote_url"]:
                raw["auth"] = existing.auth

        try:
            entry = ModelEntry.from_json(raw)
            profile = bsettings.upsert_model(profile, entry)
            bsettings.save_profile(profile)
        except SettingsError as exc:
            return page(request, f"Not saved: {exc}", edit=nickname)

        # Read after the save, because whether a token was supplied also
        # decided whether a previously proven mechanism still applies above.
        # A blank token means "keep what is stored", so that changing a branch
        # does not quietly delete a working credential.
        if supplied:
            store.set(credential_account(profile.name, entry.nickname), supplied)

        message, detail = _clone(profile.name, entry.nickname)
        return page(request, message, detail,
                    edit=entry.nickname if detail else "")

    def _clone(profile_name_: str, nickname: str) -> tuple[str, str]:
        """Clone one model and its libraries. Returns (message, error detail).

        Never raises: a failure here is something the engineer fixes on this
        page, so it is reported beside the form that caused it.

        Goes through ``session.prepare``, which clones with the shared
        library's ``run_clone``. Not ``git_service.clone_repo``, whose
        ``_inject_pat`` is unconditional and turns a tokenless ``file://``
        path into a host.
        """
        profile = load_or_start(profile_name_)
        if profile is None:
            return (f"Saved {nickname}, but no profile is open.", "")
        try:
            cfg = bsettings.to_config(profile, nickname, store=store)
        except SettingsError as exc:
            return (f"Saved {nickname}, but it cannot be cloned yet.", str(exc))

        pats = {dep.resource_name:
                bsettings.dependency_secret(profile, nickname,
                                            dep.resource_name, store)
                for dep in cfg.dependencies}
        try:
            result = bsession.prepare(cfg, dependency_pats=pats)
        except Exception as exc:              # git, network, credential, path
            # git puts the whole remote URL in its errors, credential and all.
            detail = str(exc)
            if cfg.pat:
                detail = detail.replace(cfg.pat, "***")
            return (f"Saved {nickname}, but the repository could not be opened.",
                    detail)

        # A clone that finished but could not be given a commit identity is
        # not ready, however complete it looks: git refuses to commit without
        # one, so the engineer's first patch fails with "Committer identity
        # unknown", a message pointing nowhere near the clone that caused it.
        if result.get("identity_problems"):
            return (f"{nickname}: cloned, but git would not accept a commit "
                    f"identity for it, so patching will fail.",
                    "; ".join(result["identity_problems"]))

        # Record the mechanism the probe proved, so no later fetch has to
        # rediscover it and none of them can disagree with the clone.
        proven = str(result.get("auth") or "")
        if proven:
            entry = profile.find(nickname)
            if entry.auth != proven:
                bsettings.save_profile(bsettings.upsert_model(
                    profile, replace(entry, auth=proven)))

        summary = ["cloned" if result.get("first_use") else "already on disk"]
        if result.get("commit"):
            summary.append(f"at {result['commit']}")
        if result.get("aird_file"):
            summary.append(f"model {result['aird_file']}")
        if proven:
            summary.append("credential sent "
                           + ("in the URL" if proven == "basic"
                              else "as a PRIVATE-TOKEN header"))
        cloned_libs = result.get("dependencies_cloned") or []
        if cloned_libs:
            summary.append(f"{len(cloned_libs)} librar"
                           f"{'y' if len(cloned_libs) == 1 else 'ies'}")
        return (f"{nickname} is ready — {', '.join(summary)}.", "")

    async def verify_model(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        nickname = str(form.get("nickname") or "").strip()
        profile = load_or_start(current["name"])
        assert profile is not None
        try:
            cfg = bsettings.to_config(profile, nickname, store=store)
        except SettingsError as exc:
            return page(request, f"Cannot verify {nickname}.", str(exc))
        pats = {dep.resource_name:
                bsettings.dependency_secret(profile, nickname,
                                            dep.resource_name, store)
                for dep in cfg.dependencies}
        steps = bsession.verify(cfg, dependency_pats=pats)
        worst = [s for s in steps if s["ok"] is False]
        headline = (f"{nickname}: {len(worst)} problem"
                    f"{'' if len(worst) == 1 else 's'} found."
                    if worst else f"{nickname} passed every check that could "
                                  f"be made.")
        return page(request, headline, edit="", verified=(nickname, steps))

    async def delete_model(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        nickname = str(form.get("nickname") or "").strip()
        profile = load_or_start(current["name"])
        assert profile is not None
        try:
            bsettings.save_profile(
                bsettings.remove_model(profile, nickname, store=store))
        except SettingsError as exc:
            return page(request, f"Not removed: {exc}")
        # The clone is deliberately left on disk. It is the engineer's model,
        # not this tool's cache, removing an entry from a list should never
        # delete work, and it may hold committed patches that were never
        # pushed.
        return page(request, f"Removed {nickname} from this profile. Its clone "
                             f"was left on disk.")

    async def add_library(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        nickname = str(form.get("nickname") or "").strip()
        profile = load_or_start(current["name"])
        assert profile is not None
        entry = _named_model(profile, nickname)
        if entry is None:
            return page(request, f"There is no model called {nickname!r} in "
                                 f"this profile.")
        resource = str(form.get("resource_name") or "").strip()
        if not resource:
            return page(request, "A library needs the resource name the model's "
                                 "cross-references use.", edit=nickname)
        dep = Dependency(
            resource_name=resource,
            remote_url=str(form.get("remote_url") or "").strip(),
            branch=str(form.get("branch") or "main").strip() or "main",
        )
        try:
            grown = bsettings.upsert_model(
                profile, replace(entry,
                                 dependencies=entry.dependencies + (dep,)))
            bsettings.save_profile(grown)
        except SettingsError as exc:
            return page(request, f"Not added: {exc}", edit=nickname)

        supplied = str(form.get("pat") or "").strip()
        if supplied:
            store.set(credential_account(
                profile.name, dependency_account(nickname, resource)), supplied)

        message, detail = _clone(profile.name, nickname)
        return page(request, f"Added {resource}. {message}", detail,
                    edit=nickname)

    async def delete_library(request: Request):
        denied = guard(request) or _needs_profile(request)
        if denied:
            return denied
        form = await request.form()
        nickname = str(form.get("nickname") or "").strip()
        resource = str(form.get("resource_name") or "").strip()
        profile = load_or_start(current["name"])
        assert profile is not None
        try:
            bsettings.save_profile(bsettings.remove_dependency(
                profile, nickname, resource, store=store))
        except SettingsError as exc:
            return page(request, f"Not removed: {exc}", edit=nickname)
        return page(request, f"Removed the library {resource} from {nickname}. "
                             f"Its clone was left on disk.", edit=nickname)

    app = Starlette(routes=[
        Route("/", index),
        Route("/profile/use", use_profile, methods=["POST"]),
        Route("/profile/new", new_profile, methods=["POST"]),
        Route("/profile/rename", rename_open_profile, methods=["POST"]),
        Route("/identity", save_identity, methods=["POST"]),
        Route("/model", save_model, methods=["POST"]),
        Route("/model/verify", verify_model, methods=["POST"]),
        Route("/model/delete", delete_model, methods=["POST"]),
        Route("/library", add_library, methods=["POST"]),
        Route("/library/delete", delete_library, methods=["POST"]),
    ])
    app.state.token = token
    app.state.profile_name = current
    app.state.store = store
    app.state.port = port
    return app


#: The setup page already serving on a thread, or None. Keyed by profile name
#: inside the library's idempotency check, so asking twice for the same profile
#: returns the link already handed over rather than starting a second server on
#: a second port with half the links pointing at a thread nobody kept.
_BACKGROUND: Optional[setup_shell.Loopback] = None


def serve_in_background(profile_name: str = "") -> str:
    """Start the setup page on a thread and return the link to hand over.

    The flow this exists for: the engineer starts their agent, the agent gives
    them a link, they set themselves up there, and the agent then reads its own
    configuration. Nothing has to be configured before the agent runs, which is
    the opposite of pasting a command line into a harness and is how an
    engineer actually arrives.

    **The link carries a one-time token, so it passes through the agent.**
    Stated rather than hidden: it grants this page, on this machine's loopback
    interface, for this run of this process. It is not a credential for
    anything off this machine, and no stored token is ever returned through it.
    """
    global _BACKGROUND
    running = setup_shell.serve_in_background(
        lambda token, port: build_app(profile_name, token=token, port=port),
        thread_name="capella-fabric-setup",
        key=profile_name,
        existing=_BACKGROUND,
    )
    _BACKGROUND = running
    # Returned only once the port is actually accepting connections.
    # serve_in_background returns as soon as the thread starts, which is before
    # uvicorn has bound the socket, and a link handed over in that window gives
    # the engineer a page that fails once and works on refresh -- reported as
    # flaky rather than as a race.
    setup_shell.wait_until_serving(running)
    return running.url


def serve(profile_name: str = "", *, open_browser: bool = True) -> int:
    """Run the setup page until the engineer stops it. Returns an exit code.

    Everything is printed to stderr, which the shared helper also does and for
    the same reason: under the stdio transport stdout is the JSON-RPC channel,
    and a banner on it corrupts the protocol in a way that looks like the
    server crashing.
    """
    return setup_shell.serve_foreground(
        lambda token, port: build_app(profile_name, token=token, port=port),
        banner="Capella Fabric — setup",
        open_browser=open_browser,
    )
