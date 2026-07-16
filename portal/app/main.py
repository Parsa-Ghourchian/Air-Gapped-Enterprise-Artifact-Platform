import base64
import hashlib
import hmac
import html as html_lib
import ipaddress
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import paramiko
import requests
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg import IntegrityError as DBIntegrityError
from pydantic import BaseModel, Field

from app.database import close_pool, db_conn, db_ping, row_to_dict


APP_WORKSPACE = Path(os.getenv("APP_WORKSPACE", "/workspace"))
HOST_PROJECT_ROOT = os.getenv("HOST_PROJECT_ROOT", "")
APP_VERSION = os.getenv("PORTAL_VERSION", "1.0.0")

BUNDLES_ROOT = APP_WORKSPACE / "offline-bundles/portal-jobs"
REPORTS_ROOT = APP_WORKSPACE / "reports/jobs"
CONFIGS_ROOT = APP_WORKSPACE / "configs"
PUBLISH_ROOT = APP_WORKSPACE / "published-artifacts/portal-jobs"

NEXUS_URL = os.getenv("NEXUS_URL", "http://localhost:8081").rstrip("/")
NEXUS_ADMIN_PASSWORD = os.getenv("NEXUS_ADMIN_PASSWORD", "")
NEXUS_DOCKER_HOSTED = os.getenv("NEXUS_DOCKER_HOSTED", "localhost:5000")
NEXUS_DOCKER_GROUP = os.getenv("NEXUS_DOCKER_GROUP", "localhost:5002")
NEXUS_PYPI_HOSTED = os.getenv("NEXUS_PYPI_HOSTED", "pypi-hosted")
NEXUS_APT_HOSTED = os.getenv("NEXUS_APT_HOSTED", "apt-internal-hosted")
NEXUS_RAW_OFFLINE_BUNDLES = os.getenv("NEXUS_RAW_OFFLINE_BUNDLES", "raw-offline-bundles")
NEXUS_FIREWALL_HELPER_IMAGE = os.getenv("NEXUS_FIREWALL_HELPER_IMAGE", "alpine:3.20")
NEXUS_PROTECTED_PORTS = [int(x) for x in os.getenv("NEXUS_PROTECTED_PORTS", "8081,5000,5001,5002").split(",") if x.strip()]

PYTHON2_DOCKER_IMAGE = os.getenv("PYTHON2_DOCKER_IMAGE", "python:2.7-slim")
PYTHON2_DOCKER_NETWORK = os.getenv("PYTHON2_DOCKER_NETWORK", "airgap-artifacts-platform")
PYTHON2_PIP_INDEX_URL = os.getenv("PYTHON2_PIP_INDEX_URL", f"{NEXUS_URL}/repository/pypi2-group/simple")

PORTAL_ADMIN_USER = os.getenv("PORTAL_ADMIN_USER", "admin")
PORTAL_ADMIN_PASSWORD = os.getenv("PORTAL_ADMIN_PASSWORD", "change-me")
PORTAL_ADMIN_PASSWORD_HASH = os.getenv("PORTAL_ADMIN_PASSWORD_HASH", "")
PORTAL_SESSION_SECRET = os.getenv("PORTAL_SESSION_SECRET", "dev-secret-change-me")
PORTAL_COOKIE_SECURE = os.getenv("PORTAL_COOKIE_SECURE", "false").lower() == "true"
PORTAL_LOGIN_MAX_FAILURES = int(os.getenv("PORTAL_LOGIN_MAX_FAILURES", "5"))
PORTAL_LOGIN_LOCKOUT_SECONDS = int(os.getenv("PORTAL_LOGIN_LOCKOUT_SECONDS", "300"))
PORTAL_STRICT_HOST_KEY = os.getenv("PORTAL_STRICT_HOST_KEY", "false").lower() == "true"
PORTAL_REMOTE_DEFAULT_DIR = os.getenv("PORTAL_REMOTE_DEFAULT_DIR", "/tmp/airgap-deployments")
PORTAL_ENABLE_GRYPE_SCAN = os.getenv("PORTAL_ENABLE_GRYPE_SCAN", "false").lower() == "true"
PORTAL_BLOCK_CRITICAL = os.getenv("PORTAL_BLOCK_CRITICAL", "true").lower() == "true"
PORTAL_BLOCK_LATEST_TAG = os.getenv("PORTAL_BLOCK_LATEST_TAG", "true").lower() == "true"
PORTAL_BLOCK_HIGH = os.getenv("PORTAL_BLOCK_HIGH", "false").lower() == "true"
PORTAL_BLOCK_UNPINNED_PACKAGES = os.getenv("PORTAL_BLOCK_UNPINNED_PACKAGES", "true").lower() == "true"
PORTAL_BLOCK_UNSAFE_APT = os.getenv("PORTAL_BLOCK_UNSAFE_APT", "true").lower() == "true"
PORTAL_REQUIRE_SECURITY_GATE = os.getenv("PORTAL_REQUIRE_SECURITY_GATE", "true").lower() == "true"
PORTAL_MIN_FREE_DISK_MB = int(os.getenv("PORTAL_MIN_FREE_DISK_MB", "2048"))
PORTAL_CLEANUP_KEEP_DAYS = int(os.getenv("PORTAL_CLEANUP_KEEP_DAYS", "14"))

COOKIE_NAME = "airgap_portal_session"
CSRF_COOKIE_NAME = "airgap_portal_csrf"
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_FAILURES: dict[str, list[float]] = {}

app = FastAPI(
    title="Nexus Air-Gapped Deployment Portal",
    version=APP_VERSION,
    docs_url=None,
    redoc_url=None,
)

app.mount("/static", StaticFiles(directory="/app/app/static"), name="static")


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

class LoginIn(BaseModel):
    username: str
    password: str


class ServerIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    host: str = Field(min_length=3, max_length=255)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=80)
    auth_method: str = Field(default="password")
    key_path: str = ""


class SSHPayload(BaseModel):
    server_id: str | None = None
    host: str | None = None
    port: int = 22
    username: str | None = None
    password: str | None = None
    key_path: str | None = None
    passphrase: str | None = None
    remote_dir: str = PORTAL_REMOTE_DEFAULT_DIR
    use_sudo: bool = False
    timeout: int = 10


class BundleSpec(BaseModel):
    docker_images: list[str] = []
    python_packages: list[str] = []
    python2_packages: list[str] = []
    apt_packages: list[str] = []
    apt_target: str = "ubuntu-noble"


class SecurityGateIn(BaseModel):
    bundle: BundleSpec
    deploy: SSHPayload | None = None
    extra_commands: str = ""


class SecuritySettingsIn(BaseModel):
    strict_host_key: bool = PORTAL_STRICT_HOST_KEY
    grype_scan_enabled: bool = PORTAL_ENABLE_GRYPE_SCAN
    block_latest_tag: bool = PORTAL_BLOCK_LATEST_TAG
    block_critical: bool = PORTAL_BLOCK_CRITICAL
    block_high: bool = PORTAL_BLOCK_HIGH
    block_unpinned_packages: bool = PORTAL_BLOCK_UNPINNED_PACKAGES
    block_unsafe_apt: bool = PORTAL_BLOCK_UNSAFE_APT
    require_security_gate: bool = PORTAL_REQUIRE_SECURITY_GATE
    min_free_disk_mb: int = Field(default=PORTAL_MIN_FREE_DISK_MB, ge=512, le=1048576)
    cleanup_keep_days: int = Field(default=PORTAL_CLEANUP_KEEP_DAYS, ge=1, le=365)
    remote_default_dir: str = Field(default=PORTAL_REMOTE_DEFAULT_DIR, min_length=1, max_length=255)


ACCESS_PERMISSIONS = {
    "docker_pull": "Docker pull/read from hosted, proxy, and group repositories",
    "docker_push": "Docker push/write to docker-hosted",
    "pypi_read": "Read Python packages",
    "apt_read": "Read Debian/Ubuntu packages",
    "raw_read": "Read raw offline bundles/releases/backups",
    "raw_write": "Write raw offline bundles/releases/backups",
}


class AccessGroupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    permissions: list[str] = []


class AccessPrincipalIn(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9_.@-]+$")
    display_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=180)
    password: str = Field(default="", max_length=256)
    group_ids: list[str] = []
    enabled: bool = True
    principal_type: Literal["user", "service"] = "service"


class AccessIpRuleIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    cidr: str = Field(min_length=3, max_length=64)
    description: str = Field(default="", max_length=500)
    enabled: bool = True


class DockerPublishIn(BaseModel):
    source_image: str = Field(min_length=2, max_length=240)
    target_image: str = Field(default="", max_length=240)
    repository: str = Field(default="docker-hosted", max_length=80)


class PythonPackageFetchIn(BaseModel):
    package_name: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.-]+$")
    package_version: str = Field(default="", max_length=80)
    python_version: Literal["python3", "python2"] = "python3"
    repository: str = Field(default=NEXUS_PYPI_HOSTED, max_length=80)
    include_dependencies: bool = True


class DebianPackageFetchIn(BaseModel):
    package_name: str = Field(min_length=1, max_length=160, pattern=r"^[a-z0-9][a-z0-9+.-]*$")
    package_version: str = Field(default="", max_length=120)
    target_release: str = "ubuntu-noble"
    repository: str = Field(default=NEXUS_APT_HOSTED, max_length=80)
    include_dependencies: bool = True


class DeploySpec(SSHPayload):
    enabled: bool = False
    docker_load: bool = True
    python_wheels: bool = True
    apt_mini: bool = True
    extra_commands: str = ""


class JobIn(BaseModel):
    bundle: BundleSpec
    deploy: DeploySpec = DeploySpec()


APT_TARGETS: dict[str, dict[str, str]] = {
    "ubuntu-noble": {
        "label": "Ubuntu 24.04 LTS Noble Numbat",
        "image": "ubuntu:24.04",
        "distro": "ubuntu",
        "codename": "noble",
        "components": "main universe multiverse restricted",
    },
    "ubuntu-plucky": {
        "label": "Ubuntu 25.04 Plucky Puffin",
        "image": "ubuntu:25.04",
        "distro": "ubuntu",
        "codename": "plucky",
        "components": "main universe multiverse restricted",
    },
    "ubuntu-resolute": {
        "label": "Ubuntu 26.04 LTS Resolute Raccoon",
        "image": "ubuntu:26.04",
        "distro": "ubuntu",
        "codename": "resolute",
        "components": "main universe multiverse restricted",
    },
    "debian-bullseye": {
        "label": "Debian 11 Bullseye",
        "image": "debian:11",
        "distro": "debian",
        "codename": "bullseye",
        "components": "main contrib non-free",
    },
    "debian-bookworm": {
        "label": "Debian 12 Bookworm",
        "image": "debian:12",
        "distro": "debian",
        "codename": "bookworm",
        "components": "main contrib non-free non-free-firmware",
    },
    "debian-trixie": {
        "label": "Debian 13 Trixie",
        "image": "debian:13",
        "distro": "debian",
        "codename": "trixie",
        "components": "main contrib non-free non-free-firmware",
    },
}


# -----------------------------------------------------------------------------
# Basic utilities
# -----------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    PUBLISH_ROOT.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    ensure_dirs()

    with db_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS servers (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL,
            username TEXT NOT NULL,
            auth_method TEXT NOT NULL,
            key_path TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL,
            target_server TEXT,
            payload_json TEXT NOT NULL,
            preflight_json TEXT,
            security_json TEXT,
            rollback_json TEXT,
            bundle_path TEXT,
            bundle_sha256 TEXT,
            report_html TEXT,
            report_pdf TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS job_logs (
            id BIGSERIAL PRIMARY KEY,
            job_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            CONSTRAINT fk_job_logs_job
                FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id BIGSERIAL PRIMARY KEY,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            details TEXT,
            ip TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS security_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS access_groups (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            permissions_json TEXT NOT NULL,
            nexus_role_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS access_principals (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            email TEXT NOT NULL,
            principal_type TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            nexus_user_id TEXT NOT NULL,
            last_password_set_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS access_group_members (
            principal_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            PRIMARY KEY(principal_id, group_id),
            CONSTRAINT fk_access_group_members_principal
                FOREIGN KEY(principal_id) REFERENCES access_principals(id) ON DELETE CASCADE,
            CONSTRAINT fk_access_group_members_group
                FOREIGN KEY(group_id) REFERENCES access_groups(id) ON DELETE CASCADE
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS access_ip_rules (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            cidr TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS access_enforcement (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            updated_by TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS publish_jobs (
            id TEXT PRIMARY KEY,
            artifact_type TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            repository TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            file_path TEXT,
            file_sha256 TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            error TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS publish_logs (
            id BIGSERIAL PRIMARY KEY,
            job_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL,
            CONSTRAINT fk_publish_logs_job
                FOREIGN KEY(job_id) REFERENCES publish_jobs(id) ON DELETE CASCADE
        )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_job_id_id ON job_logs(job_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_id_desc ON audit_log(id DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_jobs_created_at ON publish_jobs(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_publish_logs_job_id_id ON publish_logs(job_id, id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_group_members_group ON access_group_members(group_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_ip_rules_enabled_cidr ON access_ip_rules(enabled, cidr)")
        conn.execute(
            """
            INSERT INTO schema_migrations(version, name, applied_at)
            VALUES (?, ?, ?)
            ON CONFLICT(version) DO NOTHING
            """,
            (1, "initial_postgresql_schema", utc_now()),
        )

        seed_security_settings(conn)
        seed_access_control(conn)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return slug or secrets.token_hex(4)


SECURITY_DEFAULTS: dict[str, Any] = {
    "strict_host_key": PORTAL_STRICT_HOST_KEY,
    "grype_scan_enabled": PORTAL_ENABLE_GRYPE_SCAN,
    "block_latest_tag": PORTAL_BLOCK_LATEST_TAG,
    "block_critical": PORTAL_BLOCK_CRITICAL,
    "block_high": PORTAL_BLOCK_HIGH,
    "block_unpinned_packages": PORTAL_BLOCK_UNPINNED_PACKAGES,
    "block_unsafe_apt": PORTAL_BLOCK_UNSAFE_APT,
    "require_security_gate": PORTAL_REQUIRE_SECURITY_GATE,
    "min_free_disk_mb": PORTAL_MIN_FREE_DISK_MB,
    "cleanup_keep_days": PORTAL_CLEANUP_KEEP_DAYS,
    "remote_default_dir": PORTAL_REMOTE_DEFAULT_DIR,
}


def seed_security_settings(conn) -> None:
    now = utc_now()
    for key, value in SECURITY_DEFAULTS.items():
        conn.execute(
            """
            INSERT INTO security_settings(key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (key, json.dumps(value), now, "system"),
        )


def security_settings() -> dict[str, Any]:
    settings = dict(SECURITY_DEFAULTS)
    with db_conn() as conn:
        rows = conn.execute("SELECT key, value FROM security_settings").fetchall()

    for row in rows:
        if row["key"] not in settings:
            continue
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]

    return settings


def update_security_settings(payload: SecuritySettingsIn, user: str) -> dict[str, Any]:
    data = payload.model_dump()
    now = utc_now()
    with db_conn() as conn:
        for key, value in data.items():
            conn.execute(
                """
                INSERT INTO security_settings(key, value, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (key, json.dumps(value), now, user),
            )
    return security_settings()


def seed_access_control(conn) -> None:
    now = utc_now()
    defaults = [
        ("Registry Readers", "Trusted systems allowed to pull mirrored artifacts.", ["docker_pull", "pypi_read", "apt_read", "raw_read"]),
        ("Registry Publishers", "Trusted systems allowed to publish internal Docker and raw artifacts.", ["docker_pull", "docker_push", "raw_read", "raw_write"]),
    ]
    for name, description, permissions in defaults:
        group_id = str(uuid.uuid4())
        role_id = f"portal-acl-{slugify(name)}"
        conn.execute(
            """
            INSERT INTO access_groups(id, name, description, permissions_json, nexus_role_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO NOTHING
            """,
            (group_id, name, description, json.dumps(permissions), role_id, now, now),
        )
    conn.execute(
        """
        INSERT INTO access_enforcement(key, value, updated_at, updated_by)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO NOTHING
        """,
        ("last_status", json.dumps({"status": "NEVER_APPLIED"}), now, "system"),
    )


def effective_remote_dir(value: str | None = None) -> str:
    settings_dir = str(security_settings()["remote_default_dir"])
    if not value or value == PORTAL_REMOTE_DEFAULT_DIR:
        return settings_dir
    return value


def validate_permissions(permissions: list[str]) -> list[str]:
    clean = list(dict.fromkeys(x.strip() for x in permissions if x.strip()))
    invalid = [x for x in clean if x not in ACCESS_PERMISSIONS]
    if invalid:
        raise HTTPException(status_code=400, detail=f"Unsupported permission(s): {', '.join(invalid)}")
    if not clean:
        raise HTTPException(status_code=400, detail="Select at least one permission for this access group.")
    return clean


def validate_cidr(cidr: str) -> str:
    try:
        return str(ipaddress.ip_network(cidr.strip(), strict=False))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid IP address or CIDR: {cidr}") from exc


def validate_principal_groups(group_ids: list[str], enabled: bool, known_groups: set[str]) -> list[str]:
    clean = list(dict.fromkeys(x.strip() for x in group_ids if x.strip()))
    unknown = [x for x in clean if x not in known_groups]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown group id(s): {', '.join(unknown)}")
    if enabled and not clean:
        raise HTTPException(status_code=400, detail="Assign at least one group before enabling this account.")
    return clean


def audit(action: str, entity: str, details: Any = None, request: Request | None = None, actor: str = "admin") -> None:
    ip = request.client.host if request and request.client else ""
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO audit_log(ts, actor, action, entity, details, ip) VALUES (?, ?, ?, ?, ?, ?)",
            (utc_now(), actor, action, entity, json.dumps(details, ensure_ascii=False), ip),
        )


def log_job(job_id: str, level: str, message: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO job_logs(job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, utc_now(), level.upper(), message),
        )


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return

    keys = list(fields.keys())
    sql = "UPDATE jobs SET " + ", ".join(f"{k}=?" for k in keys) + " WHERE id=?"
    values = [fields[k] for k in keys] + [job_id]

    with db_conn() as conn:
        conn.execute(sql, values)


def log_publish(job_id: str, level: str, message: str) -> None:
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO publish_logs(job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, utc_now(), level.upper(), message),
        )


def update_publish_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    keys = list(fields.keys())
    sql = "UPDATE publish_jobs SET " + ", ".join(f"{k}=?" for k in keys) + " WHERE id=?"
    values = [fields[k] for k in keys] + [job_id]
    with db_conn() as conn:
        conn.execute(sql, values)


def run_publish_cmd(cmd: list[str], job_id: str, timeout: int | None = None, log_command: bool = True) -> str:
    if log_command:
        log_publish(job_id, "INFO", "$ " + " ".join(shlex.quote(x) for x in cmd))

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output: list[str] = []
    assert proc.stdout is not None
    started = time.time()

    for line in proc.stdout:
        if timeout and time.time() - started > timeout:
            proc.kill()
            raise RuntimeError(f"Command timeout: {' '.join(cmd)}")
        line = line.rstrip()
        output.append(line)
        if line:
            log_publish(job_id, "INFO", line)

    rc = proc.wait()
    if rc != 0:
        raise RuntimeError(f"Command failed with exit code {rc}: {' '.join(cmd)}")
    return "\n".join(output)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0

    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except FileNotFoundError:
                pass

    return total


def run_local_cmd(cmd: list[str], job_id: str | None = None, cwd: Path | None = None, timeout: int | None = None) -> str:
    if job_id:
        log_job(job_id, "INFO", "$ " + " ".join(shlex.quote(x) for x in cmd))

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output: list[str] = []
    assert proc.stdout is not None

    started = time.time()

    for line in proc.stdout:
        if timeout and time.time() - started > timeout:
            proc.kill()
            raise RuntimeError(f"Command timeout: {' '.join(cmd)}")

        line = line.rstrip()
        output.append(line)
        if job_id and line:
            log_job(job_id, "INFO", line)

    rc = proc.wait()

    if rc != 0:
        raise RuntimeError(f"Command failed with exit code {rc}: {' '.join(cmd)}")

    return "\n".join(output)


def host_path_for(container_path: Path) -> str:
    if not HOST_PROJECT_ROOT:
        raise RuntimeError("HOST_PROJECT_ROOT is not configured.")

    rel = container_path.resolve().relative_to(APP_WORKSPACE.resolve())
    return str(Path(HOST_PROJECT_ROOT) / rel)


# -----------------------------------------------------------------------------
# Nexus Access Control
# -----------------------------------------------------------------------------

def nexus_request(method: str, path: str, *, json_body: Any = None, text_body: str | None = None, expected: set[int] | None = None) -> tuple[int, Any]:
    if not NEXUS_ADMIN_PASSWORD:
        raise HTTPException(status_code=400, detail="NEXUS_ADMIN_PASSWORD is not configured.")

    expected = expected or {200, 201, 204}
    headers: dict[str, str] = {}
    data = None

    if json_body is not None:
        headers["Content-Type"] = "application/json"
    if text_body is not None:
        headers["Content-Type"] = "text/plain"
        data = text_body

    try:
        response = requests.request(
            method,
            f"{NEXUS_URL}{path}",
            auth=("admin", NEXUS_ADMIN_PASSWORD),
            json=json_body,
            data=data,
            headers=headers,
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Nexus request failed: {exc}") from exc

    if response.status_code not in expected:
        detail = response.text.strip() or f"HTTP {response.status_code}"
        raise HTTPException(status_code=502, detail=f"Nexus API error for {path}: {detail}")

    if not response.text.strip():
        return response.status_code, None

    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text


def nexus_privileges_for_permissions(permissions: list[str]) -> list[str]:
    privileges: set[str] = set()

    def repo(format_name: str, repo_name: str, actions: list[str]) -> None:
        for action in actions:
            privileges.add(f"nx-repository-view-{format_name}-{repo_name}-{action}")

    if "docker_pull" in permissions:
        for name in ["docker-hosted", "docker-proxy", "docker-group", "*"]:
            repo("docker", name, ["browse", "read"])

    if "docker_push" in permissions:
        repo("docker", "docker-hosted", ["browse", "read", "add", "edit"])

    if "pypi_read" in permissions:
        for name in ["pypi-hosted", "pypi-proxy", "pypi-group", "*"]:
            repo("pypi", name, ["browse", "read"])

    if "apt_read" in permissions:
        repo("apt", "*", ["browse", "read"])

    if "raw_read" in permissions:
        repo("raw", "*", ["browse", "read"])

    if "raw_write" in permissions:
        repo("raw", "*", ["browse", "read", "add", "edit"])

    return sorted(privileges)


def sync_nexus_role(group: dict[str, Any]) -> dict[str, Any]:
    permissions = json.loads(group["permissions_json"])
    role_id = group["nexus_role_id"]
    body = {
        "id": role_id,
        "name": group["name"],
        "description": group["description"] or "Managed by Nexus Portal access control.",
        "privileges": nexus_privileges_for_permissions(permissions),
        "roles": [],
    }

    status, _ = nexus_request("GET", f"/service/rest/v1/security/roles/{role_id}", expected={200, 404})
    if status == 404:
        nexus_request("POST", "/service/rest/v1/security/roles", json_body=body, expected={200, 201, 204})
        action = "created"
    else:
        nexus_request("PUT", f"/service/rest/v1/security/roles/{role_id}", json_body=body, expected={200, 204})
        action = "updated"

    return {"role_id": role_id, "action": action, "privileges": body["privileges"]}


def disable_nexus_anonymous() -> dict[str, Any]:
    body = {"enabled": False, "userId": "anonymous", "realmName": "NexusAuthorizingRealm"}
    nexus_request("PUT", "/service/rest/v1/security/anonymous", json_body=body, expected={200, 204})
    return {"anonymous_access": "disabled"}


def principal_roles(conn, principal_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT g.nexus_role_id
        FROM access_group_members m
        JOIN access_groups g ON g.id=m.group_id
        WHERE m.principal_id=?
        ORDER BY g.name ASC
        """,
        (principal_id,),
    ).fetchall()
    return [r["nexus_role_id"] for r in rows]


def nexus_user_exists(user_id: str) -> bool:
    _, users = nexus_request("GET", f"/service/rest/v1/security/users?userId={quote(user_id, safe='')}", expected={200})
    if not isinstance(users, list):
        return False
    return any(str(user.get("userId", "")) == user_id for user in users if isinstance(user, dict))


def sync_nexus_principal(conn, principal_id: str, password: str | None = None) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM access_principals WHERE id=?", (principal_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Principal not found")

    principal = row_to_dict(row)
    roles = principal_roles(conn, principal_id)
    names = (principal["display_name"] or principal["username"]).split(" ", 1)
    first_name = names[0]
    last_name = names[1] if len(names) > 1 else principal["principal_type"]
    status = "active" if int(principal["enabled"]) else "disabled"
    user_id = principal["nexus_user_id"]
    email = principal["email"] or f"{principal['username']}@local.invalid"

    if not nexus_user_exists(user_id):
        create_password = password or secrets.token_urlsafe(24)
        body = {
            "userId": user_id,
            "firstName": first_name,
            "lastName": last_name,
            "emailAddress": email,
            "password": create_password,
            "status": status,
            "roles": roles,
        }
        nexus_request("POST", "/service/rest/v1/security/users", json_body=body, expected={200, 201, 204})
        conn.execute("UPDATE access_principals SET last_password_set_at=?, updated_at=? WHERE id=?", (utc_now(), utc_now(), principal_id))
        return {"user_id": user_id, "action": "created", "generated_password": create_password if not password else None, "roles": roles}

    body = {
        "userId": user_id,
        "firstName": first_name,
        "lastName": last_name,
        "emailAddress": email,
        "source": "default",
        "status": status,
        "roles": roles,
    }
    nexus_request("PUT", f"/service/rest/v1/security/users/{user_id}", json_body=body, expected={200, 204})

    result: dict[str, Any] = {"user_id": user_id, "action": "updated", "roles": roles}
    if password:
        nexus_request("PUT", f"/service/rest/v1/security/users/{user_id}/change-password", text_body=password, expected={200, 204})
        conn.execute("UPDATE access_principals SET last_password_set_at=?, updated_at=? WHERE id=?", (utc_now(), utc_now(), principal_id))
        result["password_changed"] = True
    return result


def docker_login_hosted(job_id: str) -> None:
    if not NEXUS_ADMIN_PASSWORD:
        raise RuntimeError("NEXUS_ADMIN_PASSWORD is not configured.")
    log_publish(job_id, "INFO", f"Logging in to Docker registry {NEXUS_DOCKER_HOSTED}")
    proc = subprocess.run(
        ["docker", "login", NEXUS_DOCKER_HOSTED, "-u", "admin", "--password-stdin"],
        input=NEXUS_ADMIN_PASSWORD,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=60,
    )
    for line in proc.stdout.splitlines():
        if line.strip():
            log_publish(job_id, "INFO", line.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"Docker login failed for {NEXUS_DOCKER_HOSTED}")


def image_ref_name(ref: str) -> str:
    ref = ref.strip()
    if not ref:
        raise RuntimeError("Image reference is empty.")
    return ref


def hosted_image_ref(image: str) -> str:
    image = image_ref_name(image)
    registry = NEXUS_DOCKER_HOSTED.rstrip("/")
    if image.startswith(registry + "/"):
        return image
    if "/" not in image.split("/")[0] and "/" not in image.split(":")[0]:
        return f"{registry}/library/{image}"
    return f"{registry}/{image}"


def docker_image_exists_locally(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def docker_image_exists_in_nexus(target_ref: str, job_id: str) -> bool:
    try:
        docker_login_hosted(job_id)
        run_publish_cmd(["docker", "manifest", "inspect", target_ref], job_id, timeout=60)
        return True
    except Exception as exc:
        log_publish(job_id, "WARN", f"Nexus image lookup did not find {target_ref}: {exc}")
        return False


def ensure_apt_hosted_repository(job_id: str, repository: str) -> None:
    status, _ = nexus_request("GET", f"/service/rest/v1/repositories/{repository}", expected={200, 404})
    if status == 200:
        return

    log_publish(job_id, "INFO", f"Creating hosted APT repository: {repository}")
    body = {
        "name": repository,
        "online": True,
        "storage": {
            "blobStoreName": "default",
            "strictContentTypeValidation": True,
            "writePolicy": "ALLOW",
        },
        "apt": {
            "distribution": "internal",
            "flat": False,
        },
    }
    nexus_request("POST", "/service/rest/v1/repositories/apt/hosted", json_body=body, expected={200, 201, 204})


def upload_component_file(job_id: str, repository: str, field_name: str, file_path: Path) -> dict[str, Any]:
    if not NEXUS_ADMIN_PASSWORD:
        raise RuntimeError("NEXUS_ADMIN_PASSWORD is not configured.")

    log_publish(job_id, "INFO", f"Uploading {file_path.name} to Nexus repository {repository}")
    with file_path.open("rb") as fh:
        response = requests.post(
            f"{NEXUS_URL}/service/rest/v1/components",
            params={"repository": repository},
            auth=("admin", NEXUS_ADMIN_PASSWORD),
            files={field_name: (file_path.name, fh, "application/octet-stream")},
            timeout=300,
        )

    if response.status_code not in {200, 201, 204}:
        raise RuntimeError(f"Nexus upload failed: HTTP {response.status_code} {response.text[:500]}")

    return {"repository": repository, "filename": file_path.name, "status_code": response.status_code}


def python_requirement(package_name: str, package_version: str) -> str:
    if package_version.strip():
        return f"{package_name.strip()}=={package_version.strip()}"
    return package_name.strip()


def publish_downloaded_python_files(job_id: str, repository: str, out_dir: Path) -> list[dict[str, Any]]:
    files = sorted([p for p in out_dir.iterdir() if p.is_file()])
    if not files:
        raise RuntimeError("Python package download produced no files.")

    results = []
    for file_path in files:
        update_publish_job(job_id, file_sha256=sha256_file(file_path))
        results.append(upload_component_file(job_id, repository, "pypi.asset", file_path))
    return results


def run_python_fetch_publish_job(job_id: str, payload: dict[str, Any]) -> None:
    update_publish_job(job_id, status="RUNNING", started_at=utc_now())
    try:
        out_dir = PUBLISH_ROOT / job_id / "python"
        out_dir.mkdir(parents=True, exist_ok=True)
        requirement = python_requirement(payload["package_name"], payload.get("package_version", ""))
        repository = payload.get("repository") or NEXUS_PYPI_HOSTED
        include_deps = bool(payload.get("include_dependencies", True))
        python_version = payload.get("python_version", "python3")

        log_publish(job_id, "INFO", f"Fetching Python package: {requirement} ({python_version})")
        log_publish(job_id, "INFO", f"Dependencies: {'included' if include_deps else 'not included'}")

        if python_version == "python2":
            host_out = host_path_for(out_dir)
            command = (
                "python -m ensurepip || true; "
                "python -m pip install --upgrade 'pip<21' 'setuptools<45' 'wheel<0.38'; "
                f"python -m pip download {' ' if include_deps else '--no-deps '}--no-cache-dir "
                f"-d /out {shlex.quote(requirement)}"
            )
            run_publish_cmd(
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{host_out}:/out",
                    PYTHON2_DOCKER_IMAGE,
                    "bash",
                    "-lc",
                    command,
                ],
                job_id,
                timeout=900,
            )
        else:
            cmd = ["python", "-m", "pip", "download", "--no-cache-dir", "-d", str(out_dir)]
            if not include_deps:
                cmd.append("--no-deps")
            cmd.append(requirement)
            run_publish_cmd(cmd, job_id, timeout=900)

        results = publish_downloaded_python_files(job_id, repository, out_dir)
        update_publish_job(job_id, status="SUCCESS", target=f"{repository}/{requirement}", finished_at=utc_now())
        log_publish(job_id, "INFO", f"Python fetch and publish completed: {json.dumps(results)}")
    except Exception as exc:
        update_publish_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))
        log_publish(job_id, "ERROR", str(exc))


def apt_package_spec(package_name: str, package_version: str) -> str:
    if package_version.strip():
        return f"{package_name.strip()}={package_version.strip()}"
    return package_name.strip()


def apt_fetch_script(target_cfg: dict[str, str], package_spec: str, include_dependencies: bool) -> str:
    distro = target_cfg["distro"]
    codename = target_cfg["codename"]
    components = target_cfg["components"]
    package_q = shlex.quote(package_spec)
    deps_cmd = f"apt-get install -y --download-only --no-install-recommends {package_q}; cp -a /var/cache/apt/archives/*.deb /out/ 2>/dev/null || true"
    single_cmd = f"cd /out && apt-get download {package_q}"
    fetch_cmd = deps_cmd if include_dependencies else single_cmd

    return f"""
set -euo pipefail
DISTRO={shlex.quote(distro)}
CODENAME={shlex.quote(codename)}
COMPONENTS={shlex.quote(components)}

if [[ "$DISTRO" == "ubuntu" ]]; then
  if [[ "$CODENAME" == "plucky" ]]; then
    BASE_URL="http://old-releases.ubuntu.com/ubuntu"
    SECURITY_URL="http://old-releases.ubuntu.com/ubuntu"
  else
    BASE_URL="http://archive.ubuntu.com/ubuntu"
    SECURITY_URL="http://security.ubuntu.com/ubuntu"
  fi
  cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] $BASE_URL $CODENAME $COMPONENTS
deb [trusted=yes] $BASE_URL $CODENAME-updates $COMPONENTS
deb [trusted=yes] $SECURITY_URL $CODENAME-security $COMPONENTS
EOF
else
  BASE_URL="http://deb.debian.org/debian"
  SECURITY_URL="http://security.debian.org/debian-security"
  cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] $BASE_URL $CODENAME $COMPONENTS
deb [trusted=yes] $BASE_URL $CODENAME-updates $COMPONENTS
deb [trusted=yes] $SECURITY_URL $CODENAME-security $COMPONENTS
EOF
fi

apt-get update
mkdir -p /out
{fetch_cmd}
"""


def run_debian_fetch_publish_job(job_id: str, payload: dict[str, Any]) -> None:
    update_publish_job(job_id, status="RUNNING", started_at=utc_now())
    try:
        target_release = payload.get("target_release") or "ubuntu-noble"
        target_cfg = APT_TARGETS.get(target_release)
        if not target_cfg:
            raise RuntimeError(f"Unsupported Debian/Ubuntu target release: {target_release}")

        out_dir = PUBLISH_ROOT / job_id / "debian"
        out_dir.mkdir(parents=True, exist_ok=True)
        host_out = host_path_for(out_dir)
        package_spec = apt_package_spec(payload["package_name"], payload.get("package_version", ""))
        repository = payload.get("repository") or NEXUS_APT_HOSTED
        include_deps = bool(payload.get("include_dependencies", True))

        log_publish(job_id, "INFO", f"Fetching Debian package: {package_spec}")
        log_publish(job_id, "INFO", f"Target release: {target_release} ({target_cfg['label']})")
        log_publish(job_id, "INFO", f"Dependencies: {'included' if include_deps else 'not included'}")

        run_publish_cmd(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{host_out}:/out",
                target_cfg["image"],
                "bash",
                "-lc",
                apt_fetch_script(target_cfg, package_spec, include_deps),
            ],
            job_id,
            timeout=1200,
        )

        files = sorted(out_dir.glob("*.deb"))
        if not files:
            raise RuntimeError("Debian package fetch produced no .deb files.")

        ensure_apt_hosted_repository(job_id, repository)
        results = []
        for file_path in files:
            update_publish_job(job_id, file_sha256=sha256_file(file_path))
            results.append(upload_component_file(job_id, repository, "apt.asset", file_path))

        update_publish_job(job_id, status="SUCCESS", target=f"{repository}/{package_spec}", finished_at=utc_now())
        log_publish(job_id, "INFO", f"Debian fetch and publish completed: {json.dumps(results)}")
    except Exception as exc:
        update_publish_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))
        log_publish(job_id, "ERROR", str(exc))


def run_docker_publish_job(job_id: str, payload: dict[str, Any], user: str) -> None:
    update_publish_job(job_id, status="RUNNING", started_at=utc_now())
    try:
        source = image_ref_name(payload["source_image"])
        target_image = payload.get("target_image") or source
        target_ref = hosted_image_ref(target_image)

        log_publish(job_id, "INFO", f"Publishing Docker image {source} -> {target_ref}")

        if docker_image_exists_in_nexus(target_ref, job_id):
            log_publish(job_id, "INFO", f"Image already exists in Nexus: {target_ref}")
        else:
            if docker_image_exists_locally(source):
                log_publish(job_id, "INFO", f"Using local Docker image: {source}")
            else:
                log_publish(job_id, "INFO", f"Image not local. Pulling from Docker Hub/source registry: {source}")
                run_publish_cmd(["docker", "pull", source], job_id, timeout=900)

            docker_login_hosted(job_id)
            run_publish_cmd(["docker", "tag", source, target_ref], job_id, timeout=120)
            run_publish_cmd(["docker", "push", target_ref], job_id, timeout=1800)

        update_publish_job(job_id, status="SUCCESS", target=target_ref, finished_at=utc_now())
        log_publish(job_id, "INFO", "Docker publish finished successfully")
    except Exception as exc:
        update_publish_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))
        log_publish(job_id, "ERROR", str(exc))


def loaded_image_from_output(output: str) -> str | None:
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Loaded image:"):
            return line.split("Loaded image:", 1)[1].strip()
    return None


def run_docker_archive_publish_job(job_id: str, archive_path: Path, target_image: str, repository: str) -> None:
    update_publish_job(job_id, status="RUNNING", started_at=utc_now())
    try:
        archive_hash = sha256_file(archive_path)
        update_publish_job(job_id, file_sha256=archive_hash)
        log_publish(job_id, "INFO", f"Docker archive SHA256: {archive_hash}")
        log_publish(job_id, "INFO", f"Loading Docker archive: {archive_path.name}")

        output = run_publish_cmd(["docker", "load", "-i", str(archive_path)], job_id, timeout=900)
        loaded = loaded_image_from_output(output)
        source_ref = loaded or target_image
        if not source_ref:
            raise RuntimeError("Archive loaded by image ID only. Provide a target image name/tag.")

        final_ref = hosted_image_ref(target_image or source_ref)
        log_publish(job_id, "INFO", f"Publishing loaded image {source_ref} -> {final_ref}")
        docker_login_hosted(job_id)
        run_publish_cmd(["docker", "tag", source_ref, final_ref], job_id, timeout=120)
        run_publish_cmd(["docker", "push", final_ref], job_id, timeout=1800)
        update_publish_job(job_id, status="SUCCESS", target=final_ref, finished_at=utc_now())
        log_publish(job_id, "INFO", "Docker archive publish finished successfully")
    except Exception as exc:
        update_publish_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))
        log_publish(job_id, "ERROR", str(exc))


def run_file_publish_job(job_id: str, artifact_type: str, repository: str, file_path: Path) -> None:
    update_publish_job(job_id, status="RUNNING", started_at=utc_now())
    try:
        file_hash = sha256_file(file_path)
        update_publish_job(job_id, file_sha256=file_hash)
        log_publish(job_id, "INFO", f"SHA256: {file_hash}")

        if artifact_type == "python":
            result = upload_component_file(job_id, repository, "pypi.asset", file_path)
        elif artifact_type == "debian":
            ensure_apt_hosted_repository(job_id, repository)
            result = upload_component_file(job_id, repository, "apt.asset", file_path)
        else:
            raise RuntimeError(f"Unsupported artifact type: {artifact_type}")

        update_publish_job(job_id, status="SUCCESS", finished_at=utc_now(), target=f"{repository}/{file_path.name}")
        log_publish(job_id, "INFO", f"Upload finished successfully: {json.dumps(result)}")
    except Exception as exc:
        update_publish_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))
        log_publish(job_id, "ERROR", str(exc))


# -----------------------------------------------------------------------------
# Auth
# -----------------------------------------------------------------------------

def make_cookie(username: str) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + SESSION_TTL_SECONDS,
        "nonce": secrets.token_hex(8),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(raw).decode()
    sig = hmac.new(PORTAL_SESSION_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def make_csrf_token(session_cookie: str) -> str:
    return hmac.new(PORTAL_SESSION_SECRET.encode(), session_cookie.encode(), hashlib.sha256).hexdigest()


def parse_cookie(value: str | None) -> str | None:
    if not value or "." not in value:
        return None

    b64, sig = value.rsplit(".", 1)
    expected = hmac.new(PORTAL_SESSION_SECRET.encode(), b64.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(sig, expected):
        return None

    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode()))
    except Exception:
        return None

    if int(payload.get("exp", 0)) < int(time.time()):
        return None

    return str(payload.get("u", "")) or None


def login_key(request: Request, username: str) -> str:
    ip = request.client.host if request.client else "unknown"
    return f"{ip}:{username}"


def login_locked(request: Request, username: str) -> bool:
    key = login_key(request, username)
    cutoff = time.time() - PORTAL_LOGIN_LOCKOUT_SECONDS
    failures = [ts for ts in LOGIN_FAILURES.get(key, []) if ts >= cutoff]
    LOGIN_FAILURES[key] = failures
    return len(failures) >= PORTAL_LOGIN_MAX_FAILURES


def record_login_failure(request: Request, username: str) -> None:
    key = login_key(request, username)
    LOGIN_FAILURES.setdefault(key, []).append(time.time())


def clear_login_failures(request: Request, username: str) -> None:
    LOGIN_FAILURES.pop(login_key(request, username), None)


def verify_password(password: str) -> bool:
    if PORTAL_ADMIN_PASSWORD_HASH.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, expected_hash = PORTAL_ADMIN_PASSWORD_HASH.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode(),
                salt.encode(),
                int(iterations),
            ).hex()
            return hmac.compare_digest(digest, expected_hash)
        except Exception:
            return False

    return hmac.compare_digest(password, PORTAL_ADMIN_PASSWORD)


def current_user(request: Request) -> str:
    username = parse_cookie(request.cookies.get(COOKIE_NAME))
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return username


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response


@app.middleware("http")
async def csrf_protection(request: Request, call_next):
    csrf_exempt_paths = {"/api/login", "/api/logout"}
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in csrf_exempt_paths:
        session_cookie = request.cookies.get(COOKIE_NAME)
        username = parse_cookie(session_cookie)
        if username:
            expected = make_csrf_token(session_cookie or "")
            supplied = request.headers.get("X-CSRF-Token") or ""
            cookie_token = request.cookies.get(CSRF_COOKIE_NAME) or ""
            if not supplied or not hmac.compare_digest(supplied, expected) or not hmac.compare_digest(cookie_token, expected):
                return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
    return await call_next(request)


# -----------------------------------------------------------------------------
# SSH / SFTP
# -----------------------------------------------------------------------------

def get_server(server_id: str) -> dict[str, Any]:
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM servers WHERE id=?", (server_id,)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Server not found")

    return row_to_dict(row)


def resolve_ssh_payload(payload: SSHPayload | DeploySpec) -> dict[str, Any]:
    data = payload.model_dump()

    server_id = data.get("server_id")
    if server_id:
        saved = get_server(server_id)
        data["host"] = data.get("host") or saved["host"]
        data["port"] = data.get("port") or saved["port"]
        data["username"] = data.get("username") or saved["username"]
        data["key_path"] = data.get("key_path") or saved.get("key_path")

    required = ["host", "port", "username"]
    missing = [x for x in required if not data.get(x)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing SSH fields: {', '.join(missing)}")

    return data


def ssh_client_from_payload(data: dict[str, Any]) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.load_system_host_keys()

    if security_settings().get("strict_host_key"):
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    kwargs = {
        "hostname": data["host"],
        "port": int(data.get("port", 22)),
        "username": data["username"],
        "timeout": int(data.get("timeout", 12)),
        "auth_timeout": int(data.get("timeout", 12)),
        "banner_timeout": int(data.get("timeout", 12)),
    }

    key_path = data.get("key_path")
    password = data.get("password")
    passphrase = data.get("passphrase")

    if key_path:
        kwargs["key_filename"] = key_path
        kwargs["passphrase"] = passphrase
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False
    else:
        kwargs["password"] = password
        kwargs["look_for_keys"] = False
        kwargs["allow_agent"] = False

    client.connect(**kwargs)
    return client


def remote_exec(client: paramiko.SSHClient, command: str, job_id: str | None = None, check: bool = True) -> tuple[int, str, str]:
    if job_id:
        log_job(job_id, "INFO", f"REMOTE $ {command}")

    stdin, stdout, stderr = client.exec_command(command, get_pty=True)
    exit_code = stdout.channel.recv_exit_status()

    out = stdout.read().decode(errors="replace").strip()
    err = stderr.read().decode(errors="replace").strip()

    if job_id:
        if out:
            for line in out.splitlines():
                log_job(job_id, "INFO", line)
        if err:
            for line in err.splitlines():
                log_job(job_id, "WARN", line)

    if check and exit_code != 0:
        raise RuntimeError(f"Remote command failed with exit code {exit_code}: {command}\n{err}")

    return exit_code, out, err


def sftp_put(client: paramiko.SSHClient, local_path: Path, remote_path: str, job_id: str) -> None:
    size = local_path.stat().st_size
    last_pct = {"value": -1}

    def progress(sent: int, total: int):
        if total <= 0:
            return
        pct = int((sent / total) * 100)
        if pct >= last_pct["value"] + 10 or pct == 100:
            last_pct["value"] = pct
            log_job(job_id, "INFO", f"SFTP upload progress: {pct}% ({sent}/{total} bytes)")

    log_job(job_id, "INFO", f"Uploading bundle via SFTP: {local_path.name} ({size} bytes)")
    sftp = client.open_sftp()
    try:
        sftp.put(str(local_path), remote_path, callback=progress)
    finally:
        sftp.close()


# -----------------------------------------------------------------------------
# Artifact discovery
# -----------------------------------------------------------------------------

def read_lines(file_path: Path) -> list[str]:
    if not file_path.exists():
        return []

    result: list[str] = []
    for line in file_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        result.append(line)
    return result


def nexus_components(repository: str) -> list[str]:
    if not NEXUS_ADMIN_PASSWORD:
        return []

    items: list[str] = []
    url = f"{NEXUS_URL}/service/rest/v1/components"
    params: dict[str, Any] = {"repository": repository}

    for _ in range(10):
        try:
            r = requests.get(
                url,
                params=params,
                auth=("admin", NEXUS_ADMIN_PASSWORD),
                timeout=8,
            )
            if r.status_code >= 400:
                return items

            data = r.json()
        except Exception:
            return items

        for item in data.get("items", []):
            name = item.get("name")
            version = item.get("version")
            if name and version:
                if repository.startswith("pypi"):
                    items.append(f"{name}=={version}")
                else:
                    items.append(f"{name}:{version}")
            elif name:
                items.append(name)

        token = data.get("continuationToken")
        if not token:
            break

        params["continuationToken"] = token

    return sorted(set(items))


def normalize_docker_pull_candidates(image: str) -> list[str]:
    image = image.strip()
    if not image:
        return []

    candidates: list[str] = []

    if image.startswith("localhost:") or image.startswith("nexus.local:") or "/" in image.split("/")[0]:
        candidates.append(image)
    else:
        candidates.append(f"{NEXUS_DOCKER_GROUP}/{image}")
        if "/" not in image.split(":")[0]:
            candidates.append(f"{NEXUS_DOCKER_GROUP}/library/{image}")
        candidates.append(image)

    return list(dict.fromkeys(candidates))


# -----------------------------------------------------------------------------
# Preflight
# -----------------------------------------------------------------------------

def preflight_check(payload: SSHPayload, job_id: str | None = None) -> dict[str, Any]:
    data = resolve_ssh_payload(payload)
    settings = security_settings()
    remote_dir = effective_remote_dir(data.get("remote_dir"))
    sudo = "sudo " if data.get("use_sudo") else ""

    result: dict[str, Any] = {
        "target": f"{data['username']}@{data['host']}:{data['port']}",
        "remote_dir": remote_dir,
        "checks": [],
        "snapshots": {},
        "passed": True,
    }

    def add(name: str, status: str, detail: str = "") -> None:
        result["checks"].append({"name": name, "status": status, "detail": detail})
        if status == "FAIL":
            result["passed"] = False

    client = ssh_client_from_payload(data)

    try:
        _, hostname, _ = remote_exec(client, "hostname", job_id, check=False)
        add("Hostname", "OK" if hostname else "WARN", hostname)

        _, whoami, _ = remote_exec(client, "whoami", job_id, check=False)
        add("SSH User", "OK" if whoami else "WARN", whoami)

        _, os_release, _ = remote_exec(client, "cat /etc/os-release 2>/dev/null | head -n 8", job_id, check=False)
        add("OS Release", "OK" if os_release else "WARN", os_release)

        remote_exec(client, f"mkdir -p {shlex.quote(remote_dir)}", job_id, check=True)
        add("Remote Directory", "OK", remote_dir)

        code, _, _ = remote_exec(client, f"test -w {shlex.quote(remote_dir)}", job_id, check=False)
        add("Remote Directory Writable", "OK" if code == 0 else "FAIL", remote_dir)

        _, disk, _ = remote_exec(client, f"df -Pm {shlex.quote(remote_dir)} | tail -1 | awk '{{print $4}}'", job_id, check=False)
        free_mb = int(disk.strip() or "0") if disk.strip().isdigit() else 0
        min_free_mb = int(settings["min_free_disk_mb"])
        add("Free Disk Space", "OK" if free_mb >= min_free_mb else "FAIL", f"{free_mb} MB free; required {min_free_mb} MB")

        code, docker_version, _ = remote_exec(client, "docker --version", job_id, check=False)
        add("Docker CLI", "OK" if code == 0 else "WARN", docker_version)

        code, docker_info, _ = remote_exec(client, f"{sudo}docker info >/dev/null 2>&1 && echo running || echo not-running", job_id, check=False)
        add("Docker Daemon", "OK" if "running" in docker_info else "WARN", docker_info)

        code, python_version, _ = remote_exec(client, "python3 --version", job_id, check=False)
        add("Python 3", "OK" if code == 0 else "WARN", python_version)

        code, python2_version, _ = remote_exec(client, "python2 --version 2>&1", job_id, check=False)
        add("Python 2 Legacy", "OK" if code == 0 else "WARN", python2_version or "python2 not found")

        code, apt_version, _ = remote_exec(client, "apt-get --version | head -1", job_id, check=False)
        add("APT", "OK" if code == 0 else "WARN", apt_version)

        _, groups, _ = remote_exec(client, "id -nG", job_id, check=False)
        add("User Groups", "OK", groups)

        _, docker_snapshot, _ = remote_exec(client, "docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | head -100", job_id, check=False)
        result["snapshots"]["docker_images_before"] = docker_snapshot.splitlines() if docker_snapshot else []

        _, dpkg_snapshot, _ = remote_exec(client, "dpkg-query -W -f='${binary:Package}=${Version}\\n' 2>/dev/null | head -200", job_id, check=False)
        result["snapshots"]["dpkg_before"] = dpkg_snapshot.splitlines() if dpkg_snapshot else []

    finally:
        client.close()

    return result


# -----------------------------------------------------------------------------
# Security Gate
# -----------------------------------------------------------------------------

DANGEROUS_PATTERNS = [
    r"rm\s+-rf\s+/",
    r":\(\)\s*\{\s*:\|:",
    r"mkfs\.",
    r"dd\s+if=",
    r"shutdown\b",
    r"reboot\b",
    r"curl\s+.*\|\s*sh",
    r"wget\s+.*\|\s*sh",
]

APT_SAFE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")


def image_uses_latest(image: str) -> bool:
    tail = image.split("/")[-1]
    return ":" not in tail or tail.endswith(":latest")


def run_grype_scan(image: str) -> dict[str, Any]:
    if not security_settings().get("grype_scan_enabled"):
        return {"enabled": False, "status": "SKIPPED", "reason": "Grype scan disabled"}

    try:
        output = run_local_cmd(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                "/var/run/docker.sock:/var/run/docker.sock",
                "anchore/grype:latest",
                image,
                "-o",
                "json",
            ],
            timeout=180,
        )

        data = json.loads(output)
        counts: dict[str, int] = {"Critical": 0, "High": 0, "Medium": 0, "Low": 0, "Negligible": 0, "Unknown": 0}

        for match in data.get("matches", []):
            sev = match.get("vulnerability", {}).get("severity", "Unknown")
            counts[sev] = counts.get(sev, 0) + 1

        return {"enabled": True, "status": "SCANNED", "severity_counts": counts}

    except Exception as exc:
        return {"enabled": True, "status": "SCAN_FAILED", "error": str(exc)}


def security_gate(payload: SecurityGateIn) -> dict[str, Any]:
    settings = security_settings()
    errors: list[str] = []
    warnings: list[str] = []
    scan_results: dict[str, Any] = {}

    docker_images = [x.strip() for x in payload.bundle.docker_images if x.strip()]
    python_packages = [x.strip() for x in payload.bundle.python_packages if x.strip()]
    python2_packages = [x.strip() for x in payload.bundle.python2_packages if x.strip()]
    apt_packages = [x.strip() for x in payload.bundle.apt_packages if x.strip()]

    if not docker_images and not python_packages and not python2_packages and not apt_packages:
        errors.append("No artifacts selected.")

    if python2_packages:
        warnings.append("Python 2 legacy packages selected. Use only for legacy systems and keep versions pinned.")

    if settings["block_latest_tag"]:
        for image in docker_images:
            if image_uses_latest(image):
                errors.append(f"Docker image uses implicit/latest tag: {image}")

    if settings["block_unpinned_packages"]:
        for package in python_packages:
            if "==" not in package:
                errors.append(f"Python package is not pinned with ==: {package}")
        for package in python2_packages:
            if "==" not in package:
                errors.append(f"Python 2 package is not pinned with ==: {package}")

    if settings["block_unsafe_apt"]:
        for package in apt_packages:
            if not APT_SAFE_PATTERN.match(package):
                errors.append(f"APT package name is unsafe or unsupported: {package}")

    for cmd in payload.extra_commands.splitlines():
        clean = cmd.strip()
        if not clean:
            continue
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, clean, flags=re.IGNORECASE):
                errors.append(f"Dangerous command blocked: {clean}")
                break

    for image in docker_images:
        candidates = normalize_docker_pull_candidates(image)
        scan_target = candidates[0] if candidates else image
        scan = run_grype_scan(scan_target)
        scan_results[image] = scan

        if scan.get("status") == "SCAN_FAILED":
            warnings.append(f"Security scan failed for {image}: {scan.get('error')}")

        counts = scan.get("severity_counts") or {}
        critical = counts.get("Critical", 0)
        high = counts.get("High", 0)

        if settings["block_critical"] and critical > 0:
            errors.append(f"Critical vulnerabilities found in {image}: {critical}")

        if settings["block_high"] and high > 0:
            errors.append(f"High vulnerabilities found in {image}: {high}")
        elif high > 0:
            warnings.append(f"High vulnerabilities found in {image}: {high}")

    if errors and not settings["require_security_gate"]:
        warnings.append("Security gate has blocking findings but is configured for audit-only mode.")

    passed = len(errors) == 0 or not settings["require_security_gate"]

    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "scan_results": scan_results,
        "policy": {
            "block_latest_tag": settings["block_latest_tag"],
            "block_critical": settings["block_critical"],
            "block_high": settings["block_high"],
            "block_unpinned_packages": settings["block_unpinned_packages"],
            "block_unsafe_apt": settings["block_unsafe_apt"],
            "grype_scan_enabled": settings["grype_scan_enabled"],
            "require_security_gate": settings["require_security_gate"],
        },
    }


# -----------------------------------------------------------------------------
# Bundle creation
# -----------------------------------------------------------------------------

def create_apt_mini_repo(job_id: str, packages: list[str], target: str, out_dir: Path) -> None:
    if not packages:
        return

    target_cfg = APT_TARGETS.get(target)
    if not target_cfg:
        raise RuntimeError(f"Unsupported APT target: {target}")

    out_dir.mkdir(parents=True, exist_ok=True)

    distro = target_cfg["distro"]
    codename = target_cfg["codename"]
    components = target_cfg["components"]
    image = target_cfg["image"]
    package_list = " ".join(shlex.quote(x) for x in packages)
    host_out = host_path_for(out_dir)

    script = f"""
set -euo pipefail

DISTRO={shlex.quote(distro)}
CODENAME={shlex.quote(codename)}
COMPONENTS={shlex.quote(components)}
PACKAGES="{package_list}"

if [[ "$DISTRO" == "ubuntu" ]]; then
  if [[ "$CODENAME" == "plucky" ]]; then
    BASE_URL="http://old-releases.ubuntu.com/ubuntu"
    SECURITY_URL="http://old-releases.ubuntu.com/ubuntu"
  else
    BASE_URL="http://archive.ubuntu.com/ubuntu"
    SECURITY_URL="http://security.ubuntu.com/ubuntu"
  fi

  cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] $BASE_URL $CODENAME $COMPONENTS
deb [trusted=yes] $BASE_URL $CODENAME-updates $COMPONENTS
deb [trusted=yes] $SECURITY_URL $CODENAME-security $COMPONENTS
EOF
else
  BASE_URL="http://deb.debian.org/debian"
  SECURITY_URL="http://security.debian.org/debian-security"

  cat > /etc/apt/sources.list <<EOF
deb [trusted=yes] $BASE_URL $CODENAME $COMPONENTS
deb [trusted=yes] $BASE_URL $CODENAME-updates $COMPONENTS
deb [trusted=yes] $SECURITY_URL $CODENAME-security $COMPONENTS
EOF
fi

apt-get update
apt-get install -y --no-install-recommends dpkg-dev ca-certificates gzip

mkdir -p /out/pool/main
mkdir -p /out/dists/$CODENAME/main/binary-amd64

apt-get install -y --download-only --no-install-recommends $PACKAGES

cp -a /var/cache/apt/archives/*.deb /out/pool/main/ 2>/dev/null || true

cd /out
dpkg-scanpackages pool /dev/null > dists/$CODENAME/main/binary-amd64/Packages
gzip -9kf dists/$CODENAME/main/binary-amd64/Packages

cat > dists/$CODENAME/Release <<EOF
Origin: Air-Gapped Enterprise Artifact Platform
Label: $DISTRO-$CODENAME-mini
Suite: $CODENAME
Codename: $CODENAME
Architectures: amd64
Components: main
Description: Lightweight offline APT mini repository generated by Nexus Portal
EOF
"""

    log_job(job_id, "INFO", f"Building APT mini repo for {target} using {image}")

    run_local_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{host_out}:/out",
            image,
            "bash",
            "-lc",
            script,
        ],
        job_id,
    )


def build_manifest(bundle_dir: Path, base: dict[str, Any]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []

    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue

        rel = str(p.relative_to(bundle_dir))
        if rel == "metadata/manifest.json":
            continue

        files.append({
            "path": rel,
            "size": p.stat().st_size,
            "sha256": sha256_file(p),
        })

    base["files"] = files
    return base


def create_bundle(job_id: str, spec: BundleSpec, user: str, security: dict[str, Any]) -> Path:
    job_root = BUNDLES_ROOT / job_id
    bundle_dir = job_root / "bundle"
    archive_root_name = f"airgap-bundle-{job_id}"
    archive_path = job_root / f"{archive_root_name}.tar.gz"

    if job_root.exists():
        shutil.rmtree(job_root)

    (bundle_dir / "docker").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "python/wheels").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "python2/wheels").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "apt-mini-repo").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "metadata").mkdir(parents=True, exist_ok=True)

    selected_docker_images = [x.strip() for x in spec.docker_images if x.strip()]
    selected_python_packages = [x.strip() for x in spec.python_packages if x.strip()]
    selected_python2_packages = [x.strip() for x in spec.python2_packages if x.strip()]
    selected_apt_packages = [x.strip() for x in spec.apt_packages if x.strip()]

    if selected_docker_images:
        log_job(job_id, "INFO", "Preparing Docker images")

        for image in selected_docker_images:
            pulled = False

            for candidate in normalize_docker_pull_candidates(image):
                try:
                    run_local_cmd(["docker", "pull", candidate], job_id)
                    run_local_cmd(["docker", "tag", candidate, image], job_id)
                    pulled = True
                    break
                except Exception as exc:
                    log_job(job_id, "WARN", f"Pull candidate failed: {candidate} -> {exc}")

            if not pulled:
                raise RuntimeError(f"Could not pull Docker image: {image}")

        run_local_cmd(["docker", "save", "-o", str(bundle_dir / "docker/images.tar"), *selected_docker_images], job_id)
        (bundle_dir / "docker/images.txt").write_text("\n".join(selected_docker_images) + "\n")

    if selected_python_packages:
        log_job(job_id, "INFO", "Preparing Python wheels from Nexus PyPI group")

        trusted_host = NEXUS_URL.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]

        run_local_cmd(
            [
                "python",
                "-m",
                "pip",
                "download",
                "--no-cache-dir",
                "--index-url",
                f"{NEXUS_URL}/repository/pypi-group/simple",
                "--trusted-host",
                trusted_host,
                "-d",
                str(bundle_dir / "python/wheels"),
                *selected_python_packages,
            ],
            job_id,
        )

        (bundle_dir / "python/requirements.txt").write_text("\n".join(selected_python_packages) + "\n")

    if selected_python2_packages:
        log_job(job_id, "INFO", "Preparing Python 2 legacy wheels from Nexus pypi2-group")

        python2_dir = bundle_dir / "python2"
        python2_dir.mkdir(parents=True, exist_ok=True)
        (python2_dir / "requirements.txt").write_text("\n".join(selected_python2_packages) + "\n")

        host_python2_dir = host_path_for(python2_dir)
        trusted_host = PYTHON2_PIP_INDEX_URL.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]

        py2_command = (
            "python -m ensurepip || true; "
            "python -m pip install --upgrade 'pip<21' 'setuptools<45' 'wheel<0.38'; "
            f"python -m pip download --no-cache-dir --index-url {shlex.quote(PYTHON2_PIP_INDEX_URL)} "
            f"--trusted-host {shlex.quote(trusted_host)} -r /python2/requirements.txt -d /python2/wheels"
        )

        run_local_cmd(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                PYTHON2_DOCKER_NETWORK,
                "-v",
                f"{host_python2_dir}:/python2",
                PYTHON2_DOCKER_IMAGE,
                "bash",
                "-lc",
                py2_command,
            ],
            job_id,
        )

    if selected_apt_packages:
        create_apt_mini_repo(job_id, selected_apt_packages, spec.apt_target, bundle_dir / "apt-mini-repo")
        (bundle_dir / "apt/packages.txt").parent.mkdir(parents=True, exist_ok=True)
        (bundle_dir / "apt/packages.txt").write_text("\n".join(selected_apt_packages) + "\n")

    manifest = build_manifest(
        bundle_dir,
        {
            "bundle_id": job_id,
            "created_by": user,
            "created_at": utc_now(),
            "apt_target": spec.apt_target,
            "docker_images": selected_docker_images,
            "python_packages": selected_python_packages,
            "python2_packages": selected_python2_packages,
            "apt_packages": selected_apt_packages,
            "security": security,
            "format_version": "4.0",
        },
    )

    (bundle_dir / "metadata/manifest.json").write_text(json.dumps(manifest, indent=2))

    log_job(job_id, "INFO", f"Creating archive: {archive_path}")

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(bundle_dir, arcname=archive_root_name)

    archive_sha = sha256_file(archive_path)
    update_job(job_id, bundle_sha256=archive_sha)

    log_job(job_id, "INFO", f"Bundle created: {archive_path}")
    log_job(job_id, "INFO", f"Bundle SHA256: {archive_sha}")

    return archive_path


# -----------------------------------------------------------------------------
# Reports
# -----------------------------------------------------------------------------

def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_simple_pdf(path: Path, title: str, lines: list[str]) -> None:
    safe_lines = [title, "", *lines]
    y = 780
    stream_lines = ["BT", "/F1 11 Tf"]

    for line in safe_lines[:55]:
        stream_lines.append(f"50 {y} Td ({pdf_escape(line[:100])}) Tj")
        y = -16

    stream_lines.append("ET")
    content = "\n".join(stream_lines).encode()

    objects = []
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    for i, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref = len(out)
    out += f"xref\n0 {len(objects)+1}\n".encode()
    out += b"0000000000 65535 f \n"

    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()

    out += f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(out)


def generate_reports(job_id: str) -> tuple[str, str]:
    with db_conn() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        logs = conn.execute("SELECT ts, level, message FROM job_logs WHERE job_id=? ORDER BY id ASC", (job_id,)).fetchall()

    if not job:
        raise RuntimeError("Job not found for report generation")

    jobd = row_to_dict(job)
    payload = json.loads(jobd.get("payload_json") or "{}")
    preflight = json.loads(jobd.get("preflight_json") or "{}")
    security = json.loads(jobd.get("security_json") or "{}")
    rollback = json.loads(jobd.get("rollback_json") or "{}")

    report_dir = REPORTS_ROOT / job_id
    report_dir.mkdir(parents=True, exist_ok=True)

    html_path = report_dir / "deployment-report.html"
    pdf_path = report_dir / "deployment-report.pdf"
    payload_json = html_lib.escape(json.dumps(payload.get("bundle", {}), indent=2, ensure_ascii=False))
    preflight_json = html_lib.escape(json.dumps(preflight, indent=2, ensure_ascii=False))
    security_json = html_lib.escape(json.dumps(security, indent=2, ensure_ascii=False))
    rollback_json = html_lib.escape(json.dumps(rollback, indent=2, ensure_ascii=False))
    log_text = html_lib.escape("".join([f"[{r['ts']}] [{r['level']}] {r['message']}\n" for r in logs]))

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Deployment Report - {job_id}</title>
  <style>
    body {{ font-family: Arial, sans-serif; background: #0f1117; color: #f8fafc; margin: 0; padding: 32px; }}
    .card {{ background: #171a23; border: 1px solid #2b2f3a; border-radius: 20px; padding: 22px; margin-bottom: 18px; }}
    h1, h2 {{ margin-top: 0; }}
    code, pre {{ background: #090b10; color: #d1d5db; padding: 12px; border-radius: 12px; display: block; overflow: auto; }}
    .ok {{ color: #86efac; }}
    .bad {{ color: #fecaca; }}
    .muted {{ color: #98a2b3; }}
  </style>
</head>
<body>
  <h1>Air-Gapped Deployment Report</h1>
  <div class="card">
    <h2>Summary</h2>
    <p><b>Job ID:</b> {html_lib.escape(job_id)}</p>
    <p><b>Status:</b> {html_lib.escape(str(jobd.get("status") or ""))}</p>
    <p><b>Target:</b> {html_lib.escape(str(jobd.get("target_server") or ""))}</p>
    <p><b>Created:</b> {html_lib.escape(str(jobd.get("created_at") or ""))}</p>
    <p><b>Started:</b> {html_lib.escape(str(jobd.get("started_at") or ""))}</p>
    <p><b>Finished:</b> {html_lib.escape(str(jobd.get("finished_at") or ""))}</p>
    <p><b>Bundle SHA256:</b> {html_lib.escape(str(jobd.get("bundle_sha256") or ""))}</p>
  </div>

  <div class="card">
    <h2>Selected Artifacts</h2>
    <pre>{payload_json}</pre>
  </div>

  <div class="card">
    <h2>Preflight</h2>
    <pre>{preflight_json}</pre>
  </div>

  <div class="card">
    <h2>Security Gate</h2>
    <pre>{security_json}</pre>
  </div>

  <div class="card">
    <h2>Rollback Plan</h2>
    <pre>{rollback_json}</pre>
  </div>

  <div class="card">
    <h2>Logs</h2>
    <pre>{log_text}</pre>
  </div>
</body>
</html>
"""
    html_path.write_text(html)

    pdf_lines = [
        f"Job ID: {job_id}",
        f"Status: {jobd.get('status')}",
        f"Target: {jobd.get('target_server')}",
        f"Created: {jobd.get('created_at')}",
        f"Finished: {jobd.get('finished_at')}",
        f"Bundle SHA256: {jobd.get('bundle_sha256') or ''}",
        "",
        "Artifacts:",
        json.dumps(payload.get("bundle", {}), ensure_ascii=False)[:500],
        "",
        "Security:",
        json.dumps(security, ensure_ascii=False)[:500],
        "",
        "Rollback:",
        json.dumps(rollback, ensure_ascii=False)[:500],
    ]

    write_simple_pdf(pdf_path, "Air-Gapped Deployment Report", pdf_lines)

    update_job(job_id, report_html=str(html_path), report_pdf=str(pdf_path))

    return str(html_path), str(pdf_path)


# -----------------------------------------------------------------------------
# Deployment / Rollback plan
# -----------------------------------------------------------------------------

def build_rollback_plan(preflight_before: dict[str, Any], preflight_after: dict[str, Any] | None = None) -> dict[str, Any]:
    before = preflight_before.get("snapshots", {})
    after = preflight_after.get("snapshots", {}) if preflight_after else {}

    return {
        "type": "manual-assisted",
        "created_at": utc_now(),
        "notes": [
            "This project records before/after snapshots and generates safe rollback guidance.",
            "Automatic rollback is intentionally not executed without operator approval.",
        ],
        "docker_images_before": before.get("docker_images_before", []),
        "docker_images_after": after.get("docker_images_before", []),
        "dpkg_before": before.get("dpkg_before", []),
        "dpkg_after": after.get("dpkg_before", []),
        "recommended_actions": [
            "Review loaded Docker images and retag/remove only the images introduced by this job if rollback is required.",
            "Use the previous dpkg snapshot to compare package changes before reverting OS packages.",
            "Keep the original bundle and report for audit evidence.",
        ],
    }


def deploy_bundle(job_id: str, archive_path: Path, archive_sha: str, deploy: DeploySpec) -> tuple[dict[str, Any], dict[str, Any]]:
    data = resolve_ssh_payload(deploy)
    remote_dir = effective_remote_dir(deploy.remote_dir)
    remote_base = f"{remote_dir.rstrip('/')}/{job_id}"
    q_remote_base = shlex.quote(remote_base)
    remote_archive = f"{remote_base}/bundle.tar.gz"
    archive_root = f"airgap-bundle-{job_id}"

    preflight_before = preflight_check(deploy, job_id=job_id)

    if not preflight_before.get("passed"):
        raise RuntimeError("Preflight failed. Deployment stopped.")

    client = ssh_client_from_payload(data)

    try:
        remote_exec(client, f"mkdir -p {q_remote_base}", job_id)
        sftp_put(client, archive_path, remote_archive, job_id)

        code, remote_sha, _ = remote_exec(client, f"sha256sum {shlex.quote(remote_archive)} | awk '{{print $1}}'", job_id, check=False)

        if code == 0 and remote_sha.strip():
            if remote_sha.strip() != archive_sha:
                raise RuntimeError("Remote bundle SHA256 mismatch.")
            log_job(job_id, "INFO", "Remote SHA256 verification passed")
        else:
            log_job(job_id, "WARN", "Remote sha256sum not available or failed. Skipping remote checksum verification.")

        remote_exec(client, f"cd {q_remote_base} && tar -xzf bundle.tar.gz", job_id)

        sudo = "sudo " if deploy.use_sudo else ""
        bundle_path = f"{remote_base}/{archive_root}"
        q_bundle = shlex.quote(bundle_path)

        if deploy.docker_load:
            remote_exec(
                client,
                f"cd {q_bundle} && if [ -f docker/images.tar ]; then {sudo}docker load -i docker/images.tar; else echo 'No Docker archive found'; fi",
                job_id,
            )

        if deploy.python_wheels:
            wheels_dest = f"{remote_base}/python-wheels"
            remote_exec(
                client,
                f"mkdir -p {shlex.quote(wheels_dest)} && "
                f"cd {q_bundle} && "
                f"if [ -d python/wheels ]; then cp -a python/wheels/. {shlex.quote(wheels_dest)}/; fi && "
                f"echo 'Python wheels prepared at {wheels_dest}'",
                job_id,
            )

        if deploy.apt_mini:
            apt_dest = f"{remote_base}/apt-mini-repo"
            remote_exec(
                client,
                f"mkdir -p {shlex.quote(apt_dest)} && "
                f"cd {q_bundle} && "
                f"if [ -d apt-mini-repo ]; then cp -a apt-mini-repo/. {shlex.quote(apt_dest)}/; fi && "
                f"echo 'APT mini repo prepared at {apt_dest}'",
                job_id,
            )

        if deploy.extra_commands.strip():
            for cmd in deploy.extra_commands.splitlines():
                cmd = cmd.strip()
                if not cmd:
                    continue
                remote_exec(client, cmd, job_id)

    finally:
        client.close()

    preflight_after = preflight_check(deploy, job_id=job_id)
    return preflight_before, preflight_after


def run_job(job_id: str, payload: dict[str, Any], user: str) -> None:
    update_job(job_id, status="RUNNING", started_at=utc_now())
    log_job(job_id, "INFO", "Job started")

    security_result: dict[str, Any] = {}
    preflight_before: dict[str, Any] = {}
    preflight_after: dict[str, Any] = {}
    rollback_plan: dict[str, Any] = {}

    try:
        job_in = JobIn(**payload)

        security_result = security_gate(
            SecurityGateIn(
                bundle=job_in.bundle,
                deploy=job_in.deploy,
                extra_commands=job_in.deploy.extra_commands,
            )
        )

        update_job(job_id, security_json=json.dumps(security_result, ensure_ascii=False))

        if not security_result["passed"]:
            raise RuntimeError("Security Gate blocked this deployment: " + "; ".join(security_result["errors"]))

        archive_path = create_bundle(job_id, job_in.bundle, user, security_result)
        archive_sha = sha256_file(archive_path)
        update_job(job_id, bundle_path=str(archive_path), bundle_sha256=archive_sha)

        if job_in.deploy.enabled:
            log_job(job_id, "INFO", "Starting remote deployment with preflight")
            preflight_before, preflight_after = deploy_bundle(job_id, archive_path, archive_sha, job_in.deploy)
            update_job(job_id, preflight_json=json.dumps(preflight_before, ensure_ascii=False))
        else:
            log_job(job_id, "INFO", "Deployment disabled. Bundle was created only.")

        rollback_plan = build_rollback_plan(preflight_before, preflight_after)
        update_job(job_id, rollback_json=json.dumps(rollback_plan, ensure_ascii=False))

        update_job(job_id, status="SUCCESS", finished_at=utc_now())
        log_job(job_id, "INFO", "Job finished successfully")

    except Exception as exc:
        update_job(job_id, status="FAILED", finished_at=utc_now(), error=str(exc))

        if not rollback_plan:
            rollback_plan = build_rollback_plan(preflight_before, preflight_after if preflight_after else None)
            update_job(job_id, rollback_json=json.dumps(rollback_plan, ensure_ascii=False))

        log_job(job_id, "ERROR", str(exc))

    finally:
        try:
            html, pdf = generate_reports(job_id)
            log_job(job_id, "INFO", f"Report HTML: {html}")
            log_job(job_id, "INFO", f"Report PDF: {pdf}")
        except Exception as report_exc:
            log_job(job_id, "WARN", f"Could not generate report: {report_exc}")


# -----------------------------------------------------------------------------
# Cleanup / Storage Guard
# -----------------------------------------------------------------------------

def storage_guard() -> dict[str, Any]:
    settings = security_settings()
    total = directory_size(APP_WORKSPACE)
    nexus = directory_size(APP_WORKSPACE / "data/nexus")
    portal = directory_size(APP_WORKSPACE / "data/postgres")
    bundles = directory_size(BUNDLES_ROOT)
    reports = directory_size(REPORTS_ROOT)

    return {
        "workspace_mb": round(total / 1024 / 1024, 2),
        "nexus_mb": round(nexus / 1024 / 1024, 2),
        "portal_db_mb": round(portal / 1024 / 1024, 2),
        "portal_bundles_mb": round(bundles / 1024 / 1024, 2),
        "reports_mb": round(reports / 1024 / 1024, 2),
        "cleanup_keep_days": settings["cleanup_keep_days"],
    }


def cleanup_old_files(days: int) -> dict[str, Any]:
    cutoff = time.time() - (days * 86400)
    removed_files = 0
    removed_dirs = 0
    freed = 0

    for root in [BUNDLES_ROOT, REPORTS_ROOT]:
        if not root.exists():
            continue

        for p in sorted(root.rglob("*"), reverse=True):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    size = p.stat().st_size
                    p.unlink()
                    removed_files += 1
                    freed += size
            except FileNotFoundError:
                pass

        for p in sorted(root.rglob("*"), reverse=True):
            if p.is_dir():
                try:
                    p.rmdir()
                    removed_dirs += 1
                except OSError:
                    pass

    return {
        "removed_files": removed_files,
        "removed_dirs": removed_dirs,
        "freed_mb": round(freed / 1024 / 1024, 2),
        "days": days,
    }


# -----------------------------------------------------------------------------
# Security Center
# -----------------------------------------------------------------------------

def status_item(name: str, status: str, detail: str, remediation: str = "") -> dict[str, str]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "remediation": remediation,
    }


def security_status() -> dict[str, Any]:
    settings = security_settings()
    controls: list[dict[str, str]] = []
    access = list_access_control()
    trusted_ip_count = len([x for x in access["ip_rules"] if x["enabled"]])
    principal_count = len([x for x in access["principals"] if x["enabled"]])
    firewall_status = (access.get("last_enforcement") or {}).get("status", "NEVER_APPLIED")

    controls.append(status_item(
        "Portal password hash",
        "OK" if PORTAL_ADMIN_PASSWORD_HASH.startswith("pbkdf2_sha256$") else "WARN",
        "PBKDF2 password hash configured." if PORTAL_ADMIN_PASSWORD_HASH.startswith("pbkdf2_sha256$") else "Portal is using the plaintext password environment variable.",
        "Run scripts/generate-portal-password-hash.py and set PORTAL_ADMIN_PASSWORD_HASH.",
    ))
    controls.append(status_item(
        "Default portal password",
        "OK" if PORTAL_ADMIN_PASSWORD not in {"change-me", "admin"} else "FAIL",
        "Default portal password is not configured." if PORTAL_ADMIN_PASSWORD not in {"change-me", "admin"} else "Default or weak portal password is configured.",
        "Set a strong password or disable plaintext password auth by using PORTAL_ADMIN_PASSWORD_HASH.",
    ))
    controls.append(status_item(
        "Session secret",
        "OK" if len(PORTAL_SESSION_SECRET) >= 32 and PORTAL_SESSION_SECRET != "dev-secret-change-me" else "FAIL",
        "Session secret length is acceptable." if len(PORTAL_SESSION_SECRET) >= 32 and PORTAL_SESSION_SECRET != "dev-secret-change-me" else "Session secret is missing, short, or default.",
        "Set PORTAL_SESSION_SECRET to at least 32 random characters.",
    ))
    controls.append(status_item(
        "CSRF protection",
        "OK",
        "Unsafe API methods require a signed CSRF token paired with the authenticated session.",
    ))
    controls.append(status_item(
        "Login throttling",
        "OK",
        f"Login is locked after {PORTAL_LOGIN_MAX_FAILURES} failures for {PORTAL_LOGIN_LOCKOUT_SECONDS} seconds.",
    ))
    controls.append(status_item(
        "Secure cookies",
        "OK" if PORTAL_COOKIE_SECURE else "WARN",
        "Cookies require HTTPS." if PORTAL_COOKIE_SECURE else "Cookies are allowed over HTTP for local/lab use.",
        "Set PORTAL_COOKIE_SECURE=true behind HTTPS.",
    ))
    controls.append(status_item(
        "SSH host key policy",
        "OK" if settings["strict_host_key"] else "WARN",
        "Unknown SSH host keys are rejected." if settings["strict_host_key"] else "Unknown SSH host keys are auto-added.",
        "Enable strict_host_key and preload known_hosts for production targets.",
    ))
    controls.append(status_item(
        "Docker latest tag policy",
        "OK" if settings["block_latest_tag"] else "WARN",
        "Implicit and explicit latest tags are blocked." if settings["block_latest_tag"] else "Latest tags are currently allowed.",
    ))
    controls.append(status_item(
        "Package pinning policy",
        "OK" if settings["block_unpinned_packages"] else "WARN",
        "Python package selections must use exact == pins." if settings["block_unpinned_packages"] else "Unpinned Python packages are currently allowed.",
    ))
    controls.append(status_item(
        "Vulnerability scanning",
        "OK" if settings["grype_scan_enabled"] else "WARN",
        "Grype scan is enabled for Docker image selections." if settings["grype_scan_enabled"] else "Grype scan is disabled.",
        "Enable grype_scan_enabled after the scanner image is available in the environment.",
    ))
    controls.append(status_item(
        "Critical vulnerability blocking",
        "OK" if settings["block_critical"] else "WARN",
        "Docker images with critical findings are blocked." if settings["block_critical"] else "Critical findings are not blocking.",
    ))
    controls.append(status_item(
        "High vulnerability blocking",
        "OK" if settings["block_high"] else "WARN",
        "Docker images with high findings are blocked." if settings["block_high"] else "High findings produce warnings only.",
    ))
    controls.append(status_item(
        "Docker socket access",
        "WARN" if Path("/var/run/docker.sock").exists() else "OK",
        "Portal container can access the Docker socket for bundle builds." if Path("/var/run/docker.sock").exists() else "Docker socket is not mounted.",
        "Restrict portal access, keep no-new-privileges enabled, and deploy on a trusted build host.",
    ))
    controls.append(status_item(
        "Trusted registry accounts",
        "OK" if principal_count > 0 else "WARN",
        f"{principal_count} enabled trusted account(s) are managed by the portal.",
        "Create at least one service account or user before disabling broad access.",
    ))
    controls.append(status_item(
        "Trusted IP allowlist",
        "OK" if trusted_ip_count > 0 else "FAIL",
        f"{trusted_ip_count} enabled IP/CIDR rule(s) are configured.",
        "Add your workstation, build agents, and runtime subnets before applying firewall policy.",
    ))
    controls.append(status_item(
        "Registry firewall enforcement",
        "OK" if firewall_status == "APPLIED" else "WARN",
        f"Firewall enforcement status: {firewall_status}.",
        "Apply the IP firewall from Registry Access Control after every allowlist change.",
    ))
    controls.append(status_item(
        "Nexus admin password",
        "OK" if NEXUS_ADMIN_PASSWORD and NEXUS_ADMIN_PASSWORD not in {"change-me", "ChangeThisStrongPassword_12345"} else "FAIL",
        "Nexus admin password is configured." if NEXUS_ADMIN_PASSWORD and NEXUS_ADMIN_PASSWORD not in {"change-me", "ChangeThisStrongPassword_12345"} else "Nexus admin password is missing or default.",
    ))
    controls.append(status_item(
        "HTTPS upstream",
        "OK" if NEXUS_URL.startswith("https://") else "WARN",
        f"Nexus URL is {NEXUS_URL}.",
        "Use HTTPS in production or place the stack behind a trusted TLS-terminating reverse proxy.",
    ))

    score_map = {"OK": 2, "WARN": 1, "FAIL": 0}
    max_score = len(controls) * 2
    score = sum(score_map.get(item["status"], 0) for item in controls)
    readiness = round((score / max_score) * 100) if max_score else 0

    return {
        "settings": settings,
        "controls": controls,
        "readiness_score": readiness,
        "summary": {
            "ok": sum(1 for x in controls if x["status"] == "OK"),
            "warn": sum(1 for x in controls if x["status"] == "WARN"),
            "fail": sum(1 for x in controls if x["status"] == "FAIL"),
        },
    }


def list_access_control() -> dict[str, Any]:
    with db_conn() as conn:
        group_rows = conn.execute("SELECT * FROM access_groups ORDER BY name ASC").fetchall()
        principal_rows = conn.execute("SELECT * FROM access_principals ORDER BY username ASC").fetchall()
        ip_rows = conn.execute("SELECT * FROM access_ip_rules ORDER BY enabled DESC, cidr ASC").fetchall()
        member_rows = conn.execute("SELECT principal_id, group_id FROM access_group_members").fetchall()
        enforcement = conn.execute("SELECT value FROM access_enforcement WHERE key='last_status'").fetchone()

    memberships: dict[str, list[str]] = {}
    for row in member_rows:
        memberships.setdefault(row["principal_id"], []).append(row["group_id"])

    groups = []
    for row in group_rows:
        item = row_to_dict(row)
        item["permissions"] = json.loads(item.pop("permissions_json") or "[]")
        groups.append(item)

    principals = []
    for row in principal_rows:
        item = row_to_dict(row)
        item["enabled"] = bool(item["enabled"])
        item["group_ids"] = memberships.get(item["id"], [])
        principals.append(item)

    ip_rules = []
    for row in ip_rows:
        item = row_to_dict(row)
        item["enabled"] = bool(item["enabled"])
        ip_rules.append(item)

    try:
        last_enforcement = json.loads(enforcement["value"]) if enforcement else {"status": "NEVER_APPLIED"}
    except Exception:
        last_enforcement = {"status": "UNKNOWN"}

    return {
        "groups": groups,
        "principals": principals,
        "ip_rules": ip_rules,
        "permissions": ACCESS_PERMISSIONS,
        "protected_ports": NEXUS_PROTECTED_PORTS,
        "last_enforcement": last_enforcement,
    }


def active_trusted_cidrs() -> list[str]:
    with db_conn() as conn:
        rows = conn.execute("SELECT cidr FROM access_ip_rules WHERE enabled=1 ORDER BY cidr ASC").fetchall()
    return [row["cidr"] for row in rows]


def firewall_script(cidrs: list[str]) -> str:
    ports = ",".join(str(x) for x in NEXUS_PROTECTED_PORTS)
    lines = [
        "set -eu",
        "CHAIN=PORTAL_NEXUS_ACL",
        "iptables -N $CHAIN 2>/dev/null || true",
        "iptables -F $CHAIN",
    ]

    for cidr in cidrs:
        lines.append(f"iptables -A $CHAIN -p tcp -m multiport --dports {ports} -s {shlex.quote(cidr)} -j RETURN")

    lines.extend([
        f"iptables -A $CHAIN -p tcp -m multiport --dports {ports} -j DROP",
        "iptables -C DOCKER-USER -j $CHAIN 2>/dev/null || iptables -I DOCKER-USER 1 -j $CHAIN",
        "iptables -S $CHAIN",
    ])
    return "\n".join(lines)


def apply_firewall_policy(actor: str) -> dict[str, Any]:
    cidrs = active_trusted_cidrs()
    if not cidrs:
        raise HTTPException(status_code=400, detail="At least one enabled trusted IP/CIDR is required before applying firewall policy.")

    script = firewall_script(cidrs)
    cmd = [
        "docker",
        "run",
        "--rm",
        "--privileged",
        "--network",
        "host",
        NEXUS_FIREWALL_HELPER_IMAGE,
        "sh",
        "-lc",
        "command -v iptables >/dev/null 2>&1 || (apk add --no-cache iptables >/dev/null); " + script,
    ]

    started = utc_now()
    output = run_local_cmd(cmd, timeout=120)
    result = {
        "status": "APPLIED",
        "applied_at": utc_now(),
        "started_at": started,
        "applied_by": actor,
        "trusted_cidrs": cidrs,
        "protected_ports": NEXUS_PROTECTED_PORTS,
        "helper_image": NEXUS_FIREWALL_HELPER_IMAGE,
        "output": output[-4000:],
    }

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO access_enforcement(key, value, updated_at, updated_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value=excluded.value,
                updated_at=excluded.updated_at,
                updated_by=excluded.updated_by
            """,
            ("last_status", json.dumps(result), utc_now(), actor),
        )

    return result


# -----------------------------------------------------------------------------
# Routes
# -----------------------------------------------------------------------------

@app.on_event("startup")
def startup() -> None:
    init_db()


@app.on_event("shutdown")
def shutdown() -> None:
    close_pool()


@app.get("/health")
def health():
    db_ping()
    return {"status": "ok", "version": APP_VERSION, "database": "postgresql"}


@app.get("/")
def index():
    return FileResponse("/app/app/templates/index.html")


@app.post("/api/login")
async def login(payload: LoginIn, response: Response, request: Request):
    if login_locked(request, payload.username):
        audit("LOGIN_LOCKED", "portal", {"username": payload.username}, request, actor=payload.username)
        raise HTTPException(status_code=429, detail="Too many failed login attempts. Try again later.")

    if payload.username != PORTAL_ADMIN_USER or not verify_password(payload.password):
        record_login_failure(request, payload.username)
        audit("LOGIN_FAILED", "portal", {"username": payload.username}, request, actor=payload.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    session_cookie = make_cookie(payload.username)
    csrf_token = make_csrf_token(session_cookie)

    response.set_cookie(
        COOKIE_NAME,
        session_cookie,
        httponly=True,
        secure=PORTAL_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        httponly=False,
        secure=PORTAL_COOKIE_SECURE,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )

    clear_login_failures(request, payload.username)
    audit("LOGIN_SUCCESS", "portal", {"username": payload.username}, request, actor=payload.username)
    return {"ok": True, "username": payload.username}


@app.post("/api/logout")
def logout(response: Response, user: str = Depends(current_user)):
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return {"ok": True}


@app.get("/api/session")
def session(user: str = Depends(current_user)):
    return {"authenticated": True, "username": user}


@app.get("/api/stats")
def stats(user: str = Depends(current_user)):
    with db_conn() as conn:
        servers = conn.execute("SELECT COUNT(*) AS c FROM servers").fetchone()["c"]
        jobs = conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()["c"]
        success = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='SUCCESS'").fetchone()["c"]
        failed = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='FAILED'").fetchone()["c"]
        running = conn.execute("SELECT COUNT(*) AS c FROM jobs WHERE status='RUNNING'").fetchone()["c"]

    return {
        "servers": servers,
        "jobs": jobs,
        "success": success,
        "failed": failed,
        "running": running,
        "storage": storage_guard(),
        "security": security_status()["summary"],
    }


@app.get("/api/security")
def api_security(user: str = Depends(current_user)):
    return security_status()


@app.put("/api/security/settings")
def api_update_security_settings(payload: SecuritySettingsIn, request: Request, user: str = Depends(current_user)):
    before = security_settings()
    after = update_security_settings(payload, user)
    audit("UPDATE_SECURITY_SETTINGS", "security", {"before": before, "after": after}, request, actor=user)
    return security_status()


@app.get("/api/access-control")
def api_access_control(user: str = Depends(current_user)):
    return list_access_control()


@app.post("/api/access-control/groups")
def api_create_access_group(payload: AccessGroupIn, request: Request, user: str = Depends(current_user)):
    group_id = str(uuid.uuid4())
    now = utc_now()
    permissions = validate_permissions(payload.permissions)
    role_id = f"portal-acl-{slugify(payload.name)}"

    with db_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO access_groups(id, name, description, permissions_json, nexus_role_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (group_id, payload.name, payload.description, json.dumps(permissions), role_id, now, now),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="Access group name already exists.") from exc

        group = conn.execute("SELECT * FROM access_groups WHERE id=?", (group_id,)).fetchone()
        sync_result = sync_nexus_role(row_to_dict(group))

    audit("CREATE_ACCESS_GROUP", "access-control", {"group_id": group_id, "name": payload.name, "sync": sync_result}, request, actor=user)
    return {"id": group_id, "sync": sync_result, "access": list_access_control()}


@app.put("/api/access-control/groups/{group_id}")
def api_update_access_group(group_id: str, payload: AccessGroupIn, request: Request, user: str = Depends(current_user)):
    permissions = validate_permissions(payload.permissions)
    now = utc_now()

    with db_conn() as conn:
        row = conn.execute("SELECT * FROM access_groups WHERE id=?", (group_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Access group not found")

        try:
            conn.execute(
                """
                UPDATE access_groups
                SET name=?, description=?, permissions_json=?, updated_at=?
                WHERE id=?
                """,
                (payload.name, payload.description, json.dumps(permissions), now, group_id),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="Access group name already exists.") from exc
        group = conn.execute("SELECT * FROM access_groups WHERE id=?", (group_id,)).fetchone()
        sync_result = sync_nexus_role(row_to_dict(group))

    audit("UPDATE_ACCESS_GROUP", "access-control", {"group_id": group_id, "sync": sync_result}, request, actor=user)
    return {"ok": True, "sync": sync_result, "access": list_access_control()}


@app.delete("/api/access-control/groups/{group_id}")
def api_delete_access_group(group_id: str, request: Request, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM access_groups WHERE id=?", (group_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Access group not found")
        members = conn.execute("SELECT COUNT(*) AS count FROM access_group_members WHERE group_id=?", (group_id,)).fetchone()
        if members and members["count"]:
            raise HTTPException(status_code=409, detail="Remove this group from trusted accounts before deleting it.")
        group = row_to_dict(row)
        conn.execute("DELETE FROM access_groups WHERE id=?", (group_id,))

    try:
        nexus_request("DELETE", f"/service/rest/v1/security/roles/{group['nexus_role_id']}", expected={200, 204, 404})
        nexus_deleted = True
    except HTTPException:
        nexus_deleted = False

    audit("DELETE_ACCESS_GROUP", "access-control", {"group_id": group_id, "nexus_deleted": nexus_deleted}, request, actor=user)
    return {"ok": True, "access": list_access_control()}


@app.post("/api/access-control/principals")
def api_create_access_principal(payload: AccessPrincipalIn, request: Request, user: str = Depends(current_user)):
    principal_id = str(uuid.uuid4())
    now = utc_now()
    nexus_user_id = payload.username

    with db_conn() as conn:
        known_groups = {r["id"] for r in conn.execute("SELECT id FROM access_groups").fetchall()}
        group_ids = validate_principal_groups(payload.group_ids, payload.enabled, known_groups)

        try:
            conn.execute(
                """
                INSERT INTO access_principals(id, username, display_name, email, principal_type, enabled, nexus_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    principal_id,
                    payload.username,
                    payload.display_name or payload.username,
                    payload.email,
                    payload.principal_type,
                    1 if payload.enabled else 0,
                    nexus_user_id,
                    now,
                    now,
                ),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="Principal username already exists.") from exc

        for group_id in group_ids:
            conn.execute("INSERT INTO access_group_members(principal_id, group_id) VALUES (?, ?)", (principal_id, group_id))

        sync_result = sync_nexus_principal(conn, principal_id, payload.password or None)

    audit("CREATE_ACCESS_PRINCIPAL", "access-control", {"principal_id": principal_id, "username": payload.username, "sync": {k: v for k, v in sync_result.items() if k != "generated_password"}}, request, actor=user)
    return {"id": principal_id, "sync": sync_result, "access": list_access_control()}


@app.put("/api/access-control/principals/{principal_id}")
def api_update_access_principal(principal_id: str, payload: AccessPrincipalIn, request: Request, user: str = Depends(current_user)):
    now = utc_now()
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM access_principals WHERE id=?", (principal_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Principal not found")

        known_groups = {r["id"] for r in conn.execute("SELECT id FROM access_groups").fetchall()}
        group_ids = validate_principal_groups(payload.group_ids, payload.enabled, known_groups)

        try:
            conn.execute(
                """
                UPDATE access_principals
                SET username=?, display_name=?, email=?, principal_type=?, enabled=?, nexus_user_id=?, updated_at=?
                WHERE id=?
                """,
                (
                    payload.username,
                    payload.display_name or payload.username,
                    payload.email,
                    payload.principal_type,
                    1 if payload.enabled else 0,
                    payload.username,
                    now,
                    principal_id,
                ),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="Principal username already exists.") from exc
        conn.execute("DELETE FROM access_group_members WHERE principal_id=?", (principal_id,))
        for group_id in group_ids:
            conn.execute("INSERT INTO access_group_members(principal_id, group_id) VALUES (?, ?)", (principal_id, group_id))

        sync_result = sync_nexus_principal(conn, principal_id, payload.password or None)

    audit("UPDATE_ACCESS_PRINCIPAL", "access-control", {"principal_id": principal_id, "sync": sync_result}, request, actor=user)
    return {"ok": True, "sync": sync_result, "access": list_access_control()}


@app.delete("/api/access-control/principals/{principal_id}")
def api_delete_access_principal(principal_id: str, request: Request, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM access_principals WHERE id=?", (principal_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Principal not found")
        principal = row_to_dict(row)
        nexus_user_id = principal["nexus_user_id"]
        nexus_request("DELETE", f"/service/rest/v1/security/users/{quote(nexus_user_id, safe='')}", expected={200, 204, 404})
        conn.execute("DELETE FROM access_group_members WHERE principal_id=?", (principal_id,))
        conn.execute("DELETE FROM access_principals WHERE id=?", (principal_id,))

    audit("DELETE_ACCESS_PRINCIPAL", "access-control", {"principal_id": principal_id, "username": principal["username"]}, request, actor=user)
    return {"ok": True, "access": list_access_control()}


@app.post("/api/access-control/ip-rules")
def api_create_ip_rule(payload: AccessIpRuleIn, request: Request, user: str = Depends(current_user)):
    rule_id = str(uuid.uuid4())
    now = utc_now()
    cidr = validate_cidr(payload.cidr)
    with db_conn() as conn:
        try:
            conn.execute(
                """
                INSERT INTO access_ip_rules(id, name, cidr, description, enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, payload.name, cidr, payload.description, 1 if payload.enabled else 0, now, now),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="IP/CIDR rule already exists.") from exc

    audit("CREATE_IP_RULE", "access-control", {"rule_id": rule_id, "cidr": cidr}, request, actor=user)
    return {"id": rule_id, "access": list_access_control()}


@app.put("/api/access-control/ip-rules/{rule_id}")
def api_update_ip_rule(rule_id: str, payload: AccessIpRuleIn, request: Request, user: str = Depends(current_user)):
    cidr = validate_cidr(payload.cidr)
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM access_ip_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="IP rule not found")
        try:
            conn.execute(
                """
                UPDATE access_ip_rules
                SET name=?, cidr=?, description=?, enabled=?, updated_at=?
                WHERE id=?
                """,
                (payload.name, cidr, payload.description, 1 if payload.enabled else 0, utc_now(), rule_id),
            )
        except DBIntegrityError as exc:
            raise HTTPException(status_code=409, detail="IP/CIDR rule already exists.") from exc

    audit("UPDATE_IP_RULE", "access-control", {"rule_id": rule_id, "cidr": cidr}, request, actor=user)
    return {"ok": True, "access": list_access_control()}


@app.delete("/api/access-control/ip-rules/{rule_id}")
def api_delete_ip_rule(rule_id: str, request: Request, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT id FROM access_ip_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="IP rule not found")
        conn.execute("DELETE FROM access_ip_rules WHERE id=?", (rule_id,))
    audit("DELETE_IP_RULE", "access-control", {"rule_id": rule_id}, request, actor=user)
    return {"ok": True, "access": list_access_control()}


@app.post("/api/access-control/sync-nexus")
def api_sync_access_to_nexus(request: Request, user: str = Depends(current_user)):
    results: list[dict[str, Any]] = []
    with db_conn() as conn:
        groups = [row_to_dict(r) for r in conn.execute("SELECT * FROM access_groups ORDER BY name ASC").fetchall()]
        principals = [r["id"] for r in conn.execute("SELECT id FROM access_principals ORDER BY username ASC").fetchall()]

        results.append(disable_nexus_anonymous())
        for group in groups:
            results.append(sync_nexus_role(group))
        for principal_id in principals:
            results.append(sync_nexus_principal(conn, principal_id))

    audit("SYNC_NEXUS_ACCESS", "access-control", {"results": results}, request, actor=user)
    return {"ok": True, "results": results, "access": list_access_control()}


@app.get("/api/access-control/firewall-script")
def api_firewall_script(user: str = Depends(current_user)):
    cidrs = active_trusted_cidrs()
    return {
        "trusted_cidrs": cidrs,
        "protected_ports": NEXUS_PROTECTED_PORTS,
        "script": firewall_script(cidrs),
    }


@app.post("/api/access-control/apply-firewall")
def api_apply_firewall(request: Request, user: str = Depends(current_user)):
    result = apply_firewall_policy(user)
    audit("APPLY_FIREWALL_POLICY", "access-control", result, request, actor=user)
    return {"ok": True, "result": result, "access": list_access_control()}


@app.post("/api/publish/docker")
def api_publish_docker(payload: DockerPublishIn, request: Request, user: str = Depends(current_user)):
    job_id = str(uuid.uuid4())
    target = hosted_image_ref(payload.target_image or payload.source_image)
    now = utc_now()

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "docker",
                "QUEUED",
                payload.source_image,
                target,
                payload.repository,
                payload.model_dump_json(),
                user,
                now,
            ),
        )

    audit("CREATE_DOCKER_PUBLISH", "publish", {"job_id": job_id, "source": payload.source_image, "target": target}, request, actor=user)
    thread = threading.Thread(target=run_docker_publish_job, args=(job_id, payload.model_dump(), user), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


@app.post("/api/publish/docker-archive")
async def api_publish_docker_archive(
    request: Request,
    target_image: str = Form(default=""),
    repository: str = Form(default="docker-hosted"),
    file: UploadFile = File(...),
    user: str = Depends(current_user),
):
    filename = safe_upload_name(file.filename or "docker-image.tar")
    if not filename.endswith((".tar", ".tar.gz", ".tgz")):
        raise HTTPException(status_code=400, detail="Docker archive must be .tar, .tar.gz, or .tgz.")

    job_id = str(uuid.uuid4())
    file_path = await save_upload(job_id, file)
    target = hosted_image_ref(target_image) if target_image else "from-archive"

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, file_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "docker-archive",
                "QUEUED",
                filename,
                target,
                repository,
                json.dumps({"filename": filename, "target_image": target_image}),
                str(file_path),
                user,
                utc_now(),
            ),
        )

    audit("CREATE_DOCKER_ARCHIVE_PUBLISH", "publish", {"job_id": job_id, "filename": filename, "target": target}, request, actor=user)
    thread = threading.Thread(target=run_docker_archive_publish_job, args=(job_id, file_path, target_image, repository), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


@app.post("/api/publish/python-fetch")
def api_publish_python_fetch(payload: PythonPackageFetchIn, request: Request, user: str = Depends(current_user)):
    job_id = str(uuid.uuid4())
    requirement = python_requirement(payload.package_name, payload.package_version)
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                f"python-{payload.python_version}",
                "QUEUED",
                requirement,
                f"{payload.repository}/{requirement}",
                payload.repository,
                payload.model_dump_json(),
                user,
                utc_now(),
            ),
        )

    audit("CREATE_PYTHON_FETCH_PUBLISH", "publish", {"job_id": job_id, "requirement": requirement, "python_version": payload.python_version}, request, actor=user)
    thread = threading.Thread(target=run_python_fetch_publish_job, args=(job_id, payload.model_dump()), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


@app.post("/api/publish/debian-fetch")
def api_publish_debian_fetch(payload: DebianPackageFetchIn, request: Request, user: str = Depends(current_user)):
    if payload.target_release not in APT_TARGETS:
        raise HTTPException(status_code=400, detail="Unsupported Debian/Ubuntu target release.")
    job_id = str(uuid.uuid4())
    package_spec = apt_package_spec(payload.package_name, payload.package_version)
    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "debian-fetch",
                "QUEUED",
                package_spec,
                f"{payload.repository}/{payload.target_release}/{package_spec}",
                payload.repository,
                payload.model_dump_json(),
                user,
                utc_now(),
            ),
        )

    audit("CREATE_DEBIAN_FETCH_PUBLISH", "publish", {"job_id": job_id, "package": package_spec, "target_release": payload.target_release}, request, actor=user)
    thread = threading.Thread(target=run_debian_fetch_publish_job, args=(job_id, payload.model_dump()), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


def safe_upload_name(filename: str) -> str:
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._+-]+", "-", name).strip(".-")
    if not name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


async def save_upload(job_id: str, upload: UploadFile) -> Path:
    filename = safe_upload_name(upload.filename or "artifact")
    target_dir = PUBLISH_ROOT / job_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    size = 0
    with target.open("wb") as out:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            out.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    return target


@app.post("/api/publish/python")
async def api_publish_python(
    request: Request,
    repository: str = Form(default=NEXUS_PYPI_HOSTED),
    file: UploadFile = File(...),
    user: str = Depends(current_user),
):
    raise HTTPException(status_code=410, detail="Manual Python package upload is disabled. Use /api/publish/python-fetch.")
    filename = safe_upload_name(file.filename or "python-package")
    if not filename.endswith((".whl", ".tar.gz", ".zip")):
        raise HTTPException(status_code=400, detail="Python package must be a wheel, source tarball, or zip distribution.")

    job_id = str(uuid.uuid4())
    file_path = await save_upload(job_id, file)

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, file_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, "python", "QUEUED", filename, f"{repository}/{filename}", repository, json.dumps({"filename": filename}), str(file_path), user, utc_now()),
        )

    audit("CREATE_PYTHON_PUBLISH", "publish", {"job_id": job_id, "repository": repository, "filename": filename}, request, actor=user)
    thread = threading.Thread(target=run_file_publish_job, args=(job_id, "python", repository, file_path), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


@app.post("/api/publish/debian")
async def api_publish_debian(
    request: Request,
    repository: str = Form(default=NEXUS_APT_HOSTED),
    file: UploadFile = File(...),
    user: str = Depends(current_user),
):
    raise HTTPException(status_code=410, detail="Manual Debian package upload is disabled. Use /api/publish/debian-fetch.")
    filename = safe_upload_name(file.filename or "package.deb")
    if not filename.endswith(".deb"):
        raise HTTPException(status_code=400, detail="Debian artifact must be a .deb file.")

    job_id = str(uuid.uuid4())
    file_path = await save_upload(job_id, file)

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO publish_jobs(id, artifact_type, status, source, target, repository, payload_json, file_path, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (job_id, "debian", "QUEUED", filename, f"{repository}/{filename}", repository, json.dumps({"filename": filename}), str(file_path), user, utc_now()),
        )

    audit("CREATE_DEBIAN_PUBLISH", "publish", {"job_id": job_id, "repository": repository, "filename": filename}, request, actor=user)
    thread = threading.Thread(target=run_file_publish_job, args=(job_id, "debian", repository, file_path), daemon=True)
    thread.start()
    return {"id": job_id, "status": "QUEUED"}


@app.get("/api/publish/jobs")
def api_publish_jobs(user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM publish_jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [row_to_dict(r) for r in rows]


@app.get("/api/publish/jobs/{job_id}/logs")
def api_publish_job_logs(job_id: str, user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute("SELECT ts, level, message FROM publish_logs WHERE job_id=? ORDER BY id ASC", (job_id,)).fetchall()
    return [row_to_dict(r) for r in rows]


@app.get("/api/servers")
def list_servers(user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM servers ORDER BY created_at DESC").fetchall()
    return [row_to_dict(r) for r in rows]


@app.post("/api/servers")
def create_server(payload: ServerIn, request: Request, user: str = Depends(current_user)):
    server_id = str(uuid.uuid4())
    now = utc_now()

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO servers(id, name, host, port, username, auth_method, key_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server_id,
                payload.name,
                payload.host,
                payload.port,
                payload.username,
                payload.auth_method,
                payload.key_path,
                now,
                now,
            ),
        )

    audit("CREATE_SERVER", "server", payload.model_dump(), request, actor=user)
    return {"id": server_id}


@app.delete("/api/servers/{server_id}")
def delete_server(server_id: str, request: Request, user: str = Depends(current_user)):
    with db_conn() as conn:
        conn.execute("DELETE FROM servers WHERE id=?", (server_id,))

    audit("DELETE_SERVER", "server", {"server_id": server_id}, request, actor=user)
    return {"ok": True}


@app.post("/api/preflight")
def api_preflight(payload: SSHPayload, request: Request, user: str = Depends(current_user)):
    try:
        result = preflight_check(payload)
        audit("PREFLIGHT", "server", {"target": result.get("target"), "passed": result.get("passed")}, request, actor=user)
        return result
    except Exception as exc:
        audit("PREFLIGHT_FAILED", "server", {"error": str(exc)}, request, actor=user)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/security-gate")
def api_security_gate(payload: SecurityGateIn, request: Request, user: str = Depends(current_user)):
    result = security_gate(payload)
    audit("SECURITY_GATE", "job", {"passed": result["passed"], "errors": result["errors"]}, request, actor=user)
    return result


@app.get("/api/artifacts")
def artifacts(user: str = Depends(current_user)):
    docker_items = set(read_lines(CONFIGS_ROOT / "docker-images.txt"))
    python_items = set(read_lines(CONFIGS_ROOT / "python-requirements.txt"))
    python2_items = set(read_lines(CONFIGS_ROOT / "python2-requirements.txt"))
    apt_items = set(read_lines(CONFIGS_ROOT / "apt-packages.txt"))

    for item in nexus_components("docker-hosted"):
        docker_items.add(item)

    for item in nexus_components("pypi-hosted"):
        python_items.add(item)

    for item in nexus_components("pypi2-hosted"):
        python2_items.add(item)

    return {
        "docker_images": sorted(docker_items),
        "python_packages": sorted(python_items),
        "python2_packages": sorted(python2_items),
        "apt_packages": sorted(apt_items),
        "apt_targets": APT_TARGETS,
    }


@app.post("/api/jobs")
def create_job(payload: JobIn, request: Request, user: str = Depends(current_user)):
    job_id = str(uuid.uuid4())
    target = payload.deploy.server_id or payload.deploy.host or "local-bundle-only"

    with db_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs(id, type, status, target_server, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                "BUNDLE_DEPLOY" if payload.deploy.enabled else "BUNDLE_ONLY",
                "QUEUED",
                target,
                payload.model_dump_json(),
                utc_now(),
            ),
        )

    audit("CREATE_JOB", "job", {"job_id": job_id, "target": target}, request, actor=user)

    t = threading.Thread(target=run_job, args=(job_id, payload.model_dump(), user), daemon=True)
    t.start()

    return {"id": job_id, "status": "QUEUED"}


@app.get("/api/jobs")
def list_jobs(user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()

    result = []
    for row in rows:
        item = row_to_dict(row)
        item.pop("payload_json", None)
        result.append(item)
    return result


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    return row_to_dict(row)


@app.get("/api/jobs/{job_id}/logs")
def get_job_logs(job_id: str, user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT ts, level, message FROM job_logs WHERE job_id=? ORDER BY id ASC",
            (job_id,),
        ).fetchall()

    return [row_to_dict(r) for r in rows]


@app.get("/api/jobs/{job_id}/report/html")
def get_report_html(job_id: str, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT report_html FROM jobs WHERE id=?", (job_id,)).fetchone()

    if not row or not row["report_html"]:
        raise HTTPException(status_code=404, detail="HTML report not found")

    return FileResponse(row["report_html"], media_type="text/html")


@app.get("/api/jobs/{job_id}/report/pdf")
def get_report_pdf(job_id: str, user: str = Depends(current_user)):
    with db_conn() as conn:
        row = conn.execute("SELECT report_pdf FROM jobs WHERE id=?", (job_id,)).fetchone()

    if not row or not row["report_pdf"]:
        raise HTTPException(status_code=404, detail="PDF report not found")

    return FileResponse(row["report_pdf"], media_type="application/pdf", filename=f"{job_id}-deployment-report.pdf")


@app.get("/api/audit")
def audit_logs(user: str = Depends(current_user)):
    with db_conn() as conn:
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300").fetchall()

    return [row_to_dict(r) for r in rows]


@app.get("/api/storage")
def api_storage(user: str = Depends(current_user)):
    return storage_guard()


@app.post("/api/cleanup")
def api_cleanup(request: Request, user: str = Depends(current_user)):
    result = cleanup_old_files(int(security_settings()["cleanup_keep_days"]))
    audit("CLEANUP", "storage", result, request, actor=user)
    return result


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

# PROFESSIONAL_REPORTS_INSTALL
try:
    from app.professional_reports import install_professional_reports
    install_professional_reports(app)
    print("Professional report endpoints installed.")
except Exception as exc:
    print(f"Professional report endpoints failed to install: {exc}")
# END PROFESSIONAL_REPORTS_INSTALL
