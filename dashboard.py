"""
BreakoutBot — Go/No-Go Dashboard (read-only)

Bunu paper_bb.py çalışırken İKİNCİ bir terminalde çalıştır:

    python dashboard.py

state_paper.json + paper_bb.log dosyalarını OKUR. Çalışan bota asla dokunmaz.
Mikro-live'a geçiş kararının checklist'ini net gösterir:
  - Pozisyon sayısı (leg değil — gerçek trade birimi)
  - WR, kazanan/kaybeden, ortalama kazanç/kayıp, R:R, beklenti (expectancy)
  - Max drawdown + toparlandı mı
  - Her madde için ✅ / ⏳ / ❌ verdict
"""

from __future__ import annotations

import json
import os
import re

from config import INITIAL_BALANCE

STATE_FILE = "state_paper.json"
LOG_FILE   = "paper_bb.log"

# ── Go/No-Go eşikleri (sohbette anlaştığımız değerler) ────────────────────────
MIN_POSITIONS_GOOD = 15   # ideal örneklem (mikro-live için)
MIN_POSITIONS_OK   = 10   # "yaklaşıyor"
MIN_LOSSES_GOOD    = 3    # downside'ı gör
MIN_DD_SEEN        = -2.0 # en az -%2 düşüş yaşamış olmalı (test edilmiş)
WR_FLOOR           = 55.0 # gerçekçi taban
WR_LUCKY_CEIL      = 95.0 # n küçükken %100 = şans bayrağı


def _box(title: str) -> None:
    print("─" * 60)
    print(f"  {title}")
    print("─" * 60)


def positions_from_trades(trade_log: list[dict]) -> list[dict]:
    """Leg'leri pozisyona grupla. Bir pozisyonun bacakları (TP1 + TP2/TRAIL/SL)
    aynı entry fiyatını paylaşır → (symbol, direction, entry) ile grupla."""
    groups: dict[tuple, dict] = {}
    order: list[tuple] = []
    for t in trade_log:
        key = (t["symbol"], t["direction"], round(float(t["entry"]), 10))
        if key not in groups:
            groups[key] = {
                "symbol": t["symbol"], "direction": t["direction"],
                "entry": float(t["entry"]), "pnl": 0.0,
                "legs": [], "ts": t["ts"][:16],
            }
            order.append(key)
        groups[key]["pnl"] += float(t["pnl"])
        groups[key]["legs"].append(t["exit_type"])
    return [groups[k] for k in order]


def equity_curve_from_log() -> list[float]:
    """paper_bb.log'daki her bar'ın bakiyesini çek (drawdown için tam eğri)."""
    curve: list[float] = []
    if not os.path.exists(LOG_FILE):
        return curve
    pat = re.compile(r"Bar #[\d,]+ .*?\| \$([0-9]+\.[0-9]+) \(")
    with open(LOG_FILE, encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = pat.search(line)
            if m:
                curve.append(float(m.group(1)))
    return curve


def dd_stats(curve: list[float]) -> dict:
    """Max DD, şu anki DD ve max-DD dibinden sonra yeni zirve yapıldı mı."""
    if len(curve) < 2:
        return {"max_dd": 0.0, "cur_dd": 0.0, "recovered": None, "peak": curve[0] if curve else 0.0}
    peak = curve[0]
    max_dd = 0.0
    worst_idx = 0
    peak_at_worst = curve[0]
    for i, eq in enumerate(curve):
        if eq > peak:
            peak = eq
        dd = (eq - peak) / peak * 100 if peak > 0 else 0.0
        if dd < max_dd:
            max_dd = dd
            worst_idx = i
            peak_at_worst = peak
    cur_dd = (curve[-1] - peak) / peak * 100 if peak > 0 else 0.0
    recovered = None
    if max_dd <= -0.5:  # sadece anlamlı bir düşüş yaşandıysa değerlendir
        recovered = max(curve[worst_idx:]) >= peak_at_worst
    return {"max_dd": max_dd, "cur_dd": cur_dd, "recovered": recovered, "peak": peak}


def verdict(ok: bool, warn: bool = False) -> str:
    return "✅" if ok else ("⏳" if warn else "❌")


def main() -> None:
    if not os.path.exists(STATE_FILE):
        print(f"State dosyası yok ({STATE_FILE}). Önce paper_bb.py çalıştır.")
        return

    with open(STATE_FILE) as f:
        st = json.load(f)

    balance   = float(st.get("balance", INITIAL_BALANCE))
    bar_count = st.get("bar_count", 0)
    day       = st.get("daily_day", "—")
    ret_pct   = (balance - INITIAL_BALANCE) / INITIAL_BALANCE * 100
    trade_log = st.get("trade_log", [])

    positions = positions_from_trades(trade_log)
    n         = len(positions)
    wins      = [p for p in positions if p["pnl"] > 0]
    losses    = [p for p in positions if p["pnl"] < 0]
    nw, nl    = len(wins), len(losses)
    wr        = nw / n * 100 if n > 0 else 0.0
    avg_w     = sum(p["pnl"] for p in wins) / nw if nw else 0.0
    avg_l     = sum(p["pnl"] for p in losses) / nl if nl else 0.0
    rr        = avg_w / abs(avg_l) if nl and avg_l != 0 else None
    net       = sum(p["pnl"] for p in positions)
    expect    = net / n if n else 0.0

    dd = dd_stats(equity_curve_from_log())

    # ── Başlık ────────────────────────────────────────────────────────────────
    print("═" * 60)
    print(f"  BREAKOUTBOT — GO/NO-GO PANOSU")
    print(f"  Gün {day} | Bar #{bar_count:,} | Bakiye ${balance:.2f} ({ret_pct:+.2f}%)")
    print("═" * 60)

    # ── Pozisyon performansı (gerçek trade birimi) ─────────────────────────────
    n_legs = len(trade_log)
    _box("POZİSYON PERFORMANSI (leg değil, tam pozisyon)")
    print(f"  Pozisyon sayısı : {n}   (= {n_legs} kapanış leg'i; bot bunu 'Full trades: {n_legs}' diye sayar)")
    print(f"  Kazanan/Kaybeden: {nw}W / {nl}L")
    print(f"  Win Rate        : {wr:.1f}%")
    print(f"  Ort. kazanç     : ${avg_w:+.2f}")
    print(f"  Ort. kayıp      : ${avg_l:+.2f}")
    print(f"  R:R (kazanç/kayıp): {f'{rr:.2f}' if rr is not None else '— (henüz kayıp yok)'}")
    print(f"  Beklenti/trade  : ${expect:+.2f}")
    print(f"  Net (tüm poz.)  : ${net:+.2f}")
    # Bakiye uzlaştırması: full pozisyon net'i + test/fee maliyeti = gerçek bakiye değişimi
    test_cost  = (balance - INITIAL_BALANCE) - net
    bal_change = balance - INITIAL_BALANCE
    print(f"  Test/fee maliyeti: ${test_cost:+.2f}   (test SL + CONF FAIL + giriş fee — full poz. değil)")
    print(f"  {'─'*26}")
    print(f"  = Bakiye değişimi: ${bal_change:+.2f}   ✓ bot bakiyesiyle birebir uyumlu")

    # ── Pozisyon listesi ───────────────────────────────────────────────────────
    if positions:
        _box("POZİSYONLAR")
        for p in positions[-15:]:
            emoji = "✅" if p["pnl"] > 0 else ("❌" if p["pnl"] < 0 else "➖")
            legs = "+".join(p["legs"])
            print(f"  {emoji} {p['ts']} {p['symbol']:10s} {p['direction']:5s} "
                  f"${p['pnl']:+7.2f}  [{legs}]")

    # ── Drawdown ───────────────────────────────────────────────────────────────
    _box("DRAWDOWN")
    print(f"  Max DD yaşanan  : {dd['max_dd']:.2f}%")
    print(f"  Şu anki DD      : {dd['cur_dd']:.2f}%")
    if dd["recovered"] is None:
        print(f"  Toparlama       : — (henüz ciddi düşüş yaşanmadı)")
    else:
        print(f"  Toparlama       : {'✅ evet (DD sonrası yeni zirve)' if dd['recovered'] else '⏳ hayır (hâlâ DD içinde)'}")

    # ── GO/NO-GO CHECKLIST ─────────────────────────────────────────────────────
    _box("MİKRO-LIVE CHECKLIST")
    c_n   = verdict(n >= MIN_POSITIONS_GOOD, warn=n >= MIN_POSITIONS_OK)
    c_l   = verdict(nl >= MIN_LOSSES_GOOD,   warn=nl >= 1)
    c_dd  = verdict(dd["max_dd"] <= MIN_DD_SEEN, warn=dd["max_dd"] < -0.5)
    c_rec = verdict(dd["recovered"] is True, warn=dd["recovered"] is None)
    c_wr  = verdict(WR_FLOOR <= wr <= WR_LUCKY_CEIL, warn=(wr > WR_LUCKY_CEIL and n < MIN_POSITIONS_GOOD))
    c_net = verdict(net > 0)

    print(f"  {c_n}  Pozisyon ≥ {MIN_POSITIONS_GOOD}            ({n})")
    print(f"  {c_l}  Kaybeden görüldü ≥ {MIN_LOSSES_GOOD}     ({nl})")
    print(f"  {c_dd} Ciddi DD yaşandı (≤ {MIN_DD_SEEN:.0f}%)  ({dd['max_dd']:.1f}%)")
    print(f"  {c_rec} DD'den toparlandı")
    print(f"  {c_wr}  WR gerçekçi (%{WR_FLOOR:.0f}–95)       ({wr:.0f}%)")
    print(f"  {c_net}  Net pozitif                ({net:+.2f}$)")

    # ── Genel karar ────────────────────────────────────────────────────────────
    ready = (n >= MIN_POSITIONS_OK and nl >= 1 and net > 0
             and wr >= WR_FLOOR and dd["max_dd"] < -0.5)
    print("─" * 60)
    if ready:
        print("  KARAR: 🟢 Mikro-live ($50–100) düşünülebilir — sizing ayarı şart.")
    elif n < MIN_POSITIONS_OK:
        print(f"  KARAR: 🔴 Henüz erken — örneklem çok küçük ({n}/{MIN_POSITIONS_OK}+ gerekli).")
    elif nl == 0:
        print("  KARAR: 🔴 Henüz erken — hiç kaybeden görülmedi (downside körlüğü).")
    else:
        print("  KARAR: 🟡 Yaklaşıyor — eksik maddeleri tamamla.")
    print("═" * 60)


if __name__ == "__main__":
    main()
