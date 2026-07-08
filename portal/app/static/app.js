const $ = (id) => document.getElementById(id);

const state = {
  servers: [],
  artifacts: {},
  selectedJob: null,
  preflight: null,
  security: null,
};

async function api(path, options = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    try {
      const data = await res.json();
      msg = data.detail || msg;
    } catch {}
    throw new Error(msg);
  }

  return await res.json();
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[c]));
}

function toast(msg) {
  $("toast").textContent = msg;
  $("toast").classList.remove("hidden");
  setTimeout(() => $("toast").classList.add("hidden"), 3500);
}

function lines(value) {
  return value.split("\n").map(x => x.trim()).filter(Boolean);
}

function selectedValues(containerId) {
  return [...document.querySelectorAll(`#${containerId} input[type=checkbox]:checked`)].map(x => x.value);
}

function renderChecks(containerId, values) {
  const el = $(containerId);
  el.innerHTML = "";

  if (!values || !values.length) {
    el.innerHTML = `<div class="muted">No items found yet. Use manual input.</div>`;
    return;
  }

  for (const v of values) {
    const label = document.createElement("label");
    label.className = "check";
    label.innerHTML = `<input type="checkbox" value="${escapeHtml(v)}"> <span>${escapeHtml(v)}</span>`;
    el.appendChild(label);
  }
}

function sshPayload() {
  return {
    server_id: $("deployServer").value || null,
    host: $("deployHost").value || null,
    port: Number($("deployPort").value || 22),
    username: $("deployUser").value || null,
    password: $("deployPassword").value || null,
    key_path: $("deployKey").value || null,
    remote_dir: $("deployRemoteDir").value || "/tmp/airgap-deployments",
    use_sudo: $("useSudo").checked,
  };
}

function bundlePayload() {
  const dockerImages = [...selectedValues("dockerArtifacts"), ...lines($("dockerManual").value)];
  const pythonPackages = [...selectedValues("pythonArtifacts"), ...lines($("pythonManual").value)];
  const aptPackages = [...selectedValues("aptArtifacts"), ...lines($("aptManual").value)];

  return {
    docker_images: [...new Set(dockerImages)],
    python_packages: [...new Set(pythonPackages)],
    apt_packages: [...new Set(aptPackages)],
    apt_target: $("aptTarget").value,
  };
}

function fullJobPayload() {
  return {
    bundle: bundlePayload(),
    deploy: {
      enabled: $("deployEnabled").checked,
      ...sshPayload(),
      docker_load: $("runDockerLoad").checked,
      python_wheels: $("setupPython").checked,
      apt_mini: $("setupApt").checked,
      extra_commands: $("extraCommands").value,
    },
  };
}

function goStep(n) {
  document.querySelectorAll(".wizard-page").forEach(x => x.classList.add("hidden"));
  $(`wizardStep${n}`).classList.remove("hidden");
  document.querySelectorAll(".step").forEach(x => x.classList.remove("active"));
  document.querySelector(`.step[data-step="${n}"]`).classList.add("active");

  if (Number(n) === 5) {
    const b = bundlePayload();
    $("bundleSummary").innerHTML = `
      <div class="result-item OK">
        <b>Bundle Summary</b><br>
        Docker images: ${b.docker_images.length}<br>
        Python packages: ${b.python_packages.length}<br>
        APT packages: ${b.apt_packages.length}<br>
        APT target: ${escapeHtml(b.apt_target)}<br>
        Deploy enabled: ${$("deployEnabled").checked ? "Yes" : "No"}
      </div>
    `;
  }
}

async function checkSession() {
  try {
    await api("/api/session");
    $("loginView").classList.add("hidden");
    $("appView").classList.remove("hidden");
    await refreshAll();
  } catch {
    $("loginView").classList.remove("hidden");
    $("appView").classList.add("hidden");
  }
}

async function refreshAll() {
  await Promise.all([
    loadStats(),
    loadServers(),
    loadArtifacts(),
    loadJobs(),
    loadAudit(),
    loadStorage(),
  ]);
}

async function loadStats() {
  const s = await api("/api/stats");
  $("statServers").textContent = s.servers;
  $("statJobs").textContent = s.jobs;
  $("statRunning").textContent = s.running;
  $("statSuccess").textContent = s.success;
  $("statFailed").textContent = s.failed;
}

async function loadServers() {
  state.servers = await api("/api/servers");

  const list = $("serversList");
  list.innerHTML = "";

  const select = $("deployServer");
  select.innerHTML = `<option value="">Ad-hoc / Manual target</option>`;

  for (const s of state.servers) {
    const item = document.createElement("div");
    item.className = "item";
    item.innerHTML = `
      <strong>${escapeHtml(s.name)}</strong>
      <span>${escapeHtml(s.username)}@${escapeHtml(s.host)}:${s.port} • ${escapeHtml(s.auth_method)}</span>
      <div style="margin-top:10px">
        <button class="ghost" onclick="deleteServer('${s.id}')">Delete</button>
      </div>
    `;
    list.appendChild(item);

    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = `${s.name} - ${s.username}@${s.host}:${s.port}`;
    select.appendChild(opt);
  }

  if (!state.servers.length) {
    list.innerHTML = `<div class="muted">No saved servers yet.</div>`;
  }
}

async function loadArtifacts() {
  state.artifacts = await api("/api/artifacts");
  renderChecks("dockerArtifacts", state.artifacts.docker_images || []);
  renderChecks("pythonArtifacts", state.artifacts.python_packages || []);
  renderChecks("aptArtifacts", state.artifacts.apt_packages || []);
}

async function runPreflight() {
  $("preflightResult").innerHTML = `<div class="result-item WARN">Running preflight...</div>`;

  try {
    const res = await api("/api/preflight", {
      method: "POST",
      body: JSON.stringify(sshPayload()),
    });

    state.preflight = res;

    $("preflightResult").innerHTML = res.checks.map(c => `
      <div class="result-item ${escapeHtml(c.status)}">
        <b>${escapeHtml(c.status)} — ${escapeHtml(c.name)}</b><br>
        <span>${escapeHtml(c.detail || "")}</span>
      </div>
    `).join("");

    toast(res.passed ? "Preflight passed" : "Preflight has blocking issues");
  } catch (err) {
    $("preflightResult").innerHTML = `<div class="result-item FAIL">${escapeHtml(err.message)}</div>`;
  }
}

async function runSecurityGate() {
  $("securityResult").innerHTML = `<div class="result-item WARN">Running security gate...</div>`;

  try {
    const res = await api("/api/security-gate", {
      method: "POST",
      body: JSON.stringify({
        bundle: bundlePayload(),
        deploy: sshPayload(),
        extra_commands: $("extraCommands").value,
      }),
    });

    state.security = res;

    let html = "";
    html += `<div class="result-item ${res.passed ? "OK" : "FAIL"}"><b>${res.passed ? "PASSED" : "BLOCKED"}</b></div>`;

    for (const e of res.errors || []) {
      html += `<div class="result-item FAIL"><b>Error</b><br>${escapeHtml(e)}</div>`;
    }

    for (const w of res.warnings || []) {
      html += `<div class="result-item WARN"><b>Warning</b><br>${escapeHtml(w)}</div>`;
    }

    if (!res.errors.length && !res.warnings.length) {
      html += `<div class="result-item OK">No blocking security issue found.</div>`;
    }

    $("securityResult").innerHTML = html;
    toast(res.passed ? "Security Gate passed" : "Security Gate blocked the job");
  } catch (err) {
    $("securityResult").innerHTML = `<div class="result-item FAIL">${escapeHtml(err.message)}</div>`;
  }
}

async function loadJobs() {
  const jobs = await api("/api/jobs");
  const el = $("jobsList");
  el.innerHTML = "";

  for (const j of jobs) {
    const div = document.createElement("div");
    div.className = "job";
    div.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center">
        <div>
          <strong>${escapeHtml(j.id)}</strong>
          <span>${escapeHtml(j.type)} • ${escapeHtml(j.target_server || "")}</span>
        </div>
        <span class="badge ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
      </div>
      <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap">
        <button class="ghost" onclick="viewLogs('${j.id}')">View Logs</button>
        <a class="ghost" href="/api/jobs/${j.id}/report/html" target="_blank">HTML Report</a>
        <a class="ghost" href="/api/jobs/${j.id}/report/pdf" target="_blank">PDF Report</a>
      </div>
    `;
    el.appendChild(div);
  }

  if (!jobs.length) {
    el.innerHTML = `<div class="muted">No jobs yet.</div>`;
  }
}

async function viewLogs(jobId) {
  state.selectedJob = jobId;
  const logs = await api(`/api/jobs/${jobId}/logs`);
  $("jobLogs").textContent = logs.map(l => `[${l.ts}] [${l.level}] ${l.message}`).join("\n") || "No logs yet.";
}

async function loadAudit() {
  const logs = await api("/api/audit");
  const el = $("auditList");
  el.innerHTML = "";

  for (const a of logs) {
    const div = document.createElement("div");
    div.className = "audit-item";
    div.innerHTML = `
      <strong>${escapeHtml(a.action)} → ${escapeHtml(a.entity)}</strong><br>
      <span>${escapeHtml(a.ts)} • ${escapeHtml(a.actor)} • ${escapeHtml(a.ip || "")}</span>
      <pre style="white-space:pre-wrap;color:#98a2b3">${escapeHtml(a.details || "")}</pre>
    `;
    el.appendChild(div);
  }

  if (!logs.length) {
    el.innerHTML = `<div class="muted">No audit events yet.</div>`;
  }
}

async function loadStorage() {
  const s = await api("/api/storage");
  const el = $("storageStats");
  el.innerHTML = "";

  for (const [k, v] of Object.entries(s)) {
    const div = document.createElement("div");
    div.className = "stat-card";
    div.innerHTML = `<span>${escapeHtml(k)}</span><strong>${escapeHtml(v)}</strong>`;
    el.appendChild(div);
  }
}

async function deleteServer(id) {
  await api(`/api/servers/${id}`, { method: "DELETE" });
  toast("Server deleted");
  await loadServers();
}

window.deleteServer = deleteServer;
window.viewLogs = viewLogs;

document.querySelectorAll(".nav").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav").forEach(x => x.classList.remove("active"));
    btn.classList.add("active");

    document.querySelectorAll(".tab").forEach(x => x.classList.add("hidden"));
    $(btn.dataset.tab).classList.remove("hidden");
    $("pageTitle").textContent = btn.textContent;
  });
});

document.querySelectorAll(".step").forEach(btn => {
  btn.addEventListener("click", () => goStep(btn.dataset.step));
});

document.querySelectorAll("[data-next]").forEach(btn => {
  btn.addEventListener("click", () => goStep(btn.dataset.next));
});

document.querySelectorAll("[data-prev]").forEach(btn => {
  btn.addEventListener("click", () => goStep(btn.dataset.prev));
});

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("loginError").textContent = "";

  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({
        username: $("loginUser").value,
        password: $("loginPass").value,
      }),
    });
    await checkSession();
  } catch (err) {
    $("loginError").textContent = err.message;
  }
});

$("logoutBtn").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: "{}" });
  location.reload();
});

$("serverForm").addEventListener("submit", async (e) => {
  e.preventDefault();

  await api("/api/servers", {
    method: "POST",
    body: JSON.stringify({
      name: $("serverName").value,
      host: $("serverHost").value,
      port: Number($("serverPort").value || 22),
      username: $("serverUser").value,
      auth_method: $("serverAuth").value,
      key_path: $("serverKey").value,
    }),
  });

  e.target.reset();
  $("serverPort").value = "22";
  toast("Server saved");
  await loadServers();
});

$("runPreflightBtn").addEventListener("click", runPreflight);
$("runSecurityBtn").addEventListener("click", runSecurityGate);

$("createJobBtn").addEventListener("click", async () => {
  $("jobCreateResult").textContent = "Creating job...";

  try {
    const res = await api("/api/jobs", {
      method: "POST",
      body: JSON.stringify(fullJobPayload()),
    });

    $("jobCreateResult").textContent = `Job created: ${res.id}`;
    toast("Job started");
    await loadJobs();
    viewLogs(res.id);
  } catch (err) {
    $("jobCreateResult").textContent = `FAILED: ${err.message}`;
  }
});

$("refreshJobsBtn").addEventListener("click", loadJobs);
$("refreshAuditBtn").addEventListener("click", loadAudit);
$("refreshStorageBtn").addEventListener("click", loadStorage);

$("cleanupBtn").addEventListener("click", async () => {
  $("cleanupResult").textContent = "Running cleanup...";
  const res = await api("/api/cleanup", { method: "POST", body: "{}" });
  $("cleanupResult").textContent = `Removed files: ${res.removed_files}, freed: ${res.freed_mb} MB`;
  await loadStorage();
});

setInterval(async () => {
  if (!$("appView").classList.contains("hidden")) {
    await loadStats().catch(() => {});
    await loadJobs().catch(() => {});
    if (state.selectedJob) await viewLogs(state.selectedJob).catch(() => {});
  }
}, 3500);

checkSession();
