import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import shutil
import sqlite3
import subprocess
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import paramiko
import requests
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


APP_WORKSPACE = Path(os.getenv("APP_WORKSPACE", "/workspace"))
HOST_PROJECT_ROOT = os.getenv("HOST_PROJECT_ROOT", "")
DB_PATH = Path(os.getenv("PORTAL_DB_PATH", str(APP_WORKSPACE / "data/portal/portal.db")))

BUNDLES_ROOT = APP_WORKSPACE / "offline-bundles/portal-jobs"
REPORTS_ROOT = APP_WORKSPACE / "reports/jobs"
CONFIGS_ROOT = APP_WORKSPACE / "configs"

NEXUS_URL = os.getenv("NEXUS_URL", "http://localhost:8081").rstrip("/")
NEXUS_ADMIN_PASSWORD = os.getenv("NEXUS_ADMIN_PASSWORD", "")
NEXUS_DOCKER_GROUP = os.getenv("NEXUS_DOCKER_GROUP", "localhost:5002")
NEXUS_RAW_OFFLINE_BUNDLES = os.getenv("NEXUS_RAW_OFFLINE_BUNDLES", "raw-offline-bundles")

PORTAL_ADMIN_USER = os.getenv("PORTAL_ADMIN_USER", "admin")
PORTAL_ADMIN_PASSWORD = os.getenv("PORTAL_ADMIN_PASSWORD", "ChangeThisPortalPassword_12345")
PORTAL_ADMIN_PASSWORD_HASH = os.getenv("PORTAL_ADMIN_PASSWORD_HASH", "")
PORTAL_SESSION_SECRET = os.getenv("PORTAL_SESSION_SECRET", "dev-secret-change-me")
PORTAL_STRICT_HOST_KEY = os.getenv("PORTAL_STRICT_HOST_KEY", "false").lower() == "true"
PORTAL_REMOTE_DEFAULT_DIR = os.getenv("PORTAL_REMOTE_DEFAULT_DIR", "/tmp/airgap-deployments")
PORTAL_ENABLE_GRYPE_SCAN = os.getenv("PORTAL_ENABLE_GRYPE_SCAN", "false").lower() == "true"
PORTAL_BLOCK_CRITICAL = os.getenv("PORTAL_BLOCK_CRITICAL", "true").lower() == "true"
PORTAL_BLOCK_LATEST_TAG = os.getenv("PORTAL_BLOCK_LATEST_TAG", "true").lower() == "true"
PORTAL_MIN_FREE_DISK_MB = int(os.getenv("PORTAL_MIN_FREE_DISK_MB", "2048"))
PORTAL_CLEANUP_KEEP_DAYS = int(os.getenv("PORTAL_CLEANUP_KEEP_DAYS", "14"))

COOKIE_NAME = "airgap_portal_session"
SESSION_TTL_SECONDS = 8 * 60 * 60

app = FastAPI(
    title="Nexus Air-Gapped Deployment Portal",
    version="0.4.0",
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
    apt_packages: list[str] = []
    apt_target: str = "ubuntu-noble"


class SecurityGateIn(BaseModel):
    bundle: BundleSpec
    deploy: SSHPayload | None = None
    extra_commands: str = ""


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
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    BUNDLES_ROOT.mkdir(parents=True, exist_ok=True)
    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    ensure_dirs()

    with db_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            ts TEXT NOT NULL,
            level TEXT NOT NULL,
            message TEXT NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            entity TEXT NOT NULL,
            details TEXT,
            ip TEXT
        )
        """)


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


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
    return response


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

    if PORTAL_STRICT_HOST_KEY:
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
    remote_dir = data.get("remote_dir") or PORTAL_REMOTE_DEFAULT_DIR
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
        add("Free Disk Space", "OK" if free_mb >= PORTAL_MIN_FREE_DISK_MB else "FAIL", f"{free_mb} MB free")

        code, docker_version, _ = remote_exec(client, "docker --version", job_id, check=False)
        add("Docker CLI", "OK" if code == 0 else "WARN", docker_version)

        code, docker_info, _ = remote_exec(client, f"{sudo}docker info >/dev/null 2>&1 && echo running || echo not-running", job_id, check=False)
        add("Docker Daemon", "OK" if "running" in docker_info else "WARN", docker_info)

        code, python_version, _ = remote_exec(client, "python3 --version", job_id, check=False)
        add("Python 3", "OK" if code == 0 else "WARN", python_version)

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


def image_uses_latest(image: str) -> bool:
    tail = image.split("/")[-1]
    return ":" not in tail or tail.endswith(":latest")


def run_grype_scan(image: str) -> dict[str, Any]:
    if not PORTAL_ENABLE_GRYPE_SCAN:
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
    errors: list[str] = []
    warnings: list[str] = []
    scan_results: dict[str, Any] = {}

    docker_images = [x.strip() for x in payload.bundle.docker_images if x.strip()]
    python_packages = [x.strip() for x in payload.bundle.python_packages if x.strip()]
    apt_packages = [x.strip() for x in payload.bundle.apt_packages if x.strip()]

    if not docker_images and not python_packages and not apt_packages:
        errors.append("No artifacts selected.")

    if PORTAL_BLOCK_LATEST_TAG:
        for image in docker_images:
            if image_uses_latest(image):
                errors.append(f"Docker image uses implicit/latest tag: {image}")

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

        if PORTAL_BLOCK_CRITICAL and critical > 0:
            errors.append(f"Critical vulnerabilities found in {image}: {critical}")

        if high > 0:
            warnings.append(f"High vulnerabilities found in {image}: {high}")

    passed = len(errors) == 0

    return {
        "passed": passed,
        "errors": errors,
        "warnings": warnings,
        "scan_results": scan_results,
        "policy": {
            "block_latest_tag": PORTAL_BLOCK_LATEST_TAG,
            "block_critical": PORTAL_BLOCK_CRITICAL,
            "grype_scan_enabled": PORTAL_ENABLE_GRYPE_SCAN,
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
Description: Lightweight offline APT mini repository generated by Portal V4
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
    (bundle_dir / "apt-mini-repo").mkdir(parents=True, exist_ok=True)
    (bundle_dir / "metadata").mkdir(parents=True, exist_ok=True)

    selected_docker_images = [x.strip() for x in spec.docker_images if x.strip()]
    selected_python_packages = [x.strip() for x in spec.python_packages if x.strip()]
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
    <p><b>Job ID:</b> {job_id}</p>
    <p><b>Status:</b> {jobd.get("status")}</p>
    <p><b>Target:</b> {jobd.get("target_server")}</p>
    <p><b>Created:</b> {jobd.get("created_at")}</p>
    <p><b>Started:</b> {jobd.get("started_at")}</p>
    <p><b>Finished:</b> {jobd.get("finished_at")}</p>
    <p><b>Bundle SHA256:</b> {jobd.get("bundle_sha256") or ""}</p>
  </div>

  <div class="card">
    <h2>Selected Artifacts</h2>
    <pre>{json.dumps(payload.get("bundle", {}), indent=2, ensure_ascii=False)}</pre>
  </div>

  <div class="card">
    <h2>Preflight</h2>
    <pre>{json.dumps(preflight, indent=2, ensure_ascii=False)}</pre>
  </div>

  <div class="card">
    <h2>Security Gate</h2>
    <pre>{json.dumps(security, indent=2, ensure_ascii=False)}</pre>
  </div>

  <div class="card">
    <h2>Rollback Plan</h2>
    <pre>{json.dumps(rollback, indent=2, ensure_ascii=False)}</pre>
  </div>

  <div class="card">
    <h2>Logs</h2>
    <pre>{"".join([f"[{r['ts']}] [{r['level']}] {r['message']}\\n" for r in logs])}</pre>
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
    remote_dir = deploy.remote_dir or PORTAL_REMOTE_DEFAULT_DIR
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
    total = directory_size(APP_WORKSPACE)
    nexus = directory_size(APP_WORKSPACE / "data/nexus")
    portal = directory_size(APP_WORKSPACE / "data/portal")
    bundles = directory_size(BUNDLES_ROOT)
    reports = directory_size(REPORTS_ROOT)

    return {
        "workspace_mb": round(total / 1024 / 1024, 2),
        "nexus_mb": round(nexus / 1024 / 1024, 2),
        "portal_db_mb": round(portal / 1024 / 1024, 2),
        "portal_bundles_mb": round(bundles / 1024 / 1024, 2),
        "reports_mb": round(reports / 1024 / 1024, 2),
        "cleanup_keep_days": PORTAL_CLEANUP_KEEP_DAYS,
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
# Routes
# -----------------------------------------------------------------------------

@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.4.0"}


@app.get("/")
def index():
    return FileResponse("/app/app/templates/index.html")


@app.post("/api/login")
async def login(payload: LoginIn, response: Response, request: Request):
    if payload.username != PORTAL_ADMIN_USER or not verify_password(payload.password):
        audit("LOGIN_FAILED", "portal", {"username": payload.username}, request, actor=payload.username)
        raise HTTPException(status_code=401, detail="Invalid username or password")

    response.set_cookie(
        COOKIE_NAME,
        make_cookie(payload.username),
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )

    audit("LOGIN_SUCCESS", "portal", {"username": payload.username}, request, actor=payload.username)
    return {"ok": True, "username": payload.username}


@app.post("/api/logout")
def logout(response: Response, user: str = Depends(current_user)):
    response.delete_cookie(COOKIE_NAME)
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
    }


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
    apt_items = set(read_lines(CONFIGS_ROOT / "apt-packages.txt"))

    for item in nexus_components("docker-hosted"):
        docker_items.add(item)

    for item in nexus_components("pypi-hosted"):
        python_items.add(item)

    return {
        "docker_images": sorted(docker_items),
        "python_packages": sorted(python_items),
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
    result = cleanup_old_files(PORTAL_CLEANUP_KEEP_DAYS)
    audit("CLEANUP", "storage", result, request, actor=user)
    return result


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
