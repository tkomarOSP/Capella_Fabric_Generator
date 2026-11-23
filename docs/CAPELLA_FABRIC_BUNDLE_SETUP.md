# Capella Fabric, local — setting it up in Simcenter AI Agents

A local, single-user MCP server that gives an agent read and write access to
the Capella models in your own git repositories. No account, no hosted server,
no registration, and no network calls except to your own git remote and — the
first time you render a generated view — to the npm registry.

**You set it up by running the agent.** Start it, ask it to set up your model,
and it gives you a link to a page on your own machine. You fill that in once;
the agent reads it from then on. Nothing is typed into the harness except the
server's command line.

Two things go into the harness, and they are below ready to paste: **the MCP
server** and **the agent instructions**.

This is the same shape as the Cartenza Engineering Notebook, deliberately —
one setup page, one profile, one permanent clone per repository, credentials in
the operating system's store. If you have set that up, this will be familiar.
The two servers share no state, no profiles and no credentials: separate
directory, separate keychain entries, separate everything.

---

## 1. The MCP server

Transport is **stdio**, which is what Simcenter AI Agents runs locally.

| | |
|---|---|
| Command | the full path to `python.exe`, then the full path to `run_capella_fabric.py` |
| Working directory | not needed |
| Environment | `CAPELLA_TOOLS_PATH` — see below, and read it before deciding you can skip it |
| Transport | stdio (no host, no port, nothing listening) |

```json
{
  "command": "C:\\apps\\Python_3_12\\python.exe",
  "args": ["C:\\path\\to\\Capella_Fabric_Generator\\run_capella_fabric.py"],
  "env": {
    "CAPELLA_TOOLS_PATH": "C:\\path\\to\\Capella_Tools"
  }
}
```

**Spell out the interpreter.** A bare `python` on Windows commonly resolves to
the Microsoft Store stub, which answers *"Python was not found"* and looks like
a broken install.

**Use the launcher script rather than `python stdio_server.py`.** The launcher
puts this checkout at the *front* of the import path rather than the end, which
matters because an editable install elsewhere on the machine can otherwise win
— and the symptom is not an `ImportError` but a different checkout imported
silently, which has genuinely happened here. The launcher is also the outermost
place that can still print a sentence when a prerequisite is missing; without
it, a missing library reaches the harness as a bare transport error.

Set the server's **timeout to 900** if the harness offers one. Most calls take
one to three seconds, but a clone of a real model takes tens of seconds and a
first generated view downloads a layout engine.

### Requirements on the machine

- **Python 3.11 or newer.**
- **`git` on `PATH`.** The server commits and pushes by running git directly.
  It checks at start-up and says so rather than failing later, call by call.
- **`node` on `PATH`** — needed for a *generated* view, which is the usual way
  to show a single component. Measured on 2026-11-22: with node removed from
  `PATH`, `render_diagram_view` on a component fails and the entire message is
  the word `node`, while the same call with node present returns an 84 KB page.
  An **authored** diagram still renders perfectly without it (32 KB, `layout:
  stored`), because that reads positions saved in the model and needs no layout
  engine at all. So node is not optional-but-nice; it is the difference between
  two thirds of this feature working and all of it.
- **Python packages:** `mcp[cli]`, `capellambse`,
  `capellambse-context-diagrams==0.8.2`, `svgwrite`, `PyYAML`, `gitpython`,
  `keyring`, `starlette`, `uvicorn`. Flask and gunicorn are in this
  repository's `requirements.txt` for the *web* application and are not needed
  here.
- **The `mcp_desktop` foundation library, as a second checkout.** Clone
  `capella_fabric_desktop` **beside** this repository, so that
  `<parent>\capella_fabric_desktop\mcp_desktop\` sits next to
  `<parent>\Capella_Fabric_Generator\`. Nothing to configure if it is there.

  It holds the state root, the profile and credential stores, the git layer,
  the loopback setup page shell and the page server — shared with the Cartenza
  Engineering Notebook so that there is one implementation of each rather than
  two that drift. If it is missing, the server exits at start-up with a message
  naming the three ways to supply it.

  Two alternatives to the sibling clone: set `MCP_DESKTOP_HOME` to the
  directory holding the `mcp_desktop` package, or `pip install` that directory,
  after which the lookup is skipped entirely.

- **`Capella_Tools`, as a third checkout, and you almost certainly have to set
  `CAPELLA_TOOLS_PATH`.** This is the one prerequisite that fails *silently*,
  so it has its own section.

### `CAPELLA_TOOLS_PATH`, and why a wrong one is worse than a missing one

`capella_service.py` reads `CAPELLA_TOOLS_PATH` and falls back to a hard-coded
`C:\apps\.metadata\Capella_Tools`. Set the variable to wherever you actually
keep that repository.

If it is missing altogether, you get an honest `ModuleNotFoundError` at
start-up. The bad case is a `Capella_Tools` that exists but is **old**, because
nothing checks and nothing raises. Measured on this machine on 2026-11-22,
where two checkouts existed and the hard-coded default was 24 commits behind
the maintained one. Same model, same commit, same server; the only difference
was which copy was on the import path:

| | stale copy | current copy |
|---|---|---|
| `generate_fabric` output | 607,758 chars | 595,899 chars |
| Parses as YAML | **no** — `ScannerError` on an unquoted name containing a colon | yes |
| `is_actor` on components | **absent** | present, 74 times |
| Exchange endpoints named | `ref_uuid` and `ref__uuid` | `source_component_uuid` / `target_component_uuid` |
| uuids lost to duplicate keys | — (never got that far) | none, of 1,135 |

The tool reported success both times. A fabric is the thing an agent reads to
reason about your model, and `is_actor` is one of the three facts the server
teaches an agent to rely on at connect time — so a stale library does not break
loudly, it quietly makes the agent wrong about which components are actors and
which end of an exchange is the source.

**How to check, in ten seconds:** open the setup page, expand *Diagnostics —
what this server was launched with*, and read the **Capella_Tools** row. It
names the module file that was actually imported, not the directory that was
configured — which is the whole point, because a path cannot say which file
won. If it names a checkout you do not recognise or maintain, that is the
problem.

---

## 2. The agent instructions

**Shorter than you may expect, on purpose.** This server sends an agent its own
operating instructions when the client connects — which models exist, how to
choose a profile, what to do when nothing is set up, the three model facts that
look like defects and are not, and that nothing reaches the remote until a push.
So the instructions field does not need to repeat any of it, and should not: an
MCP client freezes both the tool list and the instructions at connect, and two
descriptions of the same thing is just two things to keep in step.

Paste this into the agent's instructions field. There are no parameters to
declare and no placeholders to fill in.

```text
You work on this engineer's Capella models through the local Capella Fabric
MCP server. It reads and writes real MBSE models in their own git
repositories, on this machine.

The server tells you how to use it when you connect: start with
list_my_scopes(), then open_model(). Follow what it says there — in
particular about choosing a profile, which is the engineer's decision and
never yours, and about the setup page being the only place a token is
entered. Never ask them for a repository URL or an access token yourself.

WORK IN THE MODEL'S OWN TERMS. Phases are OA, SA, LA and PA; say component,
function, exchange, capability and requirement. Call list_object_types()
before browsing rather than guessing a type name.

WHEN THEY ASK TO SEE SOMETHING, render it rather than describing it. Give
them the viewer_markdown link on its own line and never relay the page body.

BEFORE YOU WRITE, say what you are about to change and why. A patch commits
locally; nothing reaches the remote until push_model_changes succeeds, so do
not tell them their work is saved anywhere but this machine before that.
Call verify_model after a patch.

IF A WRITE IS REFUSED — read-only access, or a clone that is behind or has
diverged — say so and stop. Do not look for another way to write, and do not
reach for git: you have no git tools and that is deliberate. A divergence is
the engineer's to resolve, in Capella.

RECORD WHAT YOU LEARN where that engineer keeps it. This server holds models,
not notes.

Do not put credentials, tokens or API keys into a patch, a commit message or
anything you write.
```

If your harness does not show an MCP server's own instructions to the agent,
you will see it immediately — the agent will not know to call
`list_my_scopes()` first. In that case paste the contents of
`capella_bundle/instructions.py` into the instructions field as well.

---

## 3. The first run

1. **Start the agent and ask it to set up your model.** It calls
   `setup_model()` and gives you a link like `http://127.0.0.1:52461/?token=…`.
   On a machine without the web dependencies it hands you a command line to run
   instead; that still works.
2. **Open the link.** It works once, then the page remembers you. The page
   answers only to your own machine — a request arriving with any other `Host`
   header is refused — and it stops when the agent does.
3. **Fill in "This agent"** — a full name, the name commits are signed with,
   your own name, and what the agent is for.
4. **Add a model.** Nickname, repository URL, branch, and whether this agent
   may write to it. Supply an access token if the repository is private.

   **Check the branch.** A Capella repository is as likely to be on `master` as
   on `main`; the real `Residential_AC` used to verify this server is on
   `master`. A wrong branch is reported by **Verify** as *branch present: no*,
   with the branches that do exist listed, which is a much better experience
   than finding out during a clone.
5. **Press "Save and clone model".** This is the slow step and it belongs here
   rather than inside a tool call: a real model took **8.8 seconds** to clone,
   and a 13 MB `.aird` is normal. Anything wrong with the URL, the branch or
   the token shows up on the page rather than later inside the agent.
6. **Add library repositories, if the model depends on any.** Open the model
   again and use *Library repositories*. Declare them here even though the
   model is already cloned — they are fetched when you save. An unresolved
   cross-reference into a library that is not on disk fails in a way that reads
   as a fault in the main model.
7. **Press Verify.** Four questions, in the order they bite — see below.
8. **Tell the agent you are finished.** It will ask which profile you want to
   work in; that question is the whole of the handover, and there is nothing to
   paste or restart for it.

### Verify answers four questions

| Step | What it means |
|---|---|
| `repository reachable` | The URL resolves and the credential authenticates. A private repository with no token answers *"Repository not found"*, which reads like a wrong URL and is not. |
| `branch present` | That branch exists on the remote. Lists the ones that do if it does not. |
| `push access` | Proved with a `git push --dry-run` at a probe ref, which leaves the remote's ref list unchanged. Reported as *not checked* when there is no credential to try. |
| `Capella model present` | An `.aird` was found in the clone. *Not checked* before the first clone — two of these four can only be answered once something is on disk, and that is reported as an answer rather than hidden. |

### The access token

- Kept in your **operating system's credential store** — Windows Credential
  Manager, macOS Keychain, Secret Service on Linux. Never in a file this tool
  writes, and never anywhere the agent can read. No tool takes a credential as
  an argument or returns one.
- **Leave the field blank to keep the one already stored**, so changing a branch
  cannot silently delete a working credential.
- A public repository needs none.
- If the credential store cannot be reached, the page says so and offers a file
  instead, labelled *"file (not encrypted)"*, because that is what it is.

### What the agent may and may not do

The agent never clones, never sees a token, and cannot change which
repositories it can reach. Setting up is yours; using it is the agent's.

---

## 4. One writer, and what happens when that is not true

A clone here is a **single-writer working copy**. It fetches, it
fast-forwards, and that is all: **it will not reconcile a divergence**, and
that is a deliberate refusal rather than a missing feature.

The reason is specific to models. A knowledge repository is one file per entry,
so two people adding entries touch different files and a rebase replays them
with no interaction. A Capella model is **one file** — so two people always
touch it, and git merges it line-wise with nothing that understands the XML.
Two edits in different regions therefore rebase *cleanly, with no conflict*,
and the result is a file nobody has validated. Tried on the real model, two
disjoint patches merged cleanly and the model still opened; that is the
friendliest possible case and it proves the mechanism rather than the safety.
Nothing in git checks that a merged model is one Capella can open, and the
server would have reported "synced and reconciled" either way.

So the costs are asymmetric: a refused divergence costs a conversation, and a
silently invalid model is found by whoever next opens Capella. The server
refuses, names the model, and leaves the decision with you — your clone opens
in Capella, which is the only tool that can judge it.

Three ways a write is refused, and all three are stated with the model rather
than discovered at push time:

- **Access is `read`** — your declaration, and a setting on the setup page.
- **The clone is behind the remote.** A patch would be built on content
  somebody has already replaced, and the push is then rejected as a
  non-fast-forward *after* the patch has been written and reported as applied.
  Ask the agent to open the model again, which fetches, then patch.
- **The histories have diverged**, with commits on both sides.

---

## 5. More than one model, and more than one agent

**A profile** is a named set of models. **Which profile a session works in is
decided in the conversation**: the agent asks, you answer. So copying an agent
needs nothing — the command line is identical for every agent, and a brand-new
one can reach any profile on the machine from its first message.

A harness recipe configures an *agent definition*; which model you are working
on belongs to an *agent session*. Simcenter AI Agents does not separate the
two, so choosing in-session is what keeps a newly created agent from arriving
with nothing bound and no way to fix it from inside the conversation. Under
stdio the harness starts one server process per session, so two sessions of one
agent can be working in two different profiles.

---

## 6. Checking it works

1. Start the agent. It should list **16** tools.
2. Ask it what models it has. Expect it to ask which profile first, listing
   what each one holds, and to wait for your answer rather than picking one.
3. Ask it to open one. Expect a `session_id`, a `commit`, and `synced`. An open
   is **seconds** — 1.7 to 2.0 on a 13 MB model — because it fetches and
   fast-forwards an existing clone and never clones.
4. Ask it to browse `SA` / `Component`. First parse of the model, about 1.3
   seconds.
5. Ask it to show you a component. Expect an `http://127.0.0.1:…` link, not a
   path to copy. Open it: a self-contained page, which you can confirm by
   disconnecting the network first. A generated view took 2.4 seconds and 84
   KB; an authored diagram 32 KB with `layout: stored`.
6. Ask it for a fabric. **The server now parses its own output** before
   reporting success, so a stale `Capella_Tools` can no longer hand you 600 KB
   of YAML-shaped text that no parser will accept -- `generate_fabric` refuses
   instead, names the `capella_tools` file actually loaded, and says where it
   wrote the output so you can look at the offending line. If you see that
   message, the library is the first thing to check, not the model. Parsing it
   yourself is no longer the check that matters; what is still worth one look
   is that `is_actor` appears in the fabric, because a wrong library can drop a
   field without producing anything a parser objects to.
7. Ask it to write to a **read** model. It should refuse and say why.
8. Ask it to patch and push a **write** model on a throwaway branch, then clone
   that branch yourself and open it in Capella. "The push succeeded" and "the
   hash moved" are not the same claim as "a model Capella can open is on the
   remote".

A link lasts as long as the agent that produced it. The port is chosen fresh
each time the server starts, so a link saved from yesterday will not open; ask
for a new one.

---

## 7. If something is wrong

Open the setup page and expand **Diagnostics — what this server was launched
with**. It shows the profile in use, the interpreter, the command line, the
profile file, the credential store, git, the `capella_tools` module actually
imported, and which harness variables are set. Most of what follows is visible
there.

| Symptom | Cause |
|---|---|
| `process quit before initialization`, or a bare transport error | A prerequisite is missing and the message is on **stderr**. Read it: the launcher prints a sentence naming the real cause, including the three ways to point at `mcp_desktop`. |
| `No module named capella_tools` | `CAPELLA_TOOLS_PATH` is unset and the hard-coded default does not exist. Section 1. |
| A fabric that no YAML parser will read; an agent confidently wrong about which components are actors | A **stale** `Capella_Tools`. Check the Capella_Tools row in diagnostics — it names the file that was imported, which is not necessarily the checkout you maintain. |
| `render_diagram_view` fails and the whole message is `node` | `node` is not on `PATH`. Authored diagrams still work; generated views do not. |
| The first generated view is slow, or fails with no network | capellambse installs `elkjs` into `%LOCALAPPDATA%\capellambse\capellambse\Cache\elkjs` — an `npm install`, about 7.7 MB, once per version. It needs node and the npm registry the first time only. |
| The server exits at start-up | `git` is not on `PATH`. The stderr message names it. |
| The agent says no model is set up | Nothing configured for this profile, or the agent is bound to another one. Ask it to set up your model; check the profile name in diagnostics. |
| `has not been set up on this machine yet` naming a model | It is in your profile but was never cloned. Open the setup page, Edit it, and save. |
| `Repository not found` on a private repository | Almost always the token, not the URL — an unauthenticated private repository answers exactly this. |
| Verify says *branch present: no* and you are sure it exists | Read the branches it lists. `master` and `main` are both common in Capella repositories. |
| `synced: false` | The fetch failed. The model still opens and reads correctly; you may be looking at content behind the remote. Working offline is expected and is not an error. |
| A write refused as behind or diverged | Section 4. |
| Edits to the MCP server configuration never take effect | **An agent keeps its own copy.** In Simcenter AI Agents it is `%LOCALAPPDATA%\Siemens\sc-agents\config\recipes\<agent>.yaml`, in its `extensions:` block. Read it to see what is actually being run; create the agent again, or correct the recipe. |
| A long call appears to hang and the agent blames the server | Check the harness's own log before believing it. An agent cannot see the connection carrying its own thoughts, so a failure there reaches you as a confident story about the tool. |

---

## 8. Where things are kept

Everything is under your own user profile, and none of it is in a repository.

| | |
|---|---|
| Profiles | `%LOCALAPPDATA%\CapellaFabric\profiles\<profile>.json` |
| Model clones | `%LOCALAPPDATA%\CapellaFabric\models\<slug>\` |
| Library clones | `…\models\<slug>\deps\<library>\` |
| Rendered views and fabrics | `%LOCALAPPDATA%\CapellaFabric\views\<slug>\` |
| Access tokens | the operating system's credential store, service `capella-fabric-local` |
| ELK layout engine | `%LOCALAPPDATA%\capellambse\capellambse\Cache\elkjs\` |

On macOS and Linux the first four sit under `$XDG_STATE_HOME` or
`~/.local/state`. Nothing here is shared with the Cartenza Engineering
Notebook, which keeps its own under `Cartenza`.

**Not in `Documents` or `Desktop`, deliberately:** a corporate image routinely
redirects those into the company tenant, and the clone is a working copy of
your model.

**Views are kept outside the clone, and that is a security property rather
than tidiness.** Cloning an https repository leaves the credential in
`<clone>\.git\config`, and here that directory is never deleted — so the
loopback server is pointed at a sibling `views\` directory, and the setup page
refuses a configuration that would put views inside the model directory. A
rendered page is self-contained, so one copy out is all it costs.

**The clone is never deleted.** `close_model` forgets the session; removing a
model on the setup page removes it from the profile. Neither touches your
files. It is your model, not a cache.

---

## On packaging this as a bundle

These parts are what a Simcenter AI Agents bundle would carry, and the
`sysml-v1-to-v2-translator` is the precedent at 66 MB: a trimmed runtime, the
server, an agent recipe, a skill and a README. The equivalent here is larger,
because capellambse and lxml are not small, and because of two external
binaries that bundle has no equivalent of — **git**, and **node** for generated
views. Either require both on `PATH`, as this document does, or carry them and
accept roughly 50 MB for MinGit and more for node.

Four prerequisites do not exist yet and would each break a bundle silently:

1. **`Capella_Fabric_Generator` has no `pyproject.toml`** and is not
   pip-installable.
2. **`Capella_Tools` is a sibling clone on `sys.path`, not a dependency** —
   and its `setup.py` declares cairosvg, polarion, openai, ipywidgets and
   python-docx, none of which this server installs, because the repository is
   only path-added. A bundle has to package the two modules actually used
   (`capellambse_yaml_manager`, `Pub4C`) or fix that `install_requires`. The
   stale-copy finding in section 1 is the same loose coupling showing up a
   second way.
3. **No resource is loaded by `Path(__file__).parent`** today and it must stay
   that way — HTML here is built in process with `html.escape`, which is what
   keeps zipimport working.
4. **Nothing validates that a fabric parses**, which is how a 24-commit library
   skew produced unusable output and reported success.

Until those are closed, this document is the manual equivalent of the bundle,
and it works today.

---

*(c) Open Sun Power, LLC — Apache 2.0.*
