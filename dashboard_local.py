#!/usr/bin/env python3
"""
📊 DEGEN BOT DASHBOARD
=======================
Lokalny web dashboard do monitorowania trading bota.
Otwiera się w przeglądarce i odświeża co 10 sekund.

Uruchomienie:
    python dashboard.py

Otworzy http://localhost:8420 w przeglądarce.
"""

import http.server
import json
import os
import webbrowser
import threading
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from db import get_scan_history, get_scan_details, get_latest_scan

PORT = 8420

HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🤖 Degen Bot Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0A0A0F; color: #fff;
    font-family: 'JetBrains Mono', monospace;
    min-height: 100vh; padding: 20px;
  }
  .header {
    text-align: center; padding: 20px 0;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 24px;
  }
  .header h1 {
    font-size: 24px; font-weight: 900;
    background: linear-gradient(135deg, #00FF88, #00AAFF);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .sub { color: rgba(255,255,255,0.3); font-size: 12px; margin-top: 4px; }
  .header .live { color: #00FF88; font-size: 11px; margin-top: 8px; }
  .header .live::before { content: ''; display: inline-block; width: 8px; height: 8px;
    background: #00FF88; border-radius: 50%; margin-right: 6px;
    animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100% { opacity:1; } 50% { opacity:0.3; } }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .stat {
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; padding: 16px; text-align: center;
  }
  .stat .label { font-size: 11px; color: rgba(255,255,255,0.4); margin-bottom: 6px; }
  .stat .value { font-size: 24px; font-weight: 800; }
  .stat .value.green { color: #00FF88; }
  .stat .value.red { color: #FF4444; }
  .stat .value.yellow { color: #FFD600; }
  .stat .value.blue { color: #00AAFF; }

  h2 { font-size: 16px; font-weight: 700; margin: 24px 0 12px;
    display: flex; align-items: center; gap: 8px; }

  .position {
    background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px; padding: 16px; margin-bottom: 8px;
    transition: all 0.2s;
  }
  .position:hover { border-color: rgba(255,255,255,0.12); }
  .pos-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .pos-name { font-size: 15px; font-weight: 800; }
  .pos-score { font-size: 12px; padding: 3px 10px; border-radius: 6px; font-weight: 700; }
  .pos-details { font-size: 12px; color: rgba(255,255,255,0.4); line-height: 1.8; }
  .pos-details span { color: rgba(255,255,255,0.6); }

  .badge { display: inline-block; padding: 2px 8px; border-radius: 5px; font-size: 10px; font-weight: 700; margin-left: 6px; }
  .badge-active { background: rgba(0,255,136,0.1); color: #00FF88; border: 1px solid rgba(0,255,136,0.2); }
  .badge-moonbag { background: rgba(255,214,0,0.1); color: #FFD600; border: 1px solid rgba(255,214,0,0.2); }
  .badge-closed { background: rgba(255,255,255,0.05); color: rgba(255,255,255,0.3); border: 1px solid rgba(255,255,255,0.08); }
  .badge-stopped { background: rgba(255,68,68,0.1); color: #FF4444; border: 1px solid rgba(255,68,68,0.2); }

  .trade {
    display: flex; gap: 12px; align-items: center;
    padding: 10px 14px; font-size: 12px;
    border-bottom: 1px solid rgba(255,255,255,0.03);
  }
  .trade:last-child { border: none; }
  .trade-icon { font-size: 16px; }
  .trade-info { flex: 1; color: rgba(255,255,255,0.5); }
  .trade-time { color: rgba(255,255,255,0.2); font-size: 11px; }

  .empty { text-align: center; padding: 40px; color: rgba(255,255,255,0.2); }
  .empty .big { font-size: 36px; margin-bottom: 8px; }

  .ca { font-size: 10px; color: rgba(255,255,255,0.2); word-break: break-all; margin-top: 4px; }
  a { color: #00AAFF; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
<div class="header">
  <h1>🤖 DEGEN BOT DASHBOARD</h1>
  <div class="sub">Solana Liftoff Sniper + Auto Trader</div>
  <div class="live" id="status">Auto-refresh co 10s</div>
  <div style="margin-top:10px;font-size:13px;"><a href="/history">📜 Scan History</a></div>
</div>

<div class="grid" id="stats"></div>
<div id="positions"></div>
<div id="trades"></div>

<script>
async function loadData() {
  let positions = [];
  let trades = [];
  try { positions = await (await fetch('/api/positions')).json(); } catch {}
  try { trades = await (await fetch('/api/trades')).json(); } catch {}
  return { positions, trades };
}

function formatSOL(v) { return v ? v.toFixed(4) : '0.0000'; }
function formatTime(iso) {
  if (!iso) return '?';
  const d = new Date(iso);
  return d.toLocaleString('pl-PL', { day:'2-digit', month:'2-digit', hour:'2-digit', minute:'2-digit' });
}
function ageHours(iso) {
  if (!iso) return '?';
  const h = (Date.now() - new Date(iso).getTime()) / 3600000;
  return h < 1 ? Math.round(h*60)+'m' : Math.round(h)+'h';
}

function render({ positions, trades }) {
  const active = positions.filter(p => (p.status==='open'||p.status==='partial') && (p.cascade_level||0)===0);
  const moonbags = positions.filter(p => (p.status==='open'||p.status==='partial') && (p.cascade_level||0)>=1);
  const closed = positions.filter(p => p.status==='closed'||p.status==='stopped');

  const totalInvested = positions.reduce((s,p) => s + (p.buy_amount_sol||0), 0);
  const totalReturned = positions.reduce((s,p) => s + (p.total_sold_sol||0), 0);
  const pnl = totalReturned - totalInvested;
  const atRisk = active.reduce((s,p) => s + (p.buy_amount_sol||0), 0);

  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="label">Active (at risk)</div><div class="value green">${active.length}</div></div>
    <div class="stat"><div class="label">Moonbags (free)</div><div class="value yellow">${moonbags.length}</div></div>
    <div class="stat"><div class="label">Closed</div><div class="value">${closed.length}</div></div>
    <div class="stat"><div class="label">SOL at risk</div><div class="value blue">${formatSOL(atRisk)}</div></div>
    <div class="stat"><div class="label">Total invested</div><div class="value">${formatSOL(totalInvested)}</div></div>
    <div class="stat"><div class="label">Total returned</div><div class="value green">${formatSOL(totalReturned)}</div></div>
    <div class="stat"><div class="label">PnL</div><div class="value ${pnl>=0?'green':'red'}">${pnl>=0?'+':''}${formatSOL(pnl)} SOL</div></div>
    <div class="stat"><div class="label">Total trades</div><div class="value">${trades.length}</div></div>
  `;

  let posHTML = '';

  if (active.length > 0) {
    posHTML += '<h2>🟢 Active Positions (investment at risk)</h2>';
    active.forEach(p => { posHTML += renderPosition(p, 'active'); });
  }

  if (moonbags.length > 0) {
    posHTML += '<h2>🌙 Moonbags (free money riding)</h2>';
    moonbags.forEach(p => { posHTML += renderPosition(p, 'moonbag'); });
  }

  if (closed.length > 0) {
    posHTML += '<h2>📦 Closed</h2>';
    closed.forEach(p => { posHTML += renderPosition(p, 'closed'); });
  }

  if (positions.length === 0) {
    posHTML = '<div class="empty"><div class="big">🎯</div>Brak pozycji — bot czeka na sygnały z Liftoff Sniper</div>';
  }

  document.getElementById('positions').innerHTML = posHTML;

  // Trades
  let trHTML = '<h2>📜 Ostatnie transakcje</h2>';
  const recent = trades.slice(-15).reverse();
  if (recent.length === 0) {
    trHTML += '<div class="empty">Brak transakcji</div>';
  } else {
    recent.forEach(t => {
      const icons = { buy:'🛒', cascade_tp:'🎯', stop_loss:'🛑', manual_buy:'🛒', manual_sell:'📤', withdraw:'💸' };
      const icon = icons[t.action] || '📋';
      let info = `${t.action} — ${t.token || ''} `;
      if (t.amount_sol) info += `${t.amount_sol} SOL `;
      if (t.pnl_pct) info += `PnL: ${t.pnl_pct > 0 ? '+' : ''}${t.pnl_pct.toFixed(0)}% `;
      if (t.level) info += `Cascade #${t.level} `;
      if (t.sell_pct) info += `Sold ${t.sell_pct.toFixed(0)}% `;
      trHTML += `<div class="trade">
        <span class="trade-icon">${icon}</span>
        <span class="trade-info">${info}</span>
        <span class="trade-time">${formatTime(t.timestamp)}</span>
      </div>`;
    });
  }
  document.getElementById('trades').innerHTML = trHTML;

  document.getElementById('status').textContent =
    'Ostatnia aktualizacja: ' + new Date().toLocaleTimeString('pl-PL') + ' · Auto-refresh co 10s';
}

function renderPosition(p, type) {
  const badgeClass = type === 'moonbag' ? 'badge-moonbag' : type === 'active' ? 'badge-active' : 'badge-closed';
  const badgeLabel = type === 'moonbag' ? '🌙 MOONBAG' : type === 'active' ? '⚡ ACTIVE' : '📦 CLOSED';
  const cascade = (p.cascade_level || 0);
  const pnlSol = (p.total_sold_sol || 0) - (p.buy_amount_sol || 0);
  const sells = (p.sells || []);

  return `<div class="position">
    <div class="pos-header">
      <div>
        <span class="pos-name">${p.token_name || '?'}</span>
        <span style="color:rgba(255,255,255,0.3);font-size:12px;"> $${p.token_symbol || '?'}</span>
        <span class="badge ${badgeClass}">${badgeLabel}</span>
        ${cascade > 0 ? `<span class="badge badge-moonbag">CASCADE ${cascade}</span>` : ''}
      </div>
      <span class="pos-score" style="background:${p.score>=70?'rgba(0,255,136,0.1)':p.score>=50?'rgba(255,214,0,0.1)':'rgba(255,68,68,0.1)'};
        color:${p.score>=70?'#00FF88':p.score>=50?'#FFD600':'#FF4444'};">
        Score: ${p.score || 0}
      </span>
    </div>
    <div class="pos-details">
      Bought: <span>${formatSOL(p.buy_amount_sol)} SOL</span> · 
      Returned: <span>${formatSOL(p.total_sold_sol)} SOL</span> · 
      PnL: <span style="color:${pnlSol>=0?'#00FF88':'#FF4444'}">${pnlSol>=0?'+':''}${formatSOL(pnlSol)} SOL</span><br>
      Age: <span>${ageHours(p.buy_time)}</span> · 
      Sells: <span>${sells.length}</span> · 
      Tokens left: <span>${(p.tokens_remaining||0).toLocaleString()}</span><br>
      ${p.url ? `<a href="${p.url}" target="_blank">📊 DexScreener</a> · ` : ''}
      <span class="ca">CA: ${p.token_address || '?'}</span>
    </div>
  </div>`;
}

async function update() {
  const data = await loadData();
  render(data);
}
update();
setInterval(update, 10000);
</script>
</body>
</html>"""


HISTORY_HTML = """<!DOCTYPE html>
<html lang="pl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>📜 Scan History</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;800&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0A0A0F; color: #fff;
    font-family: 'JetBrains Mono', monospace;
    min-height: 100vh; padding: 20px;
  }
  .header {
    text-align: center; padding: 20px 0;
    border-bottom: 1px solid rgba(255,255,255,0.06);
    margin-bottom: 24px;
  }
  .header h1 {
    font-size: 24px; font-weight: 900;
    background: linear-gradient(135deg, #00FF88, #00AAFF);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .sub { color: rgba(255,255,255,0.3); font-size: 12px; margin-top: 4px; }
  a { color: #00AAFF; text-decoration: none; }
  a:hover { text-decoration: underline; }
  .nav { text-align: center; margin-bottom: 20px; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; margin-top: 12px; }
  th { text-align: left; font-size: 11px; color: rgba(255,255,255,0.4); padding: 8px 12px;
    border-bottom: 1px solid rgba(255,255,255,0.08); }
  td { padding: 10px 12px; font-size: 13px; border-bottom: 1px solid rgba(255,255,255,0.04); }
  tr:hover td { background: rgba(255,255,255,0.02); }
  .score-badge { display: inline-block; padding: 2px 8px; border-radius: 5px; font-size: 11px; font-weight: 700; }
  .empty { text-align: center; padding: 40px; color: rgba(255,255,255,0.2); }

  .details { display: none; }
  .details.open { display: table-row; }
  .details td { padding: 0; }
  .detail-table { width: 100%; background: rgba(255,255,255,0.02); }
  .detail-table th { font-size: 10px; }
  .detail-table td { font-size: 11px; color: rgba(255,255,255,0.6); padding: 6px 10px; }
  .ca { font-size: 10px; color: rgba(255,255,255,0.25); }
  .toggle-btn { cursor: pointer; color: #00AAFF; font-size: 12px; }
</style>
</head>
<body>
<div class="header">
  <h1>📜 SCAN HISTORY</h1>
  <div class="sub">Past gem scanner results from SQLite</div>
</div>
<div class="nav"><a href="/">← Back to Dashboard</a></div>
<div id="content"></div>

<script>
async function load() {
  const scans = await (await fetch('/api/history')).json();
  if (scans.length === 0) {
    document.getElementById('content').innerHTML = '<div class="empty">No scans recorded yet. Run solana_gem_scanner.py first.</div>';
    return;
  }
  let html = '<table><thead><tr><th>#</th><th>Date</th><th>Tokens</th><th>Top Score</th><th></th></tr></thead><tbody>';
  scans.forEach(s => {
    const d = new Date(s.timestamp);
    const dateStr = d.toLocaleString('pl-PL', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    const scoreColor = s.top_score >= 75 ? '#00FF88' : s.top_score >= 55 ? '#FFD600' : '#FF4444';
    html += `<tr>
      <td>${s.id}</td>
      <td>${dateStr}</td>
      <td>${s.total_tokens_found}</td>
      <td><span class="score-badge" style="background:${scoreColor}22;color:${scoreColor};border:1px solid ${scoreColor}44;">${s.top_score}/100</span></td>
      <td><span class="toggle-btn" onclick="toggleDetails(${s.id}, this)">▶ Details</span></td>
    </tr>
    <tr class="details" id="details-${s.id}"><td colspan="5"><div class="detail-inner" id="inner-${s.id}"></div></td></tr>`;
  });
  html += '</tbody></table>';
  document.getElementById('content').innerHTML = html;
}

async function toggleDetails(scanId, btn) {
  const row = document.getElementById('details-' + scanId);
  if (row.classList.contains('open')) {
    row.classList.remove('open');
    btn.textContent = '▶ Details';
    return;
  }
  const inner = document.getElementById('inner-' + scanId);
  if (!inner.innerHTML) {
    const results = await (await fetch('/api/history/' + scanId)).json();
    if (results.length === 0) {
      inner.innerHTML = '<div style="padding:12px;color:rgba(255,255,255,0.3);">No results</div>';
    } else {
      let t = '<table class="detail-table"><thead><tr><th>Token</th><th>Score</th><th>Price</th><th>Liq</th><th>Vol 24h</th><th>FDV</th><th>Age</th><th>Signal</th><th>Warnings</th></tr></thead><tbody>';
      results.forEach(r => {
        const w = r.warnings ? JSON.parse(r.warnings) : [];
        const scoreColor = r.score >= 75 ? '#00FF88' : r.score >= 55 ? '#FFD600' : '#FF4444';
        t += `<tr>
          <td>${r.token_name} <span style="color:rgba(255,255,255,0.3);">$${r.symbol}</span><br><span class="ca">${r.token_address}</span></td>
          <td><span class="score-badge" style="background:${scoreColor}22;color:${scoreColor};">${r.score}</span></td>
          <td>$${r.price_usd < 0.01 ? r.price_usd.toFixed(8) : r.price_usd.toFixed(4)}</td>
          <td>${fmt(r.liquidity_usd)}</td>
          <td>${fmt(r.volume_24h)}</td>
          <td>${fmt(r.fdv)}</td>
          <td>${Math.round(r.pair_age_hours)}h</td>
          <td style="font-size:11px;">${r.signals || ''}</td>
          <td style="font-size:10px;">${w.join(' ')}</td>
        </tr>`;
      });
      t += '</tbody></table>';
      inner.innerHTML = t;
    }
  }
  row.classList.add('open');
  btn.textContent = '▼ Hide';
}

function fmt(v) {
  if (!v) return '$0';
  if (v >= 1e6) return '$' + (v/1e6).toFixed(1) + 'M';
  if (v >= 1e3) return '$' + (v/1e3).toFixed(1) + 'K';
  return '$' + v.toFixed(0);
}

load();
</script>
</body>
</html>"""


_scan_lock = threading.Lock()
_scan_executor = ThreadPoolExecutor(max_workers=1)


def _format_scan_response(scan_row, results):
    return {
        "scan_id": scan_row["id"],
        "timestamp": scan_row["timestamp"],
        "total_tokens_found": scan_row["total_tokens_found"],
        "results": [
            {
                "token_name": r.get("token_name", ""),
                "symbol": r.get("symbol", ""),
                "score": r.get("score", 0),
                "price_usd": r.get("price_usd", 0),
                "liquidity_usd": r.get("liquidity_usd", 0),
                "signals": r.get("signals", ""),
            }
            for r in results[:10]
        ],
    }


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/positions':
            self.send_json(self.load_json('positions.json', []))
        elif path == '/api/trades':
            self.send_json(self.load_trade_log('trade_log.json'))
        elif path == '/api/history':
            qs = parse_qs(parsed.query)
            limit = int(qs.get('limit', ['50'])[0])
            self.send_json(get_scan_history(limit))
        elif path.startswith('/api/history/'):
            try:
                scan_id = int(path.split('/')[-1])
                self.send_json(get_scan_details(scan_id))
            except (ValueError, IndexError):
                self.send_json([])
        elif path == '/api/scan/latest':
            scan = get_latest_scan()
            if not scan:
                self.send_json_error(404, "No scans found")
            else:
                results = scan.pop("results", [])
                self.send_json(_format_scan_response(scan, results))
        elif path == '/history':
            self.send_html(HISTORY_HTML)
        else:
            self.send_html(HTML)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/scan':
            if not _scan_lock.acquire(blocking=False):
                self.send_json_error(409, "A scan is already running")
                return
            try:
                from solana_gem_scanner import run_scan as do_run_scan
                future = _scan_executor.submit(do_run_scan)
                results = future.result(timeout=120)
                if not results:
                    self.send_json_error(200, "Scan completed but found no tokens matching filters")
                    return
                scan = get_latest_scan()
                scan_results = scan.pop("results", [])
                self.send_json(_format_scan_response(scan, scan_results))
            except FuturesTimeoutError:
                self.send_json_error(504, "Scan timed out after 120 seconds")
            except Exception:
                self.send_json_error(500, f"Scan failed: {traceback.format_exc()}")
            finally:
                _scan_lock.release()
        else:
            self.send_json_error(404, "Not found")

    def send_html(self, content):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(content.encode('utf-8'))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def send_json(self, data):
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Client disconnected mid-response

    def send_json_error(self, code, message):
        try:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({"error": message}, ensure_ascii=False).encode('utf-8'))
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass  # Client disconnected mid-response

    def load_json(self, filename, default):
        try:
            p = Path(filename)
            if p.exists():
                return json.loads(p.read_text(encoding='utf-8'))
        except:
            pass
        return default

    def load_trade_log(self, filename):
        """Read NDJSON trade log (one JSON object per line). Falls back to legacy
        JSON-array format if the file starts with '['."""
        p = Path(filename)
        if not p.exists():
            return []
        try:
            with open(p, 'r', encoding='utf-8') as f:
                first = f.read(1)
                if not first:
                    return []
                if first == '[':
                    # Legacy JSON-array format (pre-NDJSON migration)
                    return json.loads(p.read_text(encoding='utf-8'))
            events = []
            with open(p, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            return events
        except Exception:
            return []

    def log_message(self, format, *args):
        pass  # Suppress request logs


def main():
    print()
    print("  ╔═══════════════════════════════════════════╗")
    print("  ║   📊 DEGEN BOT DASHBOARD                 ║")
    print("  ║   http://localhost:8420                   ║")
    print("  ╚═══════════════════════════════════════════╝")
    print()
    print(f"  🌐 Dashboard: http://localhost:{PORT}")
    print(f"  📂 Reading: positions.json + trade_log.json")
    print(f"  🔄 Auto-refresh: co 10 sekund")
    print(f"  ⏹  Ctrl+C aby zatrzymać")
    print()

    # Open browser
    threading.Timer(1.0, lambda: webbrowser.open(f'http://localhost:{PORT}')).start()

    server = http.server.HTTPServer(('localhost', PORT), DashboardHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 Dashboard zatrzymany.")
        server.shutdown()


if __name__ == '__main__':
    main()
