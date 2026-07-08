# BreakoutBot

> Rejim-farkındalıklı **breakout momentum + mean-reversion** kripto botu.
> Binance Futures **Testnet** üzerinde 7/24 paper-trading — gerçek para yönetmez.

Bu repo "kazanan botumu paylaşıyorum" demiyor. Hikâye daha dürüst:
**sistemi kurdum, ölçtüm, strateji kaybedince risk katmanı botu durdurdu,
postmortem yazdım, ikinci sürümü kalibre ediyorum.** Tam sonuçlar ve olay
analizi için → **[REPORT.md](REPORT.md)**.

---

## Sonuçlar — dürüst zaman çizelgesi

| Dönem | Ne oldu | Sonuç |
|---|---|---|
| **v1** · 31 May – 14 Haz 2026 | 23 sembol, canlı testnet. 45 full pozisyon. | WR %33, profit factor 0.52 → **edge negatif**. −%20.3 peak drawdown'da **hard stop tetiklendi, bot kendini durdurdu.** |
| **Olay** · 14–15 Haz | Hard stop sonrası systemd servisi ~245 kez restart döngüsüne girdi (exit kodu `RestartPreventExitStatus` ile eşleşmedi). | Kanama yok (bot her seferinde yeniden durdu) ama servis "flapping" kaldı. Postmortem: [REPORT.md §5](REPORT.md). |
| **v2** · 15 Haz – devam | State $1000'a resetlendi; filtreler sıkılaştı, evren 8 sembole indi (Faz 4c). | 3.2 haftada **0 full pozisyon** — zarar yok ama sinyal de yok (overcorrection). Sırada: sinyal hunisi telemetrisi. |

Kritik nokta: **risk sistemi tam olarak tasarlandığı gibi çalıştı.** Kaybeden
stratejiyi insan müdahalesi olmadan kesti, state'i kaydetti, pozisyon açmayı bıraktı.

---

## Nasıl çalışıyor

```
ccxt (Binance 5m klines)
        │
indicators.py ──► math_engine.py          regime.py
(RSI, BB, ATR,    (Wave 11 composite      (BTC 200-MA:
 ADX, Hurst)       skor 0–100)             BULL/NEUTRAL/BEAR)
        │                 │                     │
        └────────┬────────┘─────────────────────┘
                 ▼
          strategy.py  ◄── mean_reversion.py (NEUTRAL rejimde MR sleeve)
   (sinyal → karar,    ◄── short_sleeve.py  (test edildi, edge yok → KAPALI)
    rejim gate'leri)
                 │
                 ▼
           paper_bb.py
   (execution state machine, testnet emirleri,
    risk limitleri, state persist, systemd altında 7/24)
```

### Giriş mimarisi: Test → Confirm → Scale

Full pozisyona doğrudan girilmez; breakout önce küçük parayla yoklanır:

1. **TEST OPEN** — $20'lık probe pozisyonu (sinyal gerçek mi?)
2. **CONFIRMED / CONF FAIL** — 1 bar sonra fiyat/hacim/RSI onayı; geçemezse iptal
3. **FULL OPEN** — risk-bazlı boyutlandırılmış asıl pozisyon
4. **Çıkış** — ATR tabanlı SL / TP1 (%50 kapat + breakeven) / TP2 / trailing / timeout

Gerçek testnet log'undan bir yaşam döngüsü:

```
TEST OPEN   JUPUSDT LONG @ 0.1877  | bal=$989.25
CONFIRMED   JUPUSDT LONG pnl=$+0.049
FULL OPEN   JUPUSDT LONG @ 0.1883  | notional=$900
CLOSE FULL  JUPUSDT [TP1]   entry=0.1883 exit=0.1901   pnl=$+3.95
CLOSE FULL  JUPUSDT [TRAIL] entry=0.1883 exit=0.18965  pnl=$+2.90
```

Bu katman ucuz bir sigorta: v1'de 308 probe'un toplam maliyeti net **−$7.65** oldu
ve 228 zayıf sinyali full pozisyona dönüşmeden eledi.

### Pozisyon boyutlandırma

Sabit notional değil, **sabit dolar riski**: her full pozisyon, SL'e gelirse
~`RISK_PER_TRADE_USD` ($10 ≈ bakiyenin %1'i) kaybedecek şekilde boyutlanır.
Volatil coin küçük, sakin coin büyük pozisyon alır. v1'in ortalama kaybının
$10.34 çıkması bu mekanizmanın sahada doğrulaması.

### Risk katmanları (kill-switch'ler)

| Limit | Eşik | Aksiyon |
|---|---|---|
| `DAILY_DD_LIMIT` | −%5 (gün içi) | Yeni giriş dondurulur |
| `EQUITY_THROTTLE_DD` | −%7 (peak'ten) | Pozisyon boyutu yarıya iner |
| `PEAK_DD_LIMIT` | −%15 (peak'ten) | **Hard stop** — bot kendini durdurur |
| `DAILY_SL_LIMIT` | 2 SL / sembol / gün | O sembol o gün dondurulur |
| `MAX_OPEN` | 2 | Aynı anda en fazla 2 full pozisyon |

(14 Haziran'da tetiklenen v1 build'i −%20 limitle koşuyordu; güncel kod −%15 —
bkz. [config.py](config.py).)

---

## Canlı izleme (`watch.py`)

Bot çalışırken ikinci bir terminalde açtığın, birkaç saniyede bir yenilenen bir
monitör. `state_paper.json`'u **sadece okur** — çalışan bota dokunmaz. Drawdown'ın
throttle/hard-stop eşiklerine ne kadar kaldığını, açık pozisyonları, rejim
tablosunu ve son trade'leri tek ekranda gösterir.

```console
$ python watch.py --demo          # örnek veriyle dene (bot gerekmez)

══════════════════════════════════════════════════════════════════
  BREAKOUTBOT — LIVE WATCH   ◆ DEMO
  2026-07-08 10:28:36 UTC   ·   Gün 2026-07-08   ·   Bar #6,821
══════════════════════════════════════════════════════════════════
  Bakiye  $978.42   Getiri -2.16%   zirve $1,000.00

── DRAWDOWN ──────────────────────────────────────────────────────
  DD -2.16%  ███████··············┊························
  0%      throttle -7%                                hard -15%
  Throttle'a kalan: $48.42   Hard-stop'a: $128.42

── BUGÜN ─────────────────────────────────────────────────────────
  Günlük P&L $-6.68 (-0.68%)   Giriş: açık   SL bugün: 1
  günlük freeze eşiği -5%

── AÇIK POZİSYONLAR ──────────────────────────────────────────────
  SOLUSDT   TRAIL LONG  giriş 148.2  SL 149.4  TP1✓152.1  TP2 158  $620 · 22 bar
  UNIUSDT   PROBE LONG  giriş 9.905  SL 9.71   (yoklama) · 1 bar
  Full: 1/2

── REJİM (BTC 200-MA) ────────────────────────────────────────────
    SOL:BULL     UNI:BULL    AVAX:NEUT    NEAR:NEUT
    ADA:NEUT     INJ:BEAR     POL:BEAR     LDO:NEUT

── SON TRADE'LER ─────────────────────────────────────────────────
  ▲ 07-08 06:20 NEARUSDT  MR    TP1    $+4.05
  ▼ 07-08 08:55 POLUSDT   LONG  SL     $-10.02
  ▲ 07-08 10:30 UNIUSDT   LONG  TP1    $+6.02

── OTURUM ────────────────────────────────────────────────────────
  Pozisyon 5  ·  WR 60% (3W/2L)  ·  Net $+0.64  ·  PF 1.03
══════════════════════════════════════════════════════════════════
  read-only · botu etkilemez · testnet (gerçek para değil)
```

> Yukarıdaki tablo `state_paper.sample.json` **örnek verisidir** (UI'yi bot olmadan
> göstermek için). Gerçek testnet sonuçları için → [REPORT.md](REPORT.md).

```bash
python watch.py                 # canlı, 5 sn'de bir yenilenir (gerçek state)
python watch.py --interval 2    # daha sık yenile
python watch.py --demo          # örnek veriyle
python watch.py --once          # tek kare (ekran görüntüsü / CI)
```

---

## Backtest altyapısı

Her strateji fazı **aynı sabitlenmiş veride** koşar; metrik farkı = sadece kod farkı.
Faz kıyas tablosu ve metodoloji: **[BENCHMARKS.md](BENCHMARKS.md)**.

Deploy'daki sürüm (Faz 4c, 8 sembol): 90 günde 8/8 kârlı, PF 3.29, MaxDD −%3.25;
240 günlük derin bear'da −$16.42/ay, MaxDD −%9.66 (savunuldu, hard stop yok).
v2'nin canlıda 0 trade üretmesi ile bu backtest beklentisi arasındaki fark,
şu anki ana araştırma sorusu (bkz. [REPORT.md §6](REPORT.md)).

---

## Çalıştırma

```bash
pip install -r requirements.txt   # ccxt, pandas, numpy, requests

# Testnet emirleri için API anahtarları (dosya .gitignore'da, asla commit'lenmez):
# secrets_local.py:
#   TESTNET_API_KEY    = "..."
#   TESTNET_API_SECRET = "..."

python paper_bb.py                    # lokal simülasyon (anahtar gerekmez)
python paper_bb.py --testnet          # Binance Futures Testnet'te gerçek emir
python paper_bb.py --testnet --resume # kayıtlı state'ten devam
python paper_bb.py --status           # mevcut state özeti

python watch.py                       # canlı izleme ekranı (read-only)
python watch.py --demo                # örnek veriyle (bot gerekmez)

python bench.py fazN                  # faz backtest'i (bkz. BENCHMARKS.md)
```

Production, bir VM üzerinde iki systemd servisi olarak koşar: `breakoutbot`
(MAIN, testnet emirleri) ve `breakoutbot-test` (TEST, yeni mantık için sandbox).
Doğrulanan değişiklikler TEST'te koşturulup tüm ağaç olarak MAIN'e taşınır
([deploy_test.sh](deploy_test.sh) — hedef sunucu `BREAKOUTBOT_SERVER` env
değişkeninden okunur).

---

## Repo haritası

| Dosya | Ne |
|---|---|
| [paper_bb.py](paper_bb.py) | Ana döngü: execution state machine, testnet emirleri, risk limitleri, state persist |
| [strategy.py](strategy.py) | Sinyal → karar; rejim gate'leri, confirm mantığı |
| [math_engine.py](math_engine.py) | Wave 11 composite sinyal skoru (0–100) |
| [indicators.py](indicators.py) | RSI, Bollinger, ATR, ADX, Hurst vb. |
| [regime.py](regime.py) | BTC 200-MA rejim sınıflandırması (BULL / NEUTRAL / BEAR) |
| [mean_reversion.py](mean_reversion.py) | Range piyasa MR sleeve (NEUTRAL rejimde) |
| [short_sleeve.py](short_sleeve.py) | Short denemesi — backtest'te edge bulunamadı, kapalı ama belgeli |
| [config.py](config.py) | Tüm parametreler, tek dosyada, gerekçeli yorumlarla |
| [backtest.py](backtest.py) / [bench.py](bench.py) | Backtest motoru + sabit-veri faz kıyas harness'ı |
| [watch.py](watch.py) | Canlı izleme ekranı — state'i okur, drawdown/pozisyon/rejim/trade'leri yeniler (read-only) |
| [dashboard.py](dashboard.py) | Go/no-go kontrol panosu (tek seferlik checklist) |
| [REPORT.md](REPORT.md) | **Detaylı testnet raporu + postmortem (31 May – 7 Tem 2026)** |
| [BENCHMARKS.md](BENCHMARKS.md) | Faz-faz backtest kıyası |
| [ROADMAP.md](ROADMAP.md) | Çok-rejim evrim tasarım dokümanı |

---

## Durum ve sırada ne var

- [x] v1 canlı test → negatif edge ölçüldü, hard stop çalıştı
- [x] Postmortem: hard stop + systemd restart döngüsü ([REPORT.md §5](REPORT.md))
- [ ] **P1** — hard stop çıkışını deterministik yap (restart döngüsü fix'i)
- [ ] **P2** — sinyal hunisi telemetrisi (`scanned → regime_pass → probe → confirmed → full` sayaçları): v2'nin nerede tıkandığını ölç
- [ ] **P3** — telemetri verisiyle hedefli tek kalibrasyon değişikliği → TEST serviste doğrula → promote

---

## Feragat

Tüm rakamlar **Binance Futures Testnet** (sahte bakiye) üzerindendir. Bu proje
bir araştırma/mühendislik çalışmasıdır; **yatırım tavsiyesi değildir** ve gerçek
parayla kullanım için tasarlanmamıştır.
