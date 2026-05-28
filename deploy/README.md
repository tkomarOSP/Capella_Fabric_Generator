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
git clone -b feature/capella-7.0.1-support https://github.com/tkSDISW/Capella_Tools /opt/capella_tools
```

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
4. Click **Refresh tool list** — you should see 8 tools

**Tools available:**

| Tool | Purpose |
|---|---|
| `clone_capella_repo` | Clone a GitHub repo containing a Capella model |
| `add_dependency_repo` | Register a library repo the main model depends on |
| `list_object_types` | Discover valid phase/object_type combinations |
| `browse_model` | List all objects of a given type in a phase |
| `search_model_objects` | Search objects by name (substring match) |
| `resolve_model_uuids` | Resolve UUIDs to model objects |
| `generate_fabric` | Generate YAML fabric for resolved UUIDs |
| `cleanup_session` | Delete cloned repos and temp files |

**Typical workflow:**

```
1. clone_capella_repo(repo_url, github_pat, branch?)
2. add_dependency_repo(session_id, lib_url, github_pat, resource_name)  ← if model has libraries
3. list_object_types()                                                   ← discover valid types
4. browse_model(session_id, phase, object_type)                         ← find objects
5. resolve_model_uuids(session_id, uuids)                               ← select objects
6. generate_fabric(session_id)                                          ← produce YAML
7. cleanup_session(session_id)                                          ← release disk space
```

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
systemctl restart capella-mcp
```

If `nginx_mcp.conf` changed, also copy it and reload nginx:

```bash
cp deploy/nginx_mcp.conf /etc/nginx/sites-available/capella-mcp
nginx -t && systemctl reload nginx
```
