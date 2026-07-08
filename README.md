# 🧱 Air-Gapped Enterprise Artifact Platform

![Docker](https://img.shields.io/badge/Docker-Compose-blue.svg)
![Nexus](https://img.shields.io/badge/Nexus-Repository_Manager-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-Deployment_Portal-teal.svg)
![Traefik](https://img.shields.io/badge/Traefik-Reverse_Proxy-purple.svg)
![Prometheus](https://img.shields.io/badge/Prometheus-Monitoring-orange.svg)
![Grafana](https://img.shields.io/badge/Grafana-Dashboards-red.svg)
![Air--Gapped](https://img.shields.io/badge/Air--Gapped-Offline_Ready-black.svg)
![Version](https://img.shields.io/badge/Version-4.0_Final-success.svg)

**Air-Gapped Enterprise Artifact Platform** is a Dockerized, production-oriented internal artifact and deployment control plane designed for restricted, offline, and air-gapped environments.

The platform provides a complete internal dependency delivery workflow for Docker images, Python packages, Debian/Ubuntu packages, offline bundles, deployment automation, SSH preflight checks, security gates, audit logs, rollback planning, monitoring, and backup/restore operations.

It is built for infrastructure teams, DevOps engineers, platform engineers, private data centers, enterprise environments, and organizations that need to run deployments without direct internet access on production servers.

---

## ✨ Project Summary

This project is not just a Nexus installation.

It is an **Enterprise Air-Gapped Artifact & Deployment Control Plane** that allows an organization to control how dependencies, packages, images, and offline deployment bundles are prepared, verified, transferred, deployed, monitored, audited, backed up, and restored.

The goal is to provide one internal platform where:

```text
Docker images
Python packages
Debian / Ubuntu packages
Offline bundles
Internal artifacts
Deployment reports
Backup archives
Audit logs
```

are managed through a controlled, observable, and repeatable workflow.

---

## 🚀 Key Features

### Artifact Management

* **Nexus Repository Manager:** Central artifact repository for internal dependency delivery
* **Docker Registry Support:** Hosted, proxy, and group repositories for Docker images
* **PyPI Support:** Hosted, proxy, and group repositories for Python packages
* **APT Support:** Debian and Ubuntu package proxy repositories
* **Raw Repositories:** Storage for offline bundles, internal releases, backup archives, and deployment artifacts
* **Internal Dependency Hub:** Single controlled source for deployment dependencies

### Air-Gapped Deployment Portal

* **Professional Web Portal:** Modern V4 UI for non-CLI users
* **5-Step Deployment Wizard:** Guided deployment flow from target selection to execution
* **Target Server Management:** Save and manage deployment targets
* **SSH Connection Test:** Validate remote server access before deployment
* **Preflight Check:** Validate OS, disk, Docker, Python, APT, permissions, and remote directory
* **Artifact Selection:** Select Docker images, Python packages, and Debian/Ubuntu packages
* **Offline Bundle Builder:** Build verified deployment bundles from selected artifacts
* **SFTP Transfer:** Transfer bundles to target servers securely
* **Remote Execution:** Execute deployment commands on the destination server
* **Live Job Status:** Track queued, running, successful, and failed jobs
* **Full Job Logs:** Store and display complete operation logs
* **History:** Keep job history for traceability
* **Audit Log:** Record authentication, server actions, preflight checks, security gates, cleanup actions, and job creation

### Security & Governance

* **Security Gate:** Policy validation before build and deploy
* **No `latest` Tag Policy:** Blocks Docker images with implicit or explicit `latest` tags
* **Dangerous Command Detection:** Blocks unsafe remote commands
* **Optional Vulnerability Scan:** Grype-based vulnerability scan can be enabled on demand
* **Critical Vulnerability Blocking:** Optional policy to block images with critical findings
* **Bundle Manifest:** Every bundle includes metadata and artifact inventory
* **SHA256 Verification:** Bundle integrity is verified using checksums
* **No SSH Password Storage:** Passwords are only used during runtime and are not persisted
* **SSH Key Support:** Supports key-based deployment authentication
* **Security Headers:** Portal responses include secure HTTP headers

### Reporting & Rollback

* **Deployment Report:** HTML report for every deployment job
* **PDF Report:** Lightweight PDF report generated automatically
* **Rollback Plan:** Captures before/after snapshots and recommended rollback actions
* **Docker Snapshot:** Records existing Docker images before deployment
* **Package Snapshot:** Records package state from the target server
* **Audit Evidence:** Reports and logs provide operational evidence for review

### Monitoring & Operations

* **Prometheus:** Metrics collection
* **Grafana:** Dashboard visualization
* **Node Exporter:** Host-level metrics
* **cAdvisor:** Container-level metrics
* **Traefik Dashboard:** Reverse proxy routing overview
* **Storage Guard:** Portal view for workspace, Nexus, report, and bundle storage usage
* **Cleanup Policy:** Removes old generated bundles and reports based on retention settings
* **Backup Script:** Backup Nexus data and platform configuration
* **Restore Script:** Restore Nexus and platform data from backup archive
* **Docker Compose Hardening:** Logging limits, restart policies, health checks, memory limits, and security options

---

## 🧩 Architecture

```text
User Browser
    |
    v
Traefik Reverse Proxy
    |
    +----------------------------+
    |                            |
    v                            v
Deployment Portal              Nexus Repository Manager
FastAPI + SQLite               Docker / PyPI / APT / Raw
    |                            |
    |                            v
    |                      Internal Artifact Hub
    |
    +----------------------------+
    |
    v
Docker Engine
Bundle Build / Image Save / Artifact Packaging
    |
    v
SSH / SFTP
    |
    v
Target Server
Docker Load / Python Wheels / APT Mini Repo / Remote Commands


Monitoring Layer:
Prometheus + Grafana + Node Exporter + cAdvisor
```

---

## 🛠️ Tech Stack

| Layer               | Technology                        |
| ------------------- | --------------------------------- |
| Artifact Repository | Sonatype Nexus Repository Manager |
| Deployment Portal   | FastAPI, Python                   |
| Portal Database     | SQLite with WAL mode              |
| SSH / SFTP          | Paramiko                          |
| Reverse Proxy       | Traefik                           |
| Monitoring          | Prometheus, Grafana               |
| Host Metrics        | Node Exporter                     |
| Container Metrics   | cAdvisor                          |
| Container Runtime   | Docker                            |
| Orchestration       | Docker Compose                    |
| Security Audit      | Grype / Syft scripts              |
| Package Types       | Docker, PyPI, APT, Raw            |
| Deployment Mode     | Air-gapped / Offline-ready        |

---

## 📂 Project Structure

```bash
.
├── configs
│   ├── apt
│   │   ├── apt-mini-targets.tsv              # APT mini repository targets
│   │   └── apt-proxy-repositories.tsv        # Nexus APT proxy repository definitions
│   ├── apt-packages.txt                      # Default Debian/Ubuntu packages
│   ├── docker-images.txt                     # Default Docker image list
│   └── python-requirements.txt               # Default Python package list
│
├── docker-compose.yml                        # Core platform services
├── docker-compose.override.yml               # Portal and production hardening overrides
├── .env.example                              # Example environment configuration
├── .gitignore                                # Runtime and secret exclusion rules
├── Makefile                                  # Operational commands
├── VERSION                                   # Project version
│
├── .github
│   └── workflows
│       └── ci-example.yml                    # CI/CD example workflow
│
├── examples
│   ├── ci-app
│   │   ├── app
│   │   │   └── main.py                       # Minimal demo app
│   │   └── Dockerfile                        # Multi-stage Dockerfile example
│   └── debian-client
│       ├── debian-11-bullseye-nexus.list
│       ├── debian-12-bookworm-nexus.list
│       ├── debian-13-trixie-nexus.list
│       ├── ubuntu-24.04-noble-nexus.list
│       ├── ubuntu-25.04-plucky-nexus.list
│       └── ubuntu-26.04-resolute-nexus.list
│
├── monitoring
│   ├── grafana
│   │   ├── dashboards
│   │   │   └── airgap-platform.json          # Grafana dashboard
│   │   └── provisioning
│   │       ├── dashboards
│   │       └── datasources
│   └── prometheus
│       └── prometheus.yml                    # Prometheus scrape configuration
│
├── portal
│   ├── app
│   │   ├── main.py                           # FastAPI portal backend
│   │   ├── static
│   │   │   ├── app.js                        # Portal frontend logic
│   │   │   └── styles.css                    # Portal UI styling
│   │   └── templates
│   │       └── index.html                    # Portal UI template
│   ├── Dockerfile                            # Portal container image
│   ├── .dockerignore                         # Portal build exclusions
│   └── requirements.txt                      # Portal Python dependencies
│
└── scripts
    ├── audit-image.sh                        # Vulnerability audit helper
    ├── backup-nexus.sh                       # Nexus backup script
    ├── create-apt-mini-repo.sh               # Build lightweight offline APT repositories
    ├── create-apt-proxy-repos.sh             # Create Nexus APT proxy repositories
    ├── generate-apt-client-configs.sh        # Generate Debian/Ubuntu client source files
    ├── generate-portal-password-hash.py      # Portal password hash generator
    ├── generate-sbom.sh                      # SBOM generator
    ├── init-nexus.sh                         # Nexus repository initialization
    ├── restore-nexus.sh                      # Nexus restore script
    ├── size-report.sh                        # Storage usage report
    ├── sync-docker-images.sh                 # Sync Docker images into Nexus
    ├── sync-python-packages.sh               # Sync Python wheels into Nexus
    ├── test-platform.sh                      # Platform smoke test
    ├── upload-apt-mini-repo-to-nexus.sh      # Upload APT mini repos to Nexus Raw
    └── upload-backup-to-nexus.sh             # Upload backup archives to Nexus Raw
```

---

## ⚙️ Prerequisites

Before running the platform, make sure the host has:

* Linux host, Ubuntu recommended
* Docker
* Docker Compose plugin
* At least 8 GB RAM recommended
* At least 20 GB free disk for testing
* Internet access for initial cache/sync phase
* SSH access to target servers for deployment workflows

For fully air-gapped environments, prepare and import required Docker images, Python wheels, Debian/Ubuntu packages, and offline bundles before moving the platform to the isolated network.

---

## 🔐 Environment Variables

Create `.env` from the provided example:

```bash
cp .env.example .env
```

Important variables:

```env
# Nexus
NEXUS_URL=http://localhost:8081
NEXUS_ADMIN_PASSWORD=ChangeThisStrongPassword_12345

# Portal
HOST_PROJECT_ROOT=/absolute/path/to/airgapped-enterprise-artifact-platform
PORTAL_IMAGE=airgap-deployment-portal:0.4.0
PORTAL_ADMIN_USER=admin
PORTAL_ADMIN_PASSWORD=ChangeThisPortalPassword_12345
PORTAL_ADMIN_PASSWORD_HASH=
PORTAL_SESSION_SECRET=change-me-random-hex
PORTAL_STRICT_HOST_KEY=false
PORTAL_REMOTE_DEFAULT_DIR=/tmp/airgap-deployments

# Security Gate
PORTAL_ENABLE_GRYPE_SCAN=false
PORTAL_BLOCK_CRITICAL=true
PORTAL_BLOCK_LATEST_TAG=true
PORTAL_MIN_FREE_DISK_MB=2048

# Cleanup
PORTAL_CLEANUP_KEEP_DAYS=14
```

### Recommended Portal Password Hardening

Generate a PBKDF2 password hash:

```bash
python3 scripts/generate-portal-password-hash.py
```

Then update `.env`:

```env
PORTAL_ADMIN_PASSWORD=disabled
PORTAL_ADMIN_PASSWORD_HASH=pbkdf2_sha256$260000$...
```

Restart the portal:

```bash
docker compose restart portal
```

---

## 🌐 Local Hostnames

The platform is designed to work through local hostnames.

Update `/etc/hosts`:

```bash
sudo nano /etc/hosts
```

Add:

```text
127.0.0.1 nexus.local grafana.local prometheus.local traefik.local artifact.local portal.local
```

Validate:

```bash
getent hosts portal.local
getent hosts artifact.local
getent hosts nexus.local
```

---

## 🚀 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/your-username/airgapped-enterprise-artifact-platform.git
cd airgapped-enterprise-artifact-platform
```

### 2. Create Environment File

```bash
cp .env.example .env
```

Update required values:

```bash
nano .env
```

Make sure `HOST_PROJECT_ROOT` is set to the absolute project path:

```bash
pwd
```

Example:

```env
HOST_PROJECT_ROOT=/home/user/project/airgapped-enterprise-artifact-platform
```

### 3. Start the Platform

```bash
docker compose up -d --build
```

### 4. Check Running Containers

```bash
docker compose ps
```

Expected containers:

```text
airgap-traefik
airgap-nexus
airgap-portal
airgap-prometheus
airgap-grafana
airgap-node-exporter
airgap-cadvisor
```

### 5. Initialize Nexus Repositories

```bash
make init
```

This creates and configures:

```text
Docker hosted / proxy / group repositories
PyPI hosted / proxy / group repositories
APT proxy repositories
Raw hosted repositories
Docker Bearer Token Realm
Client configuration examples
```

### 6. Open the Platform

```text
Portal:      http://portal.local
Portal alt:  http://artifact.local
Nexus:       http://nexus.local
Grafana:     http://grafana.local
Prometheus:  http://prometheus.local
Traefik:     http://traefik.local:8080
```

Default portal login:

```text
Username: admin
Password: ChangeThisPortalPassword_12345
```

Change this before using the platform in any real environment.

---

## 🧪 Health Checks

### Portal Health

```bash
curl http://localhost:8095/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.4.0"
}
```

### Nexus Health

```bash
curl -I http://localhost:8081
```

### Prometheus

```bash
curl http://localhost:9090/-/ready
```

### Docker Compose Health

```bash
docker compose ps
```

### Compose Configuration Validation

```bash
docker compose config >/dev/null && echo "Compose OK"
```

---

## 🧭 Portal Workflow

The V4 deployment portal provides a guided 5-step workflow.

### Step 1 — Select Target Server

Select a saved target server or enter an ad-hoc SSH target:

```text
IP / Hostname
SSH Port
Username
Password or SSH Key Path
Remote Deployment Directory
Sudo Option
```

Passwords are not stored.

### Step 2 — Preflight Check

The portal validates the target server before deployment:

```text
SSH connectivity
Hostname
OS release
Remote directory creation
Remote directory write permission
Free disk space
Docker CLI availability
Docker daemon status
Python 3 availability
APT availability
User groups
Docker image snapshot
Package snapshot
```

### Step 3 — Select Artifacts

Select or manually enter:

```text
Docker images
Python packages
Debian / Ubuntu packages
APT target OS
```

Supported APT targets:

```text
Ubuntu 24.04 LTS Noble Numbat
Ubuntu 25.04 Plucky Puffin
Ubuntu 26.04 LTS Resolute Raccoon
Debian 11 Bullseye
Debian 12 Bookworm
Debian 13 Trixie
```

### Step 4 — Security Gate

The platform runs policy checks before build/deploy:

```text
No empty artifact selection
No implicit/latest Docker tag
Dangerous command blocking
Optional Grype vulnerability scan
Critical vulnerability blocking if enabled
```

### Step 5 — Build, Transfer & Deploy

The portal:

```text
Builds the offline bundle
Creates a manifest
Calculates SHA256 checksums
Transfers the bundle via SFTP
Verifies checksum on the remote server
Extracts the bundle
Runs docker load if enabled
Prepares Python wheels if enabled
Prepares APT mini repo if enabled
Runs extra remote commands if provided
Generates HTML and PDF reports
Stores logs and audit events
```

---

## 📦 Artifact Repository Model

The platform uses a hosted/proxy/group repository model.

### Docker

```text
docker-hosted   # Internal Docker images
docker-proxy    # Docker Hub cache/proxy
docker-group    # Single client endpoint
```

Typical usage:

```bash
docker login localhost:5000
docker tag nginx:1.27 localhost:5000/nginx:1.27
docker push localhost:5000/nginx:1.27
docker pull localhost:5002/nginx:1.27
```

### Python / PyPI

```text
pypi-hosted   # Internal Python packages
pypi-proxy    # PyPI cache/proxy
pypi-group    # Single pip endpoint
```

Typical usage:

```bash
pip install requests \
  --index-url http://nexus.local/repository/pypi-group/simple \
  --trusted-host nexus.local
```

### Debian / Ubuntu APT

The platform supports APT proxy repositories for:

```text
Ubuntu Noble
Ubuntu Plucky
Ubuntu Resolute
Debian Bullseye
Debian Bookworm
Debian Trixie
```

Example client source:

```text
deb http://nexus.local/repository/apt-debian-bookworm bookworm main contrib non-free non-free-firmware
deb http://nexus.local/repository/apt-debian-bookworm-updates bookworm-updates main contrib non-free non-free-firmware
deb http://nexus.local/repository/apt-debian-bookworm-security bookworm-security main contrib non-free non-free-firmware
```

### Raw Repositories

```text
raw-releases
raw-offline-bundles
raw-backups
```

Used for:

```text
Offline deployment bundles
Internal releases
Backup archives
Generated mini repositories
Operational artifacts
```

---

## 🧰 APT Client Configuration

Generate client examples:

```bash
make apt-configs
```

Generated files are available in:

```text
examples/debian-client/
```

Example files:

```text
ubuntu-24.04-noble-nexus.list
ubuntu-25.04-plucky-nexus.list
ubuntu-26.04-resolute-nexus.list
debian-11-bullseye-nexus.list
debian-12-bookworm-nexus.list
debian-13-trixie-nexus.list
```

On a target server, copy the matching file:

```bash
sudo cp debian-12-bookworm-nexus.list /etc/apt/sources.list.d/nexus.list
sudo apt update
```

---

## 📦 Offline Bundle Workflow

The platform supports two offline delivery models.

### Portal-Based Workflow

Recommended for operators:

```text
Open Portal
Select target server
Run preflight
Select artifacts
Run security gate
Create bundle and deploy
Review report and logs
```

### CLI Fallback Workflow

For administrators:

```bash
make sync-docker
make sync-python
make apt-mini
make upload-apt-mini
```

The portal is the primary V4 workflow. CLI scripts remain available for maintenance, automation, and recovery.

---

## 🔐 SSH Deployment Setup

Create a deployment key:

```bash
mkdir -p secrets

ssh-keygen -t ed25519 \
  -f secrets/airgap_deploy_key \
  -C "airgap-deploy"

chmod 600 secrets/airgap_deploy_key
```

Copy the public key to the target server:

```bash
cat secrets/airgap_deploy_key.pub
```

On the target server:

```bash
sudo useradd -m -s /bin/bash airdeploy || true
sudo mkdir -p /home/airdeploy/.ssh
sudo nano /home/airdeploy/.ssh/authorized_keys
sudo chown -R airdeploy:airdeploy /home/airdeploy/.ssh
sudo chmod 700 /home/airdeploy/.ssh
sudo chmod 600 /home/airdeploy/.ssh/authorized_keys
```

If the target user should run Docker commands:

```bash
sudo usermod -aG docker airdeploy
```

In the portal, use:

```text
/workspace/secrets/airgap_deploy_key
```

as the SSH key path.

---

## 🧪 Demo Scenario

A recommended demo flow:

1. Open the portal:

```text
http://portal.local
```

2. Show the V4 dashboard and enterprise landing view.

3. Open the Deployment Wizard.

4. Add or select a target server.

5. Run the SSH Preflight Check.

6. Show successful checks:

```text
SSH connectivity
OS release
Disk space
Docker daemon
Python 3
APT
Remote directory permission
```

7. Select a Docker image:

```text
nginx:1.27
```

8. Select a Python package:

```text
requests==2.32.4
```

9. Select Debian/Ubuntu packages:

```text
curl
wget
net-tools
```

10. Run the Security Gate.

11. Build and deploy the offline bundle.

12. Open live job logs.

13. Open the generated HTML report.

14. Download the generated PDF report.

15. Show the Audit Log.

16. Show Storage Guard and Cleanup Policy.

17. Open Nexus and show uploaded artifacts.

18. Open Grafana and show monitoring.

This demo proves that the system is not only an artifact repository, but a complete internal deployment control plane.

---

## 🧱 API Endpoints

### Portal

| Method   | Endpoint                         | Description                    |
| -------- | -------------------------------- | ------------------------------ |
| `GET`    | `/health`                        | Portal health check            |
| `POST`   | `/api/login`                     | Portal login                   |
| `POST`   | `/api/logout`                    | Portal logout                  |
| `GET`    | `/api/session`                   | Current session                |
| `GET`    | `/api/stats`                     | Platform statistics            |
| `GET`    | `/api/servers`                   | List target servers            |
| `POST`   | `/api/servers`                   | Create target server           |
| `DELETE` | `/api/servers/{server_id}`       | Delete target server           |
| `POST`   | `/api/preflight`                 | Run SSH preflight              |
| `POST`   | `/api/security-gate`             | Run security policy gate       |
| `GET`    | `/api/artifacts`                 | List available artifacts       |
| `POST`   | `/api/jobs`                      | Create deployment job          |
| `GET`    | `/api/jobs`                      | List deployment jobs           |
| `GET`    | `/api/jobs/{job_id}`             | Get job details                |
| `GET`    | `/api/jobs/{job_id}/logs`        | Get job logs                   |
| `GET`    | `/api/jobs/{job_id}/report/html` | Open HTML deployment report    |
| `GET`    | `/api/jobs/{job_id}/report/pdf`  | Download PDF deployment report |
| `GET`    | `/api/audit`                     | View audit log                 |
| `GET`    | `/api/storage`                   | Storage guard statistics       |
| `POST`   | `/api/cleanup`                   | Run cleanup policy             |

---

## 🖥️ Dashboard Overview

The Portal UI includes:

```text
Enterprise dashboard
5-step deployment wizard
Target server manager
SSH preflight result cards
Artifact selection panels
Security gate result panel
Live job status
Full job logs
Deployment history
HTML report links
PDF report links
Audit log
Storage guard
Cleanup action
```

---

## 📊 Monitoring

The monitoring layer includes:

```text
Prometheus
Grafana
Node Exporter
cAdvisor
Traefik metrics
Container metrics
Host metrics
```

Open Grafana:

```text
http://grafana.local
```

Open Prometheus:

```text
http://prometheus.local
```

Open cAdvisor directly:

```text
http://localhost:8082
```

The dashboard can be used to observe:

```text
Container health
Host CPU and memory
Disk usage
Nexus availability
Prometheus targets
Platform runtime status
```

---

## 💾 Backup & Restore

### Create Backup

```bash
make backup
```

### Upload Latest Backup to Nexus Raw

```bash
make upload-backup
```

Backups are uploaded to:

```text
raw-backups
```

### Restore Backup

```bash
make restore BACKUP=backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz
```

Backup covers:

```text
Nexus data volume
Blob stores
Nexus configuration
Platform configuration
Scripts
Monitoring configuration
Docker Compose files
```

---

## 🧹 Storage Guard & Cleanup

The portal provides a built-in Storage Guard that reports:

```text
Workspace size
Nexus data size
Portal database size
Generated bundle size
Generated report size
Cleanup retention policy
```

Cleanup retention is controlled by:

```env
PORTAL_CLEANUP_KEEP_DAYS=14
```

Run cleanup through the UI or API:

```bash
curl -X POST http://portal.local/api/cleanup
```

You can also clean generated local outputs:

```bash
make clean-generated
```

---

## 🧪 Useful Commands

### Start Platform

```bash
make up
```

### Stop Platform

```bash
make down
```

### Restart Services

```bash
make restart
```

### Show Running Containers

```bash
make ps
```

### View All Logs

```bash
make logs
```

### View Portal Logs

```bash
make portal-logs
```

### Initialize Nexus

```bash
make init
```

### Run Smoke Test

```bash
make test
```

### Generate APT Client Configs

```bash
make apt-configs
```

### Create APT Mini Repo

```bash
make apt-mini
```

### Upload APT Mini Repo to Nexus

```bash
make upload-apt-mini
```

### Generate SBOM

```bash
make sbom IMAGE=nginx:1.27
```

### Audit Image

```bash
make audit IMAGE=nginx:1.27
```

### Show Storage Report

```bash
make size
```

### Validate Compose

```bash
make final-check
```

---

## 🧹 Reset Development Data

Warning: this removes runtime data.

Stop the platform first:

```bash
docker compose down --remove-orphans
```

Remove generated runtime directories:

```bash
sudo rm -rf data offline-bundles reports apt-mini-repos secrets backups
```

Recreate required runtime directories:

```bash
mkdir -p data/nexus data/grafana data/prometheus data/portal

sudo chown -R 200:200 data/nexus
sudo chown -R 472:472 data/grafana
sudo chown -R 65534:65534 data/prometheus
sudo chown -R "$USER:$USER" data/portal
```

Start again:

```bash
docker compose up -d --build
make init
```

---

## 🛠️ Troubleshooting

### Portal does not open

Check container status:

```bash
docker compose ps
```

Check portal logs:

```bash
docker logs -f airgap-portal
```

Check health:

```bash
curl http://localhost:8095/health
```

### `portal.local` does not resolve

Check `/etc/hosts`:

```bash
getent hosts portal.local
```

Required line:

```text
127.0.0.1 nexus.local grafana.local prometheus.local traefik.local artifact.local portal.local
```

### Nexus is still starting

Nexus may take a few minutes to become healthy.

```bash
docker logs -f airgap-nexus
```

Check direct access:

```bash
curl -I http://localhost:8081
```

### Docker registry login fails

Check Docker insecure registry configuration if using local HTTP registries.

Example `/etc/docker/daemon.json`:

```json
{
  "insecure-registries": [
    "localhost:5000",
    "localhost:5001",
    "localhost:5002",
    "nexus.local:5000",
    "nexus.local:5001",
    "nexus.local:5002"
  ]
}
```

Restart Docker:

```bash
sudo systemctl restart docker
```

### Prometheus permission denied

Fix Prometheus data ownership:

```bash
sudo chown -R 65534:65534 data/prometheus
docker compose up -d prometheus
```

### Grafana permission issue

Fix Grafana data ownership:

```bash
sudo chown -R 472:472 data/grafana
docker compose up -d grafana
```

### Nexus permission issue

Fix Nexus data ownership:

```bash
sudo chown -R 200:200 data/nexus
docker compose up -d nexus
```

### Portal cannot build APT mini repo

Check Docker socket mount:

```bash
docker exec -it airgap-portal docker version
```

Check `HOST_PROJECT_ROOT`:

```bash
grep HOST_PROJECT_ROOT .env
```

It must point to the absolute project path.

### SSH preflight fails

Check target connectivity:

```bash
ssh airdeploy@TARGET_SERVER_IP
```

Check key permissions:

```bash
chmod 600 secrets/airgap_deploy_key
```

Use this key path inside the portal:

```text
/workspace/secrets/airgap_deploy_key
```

### Security Gate blocks Docker image

Avoid implicit/latest tags.

Use:

```text
nginx:1.27
```

Do not use:

```text
nginx
nginx:latest
```

---

## 🔒 Security Notes

* Do not commit `.env`.
* Do not commit `secrets/`.
* Do not commit runtime data from `data/`.
* Do not commit generated bundles or reports.
* Use SSH keys instead of passwords for production deployments.
* Change default portal and Nexus passwords before real usage.
* Keep `PORTAL_BLOCK_LATEST_TAG=true` for controlled deployments.
* Enable vulnerability scanning only when required and when scanner images are available.
* Use dedicated deployment users with limited permissions.
* Avoid granting broad sudo access to deployment users.
* Review generated reports and rollback plans before production changes.

---

## 📌 Git Ignore Policy

The following paths are intentionally excluded from Git:

```text
.env
data/
backups/
offline-bundles/
reports/
apt-mini-repos/
secrets/
.venv-tools/
*.db
*.db-wal
*.db-shm
*.log
```

These are runtime artifacts, local secrets, generated bundles, reports, or local state.

---

## 🧭 Roadmap

The V4 edition is considered the final core version.

Optional future enhancements:

```text
TLS certificates for all local routes
Role-based access control
Multi-user approval workflow
Cosign signing and verification
OPA policy-as-code integration
Advanced Nexus cleanup policies
Advanced Grafana dashboards
Remote agent mode for restricted targets
Immutable audit export
SLSA-style provenance metadata
```

---

## 📄 License

This project is released under the **MIT License**.

You are free to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of this software, provided that the original copyright notice and license permission notice are included in all copies or substantial portions of the software.

This project is provided **“as is”**, without warranty of any kind, express or implied, including but not limited to warranties of merchantability, fitness for a particular purpose, and non-infringement.

For enterprise, commercial, or internal deployments, review the license terms carefully and make sure they match your organization’s compliance and security requirements.

---

## 🏁 Final Statement

**Air-Gapped Enterprise Artifact Platform** provides a complete internal dependency and deployment workflow for organizations that need controlled delivery in restricted environments.

It combines:

```text
Nexus Repository Manager
Deployment Portal
Offline Bundle Builder
SSH/SFTP Deployment
Security Gate
Preflight Validation
Rollback Planning
HTML/PDF Reporting
Audit Logs
Monitoring
Backup/Restore
Storage Guard
```

into one Dockerized platform.

The result is a practical, enterprise-ready control plane for artifact delivery and air-gapped deployments.
