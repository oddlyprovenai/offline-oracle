# generated-by: gemini-ultra
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from offline_oracle.indexer import OracleIndex
from offline_oracle.synthesizer import OracleSynthesizer, validate_local_llm_url

UI_HTML = """<!-- generated-by: gemini-ultra -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Offline Oracle — Oddly Proven 16 GB Lab</title>
  <style>
    :root {
      --ink: #0E1116;
      --panel: #151B24;
      --border: #273244;
      --paper: #F7F5EF;
      --muted: #B6B4AD;
      --lab-green: #88D445;
      --cage-orange: #F0592A;
      --signal-blue: #3B82F6;
      --warning-red: #E5484D;
      --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--ink);
      color: var(--paper);
      font-family: var(--sans);
      min-height: 100vh;
    }
    header {
      padding: 14px 24px;
      border-bottom: 1px solid var(--border);
      background: var(--panel);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-family: var(--mono);
    }
    .dot {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--lab-green);
      margin-right: 8px;
    }
    main {
      max-width: 1360px;
      margin: 0 auto;
      padding: 20px;
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
    }
    @media (max-width: 900px) {
      main { grid-template-columns: 1fr; }
    }
    .pane {
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .search-bar {
      display: flex;
      gap: 10px;
      align-items: center;
    }
    .prompt {
      font-family: var(--mono);
      color: var(--lab-green);
      font-size: 0.84rem;
      white-space: nowrap;
    }
    input[type="text"] {
      flex: 1;
      background: var(--ink);
      color: var(--paper);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px 12px;
      font-family: var(--mono);
    }
    button {
      background: var(--lab-green);
      color: var(--ink);
      border: none;
      border-radius: 6px;
      padding: 10px 16px;
      font-family: var(--mono);
      font-weight: 700;
      cursor: pointer;
    }
    .receipt-strip {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      font-family: var(--mono);
      font-size: 0.78rem;
    }
    .pill {
      padding: 4px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
      background: var(--ink);
    }
    .pill.ok { border-color: var(--lab-green); color: var(--lab-green); }
    .pill.warn { border-color: var(--warning-red); color: var(--warning-red); }
    .answer-box {
      background: var(--ink);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      line-height: 1.6;
      white-space: pre-wrap;
      font-size: 0.94rem;
    }
    .cite-btn {
      background: transparent;
      color: var(--cage-orange);
      border: 1px solid var(--cage-orange);
      padding: 2px 7px;
      font-size: 0.78rem;
      border-radius: 4px;
      margin: 0 3px;
    }
    .source-card {
      background: var(--ink);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 14px;
      transition: border-color 0.2s ease;
    }
    .source-card.active {
      border-color: var(--lab-green);
    }
    .source-head {
      display: flex;
      justify-content: space-between;
      font-family: var(--mono);
      font-size: 0.8rem;
      color: var(--signal-blue);
      margin-bottom: 8px;
    }
    mark {
      background: var(--lab-green);
      color: var(--ink);
      padding: 1px 3px;
      border-radius: 3px;
    }
  </style>
</head>
<body>
  <header>
    <div><span class="dot"></span><strong>OFFLINE ORACLE</strong> // No Wi-Fi. Still answers. With sources.</div>
    <div style="font-size:0.8rem;color:var(--muted);">127.0.0.1 LOOPBACK · CC BY-SA 4.0 SOURCES PRESERVED</div>
  </header>
  <main>
    <section class="pane">
      <h2 style="margin:0;font-size:1.05rem;">Span-Verified Answer</h2>
      <form id="ask-form" class="search-bar">
        <span class="prompt">lab@mac-studio ~ %</span>
        <input id="q-input" type="text" value="Why do wristwatches vibrate a quartz crystal at 32768 hertz?" required />
        <button type="submit">Ask</button>
      </form>
      <div id="receipt-strip" class="receipt-strip"></div>
      <div id="answer-box" class="answer-box">Submit an offline query to inspect span-verified citations.</div>
    </section>

    <section class="pane">
      <h2 style="margin:0;font-size:1.05rem;">Verbatim Archive Paragraphs</h2>
      <div id="sources-list" style="display:flex;flex-direction:column;gap:12px;"></div>
    </section>
  </main>

  <script>
    const form = document.getElementById('ask-form');
    const qInput = document.getElementById('q-input');
    const answerBox = document.getElementById('answer-box');
    const receiptStrip = document.getElementById('receipt-strip');
    const sourcesList = document.getElementById('sources-list');

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }

    function focusAnchor(anchor, quote) {
      document.querySelectorAll('.source-card').forEach((el) => {
        el.classList.toggle('active', el.dataset.anchor === anchor);
        const bodyEl = el.querySelector('.source-body');
        const raw = bodyEl.dataset.raw;
        if (el.dataset.anchor === anchor && quote && raw.toLowerCase().includes(quote.toLowerCase())) {
          const idx = raw.toLowerCase().indexOf(quote.toLowerCase());
          const before = escapeHtml(raw.slice(0, idx));
          const mid = escapeHtml(raw.slice(idx, idx + quote.length));
          const after = escapeHtml(raw.slice(idx + quote.length));
          bodyEl.innerHTML = `${before}<mark>${mid}</mark>${after}`;
          el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        } else {
          bodyEl.textContent = raw;
        }
      });
    }

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const res = await fetch('/api/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: qInput.value })
      });
      const data = await res.json();
      const v = data.verification;
      receiptStrip.innerHTML = `
        <span class="pill ${v.verification_rate === 1 ? 'ok' : 'warn'}">VERIFIED ${v.verified_claims}/${v.total_claims}</span>
        <span class="pill">MODE: ${escapeHtml(data.mode)}</span>
        <span class="pill">LATENCY: ${escapeHtml(data.latency_ms)} ms</span>
      `;

      answerBox.innerHTML = '';
      const claimsByAnchor = new Map();
      for (const c of v.claims || []) {
        claimsByAnchor.set(`${c.slug}#p${c.paragraph_index}`, c.quoted_span);
      }

      const parts = String(data.answer).split(/(\\[[a-zA-Z0-9_-]+#p\\d+\\])/g);
      for (const part of parts) {
        const m = part.match(/^\\[([a-zA-Z0-9_-]+#p\\d+)\\]$/);
        if (m) {
          const anchor = m[1];
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'cite-btn';
          btn.textContent = `[${anchor}]`;
          btn.addEventListener('click', () => focusAnchor(anchor, claimsByAnchor.get(anchor) || ''));
          answerBox.appendChild(btn);
        } else {
          answerBox.appendChild(document.createTextNode(part));
        }
      }

      sourcesList.innerHTML = '';
      for (const src of data.sources || []) {
        const card = document.createElement('div');
        card.className = 'source-card';
        card.dataset.anchor = src.anchor;
        card.innerHTML = `
          <div class="source-head">
            <span>[${escapeHtml(src.anchor)}] · ${escapeHtml(src.title)}</span>
            <span>score ${escapeHtml(src.hybrid_score)} · ${escapeHtml(src.license)}</span>
          </div>
          <div class="source-body"></div>
        `;
        const bodyEl = card.querySelector('.source-body');
        bodyEl.dataset.raw = src.text;
        bodyEl.textContent = src.text;
        sourcesList.appendChild(card);
      }

      if (v.claims && v.claims.length > 0) {
        const first = v.claims[0];
        focusAnchor(`${first.slug}#p${first.paragraph_index}`, first.quoted_span);
      }
    });
  </script>
</body>
</html>
"""


@dataclass
class ServerHandle:
    host: str
    port: int
    url: str
    _server: ThreadingHTTPServer
    _thread: threading.Thread

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


def start_oracle_server(
    db_path: str | Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    llm_endpoint: str | None = None,
) -> ServerHandle:
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("ERR_NON_LOCAL_HOST: Offline Oracle web UI binds strictly to 127.0.0.1")
    if llm_endpoint is not None:
        validate_local_llm_url(llm_endpoint)

    resolved_db = Path(db_path)

    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: dict) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/health":
                with OracleIndex(resolved_db) as idx:
                    stats = idx.get_stats()
                self._send_json(200, {"ok": True, "telemetry": "none", **stats})
                return

            if parsed.path == "/api/ask":
                qs = parse_qs(parsed.query)
                query = (qs.get("q") or [""])[0].strip()
                with OracleIndex(resolved_db) as idx:
                    synth = OracleSynthesizer(idx, llm_endpoint=llm_endpoint)
                    out = synth.ask(query)
                self._send_json(200, out)
                return

            if parsed.path == "/":
                body = UI_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
                )
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            self._send_json(404, {"error": "NOT_FOUND"})

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/ask":
                self._send_json(404, {"error": "NOT_FOUND"})
                return
            length = min(int(self.headers.get("Content-Length", "0")), 65536)
            raw_body = self.rfile.read(length).decode("utf-8", errors="replace")
            body = json.loads(raw_body or "{}")
            query = str(body.get("query") or "").strip()
            with OracleIndex(resolved_db) as idx:
                synth = OracleSynthesizer(idx, llm_endpoint=llm_endpoint)
                out = synth.ask(query)
            self._send_json(200, out)

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer((host, port), Handler)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return ServerHandle(
        host=host,
        port=actual_port,
        url=f"http://{host}:{actual_port}/",
        _server=server,
        _thread=thread,
    )
