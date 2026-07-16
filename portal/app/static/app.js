const $ = (id) => document.getElementById(id);

const state = {
  servers: [],
  artifacts: {},
  selectedJob: null,
  preflight: null,
  security: null,
  accessControl: null,
  selectedPublishJob: null,
};

function cookie(name) {
  return document.cookie
    .split(";")
    .map(x => x.trim())
    .find(x => x.startsWith(`${name}=`))
    ?.split("=")
    .slice(1)
    .join("=") || "";
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const isFormData = options.body instanceof FormData;
  const headers = { ...(isFormData ? {} : { "Content-Type": "application/json" }), ...(options.headers || {}) };

  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = decodeURIComponent(cookie("airgap_portal_csrf"));
  }

  const res = await fetch(path, {
    credentials: "same-origin",
    headers,
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

function setResult(id, msg, status = "") {
  const el = $(id);
  el.textContent = msg;
  el.className = `mini-result ${status}`.trim();
}

function setSecurityBusy(isBusy) {
  document.querySelectorAll("#security button, #security input, #security select").forEach(el => {
    el.disabled = isBusy;
  });
}

function lines(value) {
  return value.split("\n").map(x => x.trim()).filter(Boolean);
}

function selectedValues(containerId) {
  return [...document.querySelectorAll(`#${containerId} input[type=checkbox]:checked`)].map(x => x.value);
}

function checkedValues(name) {
  return [...document.querySelectorAll(`input[name="${name}"]:checked`)].map(x => x.value);
}

function focusSecurityForm(formId, message) {
  const form = $(formId);
  if (form) form.scrollIntoView({ behavior: "smooth", block: "start" });
  setResult("accessActionResult", message);
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
  const python2Packages = [...selectedValues("python2Artifacts"), ...lines($("python2Manual").value)];
  const aptPackages = [...selectedValues("aptArtifacts"), ...lines($("aptManual").value)];

  return {
    docker_images: [...new Set(dockerImages)],
    python_packages: [...new Set(pythonPackages)],
    python2_packages: [...new Set(python2Packages)],
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
        Python 2 packages: ${(b.python2_packages || []).length}<br>
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
    loadAccessControl(),
    loadPublishJobs(),
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
        <button class="ghost danger" type="button" data-server-action="delete" data-server-id="${escapeHtml(s.id)}">Delete</button>
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
  renderChecks("python2Artifacts", state.artifacts.python2_packages || []);
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
        <button class="ghost" type="button" data-job-action="view-logs" data-job-id="${escapeHtml(j.id)}">View Logs</button>
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
  $("jobLogs").textContent = "Loading logs...";
  try {
    const logs = await api(`/api/jobs/${jobId}/logs`);
    $("jobLogs").textContent = logs.map(l => `[${l.ts}] [${l.level}] ${l.message}`).join("\n") || "No logs yet.";
  } catch (err) {
    $("jobLogs").textContent = `FAILED: ${err.message}`;
    toast("Failed to load job logs");
  }
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

function resetAccessGroupForm() {
  $("accessGroupId").value = "";
  $("accessGroupName").value = "";
  $("accessGroupDesc").value = "";
  document.querySelectorAll('input[name="accessPermission"]').forEach(x => { x.checked = false; });
  setResult("accessActionResult", "");
}

function resetAccessPrincipalForm() {
  $("accessPrincipalId").value = "";
  $("accessPrincipalUsername").value = "";
  $("accessPrincipalUsername").disabled = false;
  $("accessPrincipalDisplay").value = "";
  $("accessPrincipalEmail").value = "";
  $("accessPrincipalPassword").value = "";
  $("accessPrincipalType").value = "service";
  $("accessPrincipalEnabled").checked = true;
  document.querySelectorAll('input[name="accessPrincipalGroup"]').forEach(x => { x.checked = false; });
  setResult("accessActionResult", "");
  setResult("accessPasswordResult", "");
}

function resetAccessIpForm() {
  $("accessIpId").value = "";
  $("accessIpName").value = "";
  $("accessIpCidr").value = "";
  $("accessIpDesc").value = "";
  $("accessIpEnabled").checked = true;
  setResult("accessActionResult", "");
}

function renderAccessChoices(data) {
  const permEl = $("accessPermissionChecks");
  permEl.innerHTML = "";
  for (const [key, label] of Object.entries(data.permissions || {})) {
    const item = document.createElement("label");
    item.className = "security-choice";
    item.innerHTML = `
      <input name="accessPermission" type="checkbox" value="${escapeHtml(key)}">
      <span class="security-choice-copy">
        <strong>${escapeHtml(key)}</strong>
        <small>${escapeHtml(label)}</small>
      </span>
    `;
    permEl.appendChild(item);
  }

  const groupEl = $("accessGroupChecks");
  groupEl.innerHTML = "";
  for (const g of data.groups || []) {
    const item = document.createElement("label");
    item.className = "security-choice";
    item.innerHTML = `
      <input name="accessPrincipalGroup" type="checkbox" value="${escapeHtml(g.id)}">
      <span class="security-choice-copy">
        <strong>${escapeHtml(g.name)}</strong>
        <small>${escapeHtml((g.permissions || []).join(" • ") || "No permissions")}</small>
      </span>
    `;
    groupEl.appendChild(item);
  }
}

function renderAccessControl(data) {
  state.accessControl = data;
  $("accessGroupCount").textContent = data.groups.length;
  $("accessPrincipalCount").textContent = data.principals.length;
  $("accessIpCount").textContent = data.ip_rules.filter(x => x.enabled).length;
  $("accessPorts").textContent = data.protected_ports.join(", ");

  renderAccessChoices(data);

  const groupsEl = $("accessGroupsList");
  groupsEl.innerHTML = "";
  for (const g of data.groups) {
    const div = document.createElement("div");
    div.className = "security-record";
    div.innerHTML = `
      <div class="security-record-main">
        <div>
          <strong>${escapeHtml(g.name)}</strong>
          <span>${escapeHtml(g.description || "No description")}</span>
        </div>
        <b class="security-badge">${escapeHtml(String((g.permissions || []).length))} permissions</b>
      </div>
      <small>${(g.permissions || []).map(escapeHtml).join(" • ") || "No permissions"}</small>
      <div class="security-row-actions">
        <button class="ghost" type="button" data-access-action="edit-group" data-access-id="${escapeHtml(g.id)}">Edit</button>
        <button class="ghost danger" type="button" data-access-action="delete-group" data-access-id="${escapeHtml(g.id)}">Delete</button>
      </div>
    `;
    groupsEl.appendChild(div);
  }
  if (!data.groups.length) groupsEl.innerHTML = `<div class="security-empty">No access groups yet. Create a group before adding trusted accounts.</div>`;

  const principalsEl = $("accessPrincipalsList");
  principalsEl.innerHTML = "";
  for (const p of data.principals) {
    const names = data.groups.filter(g => (p.group_ids || []).includes(g.id)).map(g => g.name);
    const div = document.createElement("div");
    div.className = "security-record";
    div.innerHTML = `
      <div class="security-record-main">
        <div>
          <strong>${escapeHtml(p.username)}</strong>
          <span>${escapeHtml(p.principal_type)} • ${escapeHtml(p.display_name || "No display name")} • ${escapeHtml(p.email || "No email")}</span>
        </div>
        <b class="security-badge ${p.enabled ? "" : "off"}">${p.enabled ? "Enabled" : "Disabled"}</b>
      </div>
      <small>${names.map(escapeHtml).join(" • ") || "No groups assigned"}</small>
      <div class="security-row-actions">
        <button class="ghost" type="button" data-access-action="edit-principal" data-access-id="${escapeHtml(p.id)}">Edit</button>
        <button class="ghost danger" type="button" data-access-action="delete-principal" data-access-id="${escapeHtml(p.id)}">Delete</button>
      </div>
    `;
    principalsEl.appendChild(div);
  }
  if (!data.principals.length) principalsEl.innerHTML = `<div class="security-empty">No trusted users or systems yet. Add a service account or human user and assign at least one group.</div>`;

  const ipEl = $("accessIpList");
  ipEl.innerHTML = "";
  for (const r of data.ip_rules) {
    const div = document.createElement("div");
    div.className = "security-record";
    div.innerHTML = `
      <div class="security-record-main">
        <div>
          <strong>${escapeHtml(r.cidr)}</strong>
          <span>${escapeHtml(r.name)} • ${escapeHtml(r.description || "No description")}</span>
        </div>
        <b class="security-badge ${r.enabled ? "" : "off"}">${r.enabled ? "Enabled" : "Disabled"}</b>
      </div>
      <div class="security-row-actions">
        <button class="ghost" type="button" data-access-action="edit-ip" data-access-id="${escapeHtml(r.id)}">Edit</button>
        <button class="ghost danger" type="button" data-access-action="delete-ip" data-access-id="${escapeHtml(r.id)}">Delete</button>
      </div>
    `;
    ipEl.appendChild(div);
  }
  if (!data.ip_rules.length) ipEl.innerHTML = `<div class="security-empty">No trusted IP ranges yet. Add your admin workstation before applying the firewall.</div>`;

  const e = data.last_enforcement || {};
  $("accessEnforcement").innerHTML = `
    <div class="security-record">
      <div class="security-record-main">
        <div>
          <strong>${escapeHtml(e.status || "NEVER_APPLIED")}</strong>
          <span>Protected ports: ${escapeHtml((e.protected_ports || data.protected_ports).join(", "))}</span>
          <small>${escapeHtml(e.applied_at ? `Applied at ${e.applied_at} by ${e.applied_by}` : "Firewall policy has not been applied from the portal yet.")}</small>
        </div>
        <b class="security-badge ${e.status === "APPLIED" ? "" : "off"}">${escapeHtml(e.status || "PENDING")}</b>
      </div>
    </div>
  `;
}

async function loadAccessControl() {
  const data = await api("/api/access-control");
  renderAccessControl(data);
}

async function loadPublishJobs() {
  const jobs = await api("/api/publish/jobs");
  $("publishJobCount").textContent = jobs.length;
  $("publishRunning").textContent = jobs.filter(j => ["QUEUED", "RUNNING"].includes(j.status)).length;
  $("publishSuccess").textContent = jobs.filter(j => j.status === "SUCCESS").length;
  $("publishFailed").textContent = jobs.filter(j => j.status === "FAILED").length;

  const el = $("publishJobsList");
  el.innerHTML = "";
  for (const j of jobs) {
    const div = document.createElement("div");
    div.className = "job";
    div.innerHTML = `
      <div style="display:flex;justify-content:space-between;gap:12px;align-items:center">
        <div>
          <strong>${escapeHtml(j.artifact_type)} • ${escapeHtml(j.source)}</strong>
          <span>${escapeHtml(j.repository)} → ${escapeHtml(j.target || "")}</span>
          ${j.file_sha256 ? `<small>SHA256 ${escapeHtml(j.file_sha256)}</small>` : ""}
        </div>
        <span class="badge ${escapeHtml(j.status)}">${escapeHtml(j.status)}</span>
      </div>
      <div class="row-actions">
        <button class="ghost" type="button" data-publish-action="view-logs" data-publish-id="${escapeHtml(j.id)}">View Logs</button>
      </div>
    `;
    el.appendChild(div);
  }
  if (!jobs.length) el.innerHTML = `<div class="muted">No publish jobs yet.</div>`;
}

async function viewPublishLogs(jobId) {
  state.selectedPublishJob = jobId;
  $("publishLogs").textContent = "Loading logs...";
  try {
    const logs = await api(`/api/publish/jobs/${jobId}/logs`);
    $("publishLogs").textContent = logs.map(l => `[${l.ts}] [${l.level}] ${l.message}`).join("\n") || "No logs yet.";
  } catch (err) {
    $("publishLogs").textContent = `FAILED: ${err.message}`;
    toast("Failed to load publish logs");
  }
}

function accessGroupPayload() {
  const permissions = checkedValues("accessPermission");
  if (!permissions.length) throw new Error("Select at least one permission.");
  return {
    name: $("accessGroupName").value,
    description: $("accessGroupDesc").value,
    permissions,
  };
}

function accessPrincipalPayload() {
  const groupIds = checkedValues("accessPrincipalGroup");
  if ($("accessPrincipalEnabled").checked && !groupIds.length) {
    throw new Error("Assign at least one group before enabling this account.");
  }
  return {
    username: $("accessPrincipalUsername").value,
    display_name: $("accessPrincipalDisplay").value,
    email: $("accessPrincipalEmail").value,
    password: $("accessPrincipalPassword").value,
    principal_type: $("accessPrincipalType").value,
    enabled: $("accessPrincipalEnabled").checked,
    group_ids: groupIds,
  };
}

function accessIpPayload() {
  return {
    name: $("accessIpName").value,
    cidr: $("accessIpCidr").value,
    description: $("accessIpDesc").value,
    enabled: $("accessIpEnabled").checked,
  };
}

function editAccessGroup(id) {
  const group = state.accessControl.groups.find(x => x.id === id);
  if (!group) return;
  $("accessGroupId").value = group.id;
  $("accessGroupName").value = group.name;
  $("accessGroupDesc").value = group.description || "";
  document.querySelectorAll('input[name="accessPermission"]').forEach(x => {
    x.checked = (group.permissions || []).includes(x.value);
  });
  focusSecurityForm("accessGroupForm", `Editing group: ${group.name}`);
}

function editAccessPrincipal(id) {
  const principal = state.accessControl.principals.find(x => x.id === id);
  if (!principal) return;
  $("accessPrincipalId").value = principal.id;
  $("accessPrincipalUsername").value = principal.username;
  $("accessPrincipalUsername").disabled = true;
  $("accessPrincipalDisplay").value = principal.display_name || "";
  $("accessPrincipalEmail").value = principal.email || "";
  $("accessPrincipalPassword").value = "";
  $("accessPrincipalType").value = principal.principal_type || "service";
  $("accessPrincipalEnabled").checked = Boolean(principal.enabled);
  document.querySelectorAll('input[name="accessPrincipalGroup"]').forEach(x => {
    x.checked = (principal.group_ids || []).includes(x.value);
  });
  focusSecurityForm("accessPrincipalForm", `Editing account: ${principal.username}`);
}

function editAccessIpRule(id) {
  const rule = state.accessControl.ip_rules.find(x => x.id === id);
  if (!rule) return;
  $("accessIpId").value = rule.id;
  $("accessIpName").value = rule.name;
  $("accessIpCidr").value = rule.cidr;
  $("accessIpDesc").value = rule.description || "";
  $("accessIpEnabled").checked = Boolean(rule.enabled);
  focusSecurityForm("accessIpForm", `Editing IP rule: ${rule.cidr}`);
}

async function deleteServer(id) {
  if (!confirm("Delete this saved target server?")) return;
  try {
    await api(`/api/servers/${id}`, { method: "DELETE" });
    toast("Server deleted");
    await loadServers();
  } catch (err) {
    toast(`Failed to delete server: ${err.message}`);
  }
}

$("serversList").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-server-action]");
  if (!btn) return;
  e.preventDefault();

  if (btn.dataset.serverAction === "delete") {
    await deleteServer(btn.dataset.serverId);
  }
});

$("jobsList").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-job-action]");
  if (!btn) return;
  e.preventDefault();

  if (btn.dataset.jobAction === "view-logs") {
    await viewLogs(btn.dataset.jobId);
  }
});

$("publishJobsList").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-publish-action]");
  if (!btn) return;
  e.preventDefault();

  if (btn.dataset.publishAction === "view-logs") {
    await viewPublishLogs(btn.dataset.publishId);
  }
});

async function deleteAccessGroup(id) {
  if (!confirm("Delete this access group? Nexus role deletion will be attempted too.")) return;
  setResult("accessActionResult", "Deleting access group...");
  setSecurityBusy(true);
  try {
    const res = await api(`/api/access-control/groups/${id}`, { method: "DELETE" });
    renderAccessControl(res.access);
    resetAccessGroupForm();
    setResult("accessActionResult", "Access group deleted.", "OK");
    toast("Access group deleted");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
}

async function deleteAccessPrincipal(id) {
  if (!confirm("Delete this trusted account? The Nexus user will be removed from Nexus and the portal policy.")) return;
  setResult("accessActionResult", "Deleting trusted account...");
  setSecurityBusy(true);
  try {
    const res = await api(`/api/access-control/principals/${id}`, { method: "DELETE" });
    renderAccessControl(res.access);
    resetAccessPrincipalForm();
    setResult("accessActionResult", "Trusted account deleted.", "OK");
    toast("Trusted account deleted");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
}

async function deleteAccessIpRule(id) {
  if (!confirm("Delete this trusted IP rule? Re-apply the firewall afterward for network enforcement.")) return;
  setResult("accessActionResult", "Deleting trusted IP rule...");
  setSecurityBusy(true);
  try {
    const res = await api(`/api/access-control/ip-rules/${id}`, { method: "DELETE" });
    renderAccessControl(res.access);
    resetAccessIpForm();
    setResult("accessActionResult", "Trusted IP rule deleted. Re-apply firewall for enforcement.", "OK");
    toast("IP rule deleted");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
}

$("security").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-access-action]");
  if (!btn) return;
  e.preventDefault();

  const id = btn.dataset.accessId;
  switch (btn.dataset.accessAction) {
    case "edit-group":
      editAccessGroup(id);
      break;
    case "delete-group":
      await deleteAccessGroup(id);
      break;
    case "edit-principal":
      editAccessPrincipal(id);
      break;
    case "delete-principal":
      await deleteAccessPrincipal(id);
      break;
    case "edit-ip":
      editAccessIpRule(id);
      break;
    case "delete-ip":
      await deleteAccessIpRule(id);
      break;
  }
});

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
  try {
    await api("/api/logout", { method: "POST", body: "{}" });
  } finally {
    location.reload();
  }
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
$("refreshPublishBtn").addEventListener("click", loadPublishJobs);

$("resetGroupFormBtn").addEventListener("click", resetAccessGroupForm);
$("resetPrincipalFormBtn").addEventListener("click", resetAccessPrincipalForm);
$("resetIpFormBtn").addEventListener("click", resetAccessIpForm);

$("accessGroupForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setResult("accessActionResult", "Saving access group...");
  try {
    const id = $("accessGroupId").value;
    const payload = accessGroupPayload();
    setSecurityBusy(true);
    const res = await api(id ? `/api/access-control/groups/${id}` : "/api/access-control/groups", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    renderAccessControl(res.access);
    resetAccessGroupForm();
    setResult("accessActionResult", "Access group saved and synced to Nexus.", "OK");
    toast("Access group saved");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("accessPrincipalForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setResult("accessActionResult", "Saving trusted account...");
  setResult("accessPasswordResult", "");
  try {
    const id = $("accessPrincipalId").value;
    const payload = accessPrincipalPayload();
    setSecurityBusy(true);
    const res = await api(id ? `/api/access-control/principals/${id}` : "/api/access-control/principals", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    renderAccessControl(res.access);
    resetAccessPrincipalForm();
    if (res.sync?.generated_password) {
      setResult("accessPasswordResult", `Generated password for ${res.sync.user_id}: ${res.sync.generated_password}`, "OK");
    }
    setResult("accessActionResult", "Trusted account saved and synced to Nexus.", "OK");
    toast("Trusted account saved");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("accessIpForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  setResult("accessActionResult", "Saving trusted IP rule...");
  try {
    const id = $("accessIpId").value;
    const payload = accessIpPayload();
    setSecurityBusy(true);
    const res = await api(id ? `/api/access-control/ip-rules/${id}` : "/api/access-control/ip-rules", {
      method: id ? "PUT" : "POST",
      body: JSON.stringify(payload),
    });
    renderAccessControl(res.access);
    resetAccessIpForm();
    setResult("accessActionResult", "Trusted IP rule saved. Re-apply firewall for enforcement.", "OK");
    toast("Trusted IP rule saved");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("syncNexusAccessBtn").addEventListener("click", async () => {
  setResult("accessActionResult", "Syncing Nexus roles, users, and anonymous access policy...");
  setSecurityBusy(true);
  try {
    const res = await api("/api/access-control/sync-nexus", { method: "POST", body: "{}" });
    renderAccessControl(res.access);
    setResult("accessActionResult", `Nexus sync complete. Actions: ${res.results.length}`, "OK");
    toast("Nexus access synced");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("previewFirewallBtn").addEventListener("click", async () => {
  setSecurityBusy(true);
  try {
    const res = await api("/api/access-control/firewall-script");
    $("firewallPreview").textContent = res.script;
    setResult("accessActionResult", "Firewall preview refreshed.", "OK");
  } catch (err) {
    $("firewallPreview").textContent = `FAILED: ${err.message}`;
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("applyFirewallBtn").addEventListener("click", async () => {
  if (!confirm("Apply host firewall rules for Nexus registry ports? Make sure your current IP is trusted first.")) return;
  setResult("accessActionResult", "Applying host firewall policy...");
  setSecurityBusy(true);
  try {
    const res = await api("/api/access-control/apply-firewall", { method: "POST", body: "{}" });
    renderAccessControl(res.access);
    $("firewallPreview").textContent = res.result.output || "Firewall policy applied.";
    setResult("accessActionResult", "Firewall policy applied to protected Nexus ports.", "OK");
    toast("Firewall policy applied");
  } catch (err) {
    setResult("accessActionResult", `FAILED: ${err.message}`, "FAIL");
  } finally {
    setSecurityBusy(false);
  }
});

$("publishDockerForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("publishDockerResult").textContent = "Creating Docker publish job...";
  try {
    const res = await api("/api/publish/docker", {
      method: "POST",
      body: JSON.stringify({
        source_image: $("publishDockerSource").value,
        target_image: $("publishDockerTarget").value,
        repository: $("publishDockerRepo").value || "docker-hosted",
      }),
    });
    $("publishDockerResult").textContent = `Publish job created: ${res.id}`;
    toast("Docker publish started");
    await loadPublishJobs();
    viewPublishLogs(res.id);
  } catch (err) {
    $("publishDockerResult").textContent = `FAILED: ${err.message}`;
  }
});

$("publishDockerArchiveForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const file = $("publishDockerArchiveFile").files[0];
  if (!file) return;
  $("publishDockerArchiveResult").textContent = "Uploading Docker archive...";
  const form = new FormData();
  form.append("target_image", $("publishDockerArchiveTarget").value);
  form.append("repository", $("publishDockerArchiveRepo").value || "docker-hosted");
  form.append("file", file);
  try {
    const res = await api("/api/publish/docker-archive", { method: "POST", body: form });
    $("publishDockerArchiveResult").textContent = `Publish job created: ${res.id}`;
    $("publishDockerArchiveFile").value = "";
    toast("Docker archive publish started");
    await loadPublishJobs();
    viewPublishLogs(res.id);
  } catch (err) {
    $("publishDockerArchiveResult").textContent = `FAILED: ${err.message}`;
  }
});

$("publishPythonForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("publishPythonResult").textContent = "Creating Python package fetch job...";
  try {
    const res = await api("/api/publish/python-fetch", {
      method: "POST",
      body: JSON.stringify({
        package_name: $("publishPythonName").value,
        package_version: $("publishPythonVersion").value,
        python_version: $("publishPythonRuntime").value,
        repository: $("publishPythonRepo").value || "pypi-hosted",
        include_dependencies: $("publishPythonDeps").checked,
      }),
    });
    $("publishPythonResult").textContent = `Publish job created: ${res.id}`;
    toast("Python fetch started");
    await loadPublishJobs();
    viewPublishLogs(res.id);
  } catch (err) {
    $("publishPythonResult").textContent = `FAILED: ${err.message}`;
  }
});

$("publishDebianForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("publishDebianResult").textContent = "Creating Debian package fetch job...";
  try {
    const res = await api("/api/publish/debian-fetch", {
      method: "POST",
      body: JSON.stringify({
        package_name: $("publishDebianName").value,
        package_version: $("publishDebianVersion").value,
        target_release: $("publishDebianRelease").value,
        repository: $("publishDebianRepo").value || "apt-internal-hosted",
        include_dependencies: $("publishDebianDeps").checked,
      }),
    });
    $("publishDebianResult").textContent = `Publish job created: ${res.id}`;
    toast("Debian fetch started");
    await loadPublishJobs();
    viewPublishLogs(res.id);
  } catch (err) {
    $("publishDebianResult").textContent = `FAILED: ${err.message}`;
  }
});

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
    await loadPublishJobs().catch(() => {});
    if (state.selectedJob) await viewLogs(state.selectedJob).catch(() => {});
    if (state.selectedPublishJob) await viewPublishLogs(state.selectedPublishJob).catch(() => {});
  }
}, 3500);

checkSession();
