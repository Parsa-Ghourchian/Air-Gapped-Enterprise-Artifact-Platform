# Air-Gapped Enterprise Artifact Platform

Production-ready Dockerized artifact control plane for Nexus Repository Manager, offline deployment workflows, artifact publishing, registry access control, auditability, and operational monitoring.

Current release: `1.0.0`

## What This Platform Provides

This repository runs a complete internal artifact platform for restricted, offline, and air-gapped environments:

- Nexus Repository Manager for Docker, PyPI, APT, and raw artifacts
- Web portal for artifact publishing, deployment bundle creation, target server workflows, reports, audit logs, and storage visibility
- PostgreSQL-backed portal state for jobs, logs, access-control policy, publishing history, and audit records
- Security Center for Nexus registry users, groups, permissions, trusted IP/CIDR rules, and firewall enforcement
- Prometheus, Grafana, Node Exporter, cAdvisor, and Traefik for local operations and observability
- Backup and restore scripts for Nexus data, PostgreSQL portal data, and platform configuration

## Architecture

```text
Browser / Operator
    |
    v
Portal UI and API (FastAPI)
    |
    +--> PostgreSQL
    |       Portal metadata, jobs, logs, publishing history,
    |       Security Center policy, audit records, migration state
    |
    +--> Nexus REST API
    |       Repository operations, users, roles, anonymous access policy
    |
    +--> Docker Engine
    |       Image pulls, image archive loads, image tags, pushes
    |
    +--> Package tooling
            Python and Debian package fetch/publish workflows

Nexus Repository Manager
    |
    +--> Docker hosted/proxy/group registries
    +--> PyPI hosted/proxy/group repositories
    +--> APT hosted/proxy repositories
    +--> Raw repositories for bundles, backups, and generated artifacts

Monitoring
    |
    +--> Prometheus
    +--> Grafana
    +--> Node Exporter
    +--> cAdvisor
```

## Repository Structure

```text
.
├── configs/                         Seed artifact and APT target configuration
├── docs/                            Generated operator guides
├── examples/                        Client and CI examples
├── monitoring/                      Prometheus and Grafana configuration
├── portal/                          FastAPI portal application
│   ├── app/database.py              PostgreSQL connection pool and DB helpers
│   ├── app/main.py                  Portal API, workflows, schema initialization
│   ├── app/professional_reports.py  Report rendering endpoints
│   ├── app/static/                  Portal JavaScript and CSS
│   └── app/templates/               Portal HTML shell
├── scripts/                         Nexus, package, backup, restore, and validation scripts
├── docker-compose.yml               Core platform services
├── docker-compose.override.yml      Portal hardening and runtime settings
├── Makefile                         Common operational commands
└── VERSION                          Release version
```

Runtime directories are intentionally ignored by Git:

```text
data/
backups/
offline-bundles/
published-artifacts/
reports/
apt-mini-repos/
secrets/
```

## Services

| Service | Purpose | Default Local URL / Port |
| --- | --- | --- |
| `portal` | Web portal and API | `http://localhost:8095` |
| `postgres` | Portal database | internal Docker network only |
| `nexus` | Artifact repository manager | `http://localhost:8081` |
| `nexus` Docker hosted | Docker push/pull hosted registry | `localhost:5000` |
| `nexus` Docker proxy | Docker Hub proxy registry | `localhost:5001` |
| `nexus` Docker group | Combined Docker registry | `localhost:5002` |
| `prometheus` | Metrics | `http://localhost:9090` |
| `grafana` | Dashboards | `http://localhost:3000` |
| `traefik` | Reverse proxy/dashboard | `http://localhost:8080` |
| `cadvisor` | Container metrics | `http://localhost:8082` |

## Requirements

- Ubuntu or another Linux host with Docker Engine and Docker Compose plugin
- At least 8 GB RAM for a comfortable local environment
- At least 20 GB free disk for testing; more for real artifact mirrors
- Internet access during initial package/image fetch testing
- SSH access to deployment targets if using remote deployment workflows

## Initial Configuration

Create an environment file:

```bash
cp .env.example .env
```

Edit `.env` before production use:

```bash
nano .env
```

Important settings:

```env
COMPOSE_PROJECT_NAME=airgap_artifacts

NEXUS_IMAGE=sonatype/nexus3:3.93.2
NEXUS_ADMIN_PASSWORD=replace-with-strong-nexus-password
NEXUS_URL=http://localhost:8081
NEXUS_DOCKER_HOSTED=localhost:5000
NEXUS_DOCKER_PROXY=localhost:5001
NEXUS_DOCKER_GROUP=localhost:5002
NEXUS_PYPI_HOSTED=pypi-hosted
NEXUS_APT_HOSTED=apt-internal-hosted
NEXUS_RAW_OFFLINE_BUNDLES=raw-offline-bundles
NEXUS_PROTECTED_PORTS=8081,5000,5001,5002

POSTGRES_IMAGE=postgres:18-alpine
POSTGRES_DB=nexus_portal
POSTGRES_USER=nexus_portal
POSTGRES_PASSWORD=replace-with-strong-postgres-password
PORTAL_DB_POOL_MIN_SIZE=1
PORTAL_DB_POOL_MAX_SIZE=10

HOST_PROJECT_ROOT=/absolute/path/to/airgapped-enterprise-artifact-platform
PORTAL_IMAGE=airgap-deployment-portal:1.0.0
PORTAL_VERSION=1.0.0
PORTAL_ADMIN_USER=admin
PORTAL_ADMIN_PASSWORD=replace-with-strong-temporary-portal-password
PORTAL_ADMIN_PASSWORD_HASH=
PORTAL_SESSION_SECRET=replace-with-at-least-32-random-characters
PORTAL_COOKIE_SECURE=false
PORTAL_LOGIN_MAX_FAILURES=5
PORTAL_LOGIN_LOCKOUT_SECONDS=300

PORTAL_STRICT_HOST_KEY=false
PORTAL_REMOTE_DEFAULT_DIR=/tmp/airgap-deployments
PORTAL_ENABLE_GRYPE_SCAN=false
PORTAL_BLOCK_CRITICAL=true
PORTAL_BLOCK_HIGH=false
PORTAL_BLOCK_LATEST_TAG=true
PORTAL_BLOCK_UNPINNED_PACKAGES=true
PORTAL_BLOCK_UNSAFE_APT=true
PORTAL_REQUIRE_SECURITY_GATE=true
PORTAL_MIN_FREE_DISK_MB=2048
PORTAL_CLEANUP_KEEP_DAYS=14
```

For production, generate a password hash and avoid storing the portal password in plaintext:

```bash
python3 scripts/generate-portal-password-hash.py
```

Then set:

```env
PORTAL_ADMIN_PASSWORD=disabled
PORTAL_ADMIN_PASSWORD_HASH=pbkdf2_sha256$...
```

Set `PORTAL_COOKIE_SECURE=true` when the portal is served through HTTPS.

## Start the Platform

```bash
docker compose up -d --build
```

Initialize Nexus repositories:

```bash
make init
```

Check service status:

```bash
docker compose ps
curl -fsS http://localhost:8095/health
curl -fsS http://localhost:8081/service/rest/v1/status
```

Expected portal health:

```json
{"status":"ok","version":"1.0.0","database":"postgresql"}
```

## Portal Workflows

Open:

```text
http://localhost:8095
```

Portal modules:

- **Dashboard:** service links and deployment overview
- **Deployment Wizard:** target selection, SSH preflight, artifact selection, security gate, bundle/deploy workflow
- **Artifact Publishing:** Docker image import, Docker archive upload, Python package fetch, Debian package fetch
- **Target Servers:** saved SSH target management
- **History & Reports:** job logs, HTML reports, PDF reports
- **Audit Log:** portal action history
- **Security Center:** Registry Access Control
- **Storage Guard:** storage usage and cleanup policy

## Artifact Publishing

Supported publishing workflows:

- Pull image by name/tag and publish to Nexus Docker hosted
- Upload a local `docker save` archive and publish to Nexus
- Fetch Python packages for Python 2 or Python 3 and publish to PyPI hosted
- Fetch Debian/Ubuntu packages for configured target releases and publish to APT hosted

Publishing jobs are recorded in PostgreSQL and visible in the portal with status, logs, repository, target, checksum, and errors.

## Registry Access Control

Security Center manages registry access policy centrally:

- Access groups and Nexus role permissions
- Trusted user/service accounts and assigned groups
- Trusted IP addresses and CIDR ranges
- Nexus anonymous-access lockdown
- Host firewall preview and apply workflow for protected Nexus ports

Typical production flow:

1. Create least-privilege groups.
2. Create trusted users or service accounts.
3. Assign groups to accounts.
4. Add trusted admin, CI, and runtime CIDRs.
5. Click **Sync Nexus Access**.
6. Preview firewall rules.
7. Apply firewall only after confirming the current admin IP is trusted.

Unauthenticated clients should receive `401 Unauthorized` if network access is allowed but credentials are missing. Untrusted source IPs should be blocked at the network layer after firewall enforcement.

## Docker Client Configuration

For local HTTP registry testing on Ubuntu, configure Docker insecure registries:

```bash
sudo nano /etc/docker/daemon.json
```

Example:

```json
{
  "insecure-registries": [
    "localhost:5000",
    "localhost:5001",
    "localhost:5002"
  ]
}
```

Restart Docker:

```bash
sudo systemctl restart docker
docker info | grep -A20 "Insecure Registries"
```

Login and smoke-test:

```bash
echo "$NEXUS_ADMIN_PASSWORD" | docker login localhost:5000 -u admin --password-stdin
docker pull alpine:3.20
docker tag alpine:3.20 localhost:5000/test/alpine:3.20
docker push localhost:5000/test/alpine:3.20
docker pull localhost:5000/test/alpine:3.20
```

## PostgreSQL

PostgreSQL is the source of truth for portal state. The portal initializes its schema at startup and records applied schema state in `schema_migrations`.

Persistent data path:

```text
data/postgres/
```

Check database readiness:

```bash
docker exec airgap-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
docker exec airgap-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c '\dt'
```

The portal health endpoint verifies database connectivity.

## Backup and Restore

Create a backup:

```bash
make backup
```

The backup includes:

- Nexus data
- PostgreSQL portal data
- Docker Compose files
- configuration
- scripts
- monitoring configuration
- release version

Restore:

```bash
make restore BACKUP=backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz
```

Upload latest backup to Nexus raw repository:

```bash
make upload-backup
```

## Storage and Cleanup

Show local storage usage:

```bash
make size
```

Run cleanup from the portal Storage Guard or with:

```bash
make clean-generated
```

Cleanup removes generated reports, bundle outputs, published-artifact work directories, and mini APT output. It does not remove Nexus or PostgreSQL data.

## Useful Commands

```bash
make up              # Build and start the stack
make down            # Stop the stack
make restart         # Restart services
make ps              # Show service status
make logs            # Follow compose logs
make init            # Initialize Nexus repositories/configs
make test            # Run platform smoke tests
make backup          # Backup Nexus and portal PostgreSQL data
make restore BACKUP=backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz
make portal-build    # Build portal image
make portal-logs     # Follow portal logs
make final-check     # Static compose/repository checks
```

## Operational Testing Guide

A printable Ubuntu testing guide is available at:

```text
docs/Nexus_Portal_Ubuntu_Testing_Guide.pdf
```

It covers Docker registry configuration, publishing workflows, package fetches, Security Center, trusted IP enforcement, logs, reports, and troubleshooting.

## Production Hardening Checklist

- Use strong unique values for `NEXUS_ADMIN_PASSWORD`, `POSTGRES_PASSWORD`, and portal credentials.
- Prefer `PORTAL_ADMIN_PASSWORD_HASH` over plaintext portal password auth.
- Set a long random `PORTAL_SESSION_SECRET`.
- Serve the portal behind HTTPS and set `PORTAL_COOKIE_SECURE=true`.
- Restrict access to Docker registry ports with Security Center trusted CIDRs.
- Keep Nexus anonymous access disabled after syncing portal access policy.
- Keep `PORTAL_BLOCK_LATEST_TAG=true` and require pinned package inputs for controlled deployments.
- Use strict SSH host key checking and pre-load trusted target host keys for production deployments.
- Back up Nexus and PostgreSQL data before upgrades.
- Store `.env` securely and never commit it.

## Troubleshooting

Check portal logs:

```bash
docker logs airgap-portal --tail=200
```

Check Nexus logs:

```bash
docker logs airgap-nexus --tail=200
```

Check PostgreSQL:

```bash
docker compose ps postgres
docker exec airgap-postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"
```

Check registry ports:

```bash
ss -lntp | grep -E '5000|5001|5002|8081|8095'
```

Check portal-managed firewall rules:

```bash
sudo iptables -S | grep PORTAL_NEXUS
sudo iptables -S PORTAL_NEXUS_ACL
```

If Docker reports `server gave HTTP response to HTTPS client`, add the Nexus registry address to Docker `insecure-registries` and restart Docker.

## Final State

The production architecture is PostgreSQL-backed, fully Dockerized, portal-managed, and designed for controlled Nexus mirror and artifact publishing operations.
