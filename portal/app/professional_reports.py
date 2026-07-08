
import json
import html
import os
import sqlite3
import textwrap
from pathlib import Path
from datetime import datetime
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, FileResponse


def _db_path():
    return os.getenv("PORTAL_DB_PATH", "/workspace/data/portal/portal.db")


def _workspace():
    return Path(os.getenv("APP_WORKSPACE", "/workspace"))


def _connect():
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _cols(c, table):
    try:
        return [r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def _pick(row, keys, default=""):
    for k in keys:
        if k in row and row[k] not in [None, ""]:
            return row[k]
    return default


def _json_load(value):
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value[0] not in "{[":
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def _job_and_logs(job_id):
    c = _connect()
    try:
        job_cols = _cols(c, "jobs")
        if not job_cols:
            raise HTTPException(status_code=500, detail="jobs table not found")

        id_candidates = [x for x in ["id", "job_id", "uuid"] if x in job_cols]
        if not id_candidates:
            id_candidates = [job_cols[0]]

        job = None
        for col in id_candidates:
            job = c.execute(f"SELECT * FROM jobs WHERE {col} = ?", (job_id,)).fetchone()
            if job:
                break

        if not job:
            raise HTTPException(status_code=404, detail="job not found")

        job = dict(job)

        logs = []
        log_cols = _cols(c, "job_logs")
        if log_cols:
            order_col = "created_at" if "created_at" in log_cols else ("id" if "id" in log_cols else log_cols[0])
            if "job_id" in log_cols:
                rows = c.execute(f"SELECT * FROM job_logs WHERE job_id = ? ORDER BY {order_col} ASC", (job_id,)).fetchall()
                logs = [dict(r) for r in rows]
            elif "job" in log_cols:
                rows = c.execute(f"SELECT * FROM job_logs WHERE job = ? ORDER BY {order_col} ASC", (job_id,)).fetchall()
                logs = [dict(r) for r in rows]

        return job, logs
    finally:
        c.close()


def _log_line(row):
    if isinstance(row, str):
        return row
    ts = _pick(row, ["created_at", "timestamp", "time", "ts"], "")
    level = _pick(row, ["level", "severity"], "INFO")
    msg = _pick(row, ["message", "log", "line", "text"], "")
    if not msg:
        msg = " ".join(str(v) for v in row.values() if v not in [None, ""])
    return f"[{ts}] [{level}] {msg}".strip()


def _status_class(status):
    s = str(status or "").lower()
    if "success" in s or "finished" in s or s == "ok":
        return "success"
    if "fail" in s or "error" in s:
        return "failed"
    if "run" in s or "queue" in s or "pending" in s:
        return "running"
    return "neutral"


def _summary(job):
    return {
        "Job ID": _pick(job, ["id", "job_id", "uuid"]),
        "Status": _pick(job, ["status", "state"]),
        "Target": _pick(job, ["target_id", "server_id", "target", "server"]),
        "Created": _pick(job, ["created_at", "created", "start_time"]),
        "Finished": _pick(job, ["finished_at", "finished", "end_time", "completed_at"]),
        "Bundle SHA256": _pick(job, ["bundle_sha256", "sha256", "bundle_hash"]),
        "Bundle Path": _pick(job, ["bundle_path", "archive_path", "bundle"]),
    }


def _artifact_data(job):
    result = {}
    for k, v in job.items():
        lk = k.lower()
        if any(x in lk for x in ["artifact", "bundle", "spec", "payload", "request", "manifest"]):
            parsed = _json_load(v)
            if parsed is not None:
                result[k] = parsed
            elif v not in [None, ""]:
                result[k] = str(v)
    return result


def _build_html(job_id):
    job, logs = _job_and_logs(job_id)
    sm = _summary(job)
    arts = _artifact_data(job)
    status = sm.get("Status", "") or "UNKNOWN"
    generated = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    log_text = "\n".join(_log_line(x) for x in logs) if logs else "No logs were recorded for this job."

    cards = []
    for k, v in sm.items():
        cards.append(
            "<div class='card'><span>{}</span><strong>{}</strong></div>".format(
                html.escape(str(k)),
                html.escape(str(v if v not in [None, ""] else "-")),
            )
        )

    rows = []
    for k, v in job.items():
        parsed = _json_load(v)
        if parsed is not None:
            value = "<pre class='mini-pre'>{}</pre>".format(
                html.escape(json.dumps(parsed, indent=2, ensure_ascii=False))
            )
        else:
            value = html.escape(str(v if v is not None else ""))
        rows.append("<tr><th>{}</th><td>{}</td></tr>".format(html.escape(str(k)), value))

    if arts:
        art_sections = []
        for k, v in arts.items():
            display = json.dumps(v, indent=2, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
            art_sections.append(
                "<section class='section'><h2>{}</h2><pre class='json-block'>{}</pre></section>".format(
                    html.escape(str(k)),
                    html.escape(display),
                )
            )
        artifacts_html = "\n".join(art_sections)
    else:
        artifacts_html = "<section class='section'><h2>Artifacts</h2><p class='muted'>No structured artifact metadata was found.</p></section>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Deployment Report - {html.escape(str(job_id))}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root {{
  --bg: #070b14;
  --panel: rgba(15,23,42,.92);
  --panel2: rgba(17,24,39,.86);
  --border: rgba(148,163,184,.24);
  --text: #e5e7eb;
  --muted: #94a3b8;
  --accent: #f97316;
  --blue: #60a5fa;
  --success: #22c55e;
  --failed: #ef4444;
  --running: #38bdf8;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  background:
    radial-gradient(circle at top left, rgba(249,115,22,.16), transparent 34%),
    radial-gradient(circle at top right, rgba(96,165,250,.10), transparent 30%),
    var(--bg);
  color: var(--text);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  line-height: 1.55;
}}
.wrapper {{
  max-width: 1180px;
  margin: 0 auto;
  padding: 34px 28px 58px;
}}
.hero {{
  border: 1px solid var(--border);
  background: linear-gradient(135deg, rgba(17,24,39,.98), rgba(15,23,42,.92));
  border-radius: 28px;
  padding: 30px;
  box-shadow: 0 24px 80px rgba(0,0,0,.42);
}}
.kicker {{
  color: var(--accent);
  font-weight: 900;
  letter-spacing: .16em;
  font-size: 12px;
  text-transform: uppercase;
}}
h1 {{
  margin: 10px 0 8px;
  font-size: 34px;
  line-height: 1.12;
}}
.subtitle {{
  color: var(--muted);
  margin: 0;
}}
.status {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin-top: 18px;
  padding: 10px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: rgba(2,6,23,.80);
  font-weight: 900;
}}
.status:before {{
  content: "";
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--muted);
}}
.status.success:before {{
  background: var(--success);
  box-shadow: 0 0 20px rgba(34,197,94,.72);
}}
.status.failed:before {{
  background: var(--failed);
  box-shadow: 0 0 20px rgba(239,68,68,.72);
}}
.status.running:before {{
  background: var(--running);
  box-shadow: 0 0 20px rgba(56,189,248,.72);
}}
.grid {{
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
  margin-top: 20px;
}}
.card {{
  border: 1px solid var(--border);
  background: rgba(2,6,23,.50);
  border-radius: 18px;
  padding: 14px 16px;
  min-height: 84px;
}}
.card span {{
  display: block;
  color: var(--muted);
  font-size: 12px;
  font-weight: 800;
  text-transform: uppercase;
  letter-spacing: .06em;
}}
.card strong {{
  display: block;
  margin-top: 7px;
  font-size: 14px;
  overflow-wrap: anywhere;
}}
.section {{
  margin-top: 22px;
  border: 1px solid var(--border);
  background: var(--panel2);
  border-radius: 24px;
  padding: 22px;
  box-shadow: 0 18px 55px rgba(0,0,0,.24);
}}
.section h2 {{
  margin: 0 0 14px;
  font-size: 20px;
}}
.table-wrap {{
  overflow-x: auto;
}}
table {{
  width: 100%;
  border-collapse: collapse;
}}
th, td {{
  border-bottom: 1px solid rgba(148,163,184,.16);
  padding: 12px 10px;
  vertical-align: top;
  text-align: left;
}}
th {{
  width: 220px;
  color: #cbd5e1;
  font-size: 13px;
}}
td {{
  color: #e5e7eb;
  overflow-wrap: anywhere;
}}
.log-block,
.json-block,
.mini-pre {{
  margin: 0;
  background: #020617;
  color: #dbeafe;
  border: 1px solid rgba(148,163,184,.22);
  border-radius: 16px;
  padding: 16px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: "JetBrains Mono", "Fira Code", "Cascadia Mono", "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  font-size: 12.5px;
  line-height: 1.65;
}}
.log-block {{
  max-height: 650px;
}}
.muted {{
  color: var(--muted);
}}
.footer {{
  margin-top: 20px;
  color: var(--muted);
  font-size: 12px;
  text-align: center;
}}
@media (max-width: 900px) {{
  .grid {{ grid-template-columns: 1fr; }}
}}
@media print {{
  body {{ background: white; color: #111827; }}
  .hero, .section {{ box-shadow: none; break-inside: avoid; }}
  .log-block {{ max-height: none; }}
}}
</style>
</head>
<body>
<div class="wrapper">
  <header class="hero">
    <div class="kicker">Air-Gapped Enterprise Artifact Platform</div>
    <h1>Deployment Report</h1>
    <p class="subtitle">Professional execution summary, artifact metadata, and operational logs.</p>
    <div class="status {_status_class(status)}">{html.escape(str(status))}</div>
    <div class="grid">{''.join(cards)}</div>
  </header>

  {artifacts_html}

  <section class="section">
    <h2>Job Fields</h2>
    <div class="table-wrap">
      <table>{''.join(rows)}</table>
    </div>
  </section>

  <section class="section">
    <h2>Execution Logs</h2>
    <pre class="log-block">{html.escape(log_text)}</pre>
  </section>

  <div class="footer">Generated at {generated} · Job ID {html.escape(str(job_id))}</div>
</div>
</body>
</html>"""


def _pdf_escape(text):
    text = str(text).encode("latin-1", "replace").decode("latin-1")
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text, width):
    result = []
    for line in str(text).splitlines() or [""]:
        if not line:
            result.append("")
        else:
            result.extend(textwrap.wrap(line, width=width, replace_whitespace=False, drop_whitespace=False) or [""])
    return result


def _pdf_lines(job_id):
    job, logs = _job_and_logs(job_id)
    sm = _summary(job)
    arts = _artifact_data(job)

    pages = []

    cover = [("H1", "Air-Gapped Deployment Report"), ("P", "Professional execution report"), ("SPACE", "")]
    for k, v in sm.items():
        cover.append(("P", f"{k}: {v if v not in [None, ''] else '-'}"))
    pages.append(cover)

    fields = [("H1", "Job Fields"), ("SPACE", "")]
    for k, v in job.items():
        parsed = _json_load(v)
        value = json.dumps(parsed, indent=2, ensure_ascii=False) if parsed is not None else str(v if v is not None else "")
        fields.append(("P", f"{k}: {value}"))
    pages.append(fields)

    if arts:
        art_page = [("H1", "Artifacts"), ("SPACE", "")]
        for k, v in arts.items():
            art_page.append(("H2", str(k)))
            art_page.append(("MONO", json.dumps(v, indent=2, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)))
            art_page.append(("SPACE", ""))
        pages.append(art_page)

    log_page = [("H1", "Execution Logs"), ("SPACE", "")]
    for line in [_log_line(x) for x in logs] or ["No logs were recorded for this job."]:
        log_page.append(("MONO", line))
    pages.append(log_page)

    final_pages = []
    for page in pages:
        current = []
        used = 0
        for kind, text in page:
            if kind == "SPACE":
                wrapped = [""]
            elif kind in ["H1", "H2"]:
                wrapped = [text]
            elif kind == "MONO":
                wrapped = _wrap(text, 112)
            else:
                wrapped = _wrap(text, 98)

            for item in wrapped:
                current.append((kind, item))
                used += 2 if kind == "H1" else 1
                if used >= 34:
                    final_pages.append(current)
                    current = []
                    used = 0
        if current:
            final_pages.append(current)

    return final_pages


def _build_pdf(job_id):
    report_dir = _workspace() / "reports" / "jobs" / str(job_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = report_dir / "professional-deployment-report.pdf"

    pages = _pdf_lines(job_id)
    width, height = 842, 595
    margin_x = 46
    start_y = 548

    objects = []
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    n = len(pages)
    pages_id = 4 + (n * 2)
    catalog_id = pages_id + 1
    page_ids = []

    for idx, lines in enumerate(pages, start=1):
        content_id = 4 + ((idx - 1) * 2)
        page_id = content_id + 1
        page_ids.append(page_id)

        ops = []
        y = start_y
        ops.append(f"BT /F2 9 Tf {margin_x} {height - 28} Td (Air-Gapped Enterprise Artifact Platform | Page {idx}) Tj ET")
        ops.append(f"BT /F1 8 Tf {width - 250} 24 Td (Job {_pdf_escape(job_id)}) Tj ET")

        for kind, text in lines:
            if y < 48:
                break
            if kind == "H1":
                ops.append(f"BT /F2 20 Tf {margin_x} {y} Td ({_pdf_escape(text)}) Tj ET")
                y -= 28
            elif kind == "H2":
                ops.append(f"BT /F2 14 Tf {margin_x} {y} Td ({_pdf_escape(text)}) Tj ET")
                y -= 20
            elif kind == "MONO":
                ops.append(f"BT /F3 8.5 Tf {margin_x} {y} Td ({_pdf_escape(text)}) Tj ET")
                y -= 12
            elif kind == "SPACE":
                y -= 10
            else:
                ops.append(f"BT /F1 10.5 Tf {margin_x} {y} Td ({_pdf_escape(text)}) Tj ET")
                y -= 15

        stream = "\n".join(ops).encode("latin-1", "replace")
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")

        page_obj = (
            f"<< /Type /Page /Parent {pages_id} 0 R "
            f"/MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 1 0 R /F2 2 0 R /F3 3 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("latin-1")
        objects.append(page_obj)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("latin-1"))
    objects.append(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))

    pdf = bytearray()
    pdf.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]

    for i, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{i} 0 obj\n".encode("latin-1"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")

    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    pdf.extend(b"0000000000 65535 f \n")

    for off in offsets[1:]:
        pdf.extend(f"{off:010d} 00000 n \n".encode("latin-1"))

    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    )

    pdf_path.write_bytes(bytes(pdf))
    return pdf_path


def install_professional_reports(app):
    app.router.routes = [
        r for r in app.router.routes
        if getattr(r, "path", None) not in [
            "/api/jobs/{job_id}/report/html",
            "/api/jobs/{job_id}/report/pdf",
        ]
    ]

    @app.get("/api/jobs/{job_id}/report/html", response_class=HTMLResponse)
    def professional_report_html(job_id: str):
        doc = _build_html(job_id)
        report_dir = _workspace() / "reports" / "jobs" / str(job_id)
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / "professional-deployment-report.html").write_text(doc, encoding="utf-8")
        return HTMLResponse(content=doc)

    @app.get("/api/jobs/{job_id}/report/pdf")
    def professional_report_pdf(job_id: str):
        pdf = _build_pdf(job_id)
        return FileResponse(
            path=str(pdf),
            media_type="application/pdf",
            filename=f"{job_id}-professional-deployment-report.pdf",
        )
