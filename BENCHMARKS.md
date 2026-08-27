# 📊 BENCHMARKS — Faz-Faz Backtest Kıyası

> ## ⚠️ ÖLÇÜM DİKİŞİ — 2026-08-27
>
> **Bu tarihten ÖNCEKİ tüm rakamlar bugünkü harness'la yeniden üretilemez.** Araya
> dört düzeltme girdi ve her biri ölçülen büyüklüğü değiştirdi:
>
> | Düzeltme | Ne değişti | Yön |
> |---|---|---|
> | **M1** rejim ısınması | pencerenin ilk ~35 günü NEUTRAL uyduruluyor, momentum hiç işlem yapmıyordu; MR o ayı tek başına devralıyordu | eski sayılar MR lehine kaymış |
> | **M2** sleeve başına risk | momentum($10) + MR($5) tek $10 paydasında havuzlanıyordu → "expR" bir R-katsayısı değildi | eski expR anlamsız |
> | **T0** eksik bacaklar | OPEN bacakları ve probe bacakları bakiyeyi hareket ettiriyor ama trade_log'a girmiyordu | eski beklenti iyimser |
> | **E7** fill konvansiyonu | trail o barın KENDİ zirvesinden çekilip aynı barın dibiyle test ediliyordu (kurulamayan emir); belirsiz intrabar dolumları lehe çözülüyordu | aşağıya bak |
>
> **Somut fark:** Faz 6'da yayınlanan **+0.249R** (kürasyon sonrası 5 coin, 240g)
> bugünkü harness'la aynı pencerede **+0.072R havuzlanmış / +0.323R momentum**
> çıkıyor. Aşağıdaki `+0.249`, `+0.115`, `PF 3.29`, `+$55.51/ay` gibi manşetler
> **tarihsel kayıt** olarak duruyor — güncel sistemin ölçüsü değiller.
>
> E7 sürprizi: düzeltme backtest'i **iyileştirdi** (240g $1330.33 → $1370.04).
> Düzeltilen şey bir iyimserlik değil, borsada kurulamayan bir emirdi — trail
> yalnızca kapanmış barlarla yukarı çekilince pozisyonlar erken boğulmuyor.
> Ayrıntı: Faz 7.

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

---

## 🔁 Faz 6 — Evren yeniden-kürasyonu (22 Ağu 2026)

**Bağlam:** E6 (`X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5`) ~8 Ağu'da canlıya alındı ve
çıkış tarafı düzeldi (tek-bacak TP2'ler log'da görünüyor). Buna rağmen bakiye
$1000→$901,99 (−%9,8), MaxDD −%13,8 (hard-stop −%15'e 1,2 puan). Aynı dönemde
own-universe HODL +%19,2 → 29 puanlık fırsat maliyeti.

**Huni telemetrisi sinyal katmanını akladı:** `scanned=55243 probe=101 confirm_ok=24
confirm_fail=68`, probe_cost **−$2,81**. Canlı confirm oranı %26, backtest aralığı
%14–30 (ort. ~21) → probe/confirm filtresi normal çalışıyor ve ucuz. Tüm hasar
confirm'i geçen 24 tam pozisyonda.

### Kök neden: kürasyon, dağıtılan parametrelerle uyumsuzdu
8 coinlik evren **29 Tem'de ESKİ çıkış yapısıyla** (trail 1.5 + %50 kısmi) seçilmişti.
Trend-takip çıkışı (E6) farklı coinleri ödüllendirir → evren, artık var olmayan bir
sisteme fit edilmiş durumdaydı. `curate.py` ile 4 pencerede yeniden sıralandı.

### ⚠️ En önemli bulgu — kısa pencerede kürasyon TERS teper
Coin bazlı expectancy sıralama korelasyonu (Spearman, n=8):

| | 240g | canlı |
|---|:---:|:---:|
| **90g** | −0.31 | **−0.55** (işaret uyumu 1/8) |
| **240g** | — | **+0.74** |

90 günlük pencerede coin başına 5–20 pozisyon var → gürültü; 240 günlükte 25–48 →
transfer ediyor. Eski kural ("90g **VE** 240g pozitif") kısa pencerenin oy vermesine
izin verdiği için SOL ve AVAX evrene böyle girmişti.

Adayların hepsi bu tuzağı doğruladı — 90g'de parlayıp 240g'de negatife döndüler:
FET +0.240→−0.073 · XRP +0.138→−0.144 · AAVE +0.119→−0.045 · WIF +0.031→−0.154.

### Karar
| Coin | canlı (n) | fresh90 | fresh240 | pin240 | karar |
|---|---|---|---|---|---|
| UNI | +0.501 (16) | +0.277 | **+0.344** | −0.033 | TUT |
| INJ | −0.012 (4) | +0.377 | **+0.297** | +0.261 | TUT |
| ADA | −0.141 (11) | +0.137 | **+0.252** | +0.248 | TUT |
| POL | +0.665 (3) | +0.279 | **+0.181** | +0.418 | TUT |
| NEAR | −0.477 (1) | −0.111 | **+0.170** | +0.116 | TUT (n=48, en büyük örneklem) |
| **LDO** | −0.553 (13) | −0.510 | −0.145 | +0.069 | **ÇIKAR** |
| **SOL** | −0.726 (4) | −0.292 | −0.035 | −0.149 | **ÇIKAR** (4/4 negatif) |
| **AVAX** | −0.582 (3) | −0.426 | −0.178 | −0.156 | **ÇIKAR** (4/4 negatif) |

> **LDO tek başına canlıda −$71,88** = sleeve'in toplam net kaybının (−$39) ~2 katı.
> LDO olmasa momentum sleeve'i **+$33 kârdaydı**. Fix-sonrası pencerede masum görünüyor
> (1W/1L); asıl hasarı Tem'de dört ~−$11'lik kayıpla yapmış → **kısa pencereye bakmak
> yanlış coini suçlatıyor.**

**ADA/NEAR bilerek TUTULDU:** ADA'nın canlı kötülüğü istatistiksel değil (p=0.36,
8-coin Bonferroni'den geçmiyor), NEAR'ın canlı örneklemi n=1. İkisi de her iki uzun
pencerede pozitif. LDO/SOL/AVAX ise Bonferroni'den bile geçiyor (p<0.006).

### Sonuç
| Evren | expR | pozisyon | 240g beklenen |
|---|:---:|:---:|:---:|
| mevcut 8 | +0.131 | 245 | +$322 |
| **kürasyon sonrası 5** | **+0.249** | 168 | **+$418** |
| 5 + LINK + LTC | +0.222 | 202 | +$448 |

Doğrulama: 5/5 coin pozitif, hepsi PF≥1.51, worst DD −3,69%
(`backtests/curation_2026-08-22_fresh240.txt`).

**LINK/LTC eklenmedi:** iki pencerede de pozitif kalan tek adaylar ama +0.09R, n=19/15
→ sıfırdan ayırt edilemiyor. Toplam doları artırırdı (MAX_OPEN=2 hiç dolmuyor,
`blocked_max_open=0`) ama işlem başına kaliteyi seyreltir. Hesap hard-stop'a 1,2 puan
uzaktayken yazı-tura bahis eklenmedi — hesap toparlayınca ön-tanımlı kriterle tekrar bak.

### ⏭️ Açık kalan
- **Canlı/backtest seviye farkı hâlâ kapanmadı** ama küçüldü: canlı −0.071R vs 240g
  backtest +0.115R → fark 0.186R, n=55'te t≈1,8 (**anlamlı değil**). Fee modeli doğru
  (0,075%/yön, her iki tarafta kesiliyor) → sistematik bir yürütme hatası kanıtı yok;
  büyük olasılıkla küçük örneklem. 200+ pozisyonda tekrar ölç.
- **Düzeltildi (22 Ağu):** `paper_bb.py` resume'da regime sözlüğünü state'ten olduğu
  gibi geri yüklüyordu → TOKENS'tan çıkarılan coinler sözlükte kalıyordu. Alım-satımı
  etkilemiyordu (regime hep `self.tokens` üzerinden okunuyor) ama `BULL n/N` satırını
  bozuyor ("8/5"), state'e geri yazılıyor ve **track-record exporter yayımlanan evreni
  ile own-universe HODL benchmark'ını tam da bu anahtarlardan türetiyor** → site 5 coin
  koşarken 8 coin gösteriyordu. Artık resume'da mevcut evrene budanıyor.
- **Intrabar çıkış sırası iyimserliği** (`strategy.py`): aynı 5m barda hem TP1 hem SL
  değerse kod önce TP1'i sayıyor → E6'da bu, olması gereken −1R kaybı breakeven-trail'e
  çeviriyor. SL 1.5×ATR + TP1 2.0×ATR = 3,5×ATR'lik bar gerektiği için nadir, ama
  backtest'i yapısal olarak iyimser kılıyor. ✅ **Faz 7'de düzeltildi ve ölçüldü.**

---

## 🔬 Faz 7 — Canlı ayın adli incelemesi (27 Ağu 2026)

**Bağlam:** Hesap 29 Tem'de $1000'den başladı, 5 Ağu'da $1062,11 gördü, 27 Ağu'da
$946,22'ye indi (−%10,9, throttle açık, hard-stop'a $43,43). Soru: kanama nereden?

### Önce iki şüpheli elendi

**1. Simülatör sapmıyor.** Canlı botun ve backtest'in *aynı takvim ayında aynı
coinlerde* açtığı pozisyonlar eşleştirildi (n=26, giriş ±5 dk):

| Ölçüt | Sonuç |
|---|---|
| Aynı çıkış tipine varan | **26/26 (%100)** |
| PnL'i kuruşuna kadar aynı | 19/26 |
| Farkı tam 2× (equity throttle) | 7/26 |
| Fill konvansiyonu düzeltmesi | 1/26 |

Canlı hesap düşüşteydi → throttle boyutu yarıladı; backtest hesabı aynı aya +$300
tamponla girdiği için hiç frenlenmedi. **Fark bir hata değil, throttle'ın tasarımı.**
Yan sonuç: throttle bu ayda −$21'e mal oldu — kaybı yarıladığı kadar toparlanmayı da.

**2. Sinyal hunisi sapmıyor.** Backtest'in 28 girişinin **26'sı canlıda birebir var
(%93)**. Canlıdaki 10 fazla girişin tamamı açıklanıyor: 5'i LDO/AVAX (backtest
evreninde yok), 5'i 25–26 Ağu (backtest verisi 24 Ağu'da bitiyor).

> **Aylardır açık olan "canlı/backtest uçurumu" sorusu kapandı: ölçüm katmanında
> böyle bir uçurum yok.**

### Kaybın ayrıştırması (hesabın −$53,78'ini kuruşuna kadar kapatır)

| Kaynak | Poz. | WR | All-in | /poz |
|---|:---:|:---:|---:|---:|
| Kürasyonun elediği coinler (LDO, AVAX) | 5 | %20,0 | **−$30,24** | −$6,05 |
| MR sleeve | 13 | %38,5 | **−$14,81** | −$1,14 |
| Son üç gün (25–27 Ağu) | 5 | %0,0 | **−$28,94** | −$5,79 |
| Probe hunisi | — | — | −$3,74 | — |
| **Çekirdek sistem (kürasyonlu 5 coin)** | **26** | **%46,2** | **+$23,96** | **+$0,92** |
| **Hesap** | **49** | **%36,7** | **−$53,78** | **−$1,10** |

> **Çekirdek sistem canlıda KÂR ETTİ.** Kayıp, projenin kendi analizinin zaten
> "negatif" dediği pozisyonlardan geldi. Bu bir strateji arızası değil, **tasfiye
> gecikmesi**: LDO/AVAX kanıtı 22 Ağu'da netleşti, zarar 4–22 Ağu boyunca birikti.
>
> **Karşı-olgusal:** MR kapalı + LDO/AVAX bir ay önce çıkarılmış olsaydı hesap
> **$991,27** olurdu — throttle hiç devreye girmezdi.

**Son üç gün ayrı bir hikâye:** 25 Ağu'da BTC short-squeeze ile $80.894'e fırladı;
rejim BTC'ye %57,5 ağırlık verdiği için beş coinin de rejimi BULL'a döndü, bot beş
alt breakout'u aldı, beşi de stop oldu. Aynı hafta altcoin-season endeksi eşiğin çok
altındaydı → **BTC yükseliyordu, alt'lar yükselmiyordu.** Rejim kapısı işlem yaptığı
varlığın değil, BTC'nin rejimini ölçüyor. (Test edilmemiş hipotez — ablasyon gerek.)

### Sürtünme tabanı — yapısal kısıt

Canlı SL trade'lerinden stop mesafesi geri çözüldü. Kimlik:

```
maliyet/risk = (notional × 0.15%) / (notional × sl_frac) = 0.0015 / sl_frac
```

Notional sadeleşiyor → **pozisyon boyutu bu oranı değiştirmez.** Tek belirleyici,
stop mesafesinin fiyata oranı. Ölçülen ortalama `sl_frac = %0.861` (1.5×ATR, 5m):

| Stop | sl_frac | Sürtünme |
|---|:---:|:---:|
| **1.5×ATR (şu an)** | %0.86 | **0.174 R** |
| 2.25×ATR | %1.29 | 0.116 R |
| 3.0×ATR | %1.72 | 0.087 R |
| 4.5×ATR | %2.58 | 0.058 R |

240g'de ölçülen net momentum edge +0.323R; sürtünme 0.17–0.20R. **Brüt edge'in
üçte birinden fazlası kapıda ödeniyor** → sinyal kalitesindeki küçük bir bozulma
toplamı negatife çeviriyor. Risk sabit dolar olduğu için stop'u genişletmek
pozisyonu küçültür, riski değiştirmez = saf sürtünme indirimi.

**Stop genişliği taraması (240g, MR kapalı, TP+trail aynı oranda, `TIMEOUT_BARS=48` SABİT):**

| Stop | Giriş ücreti | TP2 | TMO | Momentum | PF | MaxDD |
|---|---:|:---:|:---:|:---:|:---:|:---:|
| **1.5×ATR** (mevcut) | −$111.47 | 37 | **1** | **+0.323R** | 1.90 | −5.78% |
| 2.25×ATR | −$77.59 | 30 | 14 | +0.307R | 1.81 | **−5.35%** |
| 3.0×ATR | **−$58.85** | 21 | **27** | +0.192R | 1.53 | −6.65% |

Ücret aritmetiği **doğrulandı** (111→78→59, monoton). Ama net edge de monoton
bozuldu: **zaman aşımı 1→14→27 patlıyor**, TP2 37→21 eriyor. Geometri ölçeklenirken
zaman bütçesi sabit kalınca hedefler 4 saatlik pencerede ulaşılamaz hale geliyor.

> **Sürtünme kolu gerçek ama naif çekilemez.** Orta kol dikkat çekici: edge'in
> neredeyse tamamını koruyor (+0.307 vs +0.323R, fark gürültü altında), $34 az
> ücret ödüyor ve **DD'si üç kolun en iyisi** (−5.35%). Sıradaki deney oradan:
> `X_SL_FULL_ATR=2.25 X_TP1_ATR=3.0 X_TP2_ATR=6.0 X_TRAIL_ATR=3.75 X_TIMEOUT_BARS=72`

**Çürütülen hipotez:** en kötü maliyet oranları `MAX_NOTIONAL` kelepçesinin
sıkıştığı trade'lerde (%32–47). "Kaybı bunlar taşıyor" denendi → **taşımıyorlar**
(kelepçeli −$0,77/poz vs kelepçesiz −$1,07/poz, WR %36,4 vs %36,0). Kayıp yaygın.

### Örneklem duvarı

Canlı momentum (−0.098R, n=36) vs backtest (+0.337R, n=28) → fark 0.435R,
**t = 1.02, p = 0.31 — anlamlı DEĞİL.** Pozisyon başına R'nin SD'si ≈1.7R; bu farkı
%80 güçle saptamak **kol başına ~240 pozisyon** ister = mevcut hızda ~200 gün.

> **Kural:** n < 100 pozisyonda parametre değiştirme. Bu ay alınan hiçbir karar tek
> aya dayanmamalı. E17 (TODOS) doğrulandı.

MR sleeve bu testi geçen tek bulgu: üç bağımsız ölçüm, üçü negatif, havuzlanmış n=72.

### Kod değişiklikleri

| Ne | Nerede | Etki (240g) |
|---|---|---|
| **E7 fill konvansiyonu** — belirsiz intrabar dolumu aleyhe çözülür; trail yalnız kapanmış barlarla çekilir; timeout bar ortası yerine kapanıştan dolar; gap-through bar aralığına kırpılır | `strategy.py` | $1330,33 → **$1370,04** |
| `X_ADVERSE_FILLS=0` — eski iyimser konvansiyon (yalnız tarihsel kıyas için) | `config.py` | — |
| `X_MR_ENABLED=0` — MR sleeve A/B anahtarı | `config.py` | bakiye **+$10,89**, PF 1.83→**1.90**, MaxDD −6.31%→**−5.78%** |

**E7 sürprizi:** düzeltme backtest'i **iyileştirdi**. Düzeltilen şey bir iyimserlik
değil, *borsada kurulamayan bir emirdi* — trail o barın kendi zirvesinden çekilip
aynı barın dibiyle test ediliyordu (zirvenin dipten önce geldiğini varsayar). Trail
yalnızca kapanmış barlarla çekilince pozisyonlar erken boğulmuyor. 21 test geçiyor.

⚠️ `paper_bb.py` aynı `strategy.py`'yi kullanıyor → bu düzeltme canlı davranışı da
değiştirir. Dağıtımda E6 env'leriyle birlikte gitmeli.

---

## 🧪 Faz 8 — Deney disiplini ve benimsenen çıkış seti (27 Ağu 2026)

Faz 7'nin teşhisleri **hipotez** olarak kayda geçirildi ve tek tek ölçüldü.
Altyapı: `experiments/run_arm.sh` (bir kol, bir pencere, parametreler sonuca
damgalanır) · `experiments/ledger.py` (karşılaştırma + karar) ·
`experiments/DEFTER.md` (her fikrin hipotez→ölçüm→sonuç kaydı).

**Karar kuralı:** bir kol ancak **240g VE 665g'de birden** baseline'ı geçerse alınır.
Tek pencere kanıt değil — n≈120'de pencere ortalamasının SE'si ≈0.16R.

### Benimsenen: yeni çıkış geometrisi + MR kapalı

| parametre | eski | **yeni** |
|---|---:|---:|
| `SL_FULL_ATR` | 1.5 | **2.25** |
| `TP1_ATR` / `TP2_ATR` | 2.0 / 4.0 | **3.0 / 6.0** |
| `TRAIL_ATR` | 1.5 (env'de 2.5) | **3.75** |
| `TIMEOUT_BARS` | 48 | **96** |
| `TP1_CLOSE_FRAC` | 0.50 (env'de 0.0) | **0.0** |
| `MR_ENABLED` | True | **False** |

| pencere | baseline | benimsenen | hard-stop | MaxDD |
|---|---:|---:|:---:|---|
| 240g | $1370.04 | **$1435.46** | 0 → 0 | −6.31% → −6.14% |
| 665g (`BT_RESTARTS=1`) | $731.93 | **$1085.22** | **7 → 3** | −57.3% → **−37.6%** |

**Neyin kanıtlandığı konusunda dürüst olmak gerekirse:** pozisyon başına edge
farkı (Δ+0.063R vs SE 0.085) **kanıtlanmadı** — n≈400'de bile gürültü içinde.
Kanıtlanan iki şey var: (1) ücret düşüşü saf aritmetik — risk sabit dolar olduğu
için geniş stop daha küçük notional alır (665g −$366→−$223), (2) hard-stop 7→3 ve
MaxDD −57%→−38%. **Arm, daha iyi trade seçtiği için değil, maliyet ve drawdown
davranışı için alındı.**

**Zaman bütçesi bağımsız bir kaldıraç değil.** Kontrol kolu (SL 1.5 + `TMO=96`)
yalnız +$6.84 getirdi ve çıkış dağılımını hiç değiştirmedi — dar stopta pozisyonlar
zaten 48 bardan önce çözülüyordu. `TIMEOUT_BARS` sadece stop genişleyince anlam
kazanıyor; ikisi birlikte gider.

### Reddedilenler (hepsi 240g'de kaybetti)

| kol | sonuç | ders |
|---|---|---|
| `R2-coinbull` | $1370→$1296 | Kapı tasarlandığı gibi 23 pozisyonu eledi ama **elenenler net kârlıydı**. Ağustos'taki 5 kaybı doğru teşhis etmek, ondan türeyen kuralın genel olarak iyi olduğunu göstermiyor. |
| `R2-minsl75` | $1370→$1279 | Pozisyon başına **en iyi edge** (+0.405R) ve **en iyi DD** (−5.76%) ama n 123→74. Sürtünmeyi işlem eleyerek düşürmek toplam edge'i yiyor. H0.3 ile aynı yöne işaret ediyor: dar stoplu trade'ler ücretini ödeyecek kadar edge taşıyor. |
| `R2-minsl100` | $1370→$1071 | Aynısının aşırısı (n=41). |
| `R2-btcw40` | değişiklik **YOK** | ↓ |

### 🔍 `BTC_WEIGHT` atıl bir parametre

`X_BTC_WEIGHT=0.40` koşusu baseline ile **birebir aynı** çıktı. Sebep yapısal:
skorlar üçlü {−1,0,+1} olduğu için harman yalnız `{±1, ±w, ±(1−w), 0}` olabilir;
BULL eşiği 0.40 iken `w ∈ [0.40, 0.60]` aralığının tamamında **hem `w` hem `(1−w)`
eşiği geçer** → doğruluk tablosu hiç değişmez.

> **ROADMAP §9'da "BTC ağırlığı 55-60 ortası → 0.575" diye tartışılıp kilitlenen
> karar, parametrenin ifade edemediği bir karardı.** Rejim davranışını değiştirmek
> başka bir mekanizma ister. Teste bağlandı:
> `test_btc_weight_is_inert_between_040_and_060`.

### ⚠️ Harness kusuru — kesik pencereler karşılaştırılıyordu

İlk 665g turunda üç kol da `PEAK_DD_LIMIT`'e çarpıp erken durdu (baseline ~2 ayda,
sl225t96 ~6 ayda) ve defter final bakiyeleri **karşılaştırılabilirmiş gibi** okuyup
yanlış bir ✅ verdi. Kök neden: `dump_run` `halted`/`days_run`/`n_halts` alanlarını
kaydetmiyordu; `print_portfolio_report` ekranda uyarıyor ama dump taşımıyordu.
Düzeltildi: dump artık halt alanlarını yazıyor, `ledger.py` kesik koşuda
karşılaştırmayı reddediyor (`KESİK`), uzun pencereler `BT_RESTARTS=1` ile koşuluyor.

> **Bağımsız bulgu:** Eki 2024–Ağu 2026 penceresinde **eski** sistem 665 günde
> **7 kez** hard-stop'a çarpıyordu. Yakın dönem (240g) bu tarihçeye göre çok
> müsamahakâr — bir kolu yalnız 240g'de doğrulamak, sistemin hayatta kalamadığı
> rejimi hiç görmemek demek.

### 🚨 Deploy uyarısı

systemd unit'i E6'dan kalma `X_TRAIL_ATR=2.5` taşıyor. Bu env artık benimsenen
**3.75'i EZER** ve hiç test edilmemiş bir karışım çalıştırır.
**Unit'ten `X_TP1_CLOSE_FRAC` ve `X_TRAIL_ATR` kaldırılmalı** — ikisi de varsayılan.

Ayrıca `deploy_test.sh` `metrics.py`'yi hiç göndermiyordu (elle tutulan liste);
`paper_bb.py` onu import ediyor → sunucudaki kopya eski. Liste artık import
ağacından türetiliyor, kirli ağaçtan deploy reddediliyor, SHA sunucuya yazılıyor.
