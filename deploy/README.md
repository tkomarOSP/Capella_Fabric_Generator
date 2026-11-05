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

## 4b. Pre-fetch the ELK layout binary

`render_diagram_view` generates a view around a model element using
`capellambse-context-diagrams`, which lays the graph out by calling ELK in a
subprocess. It looks for, in order: a cached binary, `deno`, or **it downloads
the binary from GitHub releases at request time**.

That last fallback works, but on a server it is the wrong moment for it:
measured at **19.9s for the first call** versus **0.3s warm**, plus an outbound
dependency while a user waits and an executable written by the service account.

Warm the cache once, as the service user, so the first real request is fast:

```bash
sudo -u www-data CAPELLA_TOOLS_PATH=/opt/capella_tools XDG_CACHE_HOME=/var/lib/capella-fabric/cache   /opt/capella_fabric_generator/.venv/bin/python -c "
from capellambse_context_diagrams import _elkjs
m = _elkjs.elk_manager
m.download_binary()
print('elk binary at', m.binary_path)
"
```

**`www-data` usually has no usable `HOME`**, so set `XDG_CACHE_HOME` to a
directory it owns and set the same value in the service environment file —
otherwise the cache lands somewhere unwritable and every call re-downloads or
fails. Create it first:

```bash
mkdir -p /var/lib/capella-fabric/cache
chown www-data:www-data /var/lib/capella-fabric/cache
```

Installing `deno` instead also works and skips the download entirely.

Verify the running service actually has it, not just the shell you tested in:

```bash
ls -la /var/lib/capella-fabric/cache/capellambse_context_diagrams/
tr '\0' '\n' < /proc/$(systemctl show capella-mcp -p MainPID --value)/environ | grep XDG_CACHE_HOME
```

On x86_64 Linux the file is `elk-v0.8.2-x86_64-unknown-linux-gnu`. The version
in that name is the pinned `capellambse-context-diagrams`, so **bumping the pin
invalidates the cache** and the next call downloads again — this step belongs in
any upgrade, not only the first install.

The download `chmod`s the binary to `0700`, owner only. Pre-fetching as root
rather than `www-data` therefore leaves a file the service cannot execute, and
that surfaces as a permission error on a user's first request rather than at
deploy time. The `sudo -u www-data` above is doing real work.

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
# or, if local changes need discarding: git reset --hard origin/master && git pull
.venv/bin/pip install -r requirements.txt   # pick up any new deps

# Restart BOTH services. capella_service.py is shared by the website and the
# MCP server, so restarting only capella-fabric leaves agents on the old code
# and on stale MCP `instructions` text -- with nothing failing to indicate it.
# Skips whichever unit this droplet doesn't run, instead of aborting on it.
for s in capella-fabric capella-mcp; do
  systemctl cat "$s" >/dev/null 2>&1 \
    && systemctl restart "$s" && echo "restarted $s"
done
systemctl is-active capella-fabric capella-mcp
```

Restarting drops in-memory sessions on both services (an opened model, a cloned
repo), so anyone mid-task will need to re-clone. Nothing is lost — models and
artifacts live in git — but time a restart accordingly rather than mid-session.

---

## Logs

```bash
journalctl -u capella-fabric -f          # gunicorn process logs
tail -f /var/log/capella-fabric/access.log
tail -f /var/log/capella-fabric/error.log
```

`capella_tools` emits a lot of `FutureWarning` lines about `.name` on relation
objects, which drown everything else. Filter them out when hunting a real fault:

```bash
journalctl -u capella-fabric --since "-30min" --no-pager | grep -v FutureWarning
```

### "Server error" on a large model, with no traceback

**No traceback is itself the diagnosis.** A gunicorn worker killed at its
deadline dies without raising, so nothing Python-level is logged — it looks like
a code fault but is a deadline. Confirm:

```bash
journalctl -u capella-fabric --since "-30min" --no-pager \
  | grep -iE "WORKER TIMEOUT|SIGKILL|worker exiting"
```

Two deadlines govern a request, and **the smaller one wins**: `--timeout` in
`capella-fabric.service` (gunicorn) and `proxy_read_timeout` in `nginx.conf`.
Both are set to 300s and should be changed together — they were once 30s
(gunicorn's default, unset) against 120s, which produced exactly this symptom.

If instead the log shows the worker dying without reaching any timeout, suspect
memory: two workers each holding a large model can exhaust a 4 GB droplet.

```bash
journalctl -k --since "-30min" | grep -i "out of memory\|oom-kill"
```

An OOM kill is a genuinely different problem — dropping to `--workers 1` is the
quick mitigation, a larger droplet the real one.

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

The config proxies **two** locations to port 8001, and both are required:

- `/mcp` — the Streamable HTTP endpoint every client talks to.
- `/view` — rendered diagram pages, handed to a human as a signed link
  (see *Rendered view links* below). Without this block, `render_diagram_view`
  still works and still returns a URL, and that URL 404s from nginx — which
  looks exactly like a code fault and is not one.

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

### MCP 5b. Rendered view links

`render_diagram_view` writes a self-contained HTML page into the session
directory and returns a **signed URL** to it, so an agent hands the human a
link instead of trying to relay the page body through the conversation. (It
tried that once: a 44 KB page could not be passed into another tool call and
the diagram ended up re-described in prose —
`After_Treatment_System_Notebook/Fabric_MCP_Issues` OBS-0023.)

Two optional environment variables, both of which can stay unset:

| Variable | Default | Why you might set it |
|---|---|---|
| `CAPELLA_VIEW_SECRET` | a fresh random value per process | Keeps existing links working across a restart. Unset is fine and no weaker — the session a link points into is gone by then anyway. |
| `CAPELLA_PUBLIC_BASE_URL` | taken from the request's `Host` + `X-Forwarded-Proto` | Only for a deployment fronted by something that rewrites those differently. |

Two things worth knowing before debugging a dead link:

- **A link dies with its session.** `MAXAGE_HOURS=4` in
  `deploy/cleanup_sessions.sh` removes the whole session directory, the
  rendered page with it, and the token's own expiry matches. A four-hour-old
  link 404ing is the design, not a fault. To keep a view, write it as an `html`
  artifact in the member's workspace repo and link to that instead.
- **The route is unauthenticated, and has to be.** FastMCP's `custom_route`
  cannot require authorization, so the capability is in the URL: an HMAC over
  session id, filename and expiry. It reaches nothing but a `view_*.html`
  inside that one session directory, and every rejection is the same bare 404.

To check the plumbing without a model, ask for a view that cannot exist. Both
answers are 404, and the body is what tells them apart:

```bash
curl -s https://capella.cartenza.ai/view/none/view_none.html
```

- `Not found` — the request reached the app. nginx is proxying `/view`
  correctly and the token check did its job. This is the good outcome.
- an HTML `404 Not Found` page — nginx answered it itself, so the
  `location /view` block is missing or nginx was not reloaded. A real signed
  URL will fail the same way, and it will look like a bug in the tool.

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

`deploy/capella-mcp.service` reads two env files, both optional (the leading
`-`, which is what keeps the standalone deployment working with neither
present — don't drop either one):

```
EnvironmentFile=-/etc/cartenza.env       # the fleet's shared values
EnvironmentFile=-/etc/capella-mcp.env    # capella's own, and wins where it overlaps
```

**Do not set a host or a shared secret in `/etc/capella-mcp.env`.** On the
Cartenza droplet, `/etc/cartenza.env` already supplies `CARTENZA_DB_PATH`,
`CARTENZA_CONNECT_SECRET_KEY`, `KP_CONNECT_BASE_URL` and `KP_VIEWER_BASE_URL`
to every kp unit, and this server now reads the same file. Nothing here needs
to name an environment, which is the point — see the warning below.

So on the Cartenza droplet this file holds only what is genuinely capella's:

```bash
cat > /etc/capella-mcp.env <<'EOF'
PYTHONPATH=/opt/knowledge_partner/kp
XDG_CACHE_HOME=/var/lib/capella-fabric/cache
EOF
chmod 600 /etc/capella-mcp.env
chown www-data:www-data /etc/capella-mcp.env
```

> **Why this section no longer names a hostname.** It used to hardcode
> `KP_CONNECT_BASE_URL=https://dev.connect.cartenza.ai` in this very recipe —
> labelled as the production droplet's. When the dev box was promoted to
> production, every kp service picked up the right hosts from the shared file
> and capella kept the stale one, because it was the one service holding its own
> copy. The symptom reached a user: a production model session handed them a
> connect link on `dev.connect.cartenza.ai` and asked them to authorize GitHub
> there. Nothing was exposed — both hostnames proxy to the same kp-connect
> process — but the fix is structural, not a corrected value. A per-service copy
> of a fleet-wide value is a drift waiting for the next box.

**On `CARTENZA_CONNECT_SECRET_KEY`:** it must match kp-connect's byte for byte,
and reading the shared file is now what guarantees that. A mismatch does not fail
at startup — it fails much later, when a connect code is finally redeemed, as an
undecryptable credential. If you ever do override it here, copy the value from
the running kp-connect unit rather than generating a new one; this class of
cross-service secret drift has bitten this project repeatedly.

`www-data` also needs read/write on `CARTENZA_DB_PATH` and its directory (SQLite
writes a `-wal`/`-shm` sibling next to the file), and read on
`/etc/cartenza.env`.

`CAPELLA_VIEW_SECRET` belongs in this file if you want view links to survive a
restart (see *MCP 5b*) — it is capella's own, not a fleet value. It is
independent of `CARTENZA_CONNECT_SECRET_KEY` on purpose and must not be set to
the same value — that one is a Fernet key for credentials, and one secret per
purpose is the rule this project keeps relearning.

### C2a. Verifying the environment actually reached the process

Reading the file back proves only what the file says. After
`systemctl daemon-reload` and a restart, check the **running process**:

```bash
sudo tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value capella-mcp)/environ \
  | grep -E 'KP_CONNECT_BASE_URL|CARTENZA_DB_PATH'
```

A value that is right in the file and wrong here means the unit was not
restarted. The end-to-end check is `begin_connect`: the URL it returns must be
on the host this droplet actually serves. Note that restarting drops in-memory
sessions, so agents mid-task will need to re-clone.

### C3. nginx and DNS

Two configs, matching the kp repo's convention — `nginx_mcp_cartenza.conf` is
**production** (`capella.cartenza.ai`), `nginx_mcp_dev_cartenza.conf` is **dev**
(`dev.capella.cartenza.ai`). Install whichever this droplet is.

Add the DNS A record first and let it resolve, then get the certificate, then
install the config. **That order is not optional:** the config names a
certificate in its 443 block, so installing it first makes `nginx -t` fail and
nginx will not reload — and on a box already serving other sites, that means you
cannot reload for anything else either until you back the file out.

```bash
# 1. after the A record resolves to this droplet
dig +short @8.8.8.8 capella.cartenza.ai

# 2. a temporary HTTP-only block, so Let's Encrypt can reach the new name
#    without touching any config that is already serving
mkdir -p /var/www/acme
cat > /etc/nginx/sites-available/acme-bootstrap <<'EOF'
server {
    listen 80;
    server_name capella.cartenza.ai;
    root /var/www/acme;
    location /.well-known/acme-challenge/ { allow all; }
}
EOF
ln -sf /etc/nginx/sites-available/acme-bootstrap /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 3. rehearse, then issue. --dry-run first: failed validations are what
#    Let's Encrypt rate-limits, and "DNS has not propagated" is the likely one
certbot certonly --webroot -w /var/www/acme -d capella.cartenza.ai --dry-run
certbot certonly --webroot -w /var/www/acme -d capella.cartenza.ai

# 4. drop the bootstrap, install the real config
rm /etc/nginx/sites-enabled/acme-bootstrap
cp deploy/nginx_mcp_cartenza.conf /etc/nginx/sites-available/capella-mcp
ln -sf /etc/nginx/sites-available/capella-mcp /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# 5. REQUIRED -- switch this lineage's renewal off webroot
sed -i 's/^authenticator = webroot$/authenticator = nginx/'   /etc/letsencrypt/renewal/capella.cartenza.ai.conf
certbot renew --cert-name capella.cartenza.ai --dry-run
```

**Step 5 is not optional, and skipping it fails silently three months later.**
`--webroot` issued the certificate against `/var/www/acme`, served by the
bootstrap block that step 4 just deleted. The real config's port-80 block
redirects everything to HTTPS, where there is no challenge location — so the
renewal request gets a 404 and the certificate quietly expires. Verify rather
than assume:

```bash
curl -s -o /dev/null -w '%{http_code}
'   https://capella.cartenza.ai/.well-known/acme-challenge/probe   # 404 = webroot is dead
```

The nginx authenticator needs no challenge path: it edits the running nginx
temporarily, validates, and reverts. It is also what every other lineage on the
Cartenza droplet uses, so one mechanism and one failure mode rather than two.

`certonly` rather than `--nginx` on purpose: it issues the certificate without
editing any config, so the file above stays the source of truth. Copying a repo
config over one certbot has already edited is how TLS was lost on this project
once before.

**Check the renewal authenticator afterwards.** Certificates issued with
`--standalone` cannot renew while nginx holds port 80, and the failure is
silent — four certificates on the Cartenza droplet reached 1–3 days of expiry
that way, hidden among unrelated failures from stale lineages
(`cousin_back_log`/note-0126). `certbot renew --dry-run` should report every
lineage succeeding and nothing failing; if a lineage fails on a port-80 bind,
set `authenticator = nginx` in its `/etc/letsencrypt/renewal/<name>.conf`.

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
