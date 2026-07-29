# 📊 BENCHMARKS — Faz-Faz Backtest Kıyası

Her faz **AYNI sabit veride** koşar → metrik değişimi = SADECE kod değişimi
(veri penceresi sabit, run-to-run drift yok).

```bash
python bench.py fazN > backtests/fazN.txt   # koş + kaydet
rm -rf backtests/data/                       # pencereyi yenile (yeni veri çeker)
BT_NO_REGIME=1 python bench.py faz0          # ablation: rejim gate KAPALI
```

## Standart test bench
- **Tokenlar:** SOLUSDT, INJUSDT, FETUSDT
- **Süre:** 90 gün (5m bar)
- **Sabit veri:** `backtests/data/*.pkl` (ilk koşuda çekildi, sonra hep aynı)
- **Pencere:** ~2026-03 → 2026-06 (90d, bugünde biten)

---

## 🏁 Faz kıyas tablosu (combined, 3 coin)

| Faz | Ne eklendi | Kârlı | Avg WR | Avg PF | Worst DD | Aylık ($1000) | Dosya |
|-----|-----------|:---:|:---:|:---:|:---:|:---:|---|
| **Faz 0** | baseline (rejim yok / throttle yok) | 3/3 | 73.1% | 1.50 | −7.73% | **$+13.46** | `backtests/faz0.txt` |
| **Faz 1** | directional regime gate (BULL %100 · NEUTRAL+BEAR %35) | 3/3 | 73.1% | 2.09 | −3.96% | $+25.13 | `backtests/faz1.txt` |
| **Faz 2** | + range mean-reversion sleeve (NEUTRAL'de, $5 risk) | 3/3 | 71.7% | **2.10** | **−3.83%** | **$+31.55** | `backtests/faz2.txt` |
| **Faz 3** | short sleeve TEST EDİLDİ → edge yok → **KAPALI** (sistem=Faz 2) | 3/3 | 71.7% | 2.10 | −3.83% | $+31.55 | `backtests/faz3.txt` |

> ✅ **Deploy edilecek sistem = Faz 2/3 = long momentum + range MR = $+31.55/ay.**
> Faz 3 short sleeve kodu yazıldı ama doğrulamada edge bulunamadı → kapatıldı (aşağı bak).

### 🔬 Faz 0 → Faz 1 — regime gate'in net katkısı
Aynı veri, **aynı girişler** (WR birebir aynı = aynı trade'ler tetiklendi), tek fark
pozisyon **boyutu**:

- **Aylık: $13.46 → $25.13 = +%87** 🚀 (neredeyse 2×)
- **Avg PF: 1.50 → 2.09** (+0.59)
- **Worst MaxDD: −7.73% → −3.96%** (neredeyse yarıya indi)
- **FET: PF 1.21 ❌ → 1.31 ✅** (gate'i geçer hale geldi)

> **Neden işe yaradı:** NEUTRAL/BEAR'daki long'lar zayıf/negatif edge'liydi.
> %35'e kısınca dragları kalktı, BULL kazananlar tam boyutta kaldı → hem getiri ↑
> hem drawdown ↓. WR değişmedi çünkü throttle giriş/çıkışı değil yalnız **boyutu**
> değiştirir.

### 🔬 Faz 1 → Faz 2 — mean-reversion sleeve'in net katkısı
Momentum sleeve aynı kaldı; NEUTRAL + düşük-ADX'te oversold dip alıp ortalamaya
satan ikinci (korelasyonsuz) sleeve eklendi ($5 risk):

- **Aylık: $25.13 → $31.55 = +%26** 🚀
- **Worst MaxDD: −3.96% → −3.83%** (artmadı, hatta hafif düştü)
- **Avg PF: 2.09 → 2.10** (korundu)
- **Avg WR: 73.1% → 71.7%** (−1.4pp — MR biraz daha düşük-WR trade ekler, beklenen)
- **MR trade:** SOL 3 (WR 67%) · INJ 5 (WR 80%) · FET 12 (WR 50%) = 20 trade, R:R ~2:1

> **Neden işe yaradı:** Momentum yatay piyasada chop'a takılıyordu; MR tam o boşlukta
> (NEUTRAL + ADX<20) oversold bounce'ları topladı. Farklı tez = korelasyonsuz getiri
> → DD'yi artırmadan +%26. FET'in MR WR'si %50 ama R:R 2:1 olduğu için yine net +.

### 🔬 Faz 3 — short sleeve: test edildi, edge YOK, kapatıldı
Ayrı, sıkı price-action short engine yazıldı (breakdown + bearish yapı + hacim
konviksiyonu; OI proxy'si). MathEngine long-biased olduğu için (0 short / 131 trade)
shortlar ayrı motordan geliyor. **3 aşamalı test sonucu: net-negatif.**

| Test | Pencere | Bear % | Short sonucu |
|---|---|---|---|
| İlk (NEUTRAL+BEAR short) | 90g | ~7% | **−$199/ay** felaket (NEUTRAL'da 112-239 over-fire, squeeze) |
| BEAR-only + sıkılaştırma | 90g | ~7% | $+12.55/ay (Faz 2'nin $19 altında — shortlar hâlâ zarar) |
| **Bear validasyon** | **240g** | **35-49%** | **net −$158/3coin** (SOL +$12, INJ −$102, FET −$68; WR %42-57, PF<1) |

**Kök neden:** Kripto bear'ları sert **short-squeeze** ralliları içerir. Breakdown-momentum
short'u (long edge'in simetriği) bu sıçramalarda stop oluyor. Long tezinin aynısı short'ta
TUTMUYOR. Kârlı short farklı mantık ister (başarısız-ralli fade, funding/likidasyon sinyali) —
breakdown momentum değil.

**Karar:** `SHORT_ENABLED=False`. Kod korundu (gelecek redesign + özel çalışma için).
Deploy = Faz 2 (long + MR). Bu, "doğrulanmamış/zararlı değişikliği deploy etme" disiplinine uyar.

---

## 📋 Per-coin detay

### Faz 0 — baseline (rejim gate KAPALI)
| Coin | Trades | WR | PF | Return | MaxDD | Rejim B/N/Be |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| SOL | 14 | 78.6% | 1.96 | +1.76% | −1.59% | 33/52/14 |
| INJ | 50 | 72.0% | 1.33 | +1.70% | −4.13% | 51/48/1 |
| FET | 67 | 68.7% | 1.21 | +0.58% | −7.73% | 22/71/7 |

### Faz 1 — directional regime gate
| Coin | Trades | WR | PF | Return | MaxDD | Rejim B/N/Be |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| SOL | 14 | 78.6% | **3.08** | +1.53% | **−0.66%** | 33/52/14 |
| INJ | 50 | 72.0% | **1.87** | **+5.25%** | **−1.64%** | 51/48/1 |
| FET | 67 | 68.7% | 1.31 | +0.76% | −3.96% | 22/71/7 |

### Faz 2 — + range mean-reversion sleeve
| Coin | Mom | MR (WR) | Comb.WR | PF | Return | MaxDD |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| SOL | 14 | 3 (67%) | 76.5% | 2.92 | **+1.64%** | −0.89% |
| INJ | 50 | 5 (80%) | 72.7% | 2.04 | **+6.73%** | −1.64% |
| FET | 67 | 12 (50%) | 65.8% | 1.34 | **+1.10%** | −3.83% |

→ MR 20 trade ekledi (R:R ~2:1); getiri Faz 1'e göre coin başına ↑, DD ~sabit.

---

## 🔬 Faz 4 — 23-coin gerçeği + rejim savunması + curation

**3-coin bench yanıltıcıydı.** SOL/INJ/FET tesadüfen en iyi tabakaydı. Tüm 23 coin'de
Faz 2 sistemi **6-7/23 kârlı, −$152/ay** (BTC PF 0.19, ETH 0.62 bile kaybediyor) —
çünkü 90g penceresi bear/chop ve momentum-long edge'i çoğu altcoin'de bu ortamda yok.

### Rejim savunma sweep'i (23 coin, 90g)
Longları rejimle kıstıkça kayıp monoton azaldı → **NEUTRAL/BEAR longları kesin kaybeden:**

| Varyant | Long N/BEAR | Kârlı | PF | Aylık |
|---|---|---|---|---|
| Faz 2 | 0.35 / 0.35 | 7/23 | 1.09 | −$151.81 |
| 4a | 0.35 / 0 | 7/23 | 1.15 | −$148.74 |
| 4b | 0.20 / 0 | 8/23 | 1.26 | −$117.23 |
| **4c BULL-only** | **0 / 0** | 9/23 | **1.69** | **−$75.23** |

**Faz 4c bake-in edildi** (`LONG_SIZE_MULT` NEUTRAL=0, BEAR=0): momentum sadece BULL'da,
MR chop'ta, bear'da hiçbir long. 240g derin bear'da da doğrulandı:
−$36.88→**−$20.37**/ay, MaxDD −20.5%→**−15.3%** (hard-stop'u önler).

### Coin curation (90g + 240g'de sağlam olanlar)
4c bile 23 coin'de −$75 (kötü coinler sürüklüyor). Her iki pencerede test → **8 coin** seçildi:

| Tutuldu (8) | 90g | 240g | Çıkarıldı (örnek) |
|---|---|---|---|
| INJ POL LDO | + | **+** | FET (240g −13%/DD−15%, bear-kırılgan) |
| SOL AVAX NEAR UNI ADA | + | kontrollü | BTC/ETH (PF 0.19/0.62), WIF/STX/AAVE/JUP/PENDLE/SUI/APT |

### ✅ FINAL DEPLOY SİSTEMİ = Faz 4c + curated 8
| Pencere | Kârlı | PF | MaxDD | Aylık |
|---|---|---|---|---|
| **90g (recent/chop)** | **8/8** | **3.29** | **−3.25%** | **+$55.51** |
| **240g (derin bear)** | 3/8 | 1.21 | −9.66% | −$16.42 (savunmalı) |

> Normal piyasada kazanır (8/8, PF 3.29), derin bear'da küçük kontrollü kayıpla savunur
> (blow-up yok). `backtests/final8_90d.txt` + `final8_240d.txt`.
>
> ⚠️ **Not:** +$55.51 = 8 coin × bağımsız $1000 sim (= $8000 sermaye varsayımı). Gerçek
> bot TEK $1000 hesapta MAX_OPEN=2 ile çalışır → canlı $/ay farklı olacak (per-coin edge
> doğrulandı, tek-hesap davranışı test servisinde görülecek).

---

## 🛡️ Faz 5 — Hardening (canlı deploy + AI ofis çift-model denetimi)

Sistem `breakoutbot-test` 2. servisine deploy edildi (sim mode, **eski bota dokunmadan**,
iki bot paralel canlı). Deploy sonrası **AI ofisi** kuruldu (Claude=Müdür + Gemini=Risk
Denetçisi, dosya-tabanlı `office/`) ve canlı `paper_bb.py`'yi denetledi. **2 turlu
çift-model review 5 gerçek bug buldu → hepsi düzeltildi.**

### Bulunan + düzeltilen 5 bug
| # | Bug | Tur | Düzeltme |
|---|-----|-----|----------|
| 1 | Testnet emri sabit `$900` (FULL_SIZE_USD×LEVERAGE) → dinamik risk-sizing + rejim frenini yok sayıyordu | Gemini-1 | `notional = s.full_notional` (dinamik, throttle+rejim dahil) |
| 2 | MR sleeve borsaya HİÇ emir göndermiyordu (sadece sanal bakiye) | Gemini-1 | `if tn:` open/close eklendi (LONG-only) |
| 4 | DD limitleri config'de −%5/−%20, throttle yok (plan −%3/−%7/−%15 ile çelişik) | Gemini-1 | risk modeli yenilendi (aşağı) |
| R1 | TEST→confirm arası BULL→NEUTRAL dönerse `size_mult=0` → **$0-notional** hayalet pozisyon / borsa reddi | Gemini-2 | `strategy.py`: `size_mult>0` guard (FULL açma, reset+cooldown) |
| R2 | Momentum-long + MR-long aynı sembolde → borsa netler → MR kapanışı momentum'u da kapatır | Gemini-2 | momentum aktifken o sembolde MR açılmaz (`mr_block`) |

> **R1 backtest'le kanıtlandı:** guard $0-notional hayalet trade'leri temizledi →
> SOL 90g **WR %35→%86, bakiye AYNI** ($1015.16, MaxDD −%0.42) = o trade'ler gerçekten
> anlamsızdı. #1/#2 testnet yolları sim'de uyur, testnet terfisinde aktifleşir.

### Yeni risk modeli (DD circuit breakers)
| Eşik | Eski | Yeni | Etki |
|---|:---:|:---:|---|
| Günlük freeze | −%5 | **−%5** | yeni girişleri o gün durdur (8-coin için gevşek tutuldu) |
| Equity throttle | (yok) | **−%7** | tepe DD'de pozisyon boyutu ×0.5 (momentum + MR) |
| Hard-stop | −%20 | **−%15** | tepe DD'de stratejiyi durdur |

> **CEO kararı:** günlük −%5 (Gemini, −%3'ün 8-coin kitapta erken-donma yaratacağını
> belirtti). Throttle (−%7) + hard-stop (−%15) sıkı; günlük fren gevşek.

### Doğrulama + canlı durum
- Syntax/import OK · SOL 90g backtest temiz (DEPLOY verdict) · regresyon yok
- `breakoutbot-test`'e deploy + restart (`--resume`, state korundu, bar #203→devam)
- `grep size_factor=5` + yeni PID `42579` = yeni kod canlı · `local simulation` · $1000
- **Ertelendi** (testnet terfisi öncesi, exec-audit rolüne): sessiz testnet hata yönetimi
  (order fail → sim yine işler); throttle'da notional MIN floor altına inebilir (borsa min üstü, OK)

> 🏢 AI ofisi: `office/` (README + org_chart + roles/risk·research·market_watch + ask.sh + log/).
> Çift-model en değerli nokta = **kod denetimi**: Claude'un "çalışıyor" dediği kodda Gemini 5 bug buldu.

---

## ⚠️ Notlar / uyarılar
- **Rejim ısınması:** ilk ~35 gün 200×4h-MA dolana kadar NEUTRAL default (throttle)
  → Faz 1 lehine hafif konservatif yanlılık (yani gerçek katkı muhtemelen ≥ ölçülen).
- **Sample:** 131 trade / 3 coin. İstatistiksel güç için (hedef 200+) Faz 2/3'te daha
  çok coin + trade gelecek.
- **Backtest ≠ canlı:** slippage/latency/funding farkı var. Aynı sunucuda 2. servis
  (breakoutbot-test) ile canlı A/B bunu doğrulayacak.
- **WR düşüşü (Faz 4c):** BULL-only longlar az ateşler → per-coin WR avg düşer (~37%)
  ama PF yükselir (3.29) = düşük-WR/yüksek-R:R profili (CTA-tarzı, research'le uyumlu).
- **Curation overfit riski:** 8 coin son 2 pencereye göre seçildi. Piyasa rejimi
  değişince (BULL) `_FULL_UNIVERSE`'e doğru yeniden-doğrula + genişlet.

---

## ⏭️ Sıradaki adım — canlı A/B izleme + testnet terfi checklist
Tüm fazlar (0→5) bitti. Sistem `breakoutbot-test`'te **canlı** (sim mode), eski bot
paralel çalışıyor. Kod diske kayıtlı (commit EDİLMEDİ).

1. **Canlı A/B izle** (gün/hafta): eski bot vs yeni regime-aware bot.
   - Şu an ikisi de flat (BULL 0/8 = bear/nötr piyasa → doğru savunma, işlem yok).
   - BULL rejimi gelince ilk gerçek trade'ler → backtest WR/PF/DD ile kıyas.
   - İzleme: 2 terminal → `journalctl -u breakoutbot -f` · `journalctl -u breakoutbot-test -f`
2. **Testnet terfisi öncesi checklist** (exec-audit rolü — Faz 5'te ertelendi):
   - Testnet emir hata yönetimi (order fail → state desync önle)
   - sim↔cüzdan birebir kıyas (shadow mode 1-2 hafta)
   - netting guard (R2) + dinamik sizing (#1) + MR execution (#2) canlı doğrulaması
3. **Sample biriktir** → 200+ trade (Davey eşiği) sonrası ölçek / Phase B (funding sleeve) kararı.

**Hızlı referans:** deploy sistemi = `config.TOKENS` (8 coin) + `regime.py` 4c + `SHORT_ENABLED=False`
+ Faz 5 hardening (dinamik sizing · MR exec · −%5/−%7/−%15 DD · $0-notional guard · netting guard).
Doğrulama: `BENCH_ALL=1 python bench.py x` → 8/8. AI ofisi: `office/` (Claude+Gemini çift-model).

---

## Faz 5 — Çıkış yapısı deneyleri (29 Tem 2026)

**Bağlam:** Canlı bot 44 günde −%6.5. Teşhis: (a) WR metriği şişikti — TP1 ayrı kayıt
olarak loglanıyor ve TP1 sonrası bacak yapısal olarak kaybedemiyor, yani her kazanan
pozisyon iki kazanan kayıt üretiyordu (canlıda WR %57.7 → gerçek %43.6); (b) trail
1.5×ATR kazananı boğuyordu.

**Metrik değişikliği:** Bu fazdan itibaren armlar **pozisyon-bazlı, havuzlanmış**
metriklerle kıyaslanır (`metrics.py`). Eski kayıt-bazlı "Avg PF" hem çift sayıyor
hem ağırlıksız ortalama alıyordu. Karar metriği: **expectancy/R**, kısıt: **MaxDD**.
Win rate artık hedef değil, teşhis.

**Armlar** (env ile, `config.py` varsayılanları değişmedi):

| Arm | Değişken | 90g exp/R | 90g PF | 240g exp/R | 240g PF |
|---|---|---|---|---|---|
| Kontrol | — | +0.228 | 1.95 | +0.057 | 1.18 |
| E1 | `X_TP1_CLOSE_FRAC=0.0` | +0.221 | 1.92 | — | — |
| E2 | `X_TRAIL_ATR=2.5` | +0.264 | 2.13 | +0.100 | 1.33 |
| E2b | `X_TRAIL_ATR=3.0` | +0.261 | 2.11 | — | — |
| E3 | `X_CONFIRM_VOL_MULT=1.5` | +0.107 ⚠️ | 1.43 | — | — |
| **E6** | `X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5` | **+0.281** | **2.15** | **+0.115** | **1.37** |
| E7 | `X_TP1_CLOSE_FRAC=0.33 X_TRAIL_ATR=2.5` | +0.270 | 2.15 | +0.105 | 1.35 |
| E8 | `X_TRAIL_ATR=2.5 X_TP2_ATR=6.0` | +0.257 | 2.06 | — | — |

**Sonuçlar:**
1. **E6 kazandı, her iki pencerede de:** 90g +%23, 240g **+%102** (expectancy iki katı).
   MaxDD ayı penceresinde de **iyileşti** (−9.63% → −8.69%). Trade sayısı sabit (215 vs 217)
   → "0 trade" sendromu yok.
2. **Kazanan armın WR'si DÜŞÜK** (%56.7 vs %60.3) ama payoff'u yüksek (1.65 vs 1.28).
   Eski WR≥55 kriteriyle bu arm reddedilirdi. Hedef fonksiyonunu düzeltmek, doğru
   cevabı bulmanın ön şartıydı.
3. **Trail genişliği asıl darboğazdı**, kısmi çıkış değil: E1 tek başına −%3, E2 tek
   başına +%16, ikisi birlikte +%23 (süperadditif — trail genişleyince kısmi çıkışı
   kaldırmak anlam kazanıyor).
4. **Hacim teyidini sıkılaştırmak zararlı** (E3: −%53, MaxDD −5.56%). Genel literatür
   tavsiyesi ("kırılımda 2-3× hacim") bu sisteme UYMUYOR — sinyal motoru hacmi zaten
   eliyor, ikinci filtre iyi trade'leri kesiyor. Körlemesine uygulanmamalı.

**Not — canlı/backtest uçurumu hâlâ açık:** düzeltilmiş metrikle bile backtest +0.228R
iken canlı −0.076R. E6 bunu tek başına kapatmaz. `paper_bb.py`'ye eklenen sinyal-hunisi
telemetrisi (probe/confirm/full sayaçları + probe_cost) bu farkın kaynağını ölçecek.
