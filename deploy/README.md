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
ln -s /etc/nginx/sites-available/capella-fabric /etc/nginx/sites-enabled/
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
