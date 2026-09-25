# BreakoutBot

> Rejim-farkındalıklı **breakout momentum** kripto botu.
> 7/24 **simülasyon**: gerçek Binance Futures fiyat verisi, yerel sanal cüzdan,
> borsaya **hiç emir gitmiyor** (API anahtarı dahi yüklü değil). Emekli sürümler
> Binance Futures Testnet'e emir gönderiyordu; bu repoda "testnet" yalnızca onları anlatır.
> Canlı kayıt: **[breakoutbot.dev](https://breakoutbot.dev)**

Bu repo "kazanan botumu paylaşıyorum" demiyor. Hikâye daha dürüst:
**sistemi kurdum, ölçtüm, strateji kaybedince risk katmanı botu durdurdu,
postmortem yazdım, ölçüm hatalarını düzelttim ve her değişikliği önceden yazılmış
kurallarla deniyorum.** Olay analizi: [REPORT.md](REPORT.md) · deneyler:
[BENCHMARKS.md](BENCHMARKS.md), [experiments/DEFTER.md](experiments/DEFTER.md).

---

## Güncel durum (25 Eylül 2026)

| | |
|---|---|
| **Mod** | Saf simülasyon: gerçek fiyat, sanal cüzdan, emir yok |
| **Sistem** | Güncel sürüm (sitede "v2"), 29 Temmuz 2026'dan beri kayıtta |
| **Strateji** | 4h BTC rejim kapısı (long yalnız BULL'da) + 5 dakikalık breakout; short ve mean-reversion kolları kapalı |
| **Çıkış seti** | Faz 8, 27 Ağustos 2026'dan beri `config.py` varsayılanı: SL 2.25×ATR · TP1 3×ATR (kısmi kapanış yok) · TP2 6×ATR · trailing 3.75×ATR · 96 bar zaman aşımı |
| **Evren** | 4 coin: ADA, INJ, NEAR, UNI (22 Eylül 2026'dan beri; POL'un çıkarılma gerekçesi [BENCHMARKS.md](BENCHMARKS.md) Faz 9'da) |
| **Sonuç** | 25 Eylül itibarıyla 115 kapalı pozisyon, $1,000 → $946.68 (−%5.3), en kötü drawdown −%13.5. Güncel rakamlar: [breakoutbot.dev](https://breakoutbot.dev/without-ai.html) |
| **Aktif deney** | AI veto (25 Eylül 2026'dan beri): Claude Opus 5.5 her tam girişi değerlendiriyor ([ai_shadow/](ai_shadow/)). Site, kuralları tek başına izleyen hesapla veto edilen girişleri atlayan kopyayı yan yana yayınlıyor. Karar kuralları veriden önce yazıldı: 100 kapalı pozisyonda ilk kontrol, 300'de karar, aynı oranda rastgele vetoya karşı ([DEFTER.md](experiments/DEFTER.md) Tur 15). |

Canlı sonuç şu an backtest beklentisinin altında (bkz. [Doğrulama](#doğrulama));
aradaki fark açık bir araştırma sorusu.

---

## Nasıl çalışıyor

```
market_data.py (Binance 5m + 4h mumlar, yalnız kapanmış bar)
        │
indicators.py ──► math_engine.py          regime.py
(RSI, BB, ATR,    (Wave 11 composite      (BTC 200-MA:
 ADX, Hurst)       skor 0–100)             BULL/NEUTRAL/BEAR)
        │                 │                     │
        └────────┬────────┘─────────────────────┘
                 ▼
          strategy.py  ◄── mean_reversion.py (NEUTRAL rejimde MR, şu an KAPALI)
   (sinyal → karar,    ◄── short_sleeve.py  (test edildi, edge yok → KAPALI)
    rejim kapıları)
                 │
                 ▼
           paper_bb.py
   (yürütme durum makinesi, sanal cüzdan,
    risk limitleri, state kaydı, systemd altında 7/24)
                 │  salt-okur
     ┌───────────┼─────────────────────┐
     ▼           ▼                     ▼
  watch.py    ai_shadow/            monitoring/
  (terminal   (AI veto kaydı,       (15 dakikalık AI
   monitörü)   Tur 15)               gözlem raporu)
```

### Giriş mimarisi: Test → Confirm → Scale

Full pozisyona doğrudan girilmez; breakout önce küçük parayla yoklanır:

1. **TEST OPEN** — $20'lık probe pozisyonu (sinyal gerçek mi?)
2. **CONFIRMED / CONF FAIL** — 1 bar sonra fiyat/hacim/RSI onayı; geçemezse iptal
3. **FULL OPEN** — risk-bazlı boyutlandırılmış asıl pozisyon
4. **Çıkış** — ATR tabanlı SL (2.25×) / TP2 (6×) / trailing (3.75×) / zaman aşımı (96 bar)

> Çıkış geometrisi 2026-08-27'de genişletildi (bkz. [BENCHMARKS.md](BENCHMARKS.md)
> Faz 8). Kısmi çıkış kapatıldı: TP1'de %50 kapatmak kazananı ~0.7R'de sınırlarken
> kayıp tam 1R kalıyordu. Geniş stop, risk sabit dolar olduğu için **daha küçük**
> pozisyon demek — aynı riske daha az ücret. 665 günlük pencerede hard-stop sayısı
> 7'den 3'e indi.

Güncel kayıtta (29 Temmuz – 25 Eylül) 471 probe açıldı, 102'si onaylanıp tam
pozisyona dönüştü; probe katmanının toplam maliyeti −$13.48.

### Pozisyon boyutlandırma

Sabit notional değil, **sabit dolar riski**: her full pozisyon, SL'e gelirse
~`RISK_PER_TRADE_USD` ($10 ≈ bakiyenin %1'i) kaybedecek şekilde boyutlanır.
Volatil coin küçük, sakin coin büyük pozisyon alır. Emekli testnet sürümünde
ortalama kaybın $10.34 çıkması bu mekanizmanın sahada doğrulaması.

### Risk katmanları (kill-switch'ler)

| Limit | Eşik | Aksiyon |
|---|---|---|
| `DAILY_DD_LIMIT` | −%5 (gün içi) | Yeni giriş dondurulur |
| `EQUITY_THROTTLE_DD` | −%7 (peak'ten) | Pozisyon boyutu yarıya iner |
| `PEAK_DD_LIMIT` | −%15 (peak'ten) | **Hard stop** — bot kendini durdurur |
| `DAILY_SL_LIMIT` | 2 SL / sembol / gün | O sembol o gün dondurulur |
| `MAX_OPEN` | 2 | Aynı anda en fazla 2 full pozisyon |

(14 Haziran'da tetiklenen ilk build −%20 limitle koşuyordu; güncel kod −%15 —
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
  read-only · botu etkilemez · simülasyon (gerçek para değil)
```

> Yukarıdaki tablo `state_paper.sample.json` **örnek verisidir** (UI'yi bot olmadan
> göstermek için; Temmuz ayındaki 8 coinlik evreni ve eski çıkış yapısını yansıtır).
> Gerçek sonuçlar için → [breakoutbot.dev](https://breakoutbot.dev).

```bash
python watch.py                 # canlı, 5 sn'de bir yenilenir (gerçek state)
python watch.py --interval 2    # daha sık yenile
python watch.py --demo          # örnek veriyle
python watch.py --once          # tek kare (ekran görüntüsü / CI)
```

---

## Doğrulama

Her deney **aynı sabitlenmiş veride** koşar; metrik farkı = sadece kod farkı.
Bir parametre değişikliği ancak 240 günlük **ve** 665 günlük pencerede birden
baseline'ı geçerse alınır ([experiments/](experiments/),
[BENCHMARKS.md](BENCHMARKS.md)). Metrikler pozisyon başına sayılır
([metrics.py](metrics.py)); kısmi çıkışlar ayrı kazanç sayılmaz.

Güncel çıkış seti (Faz 8) backtest'te:

| Pencere | Sonuç | Max DD | Hard stop |
|---|---|---|---|
| 240 gün (Ağustos 2026'ya kadar) | $1,000 → $1,435 (PF 2.00) | −%6.14 | 0 |
| 665 gün (Ekim 2024 – Ağustos 2026) | $1,000 → $1,085 (PF 1.18) | −%37.6 | 3 (her birinden sonra yeniden başlatıldığı varsayılarak) |

Canlı kayıt (−%5.3, 115 pozisyon) şu an 240 günlük beklentinin altında. Backtest
ile canlı arasındaki fark ve seçim yanlılığı riski [DEFTER.md](experiments/DEFTER.md)'de
izleniyor; bu tablo bir getiri beklentisi değildir.

---

## Yerelde çalıştırma

```bash
pip install -r requirements.txt   # ccxt, pandas, numpy, requests

python paper_bb.py                    # simülasyon (anahtar gerekmez)
python paper_bb.py --resume           # kayıtlı state'ten devam
python paper_bb.py --status           # mevcut state özeti

python watch.py                       # canlı izleme ekranı (read-only)
python watch.py --demo                # örnek veriyle (bot gerekmez)

python bench.py fazN                  # faz backtest'i (bkz. BENCHMARKS.md)
./experiments/run_arm.sh <kol> <gün> ENV=VAL…   # deney kolu (bkz. experiments/DEFTER.md)
```

`--testnet` bayrağı ve [testnet_orders.py](testnet_orders.py) emekli sürümden
kalıyor; çalışan sistem kullanmıyor.

Canlı sistem bir VM'de `breakoutbot-test` systemd servisi olarak koşar. Testnet
emirleri gönderen eski `breakoutbot` (MAIN) servisi 22 Ağustos 2026'da emekliye
ayrıldı. Değişiklikler [deploy_test.sh](deploy_test.sh) ile dağıtılır (hedef sunucu
`BREAKOUTBOT_SERVER` env değişkeninden okunur). AI veto kaydı ve gözlem raporu
ayrı, salt-okur servislerdir (bkz. [ai_shadow/](ai_shadow/) ve
[monitoring/](monitoring/) README'leri).

---

## Tarihçe

| Dönem | Ne oldu | Sonuç |
|---|---|---|
| **31 May – 14 Haz 2026** · ilk sürüm, 23 coin, testnet emirleri | 45 full pozisyon | WR %33, profit factor 0.52 → **edge negatif**. −%20.3 peak drawdown'da **hard stop tetiklendi, bot kendini durdurdu.** |
| **Olay** · 14–15 Haz | Hard stop sonrası systemd servisi ~245 kez restart döngüsüne girdi (exit kodu `RestartPreventExitStatus` ile eşleşmedi) | Kanama yok (bot her seferinde yeniden durdu); servis ayarı düzeltildi. Postmortem: [REPORT.md §5](REPORT.md) |
| **15 Haz – 22 Ağu 2026** · rejim-farkındalıklı sürüm (sitede "v1"), testnet emirleri | State $1,000'a resetlendi, evren 8 coine indi. İlk 3.2 haftada **0 full pozisyon** (aşırı düzeltme). Sonra bir ölçüm hatası bulundu: TP1 ayrı bir kazanç olarak sayılıyordu (raporlanan WR %57.7, gerçek %43.6) | 22 Ağustos'ta $896.38'de (−%10.4) emekliye ayrıldı |
| **29 Tem 2026 – devam** · güncel sürüm (sitede "v2"), simülasyon | Test servisinde canlı A/B olarak başladı (geniş trailing, kısmi çıkış yok) ve 10 Ağustos'ta kazandı. 22 Ağustos'ta evren 5 coine indi, 27 Ağustos'ta Faz 8 çıkış seti alındı, 22 Eylül'de evren 4 coine indi | Canlı kayıt: [breakoutbot.dev](https://breakoutbot.dev) |

Emekli testnet sürümünün log'undan bir yaşam döngüsü (eski çıkış yapısı; TP1'de
yarım kapanış vardı):

```
TEST OPEN   JUPUSDT LONG @ 0.1877  | bal=$989.25
CONFIRMED   JUPUSDT LONG pnl=$+0.049
FULL OPEN   JUPUSDT LONG @ 0.1883  | notional=$900
CLOSE FULL  JUPUSDT [TP1]   entry=0.1883 exit=0.1901   pnl=$+3.95
CLOSE FULL  JUPUSDT [TRAIL] entry=0.1883 exit=0.18965  pnl=$+2.90
```

O dönemde 308 probe'un toplam maliyeti net −$7.65 oldu ve 228 zayıf sinyali full
pozisyona dönüşmeden eledi. Aynı dönemin Faz 4c backtest'inin raporladığı PF 3.29,
TP1'in ayrı kazanç sayılmasıyla şişmişti; düzeltilmiş havuzlanmış PF 1.95
([BENCHMARKS.md](BENCHMARKS.md) Faz 5).

---

## Repo haritası

| Dosya | Ne |
|---|---|
| [paper_bb.py](paper_bb.py) | Ana döngü: bar işleme, risk kapıları, state kaydı |
| [market_data.py](market_data.py) | Binance public 5m/4h mum çekimi (yalnız kapanmış bar) |
| [strategy.py](strategy.py) | Sinyal → karar; rejim kapıları, confirm mantığı |
| [math_engine.py](math_engine.py) | Wave 11 composite sinyal skoru (0–100) |
| [indicators.py](indicators.py) | RSI, Bollinger, ATR, ADX, Hurst vb. |
| [regime.py](regime.py) | BTC 200-MA rejim sınıflandırması (BULL / NEUTRAL / BEAR) |
| [metrics.py](metrics.py) | Pozisyon bazlı metrikler (expectancy, payoff, başabaş WR) — tek doğruluk kaynağı |
| [mean_reversion.py](mean_reversion.py) | Range piyasa MR kolu (şu an kapalı) |
| [short_sleeve.py](short_sleeve.py) | Short denemesi — backtest'te edge bulunamadı, kapalı ama belgeli |
| [config.py](config.py) | Tüm parametreler, tek dosyada, gerekçeli yorumlarla |
| [testnet_orders.py](testnet_orders.py) | **Emekli** testnet emir yürütücüsü — çalışan sistemde kullanılmıyor |
| [backtest.py](backtest.py) / [bench.py](bench.py) | Backtest replay motoru + sabit-veri faz kıyas harness'ı |
| [backtest_data.py](backtest_data.py) | Geçmiş OHLCV çekimi + pencere cache'i (deney tekrarlanabilirliği) |
| [backtest_report.py](backtest_report.py) | Backtest çıktısı: ilerleme, raporlar, koşu dump'ları |
| [experiments/](experiments/) | Deney kolları, `DEFTER.md` hipotez defteri, `ledger.py` |
| [ai_shadow/](ai_shadow/) | AI veto kaydı (Claude Opus 5.5, Tur 15) — bota dokunmayan ayrı servis |
| [monitoring/](monitoring/) | 15 dakikalık AI gözlem raporu servisi (salt-okur) |
| [watch.py](watch.py) | Canlı izleme ekranı — state'i okur (read-only) |
| [dashboard.py](dashboard.py) | Go/no-go kontrol panosu (tek seferlik checklist) |
| [REPORT.md](REPORT.md) | Testnet raporu + postmortem (31 May – 7 Tem 2026) |
| [BENCHMARKS.md](BENCHMARKS.md) | Faz faz backtest kıyası ve kararlar |
| [ROADMAP.md](ROADMAP.md) | Çok-rejim evrim tasarım dokümanı |

---

## Sırada ne var

- [x] Hard stop sonrası restart döngüsü düzeltildi (Haziran 2026, [REPORT.md §5](REPORT.md))
- [x] Sinyal hunisi telemetrisi ve pozisyon bazlı metrikler (Temmuz 2026, [BENCHMARKS.md](BENCHMARKS.md) Faz 5)
- [x] Çıkış yapısı deneyleri → Faz 8 çıkış seti (Ağustos 2026)
- [ ] AI veto deneyi: Faz 0 (25 Eylül – 2 Ekim 2026), ardından 100 ve 300 kapalı pozisyonda önceden yazılmış kontroller
- [ ] Walk-forward doğrulama (seçilen çıkış kolundaki seçim yanlılığını ölçmek için)
- [ ] Probe katmanının gerekli olup olmadığını ölçmek

---

## Feragat

Hiçbir rakam gerçek parayla üretilmemiştir. Çalışan bot **saf simülasyondur** —
gerçek fiyat verisi, yerel sanal cüzdan, borsaya giden emir yok. Emekli sürümler
Binance Futures **Testnet**'e gerçek emir gönderiyordu (yine sahte bakiye); bu
ayrım önemli, çünkü emir gönderen bir sistemin slipaj ve icra maliyeti simülasyonda
görünmez. Hangi rakamın hangisinden geldiği `BENCHMARKS.md`'de belirtilir.

Bu proje bir araştırma/mühendislik çalışmasıdır; **yatırım tavsiyesi değildir** ve
gerçek parayla kullanım için tasarlanmamıştır.
