# Droplet Deployment Guide

## Prerequisites

- Ubuntu 22.04 LTS droplet (2 GB RAM minimum recommended)
- SSH access as root or a sudo user
- Your `Capella_Tools` directory ready to copy across

---

## 1. Install system packages

```bash
apt update && apt upgrade -y
add-apt-repository ppa:deadsnakes/ppa -y
apt update
apt install -y python3.11 python3.11-venv python3-pip nginx git
```

---

## 2. Clone the repository

```bash
git clone https://github.com/tkomarOSP/Capella_Fabric_Generator /opt/capella_fabric_generator
cd /opt/capella_fabric_generator
```

---

## 3. Install Capella_Tools

Clone directly from GitHub:

```bash
git clone https://github.com/tkSDISW/Capella_Tools /opt/capella_tools
```

Clones `master` by default — `feature/capella-7.0.1-support` (an older branch this used to pin to) has since been merged into `master`, which now also carries the data-modeling rendering support (Class/Property/Association, Exchange Item Element cardinality fields) added 2026-08-11. If an existing deployment's `/opt/capella_tools` is still on that feature branch, switch it: `cd /opt/capella_tools && git checkout master && git pull`.

---

## 4. Create the virtual environment and install dependencies

```bash
cd /opt/capella_fabric_generator
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## 5. Configure environment variables

```bash
cp .env.example .env
nano .env
```

Set:
- `SECRET_KEY` — a long random string (e.g. `python3 -c "import secrets; print(secrets.token_hex(32))"`)
- `CAPELLA_TOOLS_PATH` — `/opt/capella_tools` (or wherever you copied it)

---

## 6. Create the log directory

```bash
mkdir -p /var/log/capella-fabric
chown www-data:www-data /var/log/capella-fabric
```

Give `www-data` ownership of the app directory:

```bash
chown -R www-data:www-data /opt/capella_fabric_generator
```

---

## 7. Install and start the systemd service

```bash
cp deploy/capella-fabric.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable capella-fabric
systemctl start capella-fabric
systemctl status capella-fabric   # should show "active (running)"
```

---

## 8. Configure nginx

```bash
cp deploy/nginx.conf /etc/nginx/sites-available/capella-fabric
ln -sf /etc/nginx/sites-available/capella-fabric /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default   # remove default placeholder
nginx -t                                  # verify config
systemctl reload nginx
```

The app will now be reachable at `http://165.22.188.83/`.

---

## Updating the app (git pull workflow)

```bash
cd /opt/capella_fabric_generator
git pull
or if need to discharge changes git reset --hard origin/master && git pull
.venv/bin/pip install -r requirements.txt   # pick up any new deps
systemctl restart capella-fabric
```

---

## Logs

```bash
journalctl -u capella-fabric -f          # gunicorn process logs
tail -f /var/log/capella-fabric/access.log
tail -f /var/log/capella-fabric/error.log
```

---

## MCP Server Setup

The MCP server runs alongside the web app on the same droplet — port 8001, proxied through nginx at `mcp.innovatingwithcapella.com`. It exposes the same browse/resolve/generate workflow to Claude via the Model Context Protocol.

### Prerequisites

Complete steps 1–6 above (the web app must already be deployed).

---

### MCP 1. Install the MCP systemd service

```bash
cp deploy/capella-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable capella-mcp
systemctl start capella-mcp
systemctl status capella-mcp   # should show "active (running)"
```

Verify the server is listening:

```bash
ss -tlnp | grep 8001
```

---

### MCP 2. Install the nginx config

```bash
cp deploy/nginx_mcp.conf /etc/nginx/sites-available/capella-mcp
ln -sf /etc/nginx/sites-available/capella-mcp /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

At this point the server is reachable over HTTP only. Proceed to get SSL before connecting any client.

---

### MCP 3. Add DNS A record

In your DNS provider, add an **A record**:

| Name | Type | Value |
|---|---|---|
| `mcp` | A | `165.22.188.83` |

Wait for propagation, then verify:

```bash
nslookup mcp.innovatingwithcapella.com
```

The response should return the droplet IP.

---

### MCP 4. Obtain SSL certificate

```bash
certbot --nginx -d mcp.innovatingwithcapella.com
```

When prompted for redirect, choose **2 (Redirect)** to force HTTPS. Certbot will update the nginx config and reload nginx automatically.

Test from a local machine:

```bash
curl -s -X POST https://mcp.innovatingwithcapella.com/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
```

A response containing `"Capella Fabric Generator"` confirms the server is up.

---

### MCP 5. Install the session cleanup cron

Sessions accumulate in `/tmp/capella_fabric/`. The cleanup job removes sessions older than 4 hours.

```bash
chmod +x deploy/cleanup_sessions.sh
cp deploy/capella-sessions-cron /etc/cron.d/capella-sessions
chmod 644 /etc/cron.d/capella-sessions
```

Cleanup activity is logged to `/var/log/capella-fabric/session-cleanup.log`.
To adjust the TTL, edit `MAXAGE_HOURS` in `deploy/cleanup_sessions.sh`.

---

### MCP 6. Connect from claude.ai

1. Go to **claude.ai → Settings → Customize → Connectors**
2. Click **Add connector**
3. Enter the URL: `https://mcp.innovatingwithcapella.com/mcp`
4. Click **Refresh tool list** — you should see 12 tools

**Tools available:**

| Tool | Purpose |
|---|---|
| `begin_connect` | Start a credential-free connect (Cartenza co-deploy only — see below) |
| `clone_capella_repo` | Clone a GitHub repo containing a Capella model |
| `add_dependency_repo` | Register a library repo the main model depends on |
| `list_object_types` | Discover valid phase/object_type combinations |
| `browse_model` | List all objects of a given type in a phase |
| `search_model_objects` | Search objects by name (substring match) |
| `resolve_model_uuids` | Resolve UUIDs to model objects |
| `generate_fabric` | Generate YAML fabric for resolved UUIDs |
| `apply_model_patch` | Apply a declarative YAML patch, save, and git-commit |
| `verify_model` | Scan a phase for quality issues (missing names, unallocated functions) |
| `push_model_changes` | Push committed changes back to the remote GitHub repository |
| `cleanup_session` | Delete cloned repos and temp files |

**Typical read workflow:**

```
1. clone_capella_repo(repo_url, github_pat, branch?)
2. add_dependency_repo(session_id, lib_url, github_pat, resource_name)  ← if model has libraries
3. list_object_types()                                                   ← discover valid types
4. browse_model(session_id, phase, object_type)                         ← find objects
5. resolve_model_uuids(session_id, uuids)                               ← select objects
6. generate_fabric(session_id)                                          ← produce YAML
7. cleanup_session(session_id)                                          ← release disk space
```

**Typical write workflow:**

```
1. clone_capella_repo(repo_url, github_pat, branch?)
2. browse_model / search_model_objects                                  ← confirm target UUID(s)
3. apply_model_patch(session_id, patch_yaml, commit_message)           ← apply, save, commit
4. verify_model(session_id, phase)                                      ← scan for quality issues
5. push_model_changes(session_id)                                       ← push to GitHub
6. cleanup_session(session_id)                                          ← release disk space
```

See the main [README.md](../README.md#patch-yaml-conventions) for `apply_model_patch`'s full YAML conventions (auto-injected `_type`, data-modeling support, cardinality defaults, and what's not supported).

---

## Co-deploying the MCP server on the Cartenza droplet (optional)

Everything above deploys the MCP server **standalone**: `github_pat` is passed
directly on every call and there is no database. That is the supported default
and nothing below is required for it.

The optional variant co-locates *only the MCP server* (not the Flask web app)
on the Cartenza droplet, where `kp-auth` is installed. That turns on the
credential-free path from `cousin_back_log/note-0086`: `begin_connect` returns a
plain non-secret URL, the human authorizes in a browser, and
`clone_capella_repo(connect_code=...)` clones with no PAT in any tool argument.
`kp-auth` is an optional import — the same commit runs both ways.

### C1. Install alongside the Cartenza services

```bash
git clone https://github.com/tkomarOSP/Capella_Fabric_Generator /opt/capella_fabric_generator
git clone https://github.com/tkSDISW/Capella_Tools /opt/capella_tools
cd /opt/capella_fabric_generator
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e /opt/knowledge_partner/kp/auth   # dependencies only -- see below
```

**The `pip install` alone is not enough — it installs no code.** `kp/auth/`
*is* the `auth` package (its modules sit directly inside), but its
`pyproject.toml` lives in that same directory and declares
`packages.find where = ["."] / include = ["auth*"]`. Building from
`/opt/knowledge_partner/kp/auth`, setuptools therefore looks for an `auth/`
subdirectory *inside* `kp/auth/`, finds nothing, and produces a valid but
empty wheel. pip reports success; `import auth` then fails with
`ModuleNotFoundError` for every user, root included.

The install is still worth running -- it pulls in SQLAlchemy, PyJWT and
cryptography, which the venv genuinely needs. The code itself is reached by
putting the package's parent directory on the path instead:

```
PYTHONPATH=/opt/knowledge_partner/kp
```

That goes in the env file below. `/opt/knowledge_partner/kp/` contains `auth/`
with an `__init__.py`, so `import auth.connect` resolves directly against the
source -- which is what "installs as a bare top-level `auth`, not `kp.auth`"
means in practice, and why `mcp_server.py`'s imports read
`from auth.connect import ...`.

Verify before going further, as the user systemd will actually run as:

```bash
sudo -u www-data PYTHONPATH=/opt/knowledge_partner/kp   /opt/capella_fabric_generator/.venv/bin/python -c "import auth.connect; print('kp-auth OK')"
```

### C2. Environment file

`deploy/capella-mcp.service` reads `EnvironmentFile=-/etc/capella-mcp.env`
(the `-` makes it optional, which is what keeps the standalone deployment
working unchanged). On the Cartenza droplet, create it:

```bash
cat > /etc/capella-mcp.env <<'EOF'
PYTHONPATH=/opt/knowledge_partner/kp
CARTENZA_DB_PATH=/var/lib/cartenza/cartenza.db
CARTENZA_CONNECT_SECRET_KEY=<same value kp-connect uses>
KP_CONNECT_BASE_URL=https://dev.connect.cartenza.ai
EOF
chmod 600 /etc/capella-mcp.env
chown www-data:www-data /etc/capella-mcp.env
```

**`CARTENZA_CONNECT_SECRET_KEY` must match kp-connect's byte for byte.** A
mismatch does not fail at startup — it fails much later, when a connect code is
finally redeemed, as an undecryptable credential. This exact class of
cross-service secret drift has bitten this project repeatedly; copy the value
from the running kp-connect unit rather than generating a new one.

`www-data` also needs read/write on `CARTENZA_DB_PATH` and its directory (SQLite
writes a `-wal`/`-shm` sibling next to the file).

### C3. nginx and DNS

Use `deploy/nginx_mcp_cartenza.conf` instead of `nginx_mcp.conf`, add a DNS A
record for `dev.capella.cartenza.ai` pointing at the Cartenza droplet, then
`certbot --nginx -d dev.capella.cartenza.ai`.

`mcp_server.py`'s `allowed_hosts` already lists `capella.cartenza.ai` and
`dev.capella.cartenza.ai`. Any hostname *not* in that list returns 421 on every
request no matter what nginx says — it's a source-code list, not config.

### C4. Register the model repo on the Cartenza side

`AgentScope` is repo-agnostic, so a Capella model repo is an ordinary Cartenza
repo. On the Cartenza site: add it at `/onboarding/repos`, then scope it to an
agent at `/onboarding/agents`. `begin_connect` refuses anything outside that
registered scope, which is what makes the URL it hands back safe to print.

Prefer **Connect with GitHub** over pasting a PAT on the connect page: one
GitHub authorization also covers the model's library repos, so
`add_dependency_repo` then needs no credential of its own. A pasted PAT is a
single-use snapshot, cleared when the code is consumed, so each dependency
would need its own connect code.

---

### MCP Logs

```bash
journalctl -u capella-mcp -f
tail -f /var/log/capella-fabric/mcp-access.log
tail -f /var/log/capella-fabric/mcp-error.log
tail -f /var/log/capella-fabric/session-cleanup.log
```

---

### MCP Updating

```bash
cd /opt/capella_fabric_generator
git pull
cd /opt/capella_tools
git pull
systemctl restart capella-mcp
```

`Capella_Tools` holds the `generate_fabric` rendering logic (`capellambse_yaml_manager.py`) — a fix landing only in `Capella_Fabric_Generator` (e.g. `capella_service.py`, `mcp_server.py`) doesn't need the second `git pull`, but many fixes span both repos, so pulling both by default is safer than checking which repo actually changed each time.

If `nginx_mcp.conf` changed, also copy it and reload nginx:

```bash
cp deploy/nginx_mcp.conf /etc/nginx/sites-available/capella-mcp
nginx -t && systemctl reload nginx
```
