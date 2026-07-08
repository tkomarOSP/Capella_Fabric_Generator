# Capella Fabric Generator

**Bridge Capella MBSE models to generative AI — web app and MCP server.**

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)

The Capella Fabric Generator converts Capella systems engineering models into structured YAML "fabric" representations that any LLM (Claude, ChatGPT, Gemini, Copilot, Grok) can read and reason over. It also supports writing model changes back — enabling AI-assisted MBSE workflows.

Two complementary interfaces are provided:

| Interface | Port | Use Case |
|-----------|------|----------|
| **Web Application** (Flask) | 8000 | Interactive upload, browse by phase/name, download YAML |
| **MCP Server** (FastMCP) | 8001 | Claude and other MCP clients — full read/write access via GitHub repos |

---

## Features

- Browse Capella model objects by ARCADIA phase (OA / SA / LA / PA) and type
- Generate YAML fabric consumable by any LLM
- Apply declarative patches back to the model with auto-injected Capella types
- Full git integration — clone model repos from GitHub, commit changes, push back
- Support for multi-library models (cross-reference dependency repos)
- Quality scan: detect unnamed elements and unallocated functions
- Session-based architecture — clean temp directories, no persistent model state on server

---

## Quick Start

### Prerequisites

- Python 3.11+
- [Capella_Tools](https://github.com/tkSDISW/Capella_Tools) checked out alongside this repo (or set `CAPELLA_TOOLS_PATH`)
- A Capella model in a GitHub repository (for MCP use) or as a `.zip` archive (for web use)

```bash
git clone https://github.com/tkomarOSP/Capella_Fabric_Generator
cd Capella_Fabric_Generator
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # set SECRET_KEY and CAPELLA_TOOLS_PATH
```

### Web Application

```bash
python app.py          # development
# or
gunicorn wsgi:app -w 2 -b 0.0.0.0:8000   # production
```

Open `http://localhost:8000/` → upload a `.zip` Capella archive → browse or enter UUIDs → download YAML fabric.  
In-app documentation is available at `/help`.

### MCP Server

```bash
python mcp_server.py   # listens on localhost:8001
```

Configure Claude Desktop (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "capella-fabric": {
      "url": "https://fabric.innovatingwithcapella.com/mcp"
    }
  }
}
```

Or point to a local instance: `"url": "http://localhost:8001/mcp"`.

---

## MCP Tools Reference

All tools (except `list_object_types` and `clone_capella_repo`) require a `session_id` obtained from `clone_capella_repo`.

| Tool | Description |
|------|-------------|
| `clone_capella_repo` | Clone a GitHub Capella model repo; returns `session_id`. Accepts `branch`, `include_realized`, `include_realizing`. |
| `add_dependency_repo` | Register a library repo the main model references (call before browsing multi-library models). |
| `list_object_types` | Return all valid phase / object_type combinations. No session required. |
| `browse_model` | List all objects of a given type within a phase (e.g., all Functions in SA). |
| `search_model_objects` | Name substring search within a phase/type (case-insensitive). |
| `resolve_model_uuids` | Resolve a list of UUIDs to model objects and cache them for fabric generation. |
| `generate_fabric` | Generate YAML fabric for the resolved UUIDs in the session. |
| `apply_model_patch` | Apply a declarative YAML patch to the model, save, and git-commit. |
| `push_model_changes` | Push committed changes back to the remote GitHub repository. |
| `verify_model` | Scan a phase for quality issues: unnamed elements and unallocated functions. |
| `cleanup_session` | Delete the cloned repo and all temp files for the session. |

**Typical read workflow:** `clone_capella_repo` → (`add_dependency_repo`) → `list_object_types` → `browse_model` / `search_model_objects` → `resolve_model_uuids` → `generate_fabric` → `cleanup_session`

**Typical write workflow:** clone → browse/search → `apply_model_patch` → `verify_model` → `push_model_changes` → cleanup

---

## Patch YAML Conventions

`apply_model_patch` uses [py-capellambse's declarative format](https://dsd-dbs.github.io/py-capellambse/). Target elements with `!uuid`, use `extend:` to add children and `set:` to update attributes. Use `promise_id:` / `!promise` for forward references within the same patch.

The server **pre-processes patch YAML** and auto-injects three categories of `_type` — you may omit them entirely:

### 1. Function and activity types

Derived from the parent object's Capella phase:

| Parent phase | `extend:` attribute | Auto-injected `_type` |
|---|---|---|
| OA | `activities`, `owned_activities` | `OperationalActivity` |
| SA | `functions`, `owned_functions` | `SystemFunction` |
| LA | `functions`, `owned_functions` | `LogicalFunction` |
| PA | `functions`, `owned_functions` | `PhysicalFunction` |

```yaml
- parent: !uuid <sa-component-uuid>
  extend:
    functions:
      - name: Process Sensor Input    # _type: SystemFunction injected automatically
      - name: Validate Data
```

### 2. Component types

Derived from the parent object's Capella phase. Without `_type`, capellambse creates malformed `Part` objects instead of component elements.

| Parent phase | `extend:` attribute | Auto-injected `_type` |
|---|---|---|
| SA | `components` | `SystemComponent` |
| LA | `components` | `LogicalComponent` |
| PA | `owned_components` | `PhysicalComponent` |

```yaml
- parent: !uuid <pa-component-uuid>
  extend:
    owned_components:
      - name: Steering Cylinder LH Node    # _type: PhysicalComponent injected
        nature: NODE                        # NODE or BEHAVIOR — set explicitly
```

### 3. Property value types

Derived from the Python value type after YAML parsing. The standard pattern is one `PropertyValueGroup` per property with named children (`units`, `value`, `max_value`, `min_value`, `nominal_value`):

| Python value type | Auto-injected `_type` |
|---|---|
| string — e.g. `kg`, `W` | `StringPropertyValue` |
| float — e.g. `12.5` | `FloatPropertyValue` |
| integer — e.g. `100` | `IntegerPropertyValue` |
| boolean — `true` / `false` | `BooleanPropertyValue` |
| group container | `PropertyValueGroup` |

```yaml
- parent: !uuid <component-uuid>
  extend:
    property_value_groups:
      - name: Mass                  # PropertyValueGroup _type injected
        property_values:
          - name: units
            value: kg               # str → StringPropertyValue
          - name: value
            value: 12.5             # float → FloatPropertyValue
          - name: max_value
            value: 15.0
      - name: Power
        property_values:
          - name: units
            value: W
          - name: value
            value: 45               # int → IntegerPropertyValue
          - name: nominal_value
            value: 40
```

Explicit `_type` values in the patch are always respected; auto-injection only applies when `_type` is absent.

---

## Claude Integration — System Prompt

The full system prompt for Claude (or any MCP-capable client) is maintained at
[docs/Capella_Fabric_Generator_System_Prompt_v1.md](docs/Capella_Fabric_Generator_System_Prompt_v1.md).
Copy it into your Claude Project Instructions, then fill in your GitHub PAT and
model repo URL in the **Section 1 — User Configuration** block at the top.

It also has a live hosted copy that always reflects the current revision:
**https://app.cartenza.ai/system-prompt/system-engineer-capella**

---

## Web Application

The Flask web app provides an interactive interface for engineers who prefer a browser workflow:

1. **Upload** — drag-and-drop or browse for a Capella `.zip` project archive
2. **Browse or Enter UUIDs** — two entry modes:
   - *Browse by name*: select phase → object type → name search → check boxes
   - *Direct UUID entry*: paste one or more UUIDs
3. **Generate** — produces a YAML fabric file
4. **Download** — save the `.yaml` for use with any LLM

In-app help with workflow documentation is available at `/help`.

---

## Deployment

Full step-by-step instructions for Ubuntu 22.04 are in [deploy/README.md](deploy/README.md), covering:

- System packages and Python 3.11 venv
- systemd services for both the Flask app and MCP server (`deploy/*.service`)
- nginx reverse proxy configs for both services (`deploy/nginx*.conf`)
- Automatic session cleanup via cron (`deploy/cleanup_sessions.sh`)

The reference deployment runs at:
- `https://fabric.innovatingwithcapella.com/` — web application
- `https://mcp.innovatingwithcapella.com/mcp` — MCP server

---

## Requirements

| Package | Version | Purpose |
|---------|---------|---------|
| `flask` | ≥ 3.0 | Web application framework |
| `capellambse` | ≥ 0.6 | Capella model loader and declarative patch engine |
| `gunicorn` | ≥ 21.0 | WSGI server for production deployment |
| `mcp[cli]` | ≥ 1.0 | Model Context Protocol server (FastMCP) |
| `gitpython` | ≥ 3.1 | GitHub repo cloning and git operations |

Also required: [Capella_Tools](https://github.com/tkSDISW/Capella_Tools) — Capella metamodel utilities.  
Set `CAPELLA_TOOLS_PATH` environment variable to its location (default: `C:\apps\.metadata\Capella_Tools`).

---

## License & Attribution

Copyright 2024–2026 Open Sun Power, LLC  
Licensed under the [Apache License 2.0](LICENSE)

**Third-party components:**
- [py-capellambse](https://github.com/DSD-DBS/py-capellambse) — DB InfraGO AG — Apache 2.0
- [OpenSans](https://fonts.google.com/specimen/Open+Sans) font — SIL Open Font License 1.1
