"""Plot the observed NEAR price path and actual simulated full positions."""
import json
from pathlib import Path

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

plt.switch_backend("Agg")
ROOT = Path(__file__).resolve().parent
df = pd.read_csv(ROOT / "near_5m.csv")
# Binance timestamp denotes bar open; the bot logs the processing/close time.
df["time"] = pd.to_datetime(df["timestamp"], utc=True) + pd.Timedelta(minutes=5)
trades = json.loads((ROOT / "paired_trades.json").read_text())
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
fig, axes = plt.subplots(2, 1, figsize=(12, 8.4), layout="constrained")
fig.set_facecolor("#f8fafc")
fig.suptitle("NEARUSDT: Aynı yönde işlemler, arada gerçek geri çekilmeler", fontsize=17, weight="bold")
windows = [
    ("2026-09-04 05:00", "2026-09-04 23:55", "4 Eylül · üç ayrı pozisyon · toplam +$29,50"),
    ("2026-09-06 04:00", "2026-09-07 03:00", "6 Eylül girişleri · üçüncü işlem 7 Eylül'de kapanıyor · toplam +$28,13"),
]
for ax, (start, end, title) in zip(axes, windows):
    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    view = df[(df.time >= start) & (df.time <= end)]
    ax.plot(view.time, view.close, color="#355a80", lw=1.6, label="5 dk kapanış fiyatı")
    ax.fill_between(view.time, view.low, view.high, color="#355a80", alpha=.10)
    subset = [p for p in trades if start <= pd.Timestamp(p["start"]) <= end]
    for i, p in enumerate(subset, 1):
        a, b = pd.Timestamp(p["start"]), pd.Timestamp(p["end"])
        color = "#087f5b" if p["net"] > 0 else "#b42318"
        ax.axvspan(a, b, color=color, alpha=.055)
        ax.scatter(a, p["entry"], marker="^", s=65, color="#087f5b", zorder=5)
        ax.scatter(b, p["exit"], marker="X", s=65, color=color, zorder=5)
        ax.annotate(f"Giriş {i}\n{p['entry']:.3f}", (a, p["entry"]), xytext=(0, -36),
                    textcoords="offset points", ha="center", fontsize=9, color="#09543e")
        label = f"{p['reason']} {p['net']:+.2f}$"
        ax.annotate(label, (b, p["exit"]), xytext=(0, 15),
                    textcoords="offset points", ha="center", fontsize=9, color=color,
                    bbox={"boxstyle": "round,pad=.25", "facecolor": "white", "edgecolor": "none", "alpha": .9})
    ax.set_title(title, loc="left", fontsize=12, pad=15)
    ax.set_ylabel("Fiyat (USDT)")
    ax.set_xlabel("UTC · Türkiye saati için +3 saat")
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=start.tz))
    ax.grid(axis="y", alpha=.2)
    ax.spines[["top", "right"]].set_visible(False)
    ax.margins(y=.24, x=.03)
fig.supxlabel("▲ Giriş   ✕ Çıkış   |   PnL: tam pozisyonun giriş ve çıkış maliyetleri dahil; küçük deneme işlemleri hariç.\nKaynak: çalışan simülasyonun kayıtları + Binance USDT vadeli piyasa 5 dk mumları.", fontsize=9, color="#475569")
fig.savefig(ROOT / "near_trade_path.png", dpi=170, facecolor=fig.get_facecolor())
fig.savefig(ROOT / "near_trade_path.svg", facecolor=fig.get_facecolor())
