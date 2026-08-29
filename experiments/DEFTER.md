# Deney Defteri

Her fikir buraya **önce hipotez olarak** yazılır, sonra ölçülür, sonra sonucu
işlenir. Uygulamaya alınan bir değişikliğin bu defterde bir satırı yoksa,
uygulanmamış sayılır.

## Kurallar

1. **İki pencere kuralı.** Bir arm ancak **240g VE 665g**'de birden baseline'ı
   geçerse alınır. Tek pencerede iyileşme kanıt değil: pozisyon başına R'nin
   SD'si ≈1.7R, n≈120'de pencere ortalamasının SE'si ≈0.16R → 0.05R'lik bir
   "iyileşme" sayı kılığına girmiş gürültüdür.
2. **Mekanizma önce, etki sonra.** Yeni bir kapı önce birim testiyle "ne
   yaptığını yapıyor mu" diye doğrulanır. Sessizce hiçbir şey yapmayan bir kapı,
   baseline'ın tekrarını "sonuç" diye gösterir ve rakamlardan anlaşılmaz.
3. **Varsayılan KAPALI.** Her yeni parametre env bayrağı arkasında ve varsayılanı
   mevcut davranış. Env'i set etmemek hiçbir şeyi değiştirmemeli.
4. **Negatif sonuç da sonuçtur.** Çürüyen hipotez silinmez, "RED" olarak kalır —
   hangi yolun kapalı olduğunu bilmek de kazanç.
5. **All-in ölç.** Karar metriği, kendi giriş ücretleri yüklenmiş momentum
   expectancy'si (R). `pos_stats` beklentisi giriş ücretlerini atlar (~0.07R).

Koşum: `./experiments/run_arm.sh <arm-id> <gün> ENV=VAL ...`
Tablo:  `python3.12 experiments/ledger.py`

---

## Tur 0 — Adli inceleme (27 Ağu 2026)

Başlangıç durumu ve nereden geldiğimiz: `BENCHMARKS.md` Faz 7.
Kısaca: canlı hesap −%10.9, ama **çekirdek sistem canlıda +$23.96 kâr etti**;
kayıp kürasyon gecikmesi (LDO/AVAX −$30.24), MR sleeve (−$14.81) ve son üç
günden (−$28.94) geldi. Simülatör ve sinyal motoru aklandı (26/26 birebir).

### Tur 0'da kapanan hipotezler

| # | Hipotez | Test | Sonuç |
|---|---|---|---|
| H0.1 | Canlı/backtest ayrışması var | Aynı ay, aynı coin, eşleşmiş 26 pozisyon | **RED** — 26/26 aynı çıkış, 19'u kuruşuna kadar aynı; farklar throttle'dan |
| H0.2 | Sinyal hunisi canlıda sapıyor | Giriş zamanı eşleştirmesi | **RED** — %93 örtüşme, sapmalar evren/pencere farkıyla açıklanıyor |
| H0.3 | `MAX_NOTIONAL` kelepçeli trade'ler kaybı taşıyor | Canlı 36 momentum pozisyonu ikiye ayrıldı | **RED** — kelepçeli −$0.77/poz vs kelepçesiz −$1.07/poz |
| H0.4 | Fill konvansiyonu backtest'i şişiriyor | E7 düzeltmesi, 240g | **KISMEN** — düzeltildi ama sonuç **iyileşti** ($1330→$1370); düzeltilen şey iyimserlik değil, kurulamayan bir emirdi |
| H0.5 | Stop'u genişletmek sürtünmeyi düşürür | 1.5 / 2.25 / 3.0 ×ATR, `TIMEOUT_BARS=48` sabit | **KISMEN** — ücret 111→78→59 (aritmetik doğrulandı) ama zaman aşımı 1→14→27 patladı, net edge bozuldu |

**H0.5'ten doğan soru:** zaman bütçesi de ölçeklenirse ne olur? → Tur 1.

---

## Yan bulgu — deploy zinciri kırıktı (27 Ağu, A/B değil, bug)

`deploy_test.sh` gönderilecek dosyaları **elle tutulan bir listeden** okuyordu ve
liste `metrics.py`'yi atlıyordu. Ama `paper_bb.py:51` onu import ediyor:

```python
from metrics import aggregate_positions, position_stats
```

Sunucudaki kopya eski bir elle-kopyalamadan kalmaydı → **`metrics.py`'ye yapılan
hiçbir düzeltme canlıya ulaşmadı.** Bu, T-M1'i (canlı MR bacaklarının MOMENTUM
sayılması) doğrudan etkiler: düzeltme repoda var, canlı botta muhtemelen yok.
Canlının `_print_status` çıktısı ve `--status` özeti bu yüzden yanlış olabilir.

**Düzeltme:** liste artık `paper_bb.py`'nin import ağacından özyinelemeli
türetiliyor (`ast` ile), yani bir daha elle senkron tutulması gerekmiyor.
Ek olarak: kirli ağaçtan deploy reddediliyor (`--force` ile geçilebilir),
dağıtılan SHA sunucuya `DEPLOYED_SHA` olarak yazılıyor, ve env sözleşmesi
ekrana basılıyor. `secrets_local.py` izleyici tarafından bulunuyor ama açıkça
dışlanıyor — çalışan bot saf simülasyon, anahtar yüklü değil ve olmamalı.

⚠️ **Doğrulanmadı:** betik çalıştırılmadı (sunucuya dokunmak dışa dönük).
Sunucudaki `metrics.py`'nin gerçekten eski olup olmadığı kontrol edilmeli:
`ssh $BREAKOUTBOT_SERVER 'md5sum ~/BreakoutBot-test/metrics.py'` ile yereldeki
`md5 metrics.py` karşılaştırılmalı.

---

## Yan bulgu — track record maliyetin üçte birini gizliyordu (27 Ağu, X1)

`track_record_app/track_record/exporter.py` log'dan yalnızca `CLOSE FULL` ve
`MR TP/SL` satırlarını okuyordu. Giriş ücretleri (pozisyon açılırken kesilir) ve
probe bacakları hiçbir yayınlanan trade'de görünmüyordu.

**Düzeltme öncesi/sonrası, gerçek canlı veriyle doğrulandı:**

| Alan | Değer |
|---|---|
| `expectancy_usd` (ALL-IN, yeni) | **−$1.10** |
| `expectancy_exit_only_usd` (eskiden yayınlanan) | −$0.35 |
| `entry_fees_usd` (yeni) | −$32.81 |
| `unrecorded_cost_usd` (yeni) | −$36.55 |

Mutabakat: `all-in × 49 poz = −$53.90` ≈ bakiye değişimi `−$53.78` ✅
(fark yalnız 2 hanelik yuvarlamadan). Eski manşet gerçeğin **%32'siydi.**

Giriş ücreti yeniden türetilebiliyordu çünkü `FULL OPEN`/`MR OPEN` satırı zaten
`notional` taşıyor; oran sabit (%0.075). Ek ayrıştırma gerekmedi.

⚠️ **YAYINLANMADI.** Bu, sitedeki manşet rakamı kötüleştirir ve dışa dönüktür —
`track_record_app/deploy.sh` çalıştırılmadı. Karar Eren'in.

---

## Tur 1 — Zaman bütçesi + MR kapatma

**Tarih:** 27 Ağu 2026 · **Pencere:** 240g, ardından 665g · **Veri:** önbellek (sabit)

### Hipotezler

| Arm | Hipotez | Gerekçe |
|---|---|---|
| `R1-baseline` | — (referans) | Dağıtılan sistem: E6 çıkışları, MR açık, düzeltilmiş fill |
| `R1-mr-off` | MR sleeve'i kapatmak kârı artırır | Üç bağımsız pencerede negatif, havuzlanmış n=72 (Faz 7) |
| `R1-sl225t72` | Stop 2.25×ATR + zaman bütçesi 72 bar edge'i korur, ücreti düşürür | H0.5: 2.25× edge'in tamamını korudu (+0.307 vs +0.323R) ve DD'si en iyiydi; tek sorun zaman aşımıydı |
| `R1-sl225t96` | Aynısı, daha cömert zaman bütçesi (96 bar) | Zaman aşımı hâlâ bağlayıcıysa 72 yetmeyebilir |

### Sonuçlar — 240 gün

| arm | momR | ±SE | n | mrR | PF | bakiye | DD | ücret | TP2/SL/TRL/TMO |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1-baseline` | +0.323 | 0.153 | 123 | −0.044 | 1.83 | $1370.04 | −6.31% | −$147.14 | 37/51/34/1 |
| `R1-mr-off` | +0.323 | 0.153 | 123 | kapalı | **1.90** | $1380.93 | **−5.78%** | −$120.11 | 37/51/34/1 |
| `R1-sl225t72` | +0.380 | 0.159 | 115 | kapalı | 1.96 | $1421.82 | −6.14% | −$84.89 | 34/46/29/6 |
| **`R1-sl225t96`** | **+0.396** | 0.159 | 114 | kapalı | **2.00** | **$1435.46** | −6.14% | **−$84.64** | 35/45/31/3 |

**H1.1 (MR kapat) — 240g'de doğrulandı.** momR Δ=0.000 (beklenen: MR momentum'a
dokunmaz), ama hesap +$10.89, PF 1.83→1.90, DD −6.31%→−5.78%, ücret −$27.
Kazancın tamamı MR'ın negatif katkısının kalkmasından.

> ⚠️ **Defterde kusur bulundu ve düzeltildi:** karar metriği momentum R'siydi, ama
> MR-off kolunun etkisi tamamen başka bir sleeve'de → momR Δ=0.000 gösterip
> "değişiklik yok" gibi okunuyordu. Karar metriği **hesap getirisine** çevrildi;
> her kol hangi sleeve'e dokunursa dokunsun oradan geçer. momR teşhis olarak kaldı.

**H1.2/H1.3 (zaman bütçesi) — hipotez zinciri işledi.** Tur 0'da SL 2.25×ATR
`TIMEOUT_BARS=48` ile **+0.307R** (baseline'ın altında) idi ve teşhis "zaman
aşımı patlıyor" olmuştu. Bütçe ölçeklenince:

| `TIMEOUT_BARS` | momR | TMO çıkışı |
|---|---:|---:|
| 48 (Tur 0) | +0.307 | 14 |
| 72 | +0.380 | 6 |
| **96** | **+0.396** | **3** |

Zaman aşımı 14→3'e inerken edge +0.307→+0.396'ya çıktı — teşhis doğruydu.

> ⚠️ **İki Δ da `~` (2×SE içinde) → TEK BAŞINA KANIT DEĞİL.** 665g bekleniyor.

### Kontrol kolu — kazanç nereden geliyor? ✅ ÇÖZÜLDÜ

`R1-sl225t96` iki şeyi aynı anda değiştiriyor. `R1-t96only` (baseline geometrisi
SL 1.5×ATR + `TIMEOUT_BARS=96`, MR kapalı) ayrıştırdı:

| arm | momR | n | çıkışlar | ücret | bakiye |
|---|---:|---:|---|---:|---:|
| `R1-mr-off` (SL 1.5, TMO 48) | +0.323 | 123 | 37/51/34/1 | −$120.11 | $1380.93 |
| `R1-t96only` (SL 1.5, TMO 96) | +0.329 | 123 | 37/51/34/1 | −$120.11 | $1387.77 |
| `R1-sl225t96` (SL 2.25, TMO 96) | +0.396 | 114 | 35/45/31/3 | −$84.64 | $1435.46 |

**Zaman aşımını tek başına uzatmak neredeyse hiçbir şey yapmıyor: +$6.84.**
Çıkış dağılımı ve ücretler birebir aynı kaldı — çünkü SL 1.5×ATR geometrisinde
pozisyonlar zaten hızlı çözülüyor (48 barda TMO yalnız 1 taneydi), uzatılan
sürenin ısıracağı bir şey yok.

**Kazancın tamamı geniş stoptan: +$47.69.** Zaman bütçesi *bağımsız bir katkı
değil, geniş stopun ön koşulu* — Tur 0'da bütçe sabitken geniş stop kaybediyordu
(+0.307R), bütçe açılınca kazanmaya başladı (+0.396R).

> **Atıf doğru yapıldı:** alınacak şey stop geometrisi; `TIMEOUT_BARS` onunla
> birlikte gitmek zorunda ama tek başına bir kaldıraç değil.

---

## ⚠️ Harness kusuru — 665g kolları farklı uzunlukta pencere oynamış

İlk 665g koşusu şu tabloyu verdi ve defter buna **✅ AL** dedi:

| arm | bakiye | DD | son bar |
|---|---:|---:|---|
| `R1-baseline` | $849.73 | −15.28% | 2025-01-06 |
| `R1-mr-off` | $848.24 | −15.43% | 2025-01-07 |
| `R1-sl225t96` | $858.27 | −15.10% | 2025-04-21 |

**Karar geçersizdi.** Pencere Eki 2024 → Ağu 2026; üçü de `PEAK_DD_LIMIT`'e
çarpıp erken durmuş — baseline ~2 ayda, sl225t96 ~6 ayda. Final bakiyeler
karşılaştırılabilir değil: "kazanan" kol kısmen sadece *daha uzun hayatta
kaldığı* için önde görünüyor.

**Kök neden:** `backtest.dump_run` `halted` / `days_run` / `n_halts` alanlarını
kaydetmiyordu. `print_portfolio_report` ekranda uyarıyor (`_window_line`), ama
dump taşımayınca defter kör uçtu.

**Düzeltme:** (a) `dump_run` artık halt alanlarını yazıyor, (b) `ledger.py` kesik
koşuda karşılaştırmayı **reddediyor** (`KESİK` basıyor, sayı üretmiyor),
(c) 665g kolları `BT_RESTARTS=1` ile yeniden koşuluyor — canlı parite değil ama
kolları aynı uzunlukta oynatmanın tek yolu.

> **Bağımsız bulgu:** Eki 2024 – Ağu 2026 penceresinde dağıtılan sistem
> **~2 ayda hard-stop'a çarpıyor.** 240g penceresi (yakın dönem) bu tarihçeye
> göre çok daha müsamahakâr. Bir armı yalnız 240g'de doğrulamak, sistemin
> gerçekte hayatta kalamadığı bir rejimi hiç görmemek demek.

### Sonuçlar — 665 gün (`BT_RESTARTS=1`, dördü de tam pencereyi oynadı)

| arm | momR | ±SE | n | hard-stop | PF | bakiye | DD | ücret |
|---|---:|---:|---:|:---:|---:|---:|---:|---:|
| `R1-baseline` | −0.024 | 0.083 | 418 | **7** | 1.05 | $731.93 | −57.3% | −$366.21 |
| `R1-mr-off` | −0.044 | 0.083 | 418 | 6 | 1.03 | $748.42 | −57.1% | −$297.31 |
| `R1-sl225t72` | +0.000 | 0.085 | 399 | **3** | 1.09 | $932.51 | −39.4% | −$217.69 |
| **`R1-sl225t96`** | **+0.039** | 0.085 | 396 | **3** | **1.18** | **$1085.22** | **−37.6%** | −$222.67 |

### KARAR — üç kol da iki-pencere kuralını geçti ✅

| arm | 240g | 665g | karar |
|---|---:|---:|---|
| `R1-mr-off` | +$10.89 | +$16.49 | ✅ **AL** |
| `R1-sl225t72` | +$51.78 | +$200.58 | ✅ AL (t96 daha iyi) |
| **`R1-sl225t96`** | **+$65.42** | **+$353.29** | ✅ **AL — benimsendi** |

**Ne kanıtlandı, ne kanıtlanmadı** (dürüst ayrım):

- ✅ **KESİN (aritmetik):** ücretler düşüyor. Risk sabit DOLAR olduğu için geniş
  stop aynı risk karşılığında **daha küçük notional** alır. 665g'de −$366→−$223,
  240g'de −$147→−$85. Bu örneklem dışında geri dönmez.
- ✅ **KESİN (ölçülen):** hard-stop **7→3**, MaxDD **−57.3%→−37.6%**. Risk limitine
  daha az girmek daha az throttle ve daha az zorunlu yeniden başlatma demek.
- ❌ **KANITLANMADI:** pozisyon başına edge. Δ+0.063R'ye karşı SE 0.085 → n≈400'de
  bile gürültü içinde (t≈0.74).

> **Bu arm daha iyi trade seçtiği için değil, maliyet ve drawdown davranışı için
> alındı.** Edge iddiası olsaydı örneklem dışında geri dönebilirdi; ücret
> aritmetiği dönmez.

### Uygulandı (27 Ağu 2026)

`config.py` varsayılanları benimsenen kola çevrildi:

| parametre | eski | yeni |
|---|---:|---:|
| `SL_FULL_ATR` | 1.5 | **2.25** |
| `TP1_ATR` | 2.0 | **3.0** |
| `TP2_ATR` | 4.0 | **6.0** |
| `TRAIL_ATR` | 1.5 (env'de 2.5) | **3.75** |
| `TIMEOUT_BARS` | 48 | **96** |
| `TP1_CLOSE_FRAC` | 0.50 (env'de 0.0) | **0.0** |
| `MR_ENABLED` | True | **False** |

`backtest.LIVE_ENV` de yeni sete göre güncellendi (ters sapma yakalansın diye).

> 🚨 **DEPLOY UYARISI:** systemd unit'i E6'dan kalma `X_TRAIL_ATR=2.5` taşıyor.
> Bu env artık benimsenen **3.75'i EZER** ve hiç test edilmemiş bir karışım
> çalıştırır. Unit'ten `X_TP1_CLOSE_FRAC` ve `X_TRAIL_ATR` **kaldırılmalı** —
> ikisi de artık varsayılan.

### Yeniden test

`VERIFY-defaults` kolu **hiçbir env değişkeni olmadan** koşuldu; benimsenen
`R1-sl225t96` kolunu birebir üretmeli. Sonuç aşağıda.

**✅ BİREBİR AYNI.** Her metrik, leg sayısına kadar:

| metrik | `R1-sl225t96` | `VERIFY-defaults` |
|---|---:|---:|
| final_balance | $1435.46 | $1435.46 |
| total_return | 43.5461% | 43.5461% |
| max_dd | −6.1406% | −6.1406% |
| entry_fees | −$84.64 | −$84.64 |
| pozisyon / momentum n | 659 / 114 | 659 / 114 |
| momentum pnl | $528.15 | $528.15 |
| TP2 / SL / TRAIL / TMO | 35/45/31/3 | 35/45/31/3 |
| probe / conf_ok / conf_fail | 545 / 116 / 374 | 545 / 116 / 374 |
| leg sayısı | 1318 | 1318 |
| MR sleeve | yok | yok |

Yani `config.py` varsayılanları artık doğrulanan sistemin **kendisi** — hiçbir env
değişkeni gerekmiyor. Bu, env-sürüklenmesi sınıfındaki hataları kökten kapatır:
Faz 6'da bir backtest env'siz koştuğunda sessizce başka bir çıkış yapısını test
ediyordu; artık env'siz koşmak **doğru** olanı test ediyor.

---

## Tur 2 — Rejim kapısı + sürtünme tabanı

**Durum:** kod hazır, mekanizma testleri geçti (`tests/test_faz7_gates.py`, 6 test).
Tur 1 bitince koşulacak.

### Hipotezler

| Arm | Bayrak | Hipotez | Gerekçe |
|---|---|---|---|
| `R2-coinbull` | `X_REQUIRE_COIN_BULL=1` | BULL'un coin'in KENDİ trendiyle teyidi kaybı azaltır | BTC ağırlığı 0.575 tek başına eşiği geçiyor → coin düzken BTC'nin gücüyle BULL etiketleniyor. 25–27 Ağu'da tam bu oldu: BTC $80.9K'ya squeeze, beş coin BULL, 5/5 stop, alt-season endeksi eşiğin çok altında |
| `R2-btcw40` | `X_BTC_WEIGHT=0.40` | BTC ağırlığını düşürmek aynı işi daha yumuşak yapar | Aynı teşhis, farklı kaldıraç — hangisinin daha iyi olduğu ölçülmeli |
| `R2-minsl75` | `X_MIN_SL_FRAC=0.0075` | Stop'u çok dar olan setup'ları reddetmek (maliyet ≤%20) | `maliyet/risk = 0.0015/sl_frac`; canlıda en dar stoplar riskin %32–47'sini ücrete veriyordu |
| `R2-minsl100` | `X_MIN_SL_FRAC=0.010` | Daha sıkı eşik (maliyet ≤%15) | 0.0075 yetmezse |

> ⚠️ `R2-minsl*` için ön uyarı: H0.3 canlıda dar-stoplu trade'lerin **daha kötü
> olmadığını** gösterdi (n=11). Bu arm'ın hipotezi zaten şüpheli; backtest n=123
> ile düzgün test edecek. Beklenti düşük tutulmalı.

### Sonuçlar — 240 gün

| arm | momR | ±SE | n | PF | bakiye | DD | ücret | karar |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `R1-baseline` | +0.323 | 0.153 | 123 | 1.83 | $1370.04 | −6.31% | −$147.14 | referans |
| `R2-btcw40` | +0.323 | 0.153 | 123 | 1.83 | $1370.04 | −6.31% | −$147.14 | **ATIL** |
| `R2-coinbull` | +0.332 | 0.170 | 100 | 1.79 | $1295.95 | −6.13% | −$134.70 | **RED** |
| `R2-minsl75` | **+0.405** | 0.198 | 74 | 1.85 | $1278.72 | **−5.76%** | −$88.37 | **RED** |
| `R2-minsl100` | +0.216 | 0.265 | 41 | 1.47 | $1070.82 | −7.61% | −$54.80 | **RED** |

#### H2.2 — `BTC_WEIGHT` ATIL bir parametre 🔍

`X_BTC_WEIGHT=0.40` koşusu baseline ile **birebir aynı** çıktı: aynı 123 pozisyon,
aynı $1370.04, aynı çıkış sayıları. Sebep yapısal:

Varlık skorları üçlü {−1, 0, +1} olduğu için harmanlanan değer yalnızca
`{±1, ±w, ±(1−w), 0}` olabilir. BULL eşiği 0.40; `w ∈ [0.40, 0.60]` iken **hem `w`
hem `(1−w)` eşiği geçer** → doğruluk tablosunun her hücresi aynı yere düşer.

| w | 0.35 | **0.40 … 0.60** | 0.65 |
|---|---|---|---|
| etiketler | farklı | **hepsi birebir aynı** | farklı |

> **ROADMAP §9'da "BTC ağırlığı 55-60 ortası → 0.575" diye kilitlenen karar,
> parametrenin ifade edemediği bir karardı.** Bu ağırlığı ayarlamak bir kaldıraç
> değil. Rejim davranışını değiştirmek başka bir mekanizma ister (coin teyidi,
> daha ince skor, ya da `BULL_THRESHOLD`'u oynatmak) — yeni bir ağırlık değil.
>
> Teste bağlandı: `test_btc_weight_is_inert_between_040_and_060`. 14 dakikalık
> CPU ile öğrenilen şey artık 1 saniyelik bir assertion.

#### H2.1 — coin teyidi: teşhis yerel olarak doğru, çare genellemiyor ❌

Kapı tasarlandığı gibi çalıştı: 23 pozisyonu eledi (123→100), tam olarak
(BTC=+1, coin=0) hücresini. Ama **pozisyon başına kalite neredeyse hiç
değişmedi** (+0.332 vs +0.323, Δ+0.009) ve hesap **$1370 → $1296'ya düştü**.

Elenen 23 pozisyon net **kârlıydı**. 25–27 Ağustos'taki 5 kayıp bu pencerede
123 pozisyonun 5'i; BTC-liderli BULL'un kârlı olduğu onlarca başka an var.

> **Ders:** bir kaybı doğru teşhis etmek, o teşhisten türeyen kuralın genel
> olarak iyi olduğu anlamına gelmiyor. Yerel açıklama ≠ genel kural.

#### H2.3/H2.4 — sürtünme tabanı: kalite artıyor, dolar azalıyor ❌

`minsl75` **pozisyon başına en iyi edge'i üretti** (+0.405R, tüm kollar içinde
en yüksek) ve **en iyi drawdown'ı** (−5.76%), ücretleri $147→$88'e indirdi.
Ama pozisyon sayısı 123→74'e düştü ve hesap $1370→$1279 oldu.

Toplam edge: baseline `123 × 0.323 = 39.7R` · minsl75 `74 × 0.405 = 30.0R`.
Riski 1.32× büyütüp toplamı eşitlemek DD'yi ~%7.6'ya çıkarır (baseline'ın altında
değil) → telafi yolu da kapalı.

`minsl100` daha da agresif: n=41, momR +0.216, hesap $1071. Net zarar.

> **Kapanan soru.** Bu, H0.3 ile aynı yöne işaret ediyor: canlıda da kelepçeli
> (dar stoplu) trade'ler daha kötü değildi. İki bağımsız ölçüm aynı şeyi
> söylüyor: **sürtünme gerçek ama dar stoplu trade'ler yine de ücretini
> ödeyecek kadar edge taşıyor.** Sürtünmeyi işlem eleyerek düşürmek işe yaramaz;
> stop geometrisini büyütmek (Tur 1) yarıyor — çünkü o, işlemi elemeden
> notional'ı küçültüyor.

### Karar

Dört kolun dördü de **REDDEDİLDİ** (karar metriği: hesap getirisi, 240g).
665g koşulmadı — bir kol 240g'de kaybettiyse iki-pencere kuralını zaten geçemez.

---

## 🚀 Canlıya alındı — 2026-08-27 15:31 UTC

Deploy anı **ideal**di: her iki sleeve de flat, hiç açık pozisyon yoktu.
State **sıfırlanmadı** — hesap devam ediyor ($945.77, bar #8377), epoch aynı.

| adım | sonuç |
|---|---|
| Yedek | `/root/backups/20260827T153030Z/` (kod + state + unit override) |
| Deploy | 8 dosya, import ağacından türetildi · SHA `dded71b` sunucuda `DEPLOYED_SHA` |
| systemd env | `X_TP1_CLOSE_FRAC` + `X_TRAIL_ATR` **kaldırıldı** → `Environment=[]` |
| Restart | state korundu, `NRestarts=0`, hata yok |
| Doğrulama | sunucudaki kod SL=2.25 TP1=3.0 TP2=6.0 TRAIL=3.75 TMO=96 MR=False üretiyor |

### Deploy'un ortaya çıkardıkları

**`metrics.py` gerçekten eskiydi** — hipotez doğrulandı. Sunucudaki kopya
29 Temmuz tarihli, 8141 byte (yerel: 11133). T-M1 düzeltmesi hiç ulaşmamıştı.
Deploy sonrası test: canlı MR bacağı artık doğru sınıflandırılıyor.

**T0 de ulaşmamıştı.** `full_leg_logging_since` state'te `None`'dı → sunucudaki
`paper_bb.py` OPEN bacaklarını hiç kaydetmiyordu. Deploy sonrası damgalandı
(`2026-08-27T15:35:04`, schema v2). Yani sitedeki −$0.35'lik manşetin sebebi
sadece exporter değil, botun kendisi de eksik kaydediyormuş.

### Deploy sonrası ilk gerçek işlem — yeni geometri çalışıyor

```
16:20  🔬 TEST OPEN  UNIUSDT LONG @ 4.527
16:25  ✅ CONFIRMED  UNIUSDT pnl=$+0.095
16:25  📈 FULL OPEN  UNIUSDT LONG @ 4.552 | notional=$381
16:25  🔬 TEST OPEN  ADAUSDT LONG @ 0.2159
16:30  ❌ CONF FAIL  ADAUSDT pnl=$-0.052        ← filtre çalışıyor
```

**notional $381** — eski $900–1500 yerine. Geniş stopun aritmetiği tam olarak
bu: aynı $10 risk, daha uzak stop → daha küçük pozisyon → daha az ücret.
(Throttle aktif olduğu için ayrıca yarılanmış.) Pozisyon TP1'i geçti ve
`TP1_CLOSE_FRAC=0.0` olduğu için hiçbir şey bankalamadan 3.75×ATR trail'e geçti.

53 bar, 0 hata, 0 yeniden başlatma.

---

## 🌐 Site (breakoutbot.dev) — 4 düzeltme yayınlandı

| # | Ne | Öncesi → Sonrası |
|---|---|---|
| 1 | `expectancy_usd` all-in oldu | −$0.35 → **−$1.08** (mutabık: ×50 = −$54.00 ≈ bakiye −$54.23) |
| 2 | TRAILING'de gösterilen stop | `full_sl` (girişe EŞİT, ölü alan) → gerçek trail seviyesi |
| 3 | Backtest kartları | Faz 4c (PF 3.29, +$55.51/ay — M1/M2/T0 öncesi) → Faz 8 rakamları |
| 4 | `meta.strategy` | "Faz 4c … + mean-reversion" → "Faz 8 — regime-gated breakout momentum" |

(2) yan bir bug ortaya çıkardı: `_read_config_constants` yalnız çıplak sayı
eşleştiriyordu, `_envf("X_...", 3.75)` biçimini okuyamıyordu → benimsenen çıkış
setinin **tamamında** sessizce varsayılana düşüyordu. Düzeltildi.

Yeni alanlar: `expectancy_exit_only_usd` (eski manşet, süreklilik için),
`entry_fees_usd`, `unrecorded_cost_usd`.

---

## Tur 3 — Evren kürasyonu, seçim yanlılığı düzeltmesiyle

**Tarih:** 27 Ağu 2026 · **Aday:** 23 coin · **Pencere:** 665g (önbellek)

### Neden bu turun yöntemi öncekilerden farklı

Bu projedeki her kürasyon aynı biçimi aldı: N coini bir pencerede koştur, sırala,
ilk birkaçını al. **Bu sayı hiçbir şeyin tahmini değil.** 23 gürültülü adayın en
iyi 5'i *yapı gereği* iyi görünür — 23 yazı-turadan da gurur verici bir sıralama
çıkarabilirsiniz. `config.py` semptomu zaten belgeliyor (90g sıralaması sonraki
dönemle −0.31 korelasyon) ama hastalığı adlandırmıyor.

Bu tur üç ayrı soruyu ayrı ayrı ölçüyor:

| # | Soru | Yöntem |
|---|---|---|
| 1 | Seçim **kuralı** işe yarıyor mu? | Walk-forward: her fold'un ÖNCESİNDEKİ veriyle seç, fold'un KENDİSİNDE ölç, örneklem-dışı parçaları birleştir. İki null kola karşı: hepsi eşit ağırlık, ve aynı boyutta rastgele seçim. |
| 2 | Bu yordam beni ne sıklıkta kandırır? | **PBO** (CSCV — Bailey, Borwein, López de Prado & Zhu): pozisyon matrisini S bloğa böl, tüm dengeli eğitim/test bölüntülerinde in-sample kazananın out-of-sample medyanın altına düşme sıklığı. |
| 3 | Kazanan, kazanan olmayı hak ediyor mu? | **Deflated Sharpe** (Bailey & López de Prado): 23 bağımsız adayın beklenen maksimum Sharpe'ı sıfırdan büyüktür; kazanan önce o çıtayı aşmalı. Deneme sayısı + örneklem uzunluğu + çarpıklık + basıklık düzeltilir. |

**Purge + embargo:** Fold sınırından önce açılıp sonra kapanan pozisyon sızdırır.
Pozisyonlar **açılış** zamanına göre fold'a atanıyor, sınırı aşanlar purge
ediliyor, ardından **192 barlık embargo** uygulanıyor — çünkü `bars_held` TP1'de
sıfırlandığı için gerçek azami tutuş `2×TIMEOUT_BARS`; 665g'de ölçülen maks 187 bar.

### Altyapı

- `experiments/gen_candidates.py` — pahalı replay'i BİR KEZ yapar, coin başına
  zaman damgalı pozisyon kaydı yazar. Analiz saniyeler sürer, yöntem replay
  bedeli ödemeden iterasyona açılır.
- `experiments/wfa.py` — walk-forward + PBO + DSR.
- `tests/test_wfa_stats.py` — **16 test**, istatistik çekirdeği bilinen değerlere
  sabitlendi: ters normal CDF kuantilleri, normal örneklemde çarpıklık/basıklık,
  beklenen-maks-Sharpe'ın Monte Carlo kontrolü, PBO'nun saf gürültüde yüksek /
  gerçek edge'de düşük çıkması, C(8,4)=70 bölüntünün tam sayılması.
  *Sessizce yanlış bir yanlılık düzeltmesi, hiç olmamasından kötüdür: kimsenin
  yeniden türetmediği bir sonuca otorite kazandırır.*

### ⚠️ Önce: harness'ta hayatta-kalma yanlılığı bulundu ve düzeltildi

İlk koşuda 23 coinin **9'u** `PEAK_DD_LIMIT`'e çarpıp pencereyi yarıda bıraktı
(LDO 178 günde). Geç fold'larda yalnızca **hayatta kalanlar** kalıyordu — yani
harness'ın kendi içinde ürettiği bir hayatta-kalma yanlılığı. Fold 3'ün 7
pozisyonluk saçma sonucu bundandı.

`BT_RESTARTS=1` ile yeniden koşuldu (23/23 tam pencere) ve `gen_candidates.py`
artık env verilmezse uyarıyor, sonda kaç coinin yarıda kaldığını sayıyor.
*Bu, aynı sınıftan bugünkü ikinci tuzak — ilki 665g portföy kollarındaydı.
Uzun pencerede hard-stop, karşılaştırmayı sessizce bozan yapısal bir sorun.*

### Sonuçlar — üçü de aynı yöne işaret ediyor

**1 · Walk-forward (6 fold, purge + 192 bar embargo):**

| kol | n | ort R |
|---|---:|---:|
| **SEÇİLEN (ilk 5)** | 209 | **−0.109** |
| en kötü 8 elendi (kalan 15) | 885 | −0.058 |
| hepsi (23, eşit ağırlık) | 1350 | −0.068 |
| **rastgele 5** | 301 | **−0.021** |

**Seçim kuralı her iki null kolun da ALTINDA.** Seçim primi −0.041R, t=−0.51 —
"aktif olarak zararlı" diyemeyiz ama **"işe yarıyor" kesinlikle diyemeyiz.**
Fold 3 (n=7) atılsa da tablo değişmiyor: seçilen −0.084, hepsi −0.073,
rastgele −0.057.

Alt-eleme kolu ayrıca test edildi çünkü *en iyiyi seçmek* ile *en kötüyü elemek*
farklı sorular ve sıralamanın alt ucunda bilgi olabilirdi. Yok: −0.058 vs −0.068,
fark yok.

**2 · PBO = 0.486** (70 dengeli bölüntü, 8 blok). In-sample kazanan,
out-of-sample medyanın altına **%48.6 sıklıkta** düşüyor — yazı-tura.
Sıralama neredeyse hiç bilgi taşımıyor.

**3 · Deflated Sharpe = 0.000.** Gözlenen Sharpe −0.099; 23 denemenin şans eşiği
+0.253. Kazanan, kazanan olmayı hak etmiyor.

### 🔴 Asıl bulgu — mevcut evrenin sicili seçildiği pencerenin içinde

665 günü, kürasyonun yapıldığı pencere (son 240g) ile öncesi olarak ayırdım:

| | önceki ~425g | son 240g (kürasyon penceresi) |
|---|---:|---:|
| **dağıtılan 5 coin** | **−0.105R** (n=339) | **+0.265R** (n=121) |
| tüm 23 coin | −0.092R (n=1535) | +0.017R (n=482) |

Dağıtılan 5 coin, **seçildikleri pencerede** muhteşem (+0.265R); o pencerenin
dışında **23 coinlik ortalamadan da kötü** (−0.105 vs −0.092).

> Ders kitabı seçim yanlılığı, artık ölçülmüş durumda. Faz 6'nın "+0.249R,
> 5/5 coin pozitif" sonucu bir edge tahmini değil, seçim işleminin kendi
> izidir.

### Karar — **YENİDEN KÜRASYON YAPILMADI**

Yeni bir "en iyi 5" üretmek, az önce örneklem dışında bilgi taşımadığını
kanıtladığım yordamı bir kez daha koşturmak olurdu. Ölçüm, evrenin *hangi* 5
coin olduğunu değil, **coinleri geçmiş getiriye göre seçme fikrini** çürüttü.

Pratik sonuç:
- Mevcut 5'i değiştirmek için **kanıt yok** (yenisini seçmek için de yok).
- Sıralama gürültüyse, 5 coinde yoğunlaşmak **ödüllendirilmeyen** idiyosinkratik
  risktir. Evreni genişletmek — geçmiş getiri iddiasıyla DEĞİL, çeşitlendirme
  gerekçesiyle — savunulabilir. `MAX_OPEN=2` olduğu için genişletmek maruziyeti
  artırmaz, yalnızca aynı 2 slot için aday havuzunu büyütür. **Portföy düzeyinde
  test edilmeli** (henüz koşulmadı).
- Daha derin sorun: 665g'de eşit-ağırlık **−0.068R**. Hiçbir coin seçimi bunu
  düzeltmiyor. Pozitif sonuçların tamamı son 8 ayda yoğunlaşıyor.

---

## Yan bulgu — E16 kapatıldı (27 Ağu, A/B değil, bug)

`_load_state`, `TOKENS`'ta olmayan sembolleri atlıyordu. Açık pozisyonu olan bir
coin evrenden çıkarılırsa pozisyon **yok oluyordu**: kapanış bacağı yok,
gerçekleşmemiş PnL kaydedilmiyor, `--testnet` botunda borsa pozisyonu yönetilmeden
açık kalıyor. 22 Ağustos'taki 8→5 küçültmesinde bir kez oldu — ve Tur 3 evreni
yine değiştireceği için **tam da şimdi** kapatılması gerekiyordu.

**Çözüm — WIND-DOWN:** yetim semboller kitaba alınıyor, her barda işlenmeye devam
ediyor (stop/hedefleri ateşlenebilsin diye) ama **yeni giriş açamıyorlar**; flat
olunca kitaptan düşüyorlar. Giriş kapısı test edilebilir olsun diye
`_blocks_new_entries()` metoduna çıkarıldı. 5 test (`tests/test_winddown.py`).

---

## Tur 4 — Sıfırdan strateji arama laboratuvarı (`experiments/lab/`)

**Tarih:** 27–28 Ağu 2026 · **Talimat:** "sıfırdan düşün, sanki projeye ilk defa
başlıyormuşuz gibi, hiçbir zemin olmadan"

### Mimari kopuş: durum makinesi yerine panel

`paper_bb`/`backtest` tek sembolü bir durum makinesinden geçirir. Bu biçim, üç
seçimi **sorulamaz kılarak görünmez yapıyor**: 5 dakikalık zaman dilimi,
tek-varlık tezi, ve evren. Üçü de miras alındı; ikisi bugün ölçüldü ve tutmadı
(sürtünme kimliği 5m stop mesafelerini eziyor; coin seçimi bilgi taşımıyor).

Lab piyasayı **matris** olarak temsil ediyor: strateji `w[t,s]` ağırlık matrisi
döndürüyor, motor bir bar geciktirip maliyeti kesiyor.

| dosya | ne |
|---|---|
| `lab/panel.py` | 23 coin × istenen zaman dilimi (5m'den yeniden örnekleme), hizalı matrisler + listeleme maskesi |
| `lab/engine.py` | `r[t] = Σ w[t−1,s]·ret[t,s] − maliyet·Σ|Δw|` — gecikme ve maliyet **motorda dayatılıyor** |
| `lab/strategies.py` | 6 aile: `buy_hold`, `inv_vol`, `ts_mom`, `xs_mom`, `xs_rev`, `donchian` |
| `lab/search.py` | ızgara + eğitim/test + DSR (gerçek deneme sayısıyla) + PBO (CSCV) |
| `tests/test_lab_engine.py` | **16 test** |

**Neden gecikme motorda:** kâhin testi — `w[t] = sign(ret[t])` veren bir
"strateji" hiçbir şey kazanamamalı; `w[t] = sign(ret[t+1])` ise tam
`Σ|ret|` kazanmalı. İkisi de test edilmiş durumda. Bu projedeki her olası
lookahead hatası, her yazarın hatırlamasına bırakılmak yerine burada yapısal
olarak imkânsız.

**Neden bu altı aile:** incumbent uzayda tek bir nokta (tek-varlık, 5m,
uzun-yanlı, breakout). Aileler onun hiç değiştirmediği eksenleri değiştiriyor:
kesitsel vs zaman-serisi, momentum vs dönüş, ve **sinyalsiz null'lar**
(`buy_hold`, `inv_vol`) — bu repodaki hiçbir sonuç bugüne dek "sadece piyasada
ol"a karşı kontrol edilmemişti.

### İlk duman testi (4h, 23 coin, 119 konfig) — düzenek dürüst davranıyor

| | |
|---|---|
| eğitimde en iyi SR | **+0.90** |
| aynı konfigin TEST SR'si | **−0.08** |
| PBO | **0.700** (yazı-turadan kötü) |
| DSR | **0.000** (119 deneme hesaba katılınca) |
| null: `buy_hold` test | SR −0.51, yıllık **−41.4%** |

Kazanan uydurmadı, başarısızlığı raporladı — kurulma amacı buydu.

**Tasarım kusuru fark edildi:** 665g penceresi neredeyse tümüyle düşüş
(buy_hold −%41). Uzun-yanlı her strateji burada mahkûm ve test, stratejinin
genel değeri hakkında bilgi vermiyor. Bu yüzden ikinci panel eklendi:
**2095g × 6 coin (2020-10 → 2026-08)** — 2021 boğası ve 2022 ayısı dahil.

> Erken ama dikkat çekici: düşüş penceresinde ayakta kalan tek şey
> **uzun/kısa kesitsel momentum** (`longshort=True`) — test DD −%10.5 vs
> buy_hold −%52.3. Yönlü değil, göreli. Doğrulanması gerek.

### Sonuçlar — 2095g × 6 coin × {1h, 4h, 1d} · **357 konfigürasyon**

Eğitim: 2020-10 → ~2024-05 (2021 boğası dahil) · Test: ~2024-05 → 2026-08

| | eğitim SR | TEST SR | test yıllık | test DD |
|---|---:|---:|---:|---:|
| en iyi (ts_mom 4h, lb=200, rebal=24) | **+1.81** | **−0.37** | −38.3% | −83.2% |
| 2. (ts_mom 4h, lb=200, rebal=6) | +1.63 | −0.41 | −40.0% | −88.4% |
| 3. (donchian 4h, lb=288) | +1.60 | −0.41 | −42.3% | −83.2% |
| testte en iyi olabilen (xs_mom 4h k=3 lb=12) | +1.39 | +0.04 | −24.3% | −84.0% |
| **null: buy_hold** | — | −0.01 | **−25.8%** | **−80.3%** |
| **null: inv_vol** | — | +0.01 | −20.2% | — |

**DSR = 0.000** (357 deneme; test SR −0.008 vs şans eşiği +1.70)
**PBO = 0.171**

### Bunun okunuşu — ve neden iki metrik çelişmiyor

PBO **düşük** (0.171) ama sıralı eğitim/test **felaket**. Çelişki değil, teşhis:

- **PBO blokları karıştırır**, yani boğa ve ayı dönemlerini birbirine katar. Düşük
  PBO, sıralamanın *gürültüye uydurma* olmadığını söylüyor — bu aileler gerçekten
  bir şey yakalıyor.
- **Sıralı bölme karıştırmaz.** 2020-2024'te işe yarayan ne varsa 2024-2026'da
  çalışmayı bıraktı.

> Yani sorun aşırı-uydurma değil, **rejim**. Ve rejim şu: test döneminde
> `buy_hold` yıllık **−%25.8**, drawdown **−%80**. Alt piyasası düştü; hiçbir
> uzun-yanlı strateji bunu yenemez, çünkü yenecek bir şey yok.

Hiçbir konfigürasyon null'ları anlamlı biçimde geçmedi. **357 denemeden çıkan
şey: bu uzayda, bu dönemde, "piyasada olmak"tan iyi bir yön yok.**

### Bugünün üç ölçümü aynı yere bakıyor

| ölçüm | sonuç |
|---|---|
| Faz 8 · dağıtılan sistem, 665g | 3 hard-stop, +%8.5 (yalnız yeniden başlatma varsayımıyla) |
| Tur 3 · coin seçimi | örneklem dışında bilgi yok (PBO 0.486, DSR 0) |
| Tur 4 · 357 strateji konfigi | hiçbiri buy_hold'u geçmiyor; pozitif sonuçlar hep eğitim tarafında |

Üçü de aynı şeyi farklı yerden söylüyor: **pozitif sonuçların tamamı, seçimin
yapıldığı pencerenin içinde.** Bu bir parametre sorunu değil.

### Yarın için açık uçlar

- 665g × 23 coin × {15m, 1h, 4h} koşusu (`experiments/logs/lab_665.log`) —
  15m'in sürtünme altında ne yaptığını görmek için; erken duman testinde
  uzun/kısa kesitsel momentum ayı penceresinde DD'yi −%52'den −%10'a indirmişti.
- Uzun/kısa (piyasa-nötr) kolları ayrı incele: mutlak Sharpe'ta kaybediyorlar ama
  **düşüşte ayakta kalan tek aile** onlar. Bu, ROADMAP'in 2026-06'da not ettiği
  "delta-nötr kazanıyor, yönlü kahramanlar patlıyor" gözlemiyle örtüşüyor.
- Aileler hâlâ eksik: funding/carry (veri yok), likidite/spread filtresi,
  volatilite rejimi koşullaması.
- `search.py` artık vektörleştirildi (CSCV'de 50 bin `bar_sharpe` çağrısı
  numpy'a taşındı) — daha büyük ızgaralar mümkün.

---

## Tur 5 — Uzun/kısa (piyasa-nötr) kolun anatomisi · 28 Ağu 2026

Tur 4'ün tek umut verici bulgusu: uzun/kısa kesitsel momentum, piyasa −%40
düşerken −%2,3 kaybetmiş, DD −%10,5 vs −%54. Bunu kovalamadan önce çerçeveyi
sorgulamak gerekti.

### 1. "Ayıda ayakta kaldı" bir başarı DEĞİL

`_normalise` gross'u 1.0'a çekiyor → uzun/kısa kitap ≈ +0.5 uzun / −0.5 kısa.
Ölçüldü: **net maruziyet 0.000, piyasaya beta −0.021.** Piyasaya maruz olmayan
bir kitap düşen piyasaya kaybedemez; bu aritmetik, edge değil.

### 2. Bacak ayrıştırması — edge var, sürtünmeden küçük

Test dönemi (`decompose.py`):

| | |
|---|---|
| uzun bacak | −%15,7 |
| kısa bacak | +%17,3 |
| **brüt kesitsel yayılım** | **+%1,6** |
| devir maliyeti | **−%2,5** |
| net | −%0,9 |

Volatilite eşitlenmiş kıyas: uzun/kısa −%2,3/yıl @ %14,9 vol · aynı vole
ölçeklenmiş `buy_hold` −%8,3/yıl. Yani piyasadan iyi, ama **yıllık alfa −%1,9**.

> Dünkü `0.0015/sl_frac` bulgusunun başka kılıkta tekrarı: **edge gerçek ama
> sürtünmeden küçük.**

### 3. Parametre seçimi de transfer etmiyor (432 konfig)

| | |
|---|---|
| eğitimde ilk 10'un testte pozitif olanı | **0/10** |
| eğitim–test Sharpe korelasyonu | **−0.128** |
| eğitim kazananı | SR +2.47 → test **−1.82** |
| DSR (432 deneme) | **0.001** |

Coin seçimi için ölçülen imza (90g↔240g = −0.31), şimdi **parametre seçimi**
için de aynı: hafif NEGATİF korelasyon.

### 4. Mekanizma mı, seçim mi? — tüm ızgarada ortalama

Bir etki ancak **eğitim ve testte aynı yönde** ise mekanizmadır:

| parametre | eğitim yönü | test yönü | hüküm |
|---|---|---|---|
| **k (isim sayısı)** | çok → iyi | çok → iyi | ✅ **tutarlı** |
| rebalance | yavaş → iyi | hızlı → iyi | ❌ ters |
| buffer | 3 → iyi | 0 → iyi | ❌ ters |
| lookback | 400 → iyi | 200 → iyi | ❌ ters |
| vol_adj | True → iyi | False → iyi | ❌ ters |
| skip | ~eşit | 0 → iyi | ❌ zayıf |

**Tek tutarlı etki: daha çok isim tut.** Bu alfa değil **çeşitlendirme** — Tur
3'ün "5 coinde yoğunlaşmak ödüllendirilmeyen risk" sonucuyla aynı yere çıkıyor.

Hysteresis (`buffer`) maliyeti tasarlandığı gibi düşürdü (−%7,9→−%3,7→−%2,5)
ama neti bozdu (−%3,3→−%15,3→−%34,1): bayat pozisyon tutmak, kazandırdığı devir
maliyetinden fazlasını yiyor.

### 5. 🔴 Suçlu maliyet modeli DEĞİL

108 konfig, maliyet parametresi süpürüldü (test dönemi):

| maliyet/yön | ort net %/yıl | pozitif | en iyi |
|---|---:|---:|---:|
| %0.075 (şu anki) | −25.8% | 8/108 | +31.2% |
| %0.040 (slipajsız) | −23.9% | 12/108 | +36.7% |
| %0.020 (maker/limit) | −22.8% | 18/108 | +39.8% |
| **%0.000 (sürtünmesiz)** | **−21.7%** | 19/108 | +42.9% |

> **Sıfır maliyette bile çalışmıyor.** Maliyet ~4 puan/yıl ediyor ama bağlayıcı
> kısıt o değil. "Belki sorun yürütmedir / maker emirlere geçelim" hipotezi
> **kapandı** — sinyal test döneminde ölü.

### Karar

Uzun/kısa hattı kapandı. Dün "yarın buradan devam edelim" dediğim yol, ölçünce
çıkmaz çıktı — çerçevenin kendisi (net maruziyet ~0) beni yanıltmıştı.

---

## Tur 6 — Funding: fiyattan türemeyen ilk bilgi · 28 Ağu 2026

Dört tur boyunca sadece OHLCV vardı ve dördü de aynı cevabı verdi. Funding
**türev değil** — fiyatın söyleyemediği bir şey söylüyor ve araştırmanın (bölüm
09) sürekli işaret ettiği yer orası.

### Altyapı

| dosya | ne |
|---|---|
| `lab/fetch_funding.py` | 23 sembol × 2020-09'dan itibaren funding geçmişi (ccxt, sayfalı, tekrar-kaydı ayıklar) |
| `lab/panel.py` `load_funding` | ödemeleri bar aralığına **TOPLAR** (Binance kimi çiftte 8h kimide 4h öder; reindex sessizce üçte ikisini düşürürdü) |
| `lab/engine.py` | `evaluate(..., funding=)` → `−w·rate`. Pozitif oranda **long öder, short alır** |
| `lab/strategies.py` | `xs_funding` (nötr), `carry_short` (kontrol: piyasaya kısa) |
| `tests/test_lab_funding.py` | **12 test** — işaret konvansiyonu, gecikme, bar toplama |

> İşaret hatası çökmez; maliyeti gelire çevirir ve tam da umduğumuz sonucu
> **imal eder**. O yüzden dört ayrı testle sabitlendi.

### 🟢 İlk kez: transfer eden bir sıralama

| seçim türü | eğitim–test korelasyonu |
|---|---|
| coin seçimi (Tur 3) | −0.31 / −0.55 |
| xs_mom parametreleri (Tur 5) | −0.128 |
| **xs_funding parametreleri** | **+0.771** |

### Ayrıştırma — gelir gerçek, ama fiyat riski onu boğuyor

`xs_funding` net maruziyeti **0.00**. 18 konfigin **18'inde de funding bileşeni
pozitif** (+%5.6 … +%8.5/yıl). Fiyat bileşeni −%24.4 … +%3.2 arası, çoğu negatif.

**5.8 yıl, altı alt-dönem (lb=90 k=2 reb=6):**

| dönem | fiyat | funding | maliyet | net |
|---|---:|---:|---:|---:|
| 2020-10→2021-10 | +16.7 | +6.2 | −2.5 | +20.4 |
| 2021-10→2022-10 | −42.1 | +4.4 | −1.7 | −39.4 |
| 2022-10→2023-09 | +49.5 | +6.8 | −3.8 | +52.5 |
| 2023-09→2024-09 | −8.9 | +2.3 | −4.0 | −10.5 |
| 2024-09→2025-09 | −8.6 | +2.3 | −4.3 | −10.7 |
| 2025-09→2026-08 | +17.9 | +6.3 | −3.5 | +20.7 |
| **ortalama** | **+4.1** | **+4.7** | **−3.3** | **+5.5** |

**funding 6/6 dönemde pozitif · fiyat 3/6** — biri mekanizma, diğeri yazı-tura.

Tüm 5.8 yıl: yıllık **−%0.4**, SR +0.16, DD **−%56.7**, vol %34.5.
Güvenilir bileşen (+4.7 − 3.3 = **+%1.4/yıl**) üstüne ±%40 fiyat gürültüsü.

Kontrol kolu işini gördü: `carry_short` +%26.7 getirdi ama +%27.3'ü **fiyattan**
(düşen piyasada kısa olmak), funding katkısı yalnız +%2.7, DD −%41. Yani
"carry kazandı" diyemezdik.

### 🔑 Asıl ders — fiyat riski kabul edilecek değil, YOK EDİLECEK şey

Kurduğum şey **farklı iki varlık** arasında nötr (yüksek-funding short, düşük
long), o yüzden göreli fiyat riski taşıyor. Gerçek carry **aynı varlıkta**
long spot + short perp: varlık başına delta-nötr, fiyat riski **yapısal olarak
sıfır**, getiri = funding − maliyet.

**Basis ticareti ne kazandırırdı (brüt funding):**

| | medyan | pozitif coin |
|---|---:|---|
| tüm tarih (≈6 yıl) | **+%8.8/yıl** | 19/23 |
| son 2 yıl | **+%4.0/yıl** | 18/23 |

Prim sıkışmış (kalabalıklaşan bir ticarette beklenen) ama hâlâ geniş ve pozitif.

### Maliyet burada gerçekten bağlayıcı — xs_mom'un aksine

| maliyet/yön | xs_funding en iyi | (kıyas) xs_mom ort |
|---|---:|---:|
| %0.075 | +%4.7 | −%25.8 |
| %0.020 maker | +%9.8 | −%22.8 |
| %0.010 | +%10.7 | −%21.7 (sıfırda bile) |

xs_mom'da sıfır maliyette bile ölüydü; burada maliyeti düşürmek doğrudan neti
büyütüyor. **Yürütme bu ailede gerçek bir kaldıraç.**

### Yarın için

Gerçek basis ticareti **spot fiyat verisi** ister (elde yok, çekilebilir).
O kurulunca ölçülecekler: teminat maliyeti, funding'in negatife dönme riski,
likidasyon yönetimi, ve tek seferlik giriş/çıkış komisyonunun amortismanı.
Bu bir ızgara değil — mekanizması olan tek aday.

---

## Tur 7 — Gerçek basis ticareti ve kaldıracın sınırı · 28 Ağu 2026

Spot veri çekildi (23 sembol, 4h, 2020-09'dan). `lab/basis.py`:
`r = spot_getiri − perp_getiri + funding`, maliyet **iki bacak** üzerinden.

### Ölçüm

| | yıllık | vol | DD | Sharpe |
|---|---:|---:|---:|---:|
| tüm 5.8 yıl | +%11.7 | %2.7 | −%2.0 | +4.24 |
| **son 2 yıl** | **+%4.55** | %1.1 | **−%0.42** | +4.17 |

Tüm-dönem rakamı plan yapılacak sayı değil: 2021 tek başına +%37 ödemiş.
Sermaye haircut'ı sonrası gerçekçi aralık **+%3.0 … +%4.5**.

İnanmadan önce üç kontrol: (a) en yüksek Sharpe'lı konfig son dönemde **hiçbir
şey tutmuyordu** — sabit `min_funding` eşiği oranlar sıkışınca her coini eliyor,
yani Sharpe'ının bir kısmı "flat olmanın" Sharpe'ı; (b) 2021 katkısı ayrıldı;
(c) "ağırlık 1.0"ın sermaye karşılığı açıkça yazıldı.

### 🔴 Kaldıraç — asıl gerekçe çürüdü

Kaldıraçsız getiri stablecoin borç vermeyle aynı aralıkta, dolayısıyla bu
stratejiyi inşa etmenin **tek gerekçesi kaldıraçtı**. Ölçüldü:

Pozisyonlar ~3 ay tutuluyor. Son 2 yılda tutulan coinler **XRP +%366,
ADA +%236, UNI +%174** hareket etmiş. Perp bacağı AYRI teminat hesabındaysa
1× likidasyon eşiği ≈ +%99.5 → **kaldıraçsız bile 5 kez likide olurdu.**

| kaldıraç | likidasyon eşiği | gerçekleşen | manşet getiri |
|---|---:|---:|---:|
| 1x | +%99.5 | 5 kez | +%4.6 |
| 3x | +%32.8 | 22 kez | +%13.9 |
| 10x | +%9.5 | 35 kez | +%46.5 |

> Önceki turun "3x ile +%14/yıl" rakamı bir getiri değil, **pozisyonun hayatta
> kaldığını varsayan bir sayı** — ve kalmıyor.

**Bu bir strateji sonucu değil, operasyonel ön koşul:** ticaret cross/portfolio
margin ister — spot'un short'a teminat sayıldığı, hedge'in tanındığı yapı.
O varsa fiyat riski gerçekten sıfıra yakın ve kaldıracı likidasyon değil borsa
kuralları sınırlar. Yoksa düşük riskli falan değil.

Ve bu, tüm pozisyonu tek borsada toplar — delta-nötr çerçevesinin gizlemeye
meyilli olduğu bir risk.

---

## Tur 8 — Ölçek ve sermaye: basis ticaretinin gerçek tavanı · 29 Ağu 2026

Kaldıraç gerekçesi Tur 7'de çöktü. Geriye iki soru kaldı: **gerçekte ne kadar
büyüklük kaldırır** ve **$1 notional kaç $ sermaye ister**.

### Emir defteri derinliği stratejiyi yeniden tanımladı

Canlı defterlerden ölçülen, **iki bacak birden** giriş kayması:

| sembol | $10k | $50k | $250k | $1M |
|---|---:|---:|---:|---:|
| BTCUSDT | +0.000% | +0.000% | +0.000% | **+0.008%** |
| ETHUSDT | +0.000% | +0.000% | +0.003% | +0.023% |
| LINKUSDT | +0.027% | +0.077% | +0.389% | +3.659% |
| UNIUSDT | +0.043% | +0.122% | +0.724% | +6.864% |
| INJUSDT | +0.130% | +0.288% | +2.090% | derinlik yok |
| POLUSDT | +0.178% | +0.720% | +3.209% | +22.290% |

> Strateji yılda ~%4-5 kazanıyor. POL'de $250k girişin **tek seferlik** kayması
> %3.2 — **dokuz aylık gelir, tek işlemde.** "En yüksek funding'i seç" mantığı
> ölçekte çöküyor: en yüksek funding'li coinler en ince defterliler.

### Dağıtılabilir evren (son 2 yıl)

| evren | funding | maliyet | NET | vol | DD |
|---|---:|---:|---:|---:|---:|
| BTC + ETH | +4.85% | −0.23% | **+4.65%** | 0.56% | −0.58% |
| BTC+ETH+XRP+ADA+LINK+LTC | +5.37% | −0.39% | **+5.00%** | 0.98% | −0.37% |
| tüm 23 (önceki kurgu) | +4.98% | −0.54% | +4.55% | 1.09% | −0.42% |

Likit 6 coin, 23 coinden **daha iyi** — ince altların funding primi kaymayı
karşılamıyor.

### Sermaye verimliliği (futures bakım %2.5, başlangıç %5 → maks 20x)

| yapı | yıllık |
|---|---:|
| Portfolio Margin (spot teminat sayılır + %5 futures marjı) | **+4.43%** |
| Cross margin, muhafazakâr tampon | +3.72% |
| Ayrı hesaplar ($1 spot + $1 futures marjı, likidasyonsuz) | +2.33% |

### 🔴 KARAR — dağıtmaya değmez

**En iyi durumda (+%4.43/yıl) stablecoin borç vermeyle (~%4-8, DD ~0) aynı
aralıkta, üstelik:**

- borsa/karşı taraf riski tek noktada toplanıyor
- iki bacaklı operasyonel yük ve margin yönetimi var
- 2021'in +%37 primi gitmiş, kalabalıklaşmayla sıkışmış
- kaldıraç ancak Portfolio Margin'le mümkün, o da riski yoğunlaştırıyor

Mekanizma gerçek ve 5.8 yılın her alt-döneminde pozitif. Ama **perakende
erişimle risksiz alternatifi yenmiyor.** Bu bir başarısızlık değil, bir ölçüm:
inşa etmeden önce öğrenildi.

---

## Sıradaki fikirler (henüz hipotez değil)

- **Walk-forward.** E1–E8 arası sekiz çıkış kolu denendi ve en iyisi seçildi,
  seçim yanlılığı düzeltmesi olmadan. Deflated Sharpe / PBO literatürü tam olarak
  bunu ölçmek için var. Şu anki iki-pencere kuralı ucuz bir vekil; gerçek çözüm
  walk-forward.
- **Probe katmanı gerekli mi?** 240g'de probe drag −$8 (küçük) ama girişi 1 bar
  geciktiriyor. Kaldırmanın etkisi ölçülmedi.
- **Session filtresi (04–23 UTC).** Env'e açık değil, hiç test edilmedi.
- **Funding sleeve (Phase B).** Yönlü sistemde Sharpe ~0.8 tavanı var; delta-nötr
  taşıma stratejileri 2026'da çok daha iyi risk-ayarlı getiri üretiyor.
