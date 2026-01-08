"""
PSEUDOCODE (step-by-step plan)
1) Create a Flask web server with routes:
   - GET  /            -> colorful dashboard HTML (Sinchan-inspired palette)
   - GET  /api/state   -> JSON with bot status, prices, positions, trades, equity curve
   - POST /api/start   -> start background trading loop
   - POST /api/stop    -> stop background trading loop
2) Build a paper-trading engine:
   - Maintain thread-safe state: cash, positions, last prices, trade history, logs
   - Simulate market prices via a random walk for a small set of symbols
   - Each tick:
       a) Update simulated prices
       b) Compute simple moving averages (fast/slow)
       c) If fast crosses above slow -> buy with fixed risk budget
          If fast crosses below slow -> sell/close position
       d) Record trades, update equity and PnL
3) Run the bot loop in a background thread:
   - Use a threading.Event to stop gracefully
   - Use a Lock to protect shared state
4) Pick a random available localhost port automatically:
   - Try random ports and bind-test with sockets until one is free
5) Make it autopilot-friendly:
   - No interactive prompts
   - Print the chosen URL and start Flask
"""

from __future__ import annotations

import json
import math
import os
import random
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template_string, request


app = Flask(__name__)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_random_free_port(host: str = "127.0.0.1", min_port: int = 1024, max_port: int = 65535) -> int:
    """
    Picks a random available port by attempting to bind.
    Falls back to OS-assigned port if random tries fail.
    """
    for _ in range(128):
        port = random.randint(min_port, max_port)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


@dataclass(frozen=True)
class Trade:
    ts: str
    symbol: str
    side: str  # "BUY" | "SELL"
    qty: float
    price: float
    reason: str


@dataclass
class Position:
    symbol: str
    qty: float = 0.0
    avg_price: float = 0.0

    def market_value(self, last_price: float) -> float:
        return float(self.qty) * float(last_price)

    def unrealized_pnl(self, last_price: float) -> float:
        return (float(last_price) - float(self.avg_price)) * float(self.qty)


class PaperBot:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self.symbols: List[str] = ["SHINCHAN", "KAZAMA", "MASAO", "BOCHAN", "NENE"]
        self.cash: float = 10_000.0
        self.starting_cash: float = 10_000.0
        self.positions: Dict[str, Position] = {s: Position(symbol=s) for s in self.symbols}
        self.prices: Dict[str, float] = {s: 100.0 + 10.0 * i for i, s in enumerate(self.symbols)}
        self._price_hist: Dict[str, Deque[float]] = {s: deque([self.prices[s]] * 80, maxlen=200) for s in self.symbols}

        self.fast_window = 7
        self.slow_window = 21
        self.tick_seconds = 1.0
        self.risk_per_trade = 0.12  # fraction of cash to deploy per buy

        self.trades: Deque[Trade] = deque(maxlen=250)
        self.logs: Deque[str] = deque(maxlen=200)
        self.equity_curve: Deque[Tuple[str, float]] = deque(maxlen=600)
        self.status: str = "stopped"
        self.last_tick_ts: Optional[str] = None

        self._log("Bot initialized. Paper trading only (simulated prices).")
        self._snapshot_equity()

    def _log(self, msg: str) -> None:
        self.logs.appendleft(f"{utc_iso()}  {msg}")

    def _snapshot_equity(self) -> None:
        eq = self.compute_equity()
        self.equity_curve.append((utc_iso(), float(eq)))

    def compute_equity(self) -> float:
        total = float(self.cash)
        for sym, pos in self.positions.items():
            total += pos.market_value(self.prices[sym])
        return total

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> None:
        with self._lock:
            if self.is_running():
                self._log("Start ignored: already running.")
                return
            self._stop_event.clear()
            self.status = "running"
            self._thread = threading.Thread(target=self._run_loop, name="PaperBotThread", daemon=True)
            self._thread.start()
            self._log("Bot started.")

    def stop(self) -> None:
        with self._lock:
            if not self.is_running():
                self.status = "stopped"
                self._log("Stop ignored: already stopped.")
                return
            self._stop_event.set()
            self.status = "stopping"
            self._log("Stopping bot...")

    def reset(self) -> None:
        with self._lock:
            was_running = self.is_running()
            self._stop_event.set()
            self.status = "resetting"

        if was_running and self._thread is not None:
            self._thread.join(timeout=2.5)

        with self._lock:
            self._stop_event.clear()
            self.cash = float(self.starting_cash)
            for s in self.symbols:
                self.positions[s] = Position(symbol=s)
                base = 100.0 + 10.0 * self.symbols.index(s)
                self.prices[s] = base
                self._price_hist[s].clear()
                self._price_hist[s].extend([base] * 80)
            self.trades.clear()
            self.logs.clear()
            self.equity_curve.clear()
            self.last_tick_ts = None
            self.status = "stopped"
            self._log("Bot reset.")
            self._snapshot_equity()

    def _sma(self, values: Deque[float], window: int) -> float:
        if not values:
            return 0.0
        n = min(window, len(values))
        if n <= 0:
            return 0.0
        return float(sum(list(values)[-n:])) / float(n)

    def _simulate_next_price(self, symbol: str) -> float:
        """
        Random-walk with mild mean reversion; designed to look lively on a dashboard.
        """
        last = float(self.prices[symbol])
        base = 100.0 + 10.0 * self.symbols.index(symbol)
        drift = (base - last) * 0.003
        vol = 0.8 + 0.2 * math.sin(time.time() / 4.0)
        shock = random.gauss(0.0, vol)
        next_px = max(1.0, last + drift + shock)
        return float(round(next_px, 2))

    def _place_trade(self, symbol: str, side: str, qty: float, price: float, reason: str) -> None:
        qty = float(max(0.0, qty))
        price = float(max(0.01, price))
        if qty <= 0.0:
            return

        pos = self.positions[symbol]
        if side == "BUY":
            cost = qty * price
            if cost > self.cash:
                qty = math.floor((self.cash / price) * 100.0) / 100.0
                if qty <= 0.0:
                    return
                cost = qty * price

            new_qty = pos.qty + qty
            if new_qty <= 0.0:
                return
            pos.avg_price = (pos.avg_price * pos.qty + price * qty) / new_qty if pos.qty > 0 else price
            pos.qty = new_qty
            self.cash -= cost
            self.trades.appendleft(Trade(ts=utc_iso(), symbol=symbol, side="BUY", qty=qty, price=price, reason=reason))
            self._log(f"BUY {symbol} qty={qty:.2f} @ {price:.2f} ({reason})")
            return

        if side == "SELL":
            sell_qty = min(pos.qty, qty)
            if sell_qty <= 0.0:
                return
            proceeds = sell_qty * price
            pos.qty -= sell_qty
            if pos.qty <= 1e-9:
                pos.qty = 0.0
                pos.avg_price = 0.0
            self.cash += proceeds
            self.trades.appendleft(Trade(ts=utc_iso(), symbol=symbol, side="SELL", qty=sell_qty, price=price, reason=reason))
            self._log(f"SELL {symbol} qty={sell_qty:.2f} @ {price:.2f} ({reason})")
            return

    def _maybe_trade_symbol(self, symbol: str) -> None:
        hist = self._price_hist[symbol]
        fast = self._sma(hist, self.fast_window)
        slow = self._sma(hist, self.slow_window)
        if len(hist) < self.slow_window + 2:
            return

        # Simple cross using last two points with last-two SMA approximations
        prev_hist = list(hist)[:-1]
        prev_fast = float(sum(prev_hist[-min(self.fast_window, len(prev_hist)):])) / float(min(self.fast_window, len(prev_hist)))
        prev_slow = float(sum(prev_hist[-min(self.slow_window, len(prev_hist)):])) / float(min(self.slow_window, len(prev_hist)))

        pos = self.positions[symbol]
        px = float(self.prices[symbol])

        cross_up = prev_fast <= prev_slow and fast > slow
        cross_dn = prev_fast >= prev_slow and fast < slow

        if cross_up and pos.qty <= 0.0:
            budget = max(0.0, self.cash * float(self.risk_per_trade))
            qty = math.floor((budget / px) * 100.0) / 100.0
            self._place_trade(symbol, "BUY", qty, px, f"SMA cross UP ({self.fast_window}/{self.slow_window})")
            return

        if cross_dn and pos.qty > 0.0:
            self._place_trade(symbol, "SELL", pos.qty, px, f"SMA cross DOWN ({self.fast_window}/{self.slow_window})")
            return

    def _run_loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    for s in self.symbols:
                        self.prices[s] = self._simulate_next_price(s)
                        self._price_hist[s].append(self.prices[s])
                    for s in self.symbols:
                        self._maybe_trade_symbol(s)
                    self.last_tick_ts = utc_iso()
                    self._snapshot_equity()
                time.sleep(self.tick_seconds)
        finally:
            with self._lock:
                self.status = "stopped"
                self._log("Bot stopped.")

    def public_state(self) -> Dict[str, object]:
        with self._lock:
            equity = self.compute_equity()
            pnl = equity - self.starting_cash
            pos_view = {}
            for s, p in self.positions.items():
                last_px = float(self.prices[s])
                pos_view[s] = {
                    "qty": float(p.qty),
                    "avg_price": float(p.avg_price),
                    "last_price": last_px,
                    "market_value": float(p.market_value(last_px)),
                    "unrealized_pnl": float(p.unrealized_pnl(last_px)),
                }

            return {
                "ts": utc_iso(),
                "status": self.status,
                "running": self.is_running(),
                "last_tick_ts": self.last_tick_ts,
                "cash": float(self.cash),
                "equity": float(equity),
                "pnl": float(pnl),
                "prices": {k: float(v) for k, v in self.prices.items()},
                "positions": pos_view,
                "trades": [asdict(t) for t in list(self.trades)[:30]],
                "logs": list(self.logs)[:30],
                "equity_curve": [{"ts": ts, "equity": float(eq)} for ts, eq in list(self.equity_curve)[-240:]],
                "params": {
                    "fast_window": self.fast_window,
                    "slow_window": self.slow_window,
                    "tick_seconds": self.tick_seconds,
                    "risk_per_trade": self.risk_per_trade,
                },
            }


BOT = PaperBot()


DASHBOARD_HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Sinchan Paper Trading Bot</title>
  <style>
    :root{
      --red:#ff2e2e;
      --sun:#ffd000;
      --sky:#2aa8ff;
      --mint:#45f0b6;
      --purple:#7b61ff;
      --ink:#10121a;
      --card:#121629cc;
      --line:#ffffff22;
      --good:#19d37a;
      --bad:#ff4d6d;
    }
    * { box-sizing: border-box; }
    body{
      margin:0; color:#fff;
      font-family: "Comic Sans MS","Trebuchet MS",system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;
      background:
        radial-gradient(1200px 600px at 10% 10%, #ff2e2e55, transparent 60%),
        radial-gradient(900px 500px at 85% 20%, #ffd00055, transparent 55%),
        radial-gradient(900px 600px at 70% 85%, #2aa8ff55, transparent 60%),
        linear-gradient(135deg, #0b1020 0%, #0a0d18 55%, #090b12 100%);
      min-height:100vh;
    }
    .wrap{ max-width: 1200px; margin: 0 auto; padding: 22px 16px 40px; }
    .top{
      display:flex; gap:12px; flex-wrap:wrap; align-items:center; justify-content:space-between;
      padding:16px 18px; border:1px solid var(--line); border-radius:18px;
      background: linear-gradient(135deg, #121629cc, #12162999);
      backdrop-filter: blur(10px);
      box-shadow: 0 12px 28px #00000055;
    }
    .brand{
      display:flex; align-items:center; gap:14px;
    }
    .logo{
      width:54px; height:54px; border-radius:16px;
      background:
        radial-gradient(circle at 30% 30%, var(--sun), transparent 55%),
        radial-gradient(circle at 70% 70%, var(--sky), transparent 55%),
        linear-gradient(135deg, var(--red), var(--purple));
      box-shadow: 0 10px 22px #00000066;
      border: 2px solid #ffffff22;
    }
    h1{ margin:0; font-size:20px; letter-spacing:.2px; }
    .sub{ margin:4px 0 0; opacity:.85; font-size:12.5px; }
    .chips{ display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
    .chip{
      padding:8px 10px; border:1px solid var(--line); border-radius:999px;
      background:#0c1122aa;
      font-size:12px;
    }
    .chip b{ font-weight:800; }
    .btns{ display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
    button{
      border:0; border-radius:12px; padding:10px 12px;
      font-weight:900; cursor:pointer;
      color: var(--ink);
      box-shadow: 0 10px 22px #00000055;
      transition: transform .05s ease;
    }
    button:active{ transform: translateY(1px); }
    .start{ background: linear-gradient(135deg, var(--mint), var(--sun)); }
    .stop{ background: linear-gradient(135deg, #ff8fb1, var(--red)); }
    .reset{ background: linear-gradient(135deg, #c7d2fe, var(--sky)); }

    .grid{
      margin-top: 16px;
      display:grid;
      grid-template-columns: 1.3fr .9fr;
      gap: 14px;
    }
    @media (max-width: 980px){
      .grid{ grid-template-columns: 1fr; }
    }
    .card{
      border:1px solid var(--line);
      border-radius: 18px;
      background: linear-gradient(135deg, #121629cc, #0b1020aa);
      backdrop-filter: blur(10px);
      box-shadow: 0 12px 28px #00000055;
      overflow:hidden;
    }
    .card .hd{
      display:flex; justify-content:space-between; align-items:center;
      padding: 14px 16px;
      border-bottom:1px solid var(--line);
      background: linear-gradient(135deg, #ffffff08, transparent);
    }
    .card .hd h2{ margin:0; font-size:14px; letter-spacing:.2px; }
    .card .bd{ padding: 14px 16px; }
    .kpis{
      display:grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 10px;
    }
    @media (max-width: 980px){
      .kpis{ grid-template-columns: repeat(2, 1fr); }
    }
    .kpi{
      border:1px solid var(--line);
      border-radius: 14px;
      padding: 12px 12px;
      background: #0c1122aa;
    }
    .kpi .lab{ font-size:11px; opacity:.85; }
    .kpi .val{ font-size:18px; margin-top:6px; font-weight:1000; }
    .pos{
      width:100%;
      border-collapse: collapse;
      overflow:hidden;
      border-radius: 14px;
      border: 1px solid var(--line);
    }
    .pos th, .pos td{
      padding: 10px 10px;
      border-bottom: 1px solid #ffffff12;
      font-size: 12px;
    }
    .pos th{ text-align:left; background:#0c1122cc; }
    .pos tr:last-child td{ border-bottom:none; }
    .muted{ opacity:.85; }
    .good{ color: var(--good); font-weight: 900; }
    .bad{ color: var(--bad); font-weight: 900; }
    .mono{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace; }
    .row{
      display:grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }
    @media (max-width: 980px){
      .row{ grid-template-columns: 1fr; }
    }
    .list{
      border:1px solid var(--line);
      border-radius: 14px;
      background:#0c1122aa;
      padding: 10px 10px;
      min-height: 220px;
      overflow:auto;
    }
    .item{
      padding: 8px 8px;
      border-bottom: 1px dashed #ffffff1a;
      font-size: 12px;
      line-height: 1.25rem;
    }
    .item:last-child{ border-bottom:none; }
    .spark{
      height: 58px;
      width: 100%;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: linear-gradient(135deg, #0c1122aa, #0c112244);
      overflow:hidden;
    }
    .footer{
      margin-top: 12px;
      opacity:.8;
      font-size: 12px;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo" aria-hidden="true"></div>
        <div>
          <h1>Sinchan Paper Trading Bot</h1>
          <div class="sub">Colorful dashboard · random port · simulated market · simple SMA strategy</div>
        </div>
      </div>

      <div class="chips">
        <div class="chip">Status: <b id="status">...</b></div>
        <div class="chip">Last tick: <b id="lastTick" class="mono">...</b></div>
        <div class="chip">Fast/Slow: <b id="windows" class="mono">...</b></div>
        <div class="chip">Tick: <b id="tickSec" class="mono">...</b>s</div>
      </div>

      <div class="btns">
        <button class="start" onclick="apiPost('/api/start')">START</button>
        <button class="stop" onclick="apiPost('/api/stop')">STOP</button>
        <button class="reset" onclick="apiPost('/api/reset')">RESET</button>
      </div>
    </div>

    <div class="grid">
      <div class="card">
        <div class="hd">
          <h2>Money + Positions</h2>
          <div class="muted mono" id="ts">...</div>
        </div>
        <div class="bd">
          <div class="kpis">
            <div class="kpi">
              <div class="lab">Cash</div>
              <div class="val mono" id="cash">...</div>
            </div>
            <div class="kpi">
              <div class="lab">Equity</div>
              <div class="val mono" id="equity">...</div>
            </div>
            <div class="kpi">
              <div class="lab">PnL</div>
              <div class="val mono" id="pnl">...</div>
            </div>
            <div class="kpi">
              <div class="lab">Market vibe</div>
              <div class="val" id="vibe">...</div>
            </div>
          </div>

          <div style="margin-top:12px;" class="spark">
            <canvas id="curve" height="58"></canvas>
          </div>

          <div style="margin-top:12px;">
            <table class="pos">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th class="mono">Last</th>
                  <th class="mono">Qty</th>
                  <th class="mono">Avg</th>
                  <th class="mono">Value</th>
                  <th class="mono">uPnL</th>
                </tr>
              </thead>
              <tbody id="posBody"></tbody>
            </table>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="hd"><h2>Activity</h2><div class="muted">latest 30</div></div>
        <div class="bd">
          <div class="row">
            <div>
              <div class="muted" style="margin:0 0 8px;">Trades</div>
              <div class="list" id="trades"></div>
            </div>
            <div>
              <div class="muted" style="margin:0 0 8px;">Logs</div>
              <div class="list" id="logs"></div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="footer">
      Paper trading only. Prices are simulated for a fun local demo.
    </div>
  </div>

  <script>
    const fmt = (n) => (Number.isFinite(n) ? n.toLocaleString(undefined, {maximumFractionDigits: 2}) : "—");
    const money = (n) => (Number.isFinite(n) ? "$" + n.toLocaleString(undefined, {maximumFractionDigits: 2}) : "—");

    async function apiPost(path){
      try{
        await fetch(path, {method:"POST", headers: {"Content-Type":"application/json"}, body: "{}"});
        await refresh();
      }catch(e){}
    }

    function vibeFrom(prices){
      const arr = Object.values(prices || {});
      if(!arr.length) return "sleepy";
      const avg = arr.reduce((a,b)=>a+b,0)/arr.length;
      const spread = Math.max(...arr) - Math.min(...arr);
      if(spread > avg*0.10) return "chaotic ✨";
      if(spread > avg*0.06) return "bouncy";
      return "chill";
    }

    function drawCurve(points){
      const canvas = document.getElementById("curve");
      const ctx = canvas.getContext("2d");
      const w = canvas.width = canvas.clientWidth;
      const h = canvas.height = canvas.clientHeight;
      ctx.clearRect(0,0,w,h);
      if(!points || points.length < 2) return;

      const ys = points.map(p => p.equity);
      const minY = Math.min(...ys);
      const maxY = Math.max(...ys);
      const pad = (maxY - minY) * 0.15 + 1e-6;
      const lo = minY - pad;
      const hi = maxY + pad;

      const xStep = w / (points.length - 1);
      const yMap = (y) => h - ((y - lo) / (hi - lo)) * h;

      // gradient line
      const grad = ctx.createLinearGradient(0,0,w,0);
      grad.addColorStop(0, "#ffd000");
      grad.addColorStop(0.5, "#2aa8ff");
      grad.addColorStop(1, "#45f0b6");

      ctx.lineWidth = 2.5;
      ctx.strokeStyle = grad;
      ctx.beginPath();
      points.forEach((p, i) => {
        const x = i * xStep;
        const y = yMap(p.equity);
        if(i === 0) ctx.moveTo(x,y); else ctx.lineTo(x,y);
      });
      ctx.stroke();

      // soft fill
      const fill = ctx.createLinearGradient(0,0,0,h);
      fill.addColorStop(0, "#2aa8ff33");
      fill.addColorStop(1, "#ff2e2e00");
      ctx.fillStyle = fill;
      ctx.lineTo(w, h);
      ctx.lineTo(0, h);
      ctx.closePath();
      ctx.fill();
    }

    function setText(id, txt){ const el=document.getElementById(id); if(el) el.textContent = txt; }

    async function refresh(){
      const res = await fetch("/api/state");
      const s = await res.json();

      setText("ts", s.ts || "—");
      setText("status", s.status || "—");
      setText("lastTick", s.last_tick_ts || "—");
      setText("windows", (s.params?.fast_window ?? "—") + "/" + (s.params?.slow_window ?? "—"));
      setText("tickSec", String(s.params?.tick_seconds ?? "—"));
      setText("cash", money(s.cash));
      setText("equity", money(s.equity));

      const pnlEl = document.getElementById("pnl");
      if(pnlEl){
        pnlEl.textContent = money(s.pnl);
        pnlEl.className = "val mono " + ((s.pnl ?? 0) >= 0 ? "good" : "bad");
      }
      setText("vibe", vibeFrom(s.prices));

      const tb = document.getElementById("posBody");
      tb.innerHTML = "";
      const positions = s.positions || {};
      const symbols = Object.keys(positions).sort();
      symbols.forEach(sym => {
        const p = positions[sym];
        const tr = document.createElement("tr");
        const u = Number(p.unrealized_pnl || 0);
        tr.innerHTML = `
          <td><b>${sym}</b></td>
          <td class="mono">${fmt(p.last_price)}</td>
          <td class="mono">${fmt(p.qty)}</td>
          <td class="mono">${fmt(p.avg_price)}</td>
          <td class="mono">${money(p.market_value)}</td>
          <td class="mono ${u>=0 ? "good":"bad"}">${money(u)}</td>
        `;
        tb.appendChild(tr);
      });

      const trades = document.getElementById("trades");
      trades.innerHTML = "";
      (s.trades || []).forEach(t => {
        const div = document.createElement("div");
        const side = t.side || "";
        const cls = side === "BUY" ? "good" : "bad";
        div.className = "item mono";
        div.innerHTML = `<span class="${cls}">${side}</span> ${t.symbol} qty=${fmt(t.qty)} @ ${fmt(t.price)}<br><span class="muted">${t.ts} · ${t.reason}</span>`;
        trades.appendChild(div);
      });

      const logs = document.getElementById("logs");
      logs.innerHTML = "";
      (s.logs || []).forEach(line => {
        const div = document.createElement("div");
        div.className = "item mono";
        div.textContent = line;
        logs.appendChild(div);
      });

      drawCurve(s.equity_curve || []);
    }

    refresh();
    setInterval(refresh, 1000);
  </script>
</body>
</html>
"""


@app.get("/")
def home():
    return render_template_string(DASHBOARD_HTML)


@app.get("/api/state")
def api_state():
    return jsonify(BOT.public_state())


@app.post("/api/start")
def api_start():
    BOT.start()
    return jsonify({"ok": True})


@app.post("/api/stop")
def api_stop():
    BOT.stop()
    return jsonify({"ok": True})


@app.post("/api/reset")
def api_reset():
    BOT.reset()
    return jsonify({"ok": True})


def main() -> None:
    # Heroku provides PORT environment variable, fallback to random port for local
    port = int(os.environ.get("PORT", find_random_free_port(host="127.0.0.1")))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    
    if not os.environ.get("PORT"):
        print(json.dumps({"url": f"http://127.0.0.1:{port}", "note": "Open this in your browser"}, indent=2))
    
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()

