#!/usr/bin/env python3
"""Live bot dashboard — trades, PnL, MTM, position (15s auto-refresh).

Usage:
    python3 bot_dashboard_server.py
    open http://127.0.0.1:8765

Public VM: set BOT_DASHBOARD_TOKEN in bot_secrets.env, then visit the URL and
log in once — password is stored in an HttpOnly cookie (not in the address bar).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from bot_dashboard_data import REFRESH_SEC, collect_status
from bot_secrets import load_secrets

ROOT = os.path.dirname(os.path.abspath(__file__))
load_secrets()

PORT = int(os.environ.get("BOT_DASHBOARD_PORT", "8765"))
PUBLIC = os.environ.get("BOT_DASHBOARD_PUBLIC", "0") == "1"
DASHBOARD_TOKEN = os.environ.get("BOT_DASHBOARD_TOKEN", "").strip()
BIND = os.environ.get(
    "BOT_DASHBOARD_BIND",
    "0.0.0.0" if PUBLIC else "127.0.0.1",
)
ENABLE_CANVAS = os.environ.get("BOT_DASHBOARD_CANVAS", "0") == "1"
SESSION_COOKIE = "bot_dashboard_session"
SESSION_MAX_AGE = int(os.environ.get("BOT_DASHBOARD_SESSION_DAYS", "7")) * 86400

if PUBLIC and not DASHBOARD_TOKEN:
    sys.exit(
        "BOT_DASHBOARD_PUBLIC=1 requires BOT_DASHBOARD_TOKEN in bot_secrets.env"
    )


def _session_value() -> str:
    """Signed session id derived from dashboard token (not the raw password)."""
    return hmac.new(
        DASHBOARD_TOKEN.encode(),
        b"bot-dashboard-session-v1",
        hashlib.sha256,
    ).hexdigest()


def _canvas_refresh_loop() -> None:
    from bot_dashboard_canvas import write_canvas

    while True:
        try:
            path = write_canvas()
            print(f"Canvas refreshed → {path}")
        except Exception as exc:
            print(f"Canvas refresh error: {exc}")
        time.sleep(REFRESH_SEC)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dashboard login</title>
<style>
  :root { --bg:#0f1117; --card:#1a1d27; --text:#e8eaed; --muted:#9aa0a6; --border:#2a2f3a; --green:#3dd68c; --red:#f47174; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
    font-family:ui-sans-serif,system-ui,sans-serif; background:var(--bg); color:var(--text); }
  .box { width:min(400px,92vw); padding:28px; background:var(--card); border:1px solid var(--border); border-radius:12px; }
  h1 { margin:0 0 6px; font-size:1.25rem; }
  p { color:var(--muted); font-size:0.9rem; margin:0 0 20px; }
  label { display:block; font-size:0.8rem; color:var(--muted); margin-bottom:6px; }
  input { width:100%; padding:10px 12px; border-radius:8px; border:1px solid var(--border);
    background:#0f1117; color:var(--text); font-size:1rem; box-sizing:border-box; }
  button { margin-top:16px; width:100%; padding:11px; border:none; border-radius:8px;
    background:var(--green); color:#0f1117; font-weight:600; font-size:1rem; cursor:pointer; }
  .err { color:var(--red); font-size:0.85rem; margin-top:12px; }
</style></head>
<body><div class="box">
  <h1>Bot dashboard</h1>
  <p>Enter your dashboard password to continue.</p>
  <form method="post" action="/login">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" required autofocus>
    <button type="submit">Sign in</button>
  </form>
  {error}
</div></body></html>"""


HTML_PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Bot Live Dashboard</title>
  <style>
    :root {{
      --bg: #0f1117;
      --card: #1a1d27;
      --text: #e8eaed;
      --muted: #9aa0a6;
      --green: #3dd68c;
      --red: #f47174;
      --amber: #f5c542;
      --border: #2a2f3a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.45;
    }}
    .wrap {{ max-width: 1200px; margin: 0 auto; padding: 24px 20px 48px; }}
    h1 {{ margin: 0 0 6px; font-size: 1.6rem; font-weight: 600; }}
    .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 20px; }}
    .meta a {{ color: var(--muted); }}
    .grid {{ display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 14px 16px;
    }}
    .label {{ color: var(--muted); font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; }}
    .value {{ font-size: 1.35rem; font-weight: 600; margin-top: 4px; }}
    .sub {{ font-size: 0.8rem; color: var(--muted); margin-top: 4px; }}
    .pos {{ font-size: 1rem; font-weight: 500; }}
    .green {{ color: var(--green); }}
    .red {{ color: var(--red); }}
    .amber {{ color: var(--amber); }}
    .pill {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      font-size: 0.75rem;
      font-weight: 600;
      margin-left: 4px;
    }}
    .pill.on {{ background: #163d2a; color: var(--green); }}
    .pill.off {{ background: #3d1a1a; color: var(--red); }}
    .pill.wait {{ background: #3d3518; color: var(--amber); }}
    h2 {{ font-size: 1rem; margin: 28px 0 10px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
    th {{ color: var(--muted); font-weight: 500; font-size: 0.72rem; text-transform: uppercase; }}
    pre {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px;
      font-size: 0.72rem;
      overflow-x: auto;
      max-height: 220px;
      color: #c9cdd3;
    }}
    .err {{ color: var(--red); margin-top: 12px; }}
    .chart {{ display: flex; align-items: flex-end; gap: 10px; height: 120px; padding-top: 8px; }}
    .bar-wrap {{ flex: 1; text-align: center; min-width: 48px; }}
    .bar {{
      margin: 0 auto;
      width: 36px;
      border-radius: 4px 4px 0 0;
      min-height: 4px;
    }}
    .bar-pos {{ background: var(--green); }}
    .bar-neg {{ background: var(--red); }}
    .bar-label {{ font-size: 0.7rem; color: var(--muted); margin-top: 6px; }}
    .bar-val {{ font-size: 0.72rem; margin-bottom: 4px; }}
    .scroll {{ overflow-x: auto; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Bot live dashboard</h1>
    <p class="meta">
      Auto-refresh every {REFRESH_SEC}s ·
      <span id="updated">Loading…</span>
      <span id="proc-pill" class="pill off">—</span>
      <span id="stream-pill" class="pill wait">—</span>
      · <a href="/logout">Sign out</a>
    </p>
    <div class="grid" id="stats"></div>
    <h2>Position & levels</h2>
    <div class="card" id="position-card">Loading…</div>
    <h2>Daily PnL (last sessions)</h2>
    <div class="card" id="chart-card">No closed trades yet</div>
    <h2>Today's exits</h2>
    <div class="card scroll"><table id="today-table"><thead><tr>
      <th>Exit</th><th>Entry</th><th>Type</th><th>Symbol</th>
      <th>Entry ₹</th><th>Exit ₹</th><th>PnL</th><th>Reason</th>
    </tr></thead><tbody></tbody></table></div>
    <h2>Recent trades</h2>
    <div class="card scroll"><table id="recent-table"><thead><tr>
      <th>Exit</th><th>Entry</th><th>Type</th><th>Symbol</th>
      <th>Entry ₹</th><th>Exit ₹</th><th>PnL</th><th>Reason</th>
    </tr></thead><tbody></tbody></table></div>
    <h2>Log tail</h2>
    <pre id="log-tail"></pre>
    <p class="err" id="error" hidden></p>
  </div>
  <script>
    const REFRESH_MS = {REFRESH_SEC * 1000};
    const FETCH_OPTS = {{ credentials: 'same-origin' }};
    function pnlClass(fmt) {{
      if (!fmt || fmt === '—') return '';
      return fmt.startsWith('+') ? 'green' : 'red';
    }}
    function streamPill(stream) {{
      const map = {{
        live: ['Live ticks', 'on'],
        after_hours: ['After hours', 'wait'],
        weekend: ['Weekend', 'wait'],
        connecting: ['Connecting…', 'wait'],
        stopped: ['Stopped', 'off'],
      }};
      return map[stream] || ['—', 'off'];
    }}
    function fillTable(id, rows) {{
      const tbody = document.querySelector(`#${{id}} tbody`);
      tbody.innerHTML = '';
      if (!rows.length) {{
        tbody.innerHTML = '<tr><td colspan="8" style="color:#9aa0a6">No trades</td></tr>';
        return;
      }}
      for (const r of rows) {{
        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td>${{r.time}}</td><td>${{r.entry_time}}</td><td>${{r.type}}</td><td>${{r.symbol}}</td>
          <td>${{r.entry_fmt}}</td><td>${{r.exit_fmt}}</td>
          <td class="${{pnlClass(r.pnl_fmt)}}">${{r.pnl_fmt}}</td><td>${{r.reason}}</td>`;
        tbody.appendChild(tr);
      }}
    }}
    function renderChart(labels, values) {{
      const el = document.getElementById('chart-card');
      if (!labels.length) {{
        el.textContent = 'No closed trades yet';
        return;
      }}
      const max = Math.max(...values.map(v => Math.abs(v)), 1);
      let html = '<div class="chart">';
      for (let i = 0; i < labels.length; i++) {{
        const v = values[i];
        const h = Math.max(4, Math.round((Math.abs(v) / max) * 90));
        const cls = v >= 0 ? 'bar-pos' : 'bar-neg';
        const sign = v >= 0 ? '+' : '-';
        html += `<div class="bar-wrap">
          <div class="bar-val ${{pnlClass(sign + '₹')}}">${{sign}}₹${{Math.abs(v).toLocaleString('en-IN', {{maximumFractionDigits:0}})}}</div>
          <div class="bar ${{cls}}" style="height:${{h}}px"></div>
          <div class="bar-label">${{labels[i]}}</div></div>`;
      }}
      html += '</div>';
      el.innerHTML = html;
    }}
    function render(data) {{
      document.getElementById('updated').textContent = 'Updated ' + data.updated_at;
      const proc = document.getElementById('proc-pill');
      proc.textContent = data.bot_running ? 'Process running' : 'Process stopped';
      proc.className = 'pill ' + (data.bot_running ? 'on' : 'off');
      const [slabel, scls] = streamPill(data.stream_status);
      const stream = document.getElementById('stream-pill');
      stream.textContent = slabel;
      stream.className = 'pill ' + scls;
      const p = data.position, lv = data.levels, pnl = data.pnl;
      const mtmBlock = p.type === 'FLAT'
        ? '<div class="card"><div class="label">MTM</div><div class="value">—</div><div class="sub">Flat</div></div>'
        : `<div class="card"><div class="label">MTM (open)</div>
            <div class="value ${{pnlClass(pnl.mtm_fmt)}}">${{pnl.mtm_fmt}}</div>
            <div class="sub">${{p.ltp_fmt}} LTP · qty ${{p.qty ?? '—'}}</div></div>`;
      document.getElementById('stats').innerHTML = `
        <div class="card"><div class="label">Today total</div>
          <div class="value ${{pnlClass(pnl.today_total_fmt)}}">${{pnl.today_total_fmt}}</div>
          <div class="sub">Realized + MTM</div></div>
        ${{mtmBlock}}
        <div class="card"><div class="label">Today realized</div>
          <div class="value ${{pnlClass(pnl.today_realized_fmt)}}">${{pnl.today_realized_fmt}}</div>
          <div class="sub">${{pnl.today_exit_count}} exit(s)</div></div>
        <div class="card"><div class="label">Cumulative</div>
          <div class="value ${{pnlClass(pnl.cumulative_fmt)}}">${{pnl.cumulative_fmt}}</div></div>
        <div class="card"><div class="label">SPH</div><div class="value">${{lv.sph ?? '—'}}</div></div>
        <div class="card"><div class="label">SPL</div><div class="value">${{lv.spl ?? '—'}}</div></div>`;
      const posLine = p.type === 'FLAT'
        ? 'FLAT — no open position'
        : `${{p.type}} ${{p.symbol}} · entry ${{p.entry_price_fmt}} → LTP ${{p.ltp_fmt}} · qty ${{p.qty ?? '—'}}`;
      document.getElementById('position-card').innerHTML = `
        <div class="pos">${{posLine}}</div>
        <div class="meta" style="margin-top:8px">
          Entry time: ${{p.entry_time || '—'}} · Spot @ entry: ${{p.spot_at_entry ?? '—'}}
          · Expiry: ${{p.expiry ?? '—'}}<br>
          SL: ${{lv.sl ?? '—'}} (${{lv.sl_label}})
          ${{lv.gap_sl_locked ? ' · Gap lock: ' + lv.gap_sl_locked + ' @ ' + lv.gap_sl_locked_value : ''}}
        </div>`;
      renderChart(data.weekly_labels || [], data.weekly_values || []);
      fillTable('today-table', data.today_trades);
      fillTable('recent-table', data.recent_trades);
      document.getElementById('log-tail').textContent = (data.log_tail || []).join('\\n');
      document.getElementById('error').hidden = true;
    }}
    async function refresh() {{
      try {{
        const res = await fetch('/api/status', FETCH_OPTS);
        if (res.status === 401) {{ location.href = '/login'; return; }}
        if (!res.ok) throw new Error('HTTP ' + res.status);
        render(await res.json());
      }} catch (e) {{
        const el = document.getElementById('error');
        el.textContent = 'Refresh failed: ' + e.message;
        el.hidden = false;
      }}
    }}
    refresh();
    setInterval(refresh, REFRESH_MS);
  </script>
</body>
</html>
"""


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def _request_path(self) -> str:
        return urlparse(self.path).path

    def _parsed_cookies(self) -> cookies.SimpleCookie:
        jar = cookies.SimpleCookie()
        raw = self.headers.get("Cookie", "")
        if raw:
            jar.load(raw)
        return jar

    def _session_from_cookie(self) -> bool:
        jar = self._parsed_cookies()
        if SESSION_COOKIE not in jar:
            return False
        return hmac.compare_digest(jar[SESSION_COOKIE].value, _session_value())

    def _request_token(self) -> str:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        token = (qs.get("token") or [""])[0]
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip() or token
        return token

    def _authorized(self) -> bool:
        if not DASHBOARD_TOKEN:
            return True
        if self._session_from_cookie():
            return True
        return self._request_token() == DASHBOARD_TOKEN

    def _cookie_secure(self) -> bool:
        if self.headers.get("X-Forwarded-Proto", "").lower() == "https":
            return True
        return False

    def _set_session_cookie(self) -> None:
        secure = self._cookie_secure()
        parts = [
            f"{SESSION_COOKIE}={_session_value()}",
            "HttpOnly",
            "Path=/",
            "SameSite=Lax",
            f"Max-Age={SESSION_MAX_AGE}",
        ]
        if secure:
            parts.append("Secure")
        self.send_header("Set-Cookie", "; ".join(parts))

    def _clear_session_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax",
        )

    def _redirect(self, location: str, *, set_session: bool = False, clear_session: bool = False) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if set_session:
            self._set_session_cookie()
        if clear_session:
            self._clear_session_cookie()
        self.end_headers()

    def _send_html(self, body: str, status: int = 200, *, set_session: bool = False) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if set_session:
            self._set_session_cookie()
        self.end_headers()
        self.wfile.write(data)

    def _send_login(self, error: str = "") -> None:
        err_html = f'<p class="err">{error}</p>' if error else ""
        self._send_html(LOGIN_PAGE.replace("{error}", err_html))

    def _read_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return {k: v[0] for k, v in parse_qs(raw).items()}

    def _establish_session_from_token(self) -> bool:
        """One-time ?token= link → cookie, then redirect to clean URL."""
        if not DASHBOARD_TOKEN:
            return False
        if self._request_token() != DASHBOARD_TOKEN:
            return False
        self._redirect("/", set_session=True)
        return True

    def do_GET(self) -> None:
        path = self._request_path()

        if path == "/logout":
            self._redirect("/login", clear_session=True)
            return

        if path == "/login":
            if self._authorized() and not self._request_token():
                self._redirect("/")
                return
            self._send_login()
            return

        # Legacy bookmark: ?token=... → session cookie → clean URL
        if path in ("/", "/index.html") and self._request_token():
            if self._establish_session_from_token():
                return

        if not self._authorized():
            if path == "/api/status":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"unauthorized"}')
                return
            self._send_login()
            return

        if path in ("/", "/index.html"):
            self._send_html(HTML_PAGE)
            return

        if path == "/api/status":
            payload = json.dumps(collect_status(), default=str).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        self.send_error(404)

    def do_POST(self) -> None:
        path = self._request_path()
        if path != "/login":
            self.send_error(404)
            return

        form = self._read_form()
        password = form.get("password", "")
        if DASHBOARD_TOKEN and hmac.compare_digest(password, DASHBOARD_TOKEN):
            self._redirect("/", set_session=True)
            return

        self._send_login("Incorrect password. Try again.")


def main() -> None:
    os.chdir(ROOT)
    if ENABLE_CANVAS:
        threading.Thread(target=_canvas_refresh_loop, daemon=True).start()
    server = HTTPServer((BIND, PORT), DashboardHandler)
    mode = "login required" if DASHBOARD_TOKEN else "open"
    print(f"Bot dashboard → http://{BIND}:{PORT}  [{mode}, refresh {REFRESH_SEC}s]")
    if DASHBOARD_TOKEN:
        print("Sign in at /login — session cookie lasts "
              f"{SESSION_MAX_AGE // 86400} days.")
    if ENABLE_CANVAS:
        print("Cursor canvas refresh enabled (BOT_DASHBOARD_CANVAS=1).")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
