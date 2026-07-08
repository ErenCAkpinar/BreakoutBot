"""
BreakoutBot — Live Watch Screen (read-only)

Çalışan botu İZLEMEK için tasarlanmış canlı monitör. `state_paper.json`'u okur,
ekranı belirli aralıklarla yeniler; bota ASLA dokunmaz (sadece state dosyasını
okur). paper_bb.py'nin yanında ikinci bir terminalde çalıştır.

    python watch.py                 # canlı, 5 sn'de bir yenilenir
    python watch.py --interval 2    # 2 sn'de bir
    python watch.py --once          # tek kare (ekran görüntüsü / CI için)
    python watch.py --demo          # örnek veriyle (bot gerekmez)
    python watch.py --file path.json  # başka bir state dosyası
    python watch.py --no-color      # ANSI renk yok (log/dosya için)

Gösterdikleri: bakiye + getiri, drawdown göstergesi (throttle/hard-stop
eşiklerine ne kadar kaldığı), günlük durum + freeze, açık pozisyonlar, rejim
tablosu, son trade'ler, oturum istatistikleri. Eşikler config.py'den okunur.

⚠️ Tüm veriler Binance Futures TESTNET (kağıt) — gerçek para değil.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    from config import (
        INITIAL_BALANCE, MAX_OPEN,
        DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT,
    )
except Exception:  # config yoksa makul varsayılanlar (watch tek başına da koşsun)
    INITIAL_BALANCE, MAX_OPEN = 1000.0, 2
    DAILY_DD_LIMIT, EQUITY_THROTTLE_DD, PEAK_DD_LIMIT = -0.05, -0.07, -0.15

# ── Renkler ───────────────────────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()


def _c(code: str) -> str:
    return code if _USE_COLOR else ""


RESET  = lambda: _c("\033[0m")
BOLD   = lambda: _c("\033[1m")
DIM    = lambda: _c("\033[2m")
RED    = lambda: _c("\033[31m")
GREEN  = lambda: _c("\033[32m")
YELLOW = lambda: _c("\033[33m")
BLUE   = lambda: _c("\033[34m")
CYAN   = lambda: _c("\033[36m")
GREY   = lambda: _c("\033[90m")

W = 66  # panel genişliği


def money(x: float) -> str:
    col = GREEN() if x >= 0 else RED()
    return f"{col}${x:+,.2f}{RESET()}"


def pct(x: float, good_high: bool = True) -> str:
    ok = (x >= 0) if good_high else (x <= 0)
    return f"{GREEN() if ok else RED()}{x:+.2f}%{RESET()}"


def rule(char: str = "─") -> str:
    return GREY() + char * W + RESET()


def header(title: str) -> str:
    pad = W - len(title) - 4
    return f"{GREY()}──{RESET()} {BOLD()}{title}{RESET()} {GREY()}{'─' * max(pad, 0)}{RESET()}"


# ── Pozisyon gruplama (dashboard.py ile aynı mantık) ──────────────────────────
def positions_from_trades(trade_log: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for t in trade_log:
        key = (t["symbol"], t["direction"], round(float(t["entry"]), 10))
        if key not in groups:
            groups[key] = {"symbol": t["symbol"], "direction": t["direction"], "pnl": 0.0}
            order.append(key)
        groups[key]["pnl"] += float(t["pnl"])
    return [groups[k] for k in order]


STATE_LABEL = {
    "IDLE": "IDLE", "TEST_OPEN": "PROBE", "SCALE_OPEN": "FULL", "TRAILING": "TRAIL",
}


def dd_gauge(cur_dd_frac: float) -> str:
    """0 → PEAK_DD_LIMIT arası dolan bar. Throttle eşiği işaretli."""
    span = abs(PEAK_DD_LIMIT)                 # örn 0.15
    frac = min(abs(min(cur_dd_frac, 0.0)) / span, 1.0)
    n = int(round(frac * (W - 20)))
    total = W - 20
    throttle_at = int(round(abs(EQUITY_THROTTLE_DD) / span * total))

    if cur_dd_frac <= PEAK_DD_LIMIT + 1e-9:
        col = RED()
    elif cur_dd_frac <= EQUITY_THROTTLE_DD:
        col = YELLOW()
    else:
        col = GREEN()

    cells = []
    for i in range(total):
        if i < n:
            cells.append(f"{col}█{RESET()}")
        elif i == throttle_at:
            cells.append(f"{YELLOW()}┊{RESET()}")   # throttle işareti
        else:
            cells.append(f"{GREY()}·{RESET()}")
    return "".join(cells)


def render(st: dict, is_demo: bool) -> str:
    balance   = float(st.get("balance", INITIAL_BALANCE))
    peak      = float(st.get("peak", INITIAL_BALANCE)) or INITIAL_BALANCE
    bar_count = st.get("bar_count", 0)
    day       = st.get("daily_day", "—")
    d_start   = float(st.get("daily_start", balance))
    d_freeze  = bool(st.get("daily_freeze", False))
    d_sl      = st.get("daily_sl_count", 0)
    trade_log = st.get("trade_log", [])
    sym_states = st.get("sym_states", {})
    regime    = st.get("regime", {})

    ret_pct   = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    cur_dd    = (balance - peak) / peak if peak > 0 else 0.0
    day_pnl   = balance - d_start
    day_pct   = day_pnl / d_start * 100 if d_start else 0.0

    to_throttle = balance - peak * (1 + EQUITY_THROTTLE_DD)   # $ room before throttle
    to_hardstop = balance - peak * (1 + PEAK_DD_LIMIT)         # $ room before hard stop

    out: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # ── Başlık ────────────────────────────────────────────────────────────────
    out.append(rule("═"))
    title = f"{BOLD()}{CYAN()}BREAKOUTBOT — LIVE WATCH{RESET()}"
    tag = f"{YELLOW()}◆ DEMO{RESET()}" if is_demo else f"{GREY()}testnet{RESET()}"
    out.append(f"  {title}   {tag}")
    out.append(f"  {GREY()}{now}   ·   Gün {day}   ·   Bar #{bar_count:,}{RESET()}")
    out.append(rule("═"))

    # ── Equity ────────────────────────────────────────────────────────────────
    out.append(f"  Bakiye  {BOLD()}${balance:,.2f}{RESET()}   "
               f"Getiri {pct(ret_pct)}   "
               f"{GREY()}zirve ${peak:,.2f}{RESET()}")
    out.append("")

    # ── Drawdown göstergesi ───────────────────────────────────────────────────
    out.append(header("DRAWDOWN"))
    dd_col = RED() if cur_dd <= EQUITY_THROTTLE_DD else GREEN()
    out.append(f"  DD {dd_col}{cur_dd*100:+.2f}%{RESET()}  {dd_gauge(cur_dd)}")
    out.append(f"  {GREY()}0%{'':>6}throttle {EQUITY_THROTTLE_DD*100:.0f}%"
               f"{'':>{max(W-34,1)}}hard {PEAK_DD_LIMIT*100:.0f}%{RESET()}")
    def room(x: float) -> str:
        return f"{GREEN()}${x:,.2f}{RESET()}"
    if to_throttle > 0:
        out.append(f"  Throttle'a kalan: {room(to_throttle)}   "
                   f"Hard-stop'a: {room(to_hardstop)}")
    elif to_hardstop > 0:
        out.append(f"  {YELLOW()}⚠ THROTTLE bölgesi — sizing yarıya inmiş olabilir.{RESET()}  "
                   f"Hard-stop'a: {room(to_hardstop)}")
    else:
        out.append(f"  {RED()}■ HARD-STOP eşiği aşıldı — bot yeni pozisyon açmaz.{RESET()}")
    out.append("")

    # ── Günlük ────────────────────────────────────────────────────────────────
    out.append(header("BUGÜN"))
    fz = f"{RED()}DONDU{RESET()}" if d_freeze else f"{GREEN()}açık{RESET()}"
    out.append(f"  Günlük P&L {money(day_pnl)} ({pct(day_pct)})   "
               f"Giriş: {fz}   {GREY()}SL bugün: {d_sl}{RESET()}")
    out.append(f"  {GREY()}günlük freeze eşiği {DAILY_DD_LIMIT*100:.0f}%{RESET()}")
    out.append("")

    # ── Açık pozisyonlar ──────────────────────────────────────────────────────
    out.append(header("AÇIK POZİSYONLAR"))
    open_rows = []
    n_full = 0
    for sym, s in sym_states.items():
        stt = s.get("state", "IDLE")
        if stt == "IDLE":
            continue
        lbl = STATE_LABEL.get(stt, stt)
        dirn = s.get("direction", "") or "—"
        if stt in ("SCALE_OPEN", "TRAILING"):
            n_full += 1
            entry = s.get("full_entry", 0.0)
            sl    = s.get("full_sl", 0.0)
            tp1   = s.get("full_tp1", 0.0)
            tp2   = s.get("full_tp2", 0.0)
            notio = s.get("full_notional", 0.0)
            held  = s.get("bars_held", 0)
            tp1f  = f"{GREEN()}✓{RESET()}" if s.get("tp1_hit") else " "
            open_rows.append(
                f"  {BOLD()}{sym:9s}{RESET()} {CYAN()}{lbl:5s}{RESET()} {dirn:5s} "
                f"giriş {entry:<10.5g} SL {sl:<10.5g} TP1{tp1f}{tp1:<9.5g} TP2 {tp2:<9.5g} "
                f"{GREY()}${notio:.0f} · {held} bar{RESET()}")
        else:  # PROBE / TEST_OPEN
            entry = s.get("test_entry", 0.0)
            sl    = s.get("test_sl", 0.0)
            held  = s.get("bars_held", 0)
            open_rows.append(
                f"  {BOLD()}{sym:9s}{RESET()} {YELLOW()}{lbl:5s}{RESET()} {dirn:5s} "
                f"giriş {entry:<10.5g} SL {sl:<10.5g} {GREY()}(yoklama) · {held} bar{RESET()}")
    if open_rows:
        out.extend(open_rows)
    else:
        out.append(f"  {GREY()}— açık pozisyon yok —{RESET()}")
    out.append(f"  {GREY()}Full: {n_full}/{MAX_OPEN}{RESET()}")
    out.append("")

    # ── Rejim tablosu ─────────────────────────────────────────────────────────
    if regime:
        out.append(header("REJİM (BTC 200-MA)"))
        rcol = {"BULL": GREEN(), "BEAR": RED(), "NEUTRAL": YELLOW()}
        rlbl = {"BULL": "BULL", "BEAR": "BEAR", "NEUTRAL": "NEUT"}
        cells = []
        for sym, r in regime.items():
            short = sym.replace("USDT", "")
            cells.append(f"{short:>5}:{rcol.get(r, GREY())}{rlbl.get(r, r[:4]):<4}{RESET()}")
        # 4 sütunlu sar
        for i in range(0, len(cells), 4):
            out.append("  " + "   ".join(cells[i:i + 4]))
        out.append("")

    # ── Son trade'ler ─────────────────────────────────────────────────────────
    out.append(header("SON TRADE'LER"))
    if trade_log:
        for t in trade_log[-6:]:
            p = float(t["pnl"])
            emoji = f"{GREEN()}▲{RESET()}" if p > 0 else (f"{RED()}▼{RESET()}" if p < 0 else "·")
            ts = t["ts"][5:16].replace("T", " ")
            out.append(f"  {emoji} {GREY()}{ts}{RESET()} {t['symbol']:9s} "
                       f"{t['direction']:5s} {t['exit_type']:6s} {money(p)}")
    else:
        out.append(f"  {GREY()}— henüz kapanmış trade yok —{RESET()}")
    out.append("")

    # ── Oturum istatistiği ────────────────────────────────────────────────────
    positions = positions_from_trades(trade_log)
    n = len(positions)
    wins = [p for p in positions if p["pnl"] > 0]
    losses = [p for p in positions if p["pnl"] < 0]
    wr = len(wins) / n * 100 if n else 0.0
    net = sum(p["pnl"] for p in positions)
    avg_w = sum(p["pnl"] for p in wins) / len(wins) if wins else 0.0
    avg_l = sum(p["pnl"] for p in losses) / len(losses) if losses else 0.0
    pf = (sum(p["pnl"] for p in wins) / abs(sum(p["pnl"] for p in losses))
          if losses and sum(p["pnl"] for p in losses) != 0 else None)
    out.append(header("OTURUM"))
    out.append(f"  Pozisyon {n}  ·  WR {wr:.0f}% ({len(wins)}W/{len(losses)}L)  ·  "
               f"Net {money(net)}  ·  PF {f'{pf:.2f}' if pf is not None else '—'}")
    out.append(f"  {GREY()}ort. kazanç ${avg_w:+.2f} · ort. kayıp ${avg_l:+.2f}{RESET()}")

    out.append(rule("═"))
    out.append(f"  {GREY()}read-only · botu etkilemez · testnet (gerçek para değil){RESET()}")
    return "\n".join(out)


def load_state(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None  # bot tam o an yazıyor olabilir → sonraki tur'da tekrar dene


def main() -> None:
    ap = argparse.ArgumentParser(description="BreakoutBot canlı izleme ekranı")
    ap.add_argument("--file", default="state_paper.json", help="state dosyası yolu")
    ap.add_argument("--interval", type=float, default=5.0, help="yenileme aralığı (sn)")
    ap.add_argument("--once", action="store_true", help="tek kare çiz ve çık")
    ap.add_argument("--demo", action="store_true", help="örnek veriyle koş (bot gerekmez)")
    ap.add_argument("--no-color", action="store_true", help="ANSI renkleri kapat")
    args = ap.parse_args()

    global _USE_COLOR
    if args.no_color:
        _USE_COLOR = False

    path = "state_paper.sample.json" if args.demo else args.file

    def draw_once() -> None:
        st = load_state(path)
        if not _USE_COLOR or args.once:
            pass
        else:
            sys.stdout.write("\033[2J\033[H")  # temizle + başa al
        if st is None:
            print(rule("═"))
            print(f"  {YELLOW()}state dosyası bekleniyor:{RESET()} {path}")
            print(f"  {GREY()}Önce paper_bb.py'yi çalıştır ya da: python watch.py --demo{RESET()}")
            print(rule("═"))
            return
        print(render(st, is_demo=args.demo))

    if args.once:
        draw_once()
        return

    try:
        while True:
            draw_once()
            time.sleep(max(args.interval, 0.5))
    except KeyboardInterrupt:
        print(f"\n{GREY()}izleme durduruldu.{RESET()}")


if __name__ == "__main__":
    main()
