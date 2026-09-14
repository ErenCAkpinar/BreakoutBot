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

## NEAR tekrar giriş incelemesi — 2026-09-10

**Soru:** 4 ve 6 Eylül'de NEARUSDT aynı BULL rejiminde birkaç kez TP2'de
kapanıp yeniden LONG açıyor. Tek pozisyon taşımak daha fazla net getiri sağlar mı?

**Önceden belirlenen H-N1:** Sabit TP2 tavanını kaldırıp mevcut 3.75 ATR trail,
96 bar faz timeout'u, giriş sinyalleri ve risk kurallarını korumak, güçlü
hareketlerde kazananı uzatır ve yeniden giriş maliyetini azaltabilir. Ters risk:
gerçekleşmemiş kârın geri verilmesi ve iki portföy yuvasından birinin daha uzun
meşgul kalması. Günlük tek işlem kotası ayrı hipotezdir; bu deney onu ölçmez.

Kontrol `N1-control-20260910`: güncel config varsayılanları.
Aday `N1-no-tp2-20260910`: yalnız `X_TP2_ATR=inf` (sonlu fiyatlarda TP2 erişilemez;
SL/trail/timeout hâlâ işler). Çalışan botun kodu veya ayarları değiştirilmedi.

İlk tarama: aynı sabitlenmiş 240g verisi, 5 coin, ortak hesap, tüm yürütme
maliyetleri ve portföy kapılarıyla `experiments/run_arm.sh` üzerinden iki kol.
240g'de hesap getirisini iyileştirmezse ret; iyileştirirse 665g karşılaştırması
gerekir. İki pencereyi geçmeden benimseme yok; geçmesi de istatistiksel kanıt
veya yeni veri üzerinde doğrulama yerine geçmez.

### 240g sonucu — 11 Eylül'de incelendi

| Kol | Net hesap kârı | Son bakiye | Full | MaxDD (gerçekleşmiş bakiye) | Hard stop |
|---|---:|---:|---:|---:|---:|
| N1-control-20260910 | +$435.46 | $1435.46 | 114 | −%6.14 | 0 |
| N1-no-tp2-20260910 | **+$540.58** | **$1540.58** | 114 | −%6.23 | 0 |

Kontrol, benimsenmiş `R1-sl225t96_240d` sonucunun **1.318 bacağını ve bakiyesini
birebir** tekrar üretti. Her iki kol tam 240g koştu; açık bacak yok, mutabakat
hatası ~0. Adayda TP2=0, SL=42, TRAIL=65, TIMEOUT=7.

Fark +$105.12; komisyon tasarrufu değil (giriş maliyeti iki kolda da $84.64,
full sayısı 114). INJ net katkı artışı +$93.72, NEAR +$26.31, ADA +$35.48;
UNI −$42.16, POL −$8.24. İki eşleşen INJ işleminin ek kârı toplam farktan
büyük: nadir uzun kazananlara bağımlılık var. 240g taraması olumlu; **benimseme
kararı değil**.

Eylül'deki altı gerçek NEAR girişinde ayrı çıkış replay'i yapıldı. Kontrolün
altı çıkış zamanı/türü/fiyatı kayıtları tekrar üretti. Yalnız TP2 kaldırılınca
dört TP2 kazancı da azaldı: net $12.60→$4.83, $12.83→$11.41,
$12.88→$10.07, $25.98→$11.75; mevcut trail geri çekilmelerde kapatıyor.
Bu eşleşmiş-giriş teşhisi portföy testi değildir; yeniden giriş zamanlarını
aynı varsayar. Eylül örneği ile 240g sonucun ters yönlü olması, tek ekranın
strateji değişikliği için kanıt olamayacağını gösteriyor.

665g aday başlatıldı: `./experiments/run_arm.sh N1-no-tp2-20260910 665
BT_RESTARTS=1 X_TP2_ATR=inf`. Referans kayıtlı `R1-sl225t96_665d` ($1085.22,
MaxDD −%37.56, 3 hard stop); çekirdek kod benimsenmiş sürümle aynı, taze 240g
kontrolü de bunu davranışsal olarak doğruladı. 665g kontrolü yeniden koşulmadı.
### 665g tamamlandı — N1 reddedildi

| Kol | Net hesap kârı | Son bakiye | Full | MaxDD (gerçekleşmiş bakiye) | Hard stop |
|---|---:|---:|---:|---:|---:|
| R1-sl225t96 (kayıtlı kontrol) | **+$85.22** | **$1085.22** | 396 | **−%37.56** | **3** |
| N1-no-tp2-20260910 (yeni) | +$68.89 | $1068.89 | 387 | −%45.57 | 4 |

Aday tam 665g'yi tamamladı; TP2=0, SL=193, TRAIL=175, TIMEOUT=19; 5.090 bacak,
açık bacak yok, mutabakat ~0. Giriş maliyeti $222.67→$215.30 azalsa da
net kâr **$16.33 azaldı**; MaxDD **8.01 yüzde puan kötüleşti**, bir ek hard
stop gerekti. Her iki 665g sonucu operatör restart'ı varsayımı içerir.

**Karar: RED.** 240g'de +$105.12 iyileşme, 665g'de −$16.33 kötüleşme:
iki pencere şartını geçmiyor. Bu küçük kâr farkı, hangi kuralın daha iyi
olduğuna ilişkin istatistiksel kanıt değildir; benimseme şartı sağlanmadı ve risk davranışı da
kötüleşti. Mevcut TP2 ve yeniden giriş düzeni korundu; canlı değişiklik yok.

NEAR katkısı tek başına iki pencerede iyileşti (240g +$56.82→+$83.13;
665g −$54.53→−$9.19). Bütün coinlerin çıkışı değişmiş bir portföyden geldiği
için bu, NEAR'a özel parametre kararı değildir. Ayrı hipotez ve yeni veri
doğrulaması olmadan coin bazında seçip uygulama yok. Eylül'deki dört gerçek
TP2 işleminin eşleşmiş giriş analizinde hepsi kötüleşti.

Ayrıntılar ve fiyat grafiği: [NEAR incelemesi](reports/20260910-near/REPORT.md).

---

## N2 — TP2'de yarım kapanış, kalanla devam — 2026-09-11

**Kullanıcı isteği:** TP2'de bir kısmını kapatıp kalanı taşıma fikrini dene.
**Önceden belirlenen tek kol:** %50 TP2 kapanışı; kalan %50 aynı 3.75 ATR trail
ve mevcut 96 bar faz deadline'ıyla devam eder. TP2'de sayaç sıfırlanmaz. Başlangıç
riski, SL, TP1, sinyal, cooldown ve portföy slotu kuralları aynı. Kalan parça
açıkken sembolde yeni giriş olmaz ve MAX_OPEN slotu serbest kalmaz.

Hipotez: N1'in büyük kazananlarını kısmen korurken TP2'de kârın yarısını
gerçekleştirmek, geri vermeyi ve bakiye düşüşünü azaltabilir. Karşı risk:
pozisyonlar slot tutmaya devam eder, uzun kazananların getirisi yarılanır ve
partial PnL hesap risk frenlerinin sonraki kararlarını değiştirir.

Uygulama yalnız `experiments/tp2_runner.py` ve araştırma başlatıcısında;
`strategy.py`, `config.py`, `paper_bb.py`, `metrics.py` değişmedi. Başlatıcı
yalnız kendi replay sürecinde strateji sınıfını ve TP2_PARTIAL toplama kuralını
seçer. İki çıkış bacağı tek pozisyon sayılır; her parçanın çıkış maliyeti
yalnız kendi büyüklüğünden alınır. Hedef+eski trail aynı mumda görülürse tam
TRAIL öncelikli; hedef+timeout aynı mumda görülürse yarım TP2 ve kalan için
aynı mum kapanışında TIMEOUT. Kısmi TP2 tek seferliktir.

Koşular (iki pencere de tamamlanacak; oran taraması yok):

```sh
./experiments/run_arm.sh N2-tp2-half-20260911 240 BT_TP2_CLOSE_FRAC=0.5
./experiments/run_arm.sh N2-tp2-half-20260911 665 BT_RESTARTS=1 BT_TP2_CLOSE_FRAC=0.5
python3.12 experiments/ledger.py
```

Mevcut referanslar N1-control-20260910_240d ($1435.46) ve
R1-sl225t96_665d ($1085.22). İkinci referans operatör restart'ları varsayar;
aday da aynı varsayımla koşar. Aynı sabitlenmiş veriler kullanılır. N1'in
TP2'siz uç noktası ayrıca karşılaştırmada gösterilir. Benimseme için her iki
pencerede mevcut hesabın net getirisini geçmek gerekir; DD ve hard stop da
raporlanır. Deney sonucundan bağımsız, bu istek çalışan bota deploy kapsamıyor.

**Durum: tamamlandı — RED.** İki koşu da tüm pencereyi bitirdi. Net rakamlar
probe + giriş + çıkış maliyetleri dahil tek $1000 hesabın getirisidir.

| Pencere | Mevcut net | N2 yarım TP2 net | Fark | Mevcut DD → N2 DD | Hard stop |
|---|---:|---:|---:|---:|---:|
| 240g | +$435.46 | +$508.62 | **+$73.16** | %6.14 → %6.18 | 0 → 0 |
| 665g | +$85.22 | +$58.71 | **−$26.52** | %37.56 → %37.35 | 3 → 3 |

665g üç karşılaştırma da operatör restart varsayımıyla; kesintisiz çalışma
değil. N1 TP2'siz kolun netleri +$540.58 / +$68.89 ve DD'leri %6.23 / %45.57
idi. N2 uzun dönemde N1'in düşüşünü azaltıyor, fakat getirisi iki referansın
da altında. Mevcut sisteme göre 0.21 yüzde puanlık DD iyileşmesi, iki pencerede
birden üstünlük şartının sağlanmadığı gerçeğini değiştirmiyor.

240g: 114 ana pozisyon, 35 yarım TP2 (kalan 31 TRAIL / 4 TIMEOUT).
665g: 387 ana pozisyon, 81 yarım TP2 (kalan 74 TRAIL / 7 TIMEOUT).
N1 ve N2'nin girişleri iki pencerede aynı; 665g'de 96 pozisyonun büyüklüğü
farklı. Erken kâr gerçekleşmesi hesap risk frenlerini değiştiriyor; portföy
sonucu iki uç kuralın aritmetik ortalaması değil.

Eylül'deki altı gerçek NEAR girişi sabit tutulduğunda mevcut **+$57.63**,
yarım TP2 **+$44.52**, TP2'siz **+$31.41**. N2 farkı **−$13.11**; dört TP2
işlemi kötüleşti, ilk TRAIL ve son SL değişmedi. Bu ayrı çalışma girişleri ve
büyüklükleri sabit tutan çıkış teşhisi; portföy backtest'i değildir.

Kontroller: 117 pytest geçti (23 yeni mekanizma testi); yeni araştırma
dosyaları Ruff temiz, strateji/başlatıcı mypy temiz. Altı koşunun her çıkış
maliyeti, pozisyon eşleşmesi, hesap neti ve bar sonu nakit DD'si bağımsız
hesapla uzlaştı. Açık pozisyon kalmadı. 12 veri cache hash'i önceki manifestle
aynı. İki pencere örtüşür; funding ve mum içi açık PnL düşüşü modellenmiyor.

`ledger.py` çalıştırıldı, fakat varsayılan karşılaştırması eski `R1-baseline`'ı
kullanıyor; oradaki “AL” bugünkü referansa göre geçerli değil. N2 için karar
yukarıda açıkça belirtilen mevcut geometriye göre verildi. Karşılaştırmayı
tekrar üretmek için:

```sh
python3.12 experiments/reports/20260911-tp2-half/compare_portfolios.py
python3.12 experiments/reports/20260911-tp2-half/replay_near_fixed_entries.py
```

**Karar:** %50/%50 kolu alınmadı; mevcut TP2 ve yeniden giriş düzeni korundu.
Diğer oranlar denenmedi; bu sonuç bütün kısmi çıkış tasarımları için genelleme
değil. Çalışan simülasyon botuna değişiklik veya deploy yok.
[Tam rapor ve ham sonuçlar](reports/20260911-tp2-half/REPORT.md).

---

## Tur 9 — Sentetik piyasa: "bug mu, edge yokluğu mu?" · 13 Eyl 2026

**Soru (kullanıcı):** Proje hep aynı yerde sayıyor — bir ileri, bir geri. Bu bir
sorundan mı kaynaklanıyor? Gelecek (2027–2030) fiyat verisiyle test edelim.

**Canlı durum (breakoutbot.dev, 46 gün):** $1000 → $990.63, 84 işlem, işlem başına
beklenti **−$0.11**, haftalık PnL +19/+39/−17/−15/−39/+81/−23 — ortalaması sıfır,
SD ≈ $40/hafta. Gerçek veride aynı geometri: 240g **+0.396R** (n=114), 665g
**+0.039R** (n=396). Pencere uzadıkça R sıfıra iniyor.

**Neden sentetik veri:** Bu repodaki her backtest tarihin aldığı TEK yolu tekrar
oynatır; o yolda "makine bozuk" ile "piyasada hasat edilecek şey yok" aynı görünür.
Kontrol ettiğimiz bir piyasada ikisi ayrışır. "2027–2030 tahmini fiyat verisi"
diye bir şey yoktur — fiyat tahmin edilemez; var olan tek dürüst karşılığı
**varsayımları açık yazılmış senaryolar**dır. Düzenek: `experiments/synth/`.

| dosya | ne |
|---|---|
| `synth/gen.py` | Kalibre üreteç: 5 coin + BTC, 5m OHLCV, tek-faktör korelasyon (β 1.0–1.3), GARCH(1,1) oynaklık kümelenmesi, gün-içi profil (13–17 UTC ×1.6), 20 alt-adımdan mum + fitil kalibrasyonu (range/gövde 2.01, gerçek 2.09) |
| `synth/run.py` | Her tohum = `backtest.run_portfolio` (canlı-parite motoru, dokunulmadı); paralel tohumlar; piyasanın ölçülen VR'ı sonucun yanına yazılır |
| `synth/report.py` | Senaryolar × gerçek referanslar tablosu |
| `tests/test_synth.py` | 12 mekanizma testi: ızgara, OHLC geçerliliği, determinizm, β, fitil, her yasanın iddia ettiği VR'ı ekip ekmediği, Markov karışımı, bootstrap'ın gerçek gün şekillerini ve eş-zamanlı blokları koruduğu |

**Senaryolar ve ektikleri (ADAUSDT, 3×400g ölçüm; VR = varyans oranı, 1 = rastgele yürüyüş):**

| senaryo | VR 4h | VR 8h | VR 1g | VR 5g | anlamı |
|---|--:|--:|--:|--:|---|
| `null` | 1.01 | 1.01 | 1.02 | 1.02 | sürüklenmesiz rastgele yürüyüş — **hiçbir strateji edge'e sahip olamaz** |
| `trend` | 1.22 | 1.39 | 1.76 | 2.07 | botun 1h–8h tutma ufkuna ekilmiş momentum — breakout'un hasat etmek için yapıldığı şey |
| `trend_slow` | 1.04 | 1.08 | 1.17 | 1.48 | çok-günlük momentum; 8h'lik tutuşun içinde görünmez, 4h rejim kapısına görünür |
| `chop` | 0.95 | — | 0.72 | 0.30 | ortalamaya dönüş — breakout burada kaybetmeli |
| `bootstrap` | gerçek | gerçek | gerçek | ~0.7 | GERÇEK günler (5 günlük bloklar, tüm coinlerde aynı bloklar) yeniden sıralanıp zincirlenir |
| `2027`–`2030` | — | — | — | — | Markov rejim yılları (UP/DOWN/CHOP karışımı yıl başına verilmiş: 2027 %55/15/30 genişleme, 2028 %15/45/40 düşüş, 2029 %20/20/60 yatay, 2030 %40/20/40 toparlanma); ortalama rejim süresi ≈15/(1−p) gün |
| GERÇEK 240g | 0.93 | — | 0.97 | 0.98 | referans: gerçek 5m kripto 4h ufkunda hafif **ortalamaya dönen** |

### Hipotezler (ölçümden önce yazıldı)

| # | Hipotez | Öngörü eğer DOĞRU | Öngörü eğer YANLIŞ |
|---|---|---|---|
| H9.1 | Motorda lookahead/muhasebe hatası yok | `null`'da all-in momR ≈ −(giriş+çıkış ücreti) ≈ **−0.14R**, SE içinde | momR > 0 → sızıntı (lookahead); momR ≪ −0.14R → çıkış geometrisinde yapısal maliyet |
| H9.2 | Makine, VAR OLAN bir edge'i hasat edebiliyor | `trend`'de momR belirgin **pozitif** (≥ +0.3R) ve P(kâr) ≈ 100% | `trend`'de bile sıfır/negatif → giriş-çıkış mekanizması kırık; sorun piyasada değil |
| H9.3 | Canlı sıfır, edge yokluğundandır | `null` ≈ canlı ≈ 665g (hepsi ~0/−0.1R); `trend` ≫ hepsi | `null` ≪ canlı → canlıda görünmeyen bir kaçak var |
| H9.4 | 240g'deki +0.396R bir şanslı pencere | `bootstrap` (aynı günler, farklı sıra) dağılımında 240g sonucu üst kuyrukta; `bootstrap` ortalaması ≈ 0 | bootstrap ortalaması da +0.3R → gün-içi yapıda gerçek bir şey var, sıralama değil |
| H9.5 | Rejim kapısı işe yarıyor | `2028`/`chop`'ta kayıp `bear`/`null`'a göre sınırlı (kapı pozisyon almıyor) | düşüş yıllarında hard stop'lar |

**Yan bulgu (ölçümden önce, kalibrasyon sırasında):** `indicators.hurst_exponent`
saf beyaz gürültüde **0.655 ± 0.028** veriyor (R/S küçük-örnek yanlılığı, Anis-Lloyd
düzeltmesi yok). Gerçek veride 0.645 (pencerelerin %99'u ≥0.58), GBM'de %100.
`HURST_LONG_MIN=0.58` kapısı **hiçbir şeyi filtrelemiyor**; yalnızca güçlü
ortalamaya dönen serileri (AR −0.3 → H 0.57) keser. "Trend rejimi şartı"
diye belgelenen şey fiilen kapalı bir kapı değil, açık bir kapı.

**Yan bulgu — gerçek veri, sentetik koşu beklenmeden (13 Eyl):** 665g koşusu
(`R1-sl225t96_665d`) 240g penceresini (`N1-control_240d`, 2025-12-29 → 2026-08-24)
**içeriyor**. Aynı koşunun legs'i pencere sınırında ikiye bölündü:

| parça | pozisyon | net (all-in) | momR |
|---|--:|--:|--:|
| 240g İÇİ (Faz 8 geometrisinin seçildiği pencere) | 114 | **+$444.54** | **+0.390** |
| 240g DIŞI — 2024-10-28 → 2025-12-28 (425g) | 282 | **−$290.98** | **−0.103** |
| canlı, pencere SONRASI (2026-07-29 → 09-13) | 71 | ≈ $0 | ≈ 0.00 |

2026 öncesi 15 ayın 13'ü negatif (ay bazı tablo `experiments/synth/`
raporunda). Edge, yalnızca çıkış geometrisinin üzerinde seçildiği pencerede var;
öncesinde de sonrasında da yok. "Bir ileri bir geri" tam olarak buna benzer:
in-sample +0.39R'nin dışarıdaki karşılığı ≈ 0.

Koşum:

```sh
./experiments/synth/run_all.sh            # ~3.5 saat, 6 paralel iş
python3.12 experiments/synth/report.py    # tablo
python3.12 experiments/synth/report.py --md
```

**Durum: tamamlandı** (13 Eyl 11:00 → 14 Eyl 04:50 UTC). Sonuçlar sırayla aşağıda.

### Sonuç 1 — `null` 240g (6 tohum, 702 momentum pozisyonu)

| tohum | bakiye | DD | gün | halt | mom n | momR all-in | exit-only | PF | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $859.53 | −15.2% | 107 | 1 | 61 | −0.213 | −0.026 | 0.57 | 4/34/14/9 |
| 2 | $946.00 | −15.1% | 128 | 1 | 152 | −0.021 | +0.004 | 1.05 | 31/82/30/9 |
| 3 | $1080.09 | −13.0% | 240 | 0 | 135 | +0.084 | +0.016 | 1.26 | 30/71/26/8 |
| 4 | $1005.02 | −9.2% | 240 | 0 | 110 | +0.026 | +0.010 | 1.13 | 27/56/18/9 |
| 5 | $919.38 | −15.4% | 102 | 1 | 75 | −0.088 | −0.007 | 0.87 | 13/43/18/1 |
| 6 | $948.32 | −15.2% | 152 | 1 | 169 | −0.014 | +0.004 | 1.06 | 33/88/37/11 |
| **havuz** | **$960 ± 76** | −13.8% | | **4/6** | 117 | **−0.016** (±0.042 SE) | ≈0.00 | | |

Ücret tabanı: giriş $0.58/poz = 0.058R, gidiş-dönüş ≈ **−0.115R**.

- **H9.1 TUTUYOR.** momR > 0 değil → lookahead yok. Ücret tabanının ~0.1R üstünde
  ama 1.5 SE (pozisyon bazlı SE 1.7/√702 = 0.064R) — gürültü. Daha fazla tohumla
  bakılabilir; şu an bir sızıntı iddiası için kanıt yok.
- **H9.3 TUTUYOR.** `null` −0.016R ≈ canlı ≈0.00R ≈ gerçek pencere-dışı −0.103R ≈
  665g +0.039R. Dördü de aynı dağılımdan; canlıda görünmeyen bir kaçak yok.
- **Kill-switch dinamiği:** edge'siz piyasada geometri 4/6 tohumda 102–152. günde
  −15% hard stop'a çarpıyor. Canlının 46 günde −13.5% DD görmesi ve 14 DD-devre
  olayı bunun beklenen görüntüsü. Rastgele bir 240g penceresinde bakiye
  $860–$1080; gerçek 240g'nin +$435'i bu dağılımın 5+ SD dışında — ama o pencere
  rastgele değil, geometrinin seçildiği pencere (bkz. yan bulgu yukarıda).
- Huni: tohum başına 360–1006 probe → 61–170 tam pozisyon (onay oranı ~%15).

### Sonuç 2 — `trend` 240g (6 tohum, 805 momentum pozisyonu) — **H9.2 RED**

Botun kendi tutma ufkuna (1h–8h) ekilmiş momentum: ölçülen VR 4h 1.19 · 1g 1.69 ·
5g 1.79. Seed 2'de ADA pencerede **+%439**, rejim %72 BULL.

| tohum | bakiye | DD | gün | halt | mom n | momR all-in | WR | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $1049.23 | -14.9% | 240 | 0 | 237 | +0.037 | 40.9% | 58/122/46/11 |
| 2 | $888.05 | -15.1% | 124 | 1 | 132 | -0.064 | 38.1% | 26/76/23/7 |
| 3 | $879.27 | -15.1% | 143 | 1 | 111 | -0.087 | 38.7% | 18/61/22/10 |
| 4 | $872.39 | -15.4% | 146 | 1 | 113 | -0.094 | 39.2% | 21/62/26/4 |
| 5 | $919.84 | -15.1% | 172 | 1 | 88 | -0.065 | 34.0% | 13/47/23/5 |
| 6 | $911.09 | -15.4% | 135 | 1 | 124 | -0.055 | 42.9% | 25/68/25/6 |
| **havuz** | **$920 ± 66** | −15.2% | | **5/6** | 134 | **−0.040** (±0.019 SE) | 39.0% | |

**Kâhin kontrolü (aynı piyasa, aynı sürtünme 0.15% gidiş-dönüş):** "4h getirisi > 0
→ al, 8h tut" üç satırlık kural seed 2'de ADA **+%104**, NEAR **+%225**, INJ
**+%95**; null'da aynı kural −%8/+%17/+%31 ve −%160/−%61/−%117 (gürültü). Edge
oradaydı, hasat edilebilirdi; deployed makine −$112 yapıp hard stop'a çarptı.

**Asıl bulgu — çıkış dağılımı piyasadan bağımsız:**

| piyasa | momR | WR | payoff | TP2 | SL | TRAIL | TMO |
|---|--:|--:|--:|--:|--:|--:|--:|
| `null` — rastgele yürüyüş | −0.016 | 40.5% | 1.47 | 20% | 53% | 20% | 7% |
| `trend` — VR 4h 1.19 / 1g 1.69 | −0.040 | 39.0% | 1.49 | 20% | 54% | 20% | 5% |
| GERÇEK 665g | +0.039 | | | 21% | 51% | 25% | 3% |
| GERÇEK 240g (in-sample) | +0.396 | | | 31% | 39% | 24% | 2% |

Sistemin sonucu piyasanın trend olup olmamasına bağlı değil; kendi geometrisi
belirliyor. Neden (trend seed 2, ADA): ATR/fiyat medyanı 0.337% → **SL 0.76%**,
TP2 2.02%; **8 saatlik getiri SD'si 2.79% = SL'nin 3.7 katı**; ekilen edge 8h'te
+0.17%/işlem = **SL'nin 1/4.5'i**. Stop, sürüklenmenin etkisi birikemeden
gürültüyle vuruluyor — pozisyonların %53'ü SL'de bitiyor, piyasa ne olursa
olsun. Bu, `DEFTER` Tur 4'teki "sürtünme kimliği 5m stop mesafelerini eziyor"
bulgusunun mekanik karşılığı: 5m'de 2.25×ATR stop, 8 saatlik tutuşun gürültüsü
içinde bir zar.

Gerçek 665g'nin çıkış dağılımı (21/51/25/3) sentetik null'la (20/53/20/7)
birebir; tek sapan, geometrinin üzerinde seçildiği 240g penceresi (31/39).
**Cevap: "bir ileri bir geri" bir bug değil, bu geometrinin edge'li piyasada
bile üretebildiği tek şey.** Kod doğru çalışıyor (H9.1); tasarım, sinyalin
işleyebileceği ufuktan çok daha kısa bir stop mesafesiyle kuruluyor (H9.2 RED).

### Sonuç 3 — `chop` 240g (6 tohum, 336 momentum pozisyonu) — H9.5 kısmen

Ortalamaya dönen piyasa (VR 1g 0.72, 5g 0.30). **6/6 hard stop**, 35–120. günde.

| tohum | bakiye | DD | gün | halt | mom n | momR all-in | WR | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $849.99 | -15.0% | 98 | 1 | 72 | -0.182 | 37.1% | 8/38/21/5 |
| 2 | $889.83 | -15.4% | 121 | 1 | 77 | -0.123 | 39.6% | 12/43/19/3 |
| 3 | $934.88 | -15.2% | 95 | 1 | 52 | -0.113 | 39.9% | 7/29/10/6 |
| 4 | $856.90 | -15.4% | 94 | 1 | 54 | -0.243 | 35.1% | 7/35/12/0 |
| 5 | $872.90 | -15.3% | 91 | 1 | 52 | -0.214 | 35.7% | 7/34/10/1 |
| 6 | $845.57 | -15.4% | 32 | 1 | 29 | -0.518 | 38.5% | 1/21/6/1 |
| **havuz** | **$875 ± 34** | −15.3% | | **6/6** | 56 | **-0.202** (±0.061 SE) | 37.6% | 12%/60%/23%/5% |

Breakout, ortalamaya dönüşte beklendiği gibi kaybediyor: null'dan ~0.19R daha
kötü, çıkış dağılımı **kayıyor** (TP2 %20→%12, SL %53→%60, payoff 1.47→0.98).
Asimetri dikkat çekici: sistem piyasanın rastgeleden *kötü* olmasına tepki
veriyor, *iyi* olmasına (trend) vermiyor. Stop gürültünün içinde olduğu için
ortalamaya dönüş fiyatı stop'a geri çekiyor; trend ise stop'un hayatta kalma
süresi içinde sonucu değiştirecek kadar uzağa gidemiyor. Rejim kapısı
(H9.5) pozisyon almayı durdurmuyor: chop'ta tohum başına 56 pozisyon açıldı
(null'da 117). Kapının "BULL" etiketi bir trend teşhisi değil; VR<1 piyasada da
açılıyor.

**Üç yasanın özeti:** chop −0.202R · null −0.016R · trend −0.040R. Piyasanın
öngörülebilirliği VR 1g 0.68 → 1.05 → 1.69 arasında değişirken sistem
"rastgeleden kötü"yü cezalandırılarak görüyor, "rastgeleden iyi"yi hiç görmüyor.
Bir breakout sistemi için bu, yukarı yönlü kuyruğun tamamen kapalı olması demek.

### Harness kusuru — yıl senaryoları v1 çöpe (13 Eyl, ~17:30)

`2027`–`2030` v1 koşuları (12 tohum, ~14 saat CPU) **geçersiz**: CHOP rejiminin
ortalamaya dönüş çapası fiyatı takip etmiyor, başlangıç fiyatına demirli
kalıyordu. Boğa koşusunu izleyen ilk CHOP günü fiyatı 1 günlük yarı-ömürle
başlangıca geri çekiyordu — 10 tohumun 6'sında BTC yıl sonu ≈ +0%, VR 5g 3.9
(gerçek 0.98). Fark ediliş: yıl özet tablosunda BTC getirisi sütunu. Düzeltme:
çapa, ortalamaya dönüş olmayan günlerde fiyatı izler (`gen.py`, `simulate`).
Regresyon testi `test_chop_anchor_follows_the_price_after_a_trend`. v1 sonuçları
`synth/results/discarded_v1/` altında, karşılaştırma dışı. `null`/`trend`/`chop`
etkilenmedi (çapa ya yok ya da fiyat başlangıçtan ayrılmıyor); Sonuç 1–3 geçerli.
Kayıt için v1'in söylediği: 12 tohumun 11'i zarar, havuz momR ≈ −0.08R — yani
kusur sonucu sistemin lehine çevirmemişti, ama sayılar rapor edilmez.

### Sonuç 4 — `bootstrap` 240g (6 tohum, 502 momentum pozisyonu) — H9.4 beklenmedik

Kaynak: 240g+40w cache'inin GERÇEK günleri (2025-11 → 2026-08), 5 günlük bloklar
halinde, tüm coinlerde aynı bloklar, rastgele sırayla zincirlenmiş. Her mumun
şekli, hacmi, gün-içi profili ve coinler arası korelasyon gerçek; yalnızca
çok-günlük sıralama rastgele.

| tohum | bakiye | DD | mom n | momR all-in | WR | TP2/SL/TRL/TMO | ADA getiri |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $1240.20 | -5.7% | 78 | +0.323 | 40.5% | 22/37/17/2 | -50% |
| 2 | $919.49 | -13.4% | 93 | -0.069 | 38.3% | 19/54/18/2 | -19% |
| 3 | $1157.16 | -4.7% | 68 | +0.253 | 36.8% | 16/27/24/1 | -57% |
| 4 | $906.33 | -10.7% | 59 | -0.146 | 39.0% | 8/31/17/3 | -63% |
| 5 | $1313.51 | -4.0% | 82 | +0.397 | 42.3% | 25/33/20/4 | -70% |
| 6 | $1651.46 | -5.0% | 122 | +0.553 | 39.4% | 43/45/32/2 | -69% |
| **havuz** | **$1198 ± 278** | −7.2% | 83 | **+0.254** (±0.111 SE) | 39.4% | 26/45/25/3 | |

Halt 0/6. Gün sırası karıştırılınca edge **kaybolmuyor**: çıkış dağılımı
(26/45/25) gerçek 240g'ye (31/39/24) benziyor, sentetiklere (20/53/20) değil.
Payoff 2.40 (null 1.47) — kazananlar daha büyük; kaynak dönem ayı (ADA −19…−70%)
olduğu hâlde.

**Yorum:** 240g'deki +0.39R günlerin sıralamasında değil, **o günlerin gün-içi
yapısında**. Bootstrap aynı günlerden örneklediği için iki açıklamayı ayıramaz:
(a) o dönemin günlerinde geometrinin yakaladığı gerçek bir gün-içi yapı vardı ve
canlıda kayboldu (rejim değişimi); (b) geometri (E1–E8 seçimi) tam o günlerin
gün-içi yapısına uyduruldu. Sentetik üreteç gün-içi *oynaklık* profilini taşıyor
ama gün-içi *yönlü* yapıyı (ör. ABD seansı açılış kırılmaları) taşımıyor — null
ile bootstrap arasındaki farkın kaynağı bu olabilir.

**Ayırt edici test (önceden kaydedildi, koşuyor):** aynı bootstrap, kaynak =
665g cache'inin **2025-12-29 öncesi** 466 günü (geometrinin görmediği dönem).
Öngörü: (b) doğruysa ≈ −0.1R (gerçek pencere-dışı koşuyla uyumlu), (a) doğruysa
pozitif. `bootstrap_240d_oos.json`.

### Sonuç 5 — `bootstrap` OOS 240g (6 tohum, 571 momentum pozisyonu) — **H9.4 kapandı: (b), seçim yanlılığı**

Kaynak: 665g cache'inin **2025-12-29 öncesi** 466 günü; aynı makine, aynı
bootstrap, aynı 5 günlük bloklar.

| tohum | bakiye | DD | halt | mom n | momR all-in | payoff | TP2/SL/TRL/TMO | ADA getiri |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $849.92 | -15.0% | 1 | 76 | -0.182 | 0.95 | 9/40/24/3 | -35% |
| 2 | $978.17 | -12.2% | 0 | 71 | -0.011 | 1.72 | 16/38/13/4 | -69% |
| 3 | $908.98 | -15.0% | 1 | 118 | -0.058 | 1.4 | 20/57/35/6 | -41% |
| 4 | $1049.09 | -9.9% | 0 | 117 | +0.062 | 2.07 | 29/54/31/3 | -47% |
| 5 | $912.49 | -13.2% | 0 | 140 | -0.047 | 1.59 | 28/75/27/10 | -47% |
| 6 | $866.23 | -15.1% | 1 | 49 | -0.251 | 0.97 | 7/32/10/0 | -29% |
| **havuz** | **$927 ± 74** | −13.4% | **3** | 95 | **−0.058** (±0.047 SE) | 1.45 | 19/52/25/5 | |

| bootstrap kaynağı | momR | P(kâr) | payoff | TP2/SL/TRL |
|---|--:|--:|--:|--:|
| in-sample günler (2025-11 → 2026-08) | **+0.254** | 4/6 | 2.40 | 26/45/25 |
| OOS günler (2024-09 → 2025-12) | **−0.058** | 1/6 | 1.45 | 19/52/25 |

Fark **+0.300R, SE 0.121, t = 2.48**. OOS bootstrap'ın çıkış dağılımı null
(20/53/20) ve gerçek 665g (21/51/25) ile birebir. Değişen tek şey hangi
günlerden örneklendiği: geometrinin (E1–E8, Faz 8) üzerinde seçildiği günlerde
+0.25R, onun dışında ücret tabanı. Açıklama (a) — "o dönemde gerçek bir gün-içi
yapı vardı" — dışlanmadı ama gereksiz: o yapı her hâlükârda yalnızca seçim
penceresinde var, öncesinde yok (bu test), sonrasında yok (canlı 46g ≈ 0).

**Tur 9'un birleşik tablosu (momentum all-in R):**

| edge YOK | | edge VAR | |
|---|--:|---|--:|
| `null` sentetik | −0.016 | in-sample bootstrap | +0.254 |
| `trend` sentetik (VR 4h 1.19) | −0.040 | GERÇEK 240g | +0.396 |
| OOS bootstrap (gerçek günler) | −0.058 | | |
| GERÇEK pencere-dışı 425g | −0.103 | | |
| canlı 46g | ≈ 0.00 | | |
| `2027` / `2028` rejim yılları | −0.012 / −0.057 | | |

Sağ sütundaki iki sayı aynı 280 günün üzerinde. Sol sütundaki yedi sayı, o
günlerin dışındaki her şey — sentetik, gerçek, canlı — ve hepsi ücret tabanının
±0.1R'si içinde.

**Karar:** "Bir ileri bir geri" bir bug değil (H9.1 ✓), piyasa şansızlığı değil
(H9.2 RED: ekilen trendi de alamıyor), kürasyon/deploy sorunu değil (Tur 0–3
zaten elemişti). Faz 8 geometrisi 2025-12 → 2026-08 günlerinin gün-içi yapısına
uydurulmuş; o yapının dışında sistemin beklentisi ≈ −ücret. Canlı hesap tam
olarak bunu yaşıyor. Parametre ayarı bunu düzeltmez — sekiz kol denenmiş, en
iyisi seçilmiş ve seçim yanlılığı tam da bu tabloyu üretmiş. Sırada olması
gereken: geometriyi değil **ufku** değiştiren bir tasarım (stop mesafesi ≥
tutma ufkunun gürültüsü; ör. 4h barlarda ATR, 1–5 günlük tutuş) ve onu
`synth/trend` üzerinde önce **hasat edebildiğini** kanıtlamak — gerçek veriye
gitmeden. Düzenek buna hazır (`X_*` env'leri sentetik koşuya geçer).

### Sonuç 6 — `2027`–`2030` rejim yılları v2 (12 tohum × 365g, restart)

Yıl etiketleri **beklenen** karışımdır; gerçekleşen karışım ve BTC yıl getirisi
tohum başına verilir (yıllık gürültü SD ≈ %50; "genişleme yılı" etiketi bile
BTC −%58 üretebiliyor).

| yıl | tohum | gerçekleşen UP/DOWN/CHOP | BTC yıl | BULL bar | bakiye | DD | halt | mom n | momR | TP2/SL/TRL/TMO |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 2027 | 1 | 42%/29%/29% | +195% | 48% | $1154.41 | -18.3% | 1 | 305 | +0.068 | 63/159/65/18 |
| 2027 | 2 | 59%/9%/32% | +251% | 47% | $808.72 | -23.4% | 2 | 270 | -0.052 | 56/158/44/12 |
| 2027 | 3 | 47%/29%/24% | -58% | 26% | $808.57 | -24.4% | 1 | 177 | -0.087 | 29/93/47/8 |
| 2028 | 1 | 9%/37%/54% | +137% | 47% | $824.12 | -29.9% | 4 | 313 | -0.037 | 56/166/70/21 |
| 2028 | 2 | 22%/37%/41% | +134% | 38% | $716.30 | -34.1% | 3 | 249 | -0.095 | 47/148/41/13 |
| 2028 | 3 | 4%/44%/52% | -67% | 14% | $953.58 | -13.1% | 0 | 95 | -0.023 | 20/51/22/2 |
| 2029 | 1 | 9%/23%/68% | +164% | 54% | $713.56 | -29.0% | 3 | 335 | -0.065 | 60/179/81/15 |
| 2029 | 2 | 25%/28%/46% | +83% | 37% | $772.74 | -29.7% | 2 | 214 | -0.085 | 40/126/37/11 |
| 2029 | 3 | 5%/35%/60% | -56% | 16% | $980.16 | -12.7% | 0 | 102 | +0.004 | 20/50/28/4 |
| 2030 | 1 | 32%/14%/54% | +318% | 53% | $1045.89 | -19.9% | 1 | 326 | +0.032 | 74/175/59/18 |
| 2030 | 2 | 54%/5%/41% | +420% | 54% | $846.84 | -31.7% | 3 | 317 | -0.031 | 66/180/55/16 |
| 2030 | 3 | 40%/8%/52% | -9% | 24% | $848.16 | -20.7% | 1 | 137 | -0.086 | 23/73/38/3 |

**12 yıl-tohumu havuzu:** momR **-0.034** (tohum -0.038 ± 0.015), n=2840,
bakiye $873 ± 135, **P(kâr) 2/12**, DD ort -23.9% (en kötü -34.1%),
**hard stop toplam 21 = yıl başına 1.8**, çıkış 20%/55%/21%/5%.
Sonucun gerçekleşen UP payıyla korelasyonu -0.03, BTC yıl getirisiyle +0.30
(BTC -67% … +420% aralığında) — boğa yılı da ayı yılı da aynı makineyi
üretiyor. "2027–2030'da ne olur" sorusunun bu geometri için cevabı: rejimden
bağımsız, yılda ~2 kill-switch, beklenti ≈ −ücret.

### Sonuç 7 — `trend_slow` 240g (6 tohum, 518 momentum pozisyonu)

Çok-günlük momentum (VR 4h 1.02 · 1g 1.15 · 5g 1.28): 8 saatlik tutuşun içinde
görünmez, 4h rejim kapısına görünür. **6/6 hard stop**, 59–141. günde, havuz
**−0.125R**.

| tohum | bakiye | DD | gün | BULL bar | ADA getiri | mom n | momR | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $870.97 | -15.3% | 89 | 40% | +75% | 70 | -0.167 | 10/40/14/6 |
| 2 | $849.26 | -15.2% | 59 | 95% | +366% | 91 | -0.149 | 14/52/20/5 |
| 3 | $936.51 | -15.1% | 141 | 48% | -68% | 126 | -0.032 | 25/62/28/11 |
| 4 | $851.40 | -15.2% | 123 | 30% | +219% | 77 | -0.177 | 9/43/21/4 |
| 5 | $849.38 | -15.1% | 99 | 50% | +4% | 79 | -0.164 | 12/47/18/2 |
| 6 | $897.90 | -15.0% | 63 | 59% | +5% | 75 | -0.121 | 13/40/18/4 |

Seed 2: rejim barların **%95'inde BULL**, ADA pencerede **+%366** — bot 59. günde
−15%'e çarptı. Rejim kapısı doğru şeyi görüyor (BULL), sizing'i açıyor, ve
geometri bunu yine 5m gürültüsüne veriyor. `trend`'den kötü olması (−0.04 →
−0.125) tutarlı: burada 8h ufkunda ekilmiş bir şey yok, yalnızca daha fazla
BULL etiketi = daha fazla tam boy pozisyon = daha fazla stop.

### Kapanış tablosu (`python3.12 experiments/synth/report.py --md`)

| senaryo | tohum | gün | bakiye μ ± sd | min / max | P(kâr) | DD μ / en kötü | halt | mom n | momR all-in | TP2/SL/TRL/TMO | VR 4h/1d/5d |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| null 240g | 6 | 240 | $960 ± 76 | 860 / 1080 | 33% | -13.8% / -15.4% | 4 | 117 | **-0.016** ± 0.042 | 23/62/24/8 | 1.02/1.05/0.89 |
| trend 240g | 6 | 240 | $920 ± 66 | 872 / 1049 | 17% | -15.2% / -15.4% | 5 | 134 | **-0.040** ± 0.019 | 27/73/28/7 | 1.19/1.69/1.79 |
| trend_slow 240g | 6 | 240 | $876 ± 35 | 849 / 937 | 0% | -15.2% / -15.3% | 6 | 86 | **-0.125** ± 0.022 | 14/47/20/5 | 1.02/1.15/1.28 |
| chop 240g | 6 | 240 | $875 ± 34 | 846 / 935 | 0% | -15.3% / -15.4% | 6 | 56 | **-0.202** ± 0.061 | 7/33/13/3 | 0.91/0.68/0.24 |
| bootstrap 240g | 6 | 240 | $1198 ± 278 | 906 / 1651 | 67% | -7.2% / -13.4% | 0 | 83 | **+0.254** ± 0.111 | 22/38/21/2 | 0.94/0.93/0.77 |
| bootstrap 240g [oos] | 6 | 240 | $927 ± 74 | 850 / 1049 | 17% | -13.4% / -15.1% | 3 | 95 | **-0.058** ± 0.047 | 18/49/23/4 | 0.59/0.56/0.52 |
| 2027 365g (restart) | 3 | 365 | $924 ± 200 | 809 / 1154 | 33% | -22.0% / -24.4% | 4 | 250 | **-0.012** ± 0.047 | 49/137/52/13 | 1.07/1.23/1.56 |
| 2028 365g (restart) | 3 | 365 | $831 ± 119 | 716 / 954 | 0% | -25.7% / -34.1% | 7 | 219 | **-0.057** ± 0.022 | 41/122/44/12 | 1.06/1.21/1.35 |
| 2029 365g (restart) | 3 | 365 | $822 ± 140 | 714 / 980 | 0% | -23.8% / -29.7% | 5 | 217 | **-0.061** ± 0.027 | 40/118/49/10 | 1.04/1.05/0.94 |
| 2030 365g (restart) | 3 | 365 | $914 ± 115 | 847 / 1046 | 33% | -24.1% / -31.7% | 5 | 260 | **-0.014** ± 0.034 | 54/143/51/12 | 1.05/1.21/1.47 |
| GERÇEK 240g (mevcut geometri) | 1 | 240 | $1435 | 1435 / 1435 | 100% | -6.1% / -6.1% | 0 | 114 | **+0.396** | 35/45/31/3 |  |
| GERÇEK 665g (mevcut geometri, restart) | 1 | 665 | $1085 | 1085 / 1085 | 100% | -37.6% / -37.6% | 3 | 396 | **+0.039** | 83/203/99/11 |  |


**Toplam:** 60 tohum-koşu (48 × 240g + 12 × 365g), ~40 saat CPU, 12 senaryo
dosyası `experiments/synth/results/`. Tüm hipotezler yukarıda ölçümden önce
yazıldı; H9.1 ✓, H9.2 RED, H9.3 ✓, H9.4 → seçim yanlılığı, H9.5 kısmen (kapı
pozisyon almayı durdurmuyor).

**Durum: tamamlandı — 14 Eyl 2026 04:50 UTC.** Çalışan bota değişiklik veya
deploy yok. Sıradaki hipotez için yukarıdaki "Karar" bölümüne bak.


---

## Tur 10 — Stop mesafesini ufkun gürültüsüne ölçeklemek · 14 Eyl 2026

**Kullanıcı isteği:** Tur 9'un önerdiği kolu dene — "stop mesafesini gürültüye
ölçekleyen bir kol". Ve bir soru: "ne kadar mükemmeliyeti ararsan o kadar
kusurlu olursun; bazen basitlik mükemmelliktir" — ne düşünüyorsun?

**Mekanizma (Tur 9'dan):** SL 2.25×ATR = 0.76%; 8 saatlik tutuşun gürültü SD'si
2.79% ≈ 8.3×ATR; ekilen edge 8h'te +0.17%. Stop, gürültünün 1/3.7'sinde —
sürüklenme birikemeden vuruluyor. Çıkış dağılımı bu yüzden piyasadan bağımsız.

**Hipotez H10.1:** Geometri (SL/TP1/TP2/TRAIL) tutma ufkunun gürültüsüne
ölçeklenir ve timeout buna göre uzatılırsa, **aynı giriş sinyaliyle** makine
`synth/trend`'de ekilen edge'i hasat eder.
- Öngörü DOĞRU ise: `trend`'de momR belirgin pozitif (≥ +0.10R, 6 tohum havuzu),
  `null`'da ≈ −ücret. Çıkış dağılımı `trend` ile `null` arasında **farklılaşır**
  (Tur 9'da farklılaşmıyordu) — bu, sonucun artık piyasaya bağlı olduğunun
  işareti.
- Öngörü YANLIŞ ise: `trend`'de de ≈ 0 → giriş sinyalinin kendisi (5m breakout
  puanı) latent sürüklenmeyle ilişkisiz; çıkış geometrisi değil, giriş bilgisiz.
  O zaman makinenin kurtarılacak parçası kalmaz.

**Kollar (yalnız `X_*` env; kod değişmedi):**

| kol | SL | TP1 | TP2 | TRAIL | TIMEOUT | anlamı |
|---|--:|--:|--:|--:|--:|---|
| baseline | 2.25 | 3 | 6 | 3.75 | 96 (8h) | Tur 9'da ölçüldü |
| `wide3` | 6.75 | 9 | 18 | 11.25 | 288 (24h) | geometri ×3, SL ≈ 0.8× 8h gürültüsü |
| `wide4` | 9 | 12 | 24 | 15 | 576 (48h) | geometri ×4, SL ≈ 1.1× 8h gürültüsü |

Sıra: `wide3`×`trend` → `wide3`×`null` → `wide4`×`trend` (→ `wide4`×`null`
yalnız wide4 trend'de bir şey gösterirse). 6 tohum, aynı tohumlar (1–6), aynı
piyasalar — fark yalnız geometri. Not: ×4'te notional $10/3% = $278 →
MIN_NOTIONAL $300'a kelepçelenir, gerçek risk ~$10.8; R yine $10 üzerinden.

```sh
X_SL_FULL_ATR=6.75 X_TP1_ATR=9 X_TP2_ATR=18 X_TRAIL_ATR=11.25 X_TIMEOUT_BARS=288 \
  python3.12 experiments/synth/run.py --scenario trend --days 240 --seeds 6 --tag wide3
```

**Karar kuralı (ölçümden önce):** `wide*` × `trend` havuz momR ≥ +0.10R VE
`wide*` × `null` ≤ +0.05R ise H10.1 kabul; ardından gerçek OOS bootstrap
(2024-09→2025-12) — orada da ≥ 0 değilse gerçek piyasada hasat edecek şey yok
demektir ve kol yine alınmaz. Gerçek 240g'ye **bakılmaz** (seçim penceresi).

**Hipotez H10.2 — naif kural (kullanıcı isteği, ölçümden önce):** Tur 9'un
kâhin kuralı ("4h getirisi > 0 → al, 8h tut", uzun-yalnız, aynı sürtünme
0.075%/taraf) `synth/trend`'de kazandı çünkü trend oraya ekilmişti. Gerçek 5m
kripto 4h ufkunda hafif **ortalamaya dönen** (VR 4h 0.93, 240g; OOS bootstrap
kaynağında 0.59). Öngörü: naif kural gerçek OOS günlerde (2024-09 → 2025-12,
gerçek sırayla, bootstrap'sız) **≤ 0** — ücret tabanı ya da altı. `null`'da ≈
−ücret, `chop`'ta negatif, `trend`'de pozitif (kalibrasyon). In-sample 240g'de
ne çıkarsa çıksın karar için kullanılmaz.
- Öngörü DOĞRU ise: bu ufukta gerçek veride ne makine ne basit kural hasat
  edecek bir şey bulmuyor → sorun ufuk/piyasa; Tur 4 ile tutarlı.
- Öngörü YANLIŞ ise (OOS'ta anlamlı pozitif): basit kural makineyi gerçek veride
  de yeniyor → makinenin giriş katmanı değil, çıkış geometrisi tek suçlu; Tur 10.1
  ile birlikte okunur.

Kural iki varyantla ölçülür: stopsuz (saf ufuk sinyali) ve **gürültü-ölçekli
stop** (SL = 1× 8h getiri SD'si, ≈ 2.8%) — ikincisi R cinsinden botla
karşılaştırılabilir. Parametre taraması yok: look=48, hold=96 (Tur 9'da
kullanılan), seans filtresi yok. Araç: `experiments/synth/naive.py`.

### Sonuç H10.2 — naif kural, 17 piyasa (`python3.12 experiments/synth/naive.py --md`)

| piyasa | n | stopsuz: ort %/işlem | WR | coin-ort toplam % | +coin | stop 1×SD: R | SL payı | toplam % |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| synth null s1 | 3100 | +0.053 | 50% | +33% | 4/5 | **+0.014** | 26% | +26% |
| synth null s2 | 3094 | -0.003 | 50% | -2% | 2/5 | **-0.008** | 28% | -11% |
| synth null s3 | 3100 | -0.117 | 47% | -73% | 0/5 | **-0.053** | 30% | -91% |
| synth trend s1 | 3020 | +0.189 | 52% | +114% | 5/5 | **+0.073** | 22% | +145% |
| synth trend s2 | 3051 | +0.255 | 52% | +155% | 5/5 | **+0.086** | 23% | +171% |
| synth trend s3 | 2973 | -0.076 | 50% | -45% | 1/5 | **-0.006** | 26% | -15% |
| synth chop s1 | 3094 | -0.119 | 47% | -73% | 0/5 | **-0.077** | 31% | -128% |
| synth chop s2 | 3096 | -0.189 | 45% | -117% | 0/5 | **-0.099** | 34% | -166% |
| synth chop s3 | 3108 | -0.175 | 46% | -109% | 0/5 | **-0.092** | 33% | -150% |
| bootstrap in-sample s1 | 3062 | -0.157 | 43% | -96% | 0/5 | **-0.093** | 26% | -145% |
| bootstrap OOS s1 | 3092 | -0.145 | 48% | -90% | 1/5 | **-0.045** | 21% | -95% |
| bootstrap in-sample s2 | 3113 | -0.122 | 43% | -76% | 1/5 | **-0.084** | 25% | -131% |
| bootstrap OOS s2 | 3083 | -0.212 | 47% | -131% | 0/5 | **-0.076** | 27% | -140% |
| bootstrap in-sample s3 | 3088 | -0.153 | 43% | -94% | 0/5 | **-0.093** | 25% | -146% |
| bootstrap OOS s3 | 3121 | -0.213 | 45% | -133% | 0/5 | **-0.065** | 24% | -131% |
| GERÇEK 240g in-sample (2025-11→2026-08) | 3080 | -0.112 | 43% | -69% | 0/5 | **-0.073** | 25% | -114% |
| GERÇEK OOS (2024-09→2025-12, gerçek sıra) | 5516 | -0.119 | 47% | -131% | 0/5 | **-0.041** | 23% | -151% |

  Σ synth null                                  9294      -0.023                      |   -0.017
  Σ synth trend                                 9044      +0.124                      |   +0.053
  Σ synth chop                                  9298      -0.161                      |   -0.095
  Σ bootstrap in-sample                         9263      -0.144                      |   -0.096
  Σ bootstrap OOS                               9296      -0.190                      |   -0.066

(n = 5 coinin toplam işlem sayısı; R = stoplu varyantta işlem başına net getiri /
stop mesafesi; "SL payı" = stopla biten işlemlerin oranı.)

- **Öngörü TUTTU.** Gerçek OOS (466 gün, gerçek sıra): stopsuz −0.119%/işlem,
  5/5 coin negatif; stoplu **−0.041R**. Ücret tabanı (0.15% / 2.8% ≈ 0.054R)
  civarı — hasat edilen şey yok.
- Kalibrasyon: `trend`'de +0.124% / **+0.053R** (3 tohumdan 2'si güçlü pozitif),
  `chop`'ta −0.095R, `null`'da −0.017R. Kural ekilen edge'i görüyor; gerçek
  veride görecek şey bulamıyor.
- **In-sample 240g'de de −0.112% / −0.073R, 0/5 coin.** Makinenin +0.39R yaptığı
  günlerde genel bir 4h-momentum özelliği YOK. Makinenin in-sample sayısı o
  günlerin ufuk özelliği değil, geometrinin o günlere özgü uyumu — Tur 9'un
  seçim-yanlılığı hükmünü bağımsız bir yoldan doğruluyor.
- `null`'ın ücret tabanının biraz üstünde çıkması (−0.023 vs −0.15%) log-drift 0
  iken aritmetik getirinin Jensen terimi (+0.04%/8h) ve 3 tohumluk yol
  varyansı; lookahead değil (kural yapısal olarak göremez).

**Okuma:** bu ufukta gerçek piyasada ne makine ne basit kural bir şey buluyor.
Sorun giriş sinyalinin karmaşıklığı ya da çıkışın ölçeği değil; **ufkun
kendisi boş** (VR 4h 0.93 — hafif ortalamaya dönen). Tur 4 (357 konfig, 4h/1g/1h
dahil, hiçbiri buy&hold'u geçemedi) ile tutarlı.

### Sonuç H10.1a — `wide3` × `trend` (6 tohum, aynı piyasalar, aynı giriş)

| tohum | baseline bakiye | n | momR | TP2/SL/TRL/TMO | wide3 bakiye | DD | halt | n | momR | payoff | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $1049.23 | 237 | +0.037 | 58/122/46/11 | $1843.48 | -7.8% | 0 | 171 | **+0.513** | 2.82 | 41/56/23/51 |
| 2 | $888.05 | 132 | -0.064 | 26/76/23/7 | $1276.40 | -12.1% | 0 | 192 | **+0.168** | 1.99 | 37/74/23/58 |
| 3 | $879.27 | 111 | -0.087 | 18/61/22/10 | $914.64 | -15.2% | 1 | 67 | **-0.102** | 1.39 | 6/33/9/19 |
| 4 | $872.39 | 113 | -0.094 | 21/62/26/4 | $971.47 | -12.1% | 0 | 121 | **-0.001** | 1.47 | 21/52/18/30 |
| 5 | $919.84 | 88 | -0.065 | 13/47/23/5 | $1383.65 | -5.8% | 0 | 115 | **+0.361** | 2.77 | 22/45/13/35 |
| 6 | $911.09 | 124 | -0.055 | 25/68/25/6 | $1157.59 | -11.0% | 0 | 176 | **+0.110** | 1.66 | 30/75/20/51 |

| | havuz momR | tohum ort | P(kâr) | bakiye | halt | WR | payoff | TP2/SL/TRL/TMO |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| baseline (SL 2.25, TMO 96) | −0.040 | −0.055 ± 0.019 | 1/6 | $920 ± 66 | 5 | 39.0% | 1.49 | 20/54/20/5 |
| **wide3** (SL 6.75, TMO 288) | **+0.207** | +0.175 ± 0.093 | 4/6 | $1258 ± 337 | 1 | 40.4% | 2.02 | 19/40/13/**29** |

Eşleştirilmiş fark wide3 − baseline: **+0.230R ± 0.078, t = 2.95**, 5/6 tohumda
iyileşme. Çıkış dağılımı ilk kez piyasaya tepki veriyor: SL %54 → %40, TIMEOUT
%5 → %29 — 24 saat sınırı bağlıyor; pozisyonlar artık stopla değil zamanla
çözülüyor, ve zaman sürüklenmenin lehine. **Giriş sinyali latent sürüklenmeyle
ilişkili**: aynı 5m breakout puanı, çıkış ufka ölçeklenince +0.2R hasat ediyor.
H10.1'in "YANLIŞ ise" dalı ("giriş bilgisiz") dışlandı.

Not: bu, sentetik `trend` üzerinde bir **mekanizma** kanıtıdır — geometri ufka
ölçeklenirse makine var olan edge'i alabiliyor. Gerçek veride o edge'in
olmadığını H10.2 zaten ölçtü (naif kural OOS'ta −0.04R). Sonraki adım geometri
değil ufuk/sinyal: hangi ufukta gerçek veride VR > 1 var?

### Sonuç H10.1b — `wide3` × `null` (6 tohum, sızıntı kontrolü)

| tohum | baseline bakiye | momR | wide3 bakiye | DD | n | momR | TP2/SL/TRL/TMO |
|--:|--:|--:|--:|--:|--:|--:|--:|
| 1 | $859.53 | -0.213 | $1039.57 | -12.4% | 152 | **+0.050** | 19/51/19/63 |
| 2 | $946.00 | -0.021 | $1004.33 | -12.3% | 209 | **+0.022** | 18/76/37/78 |
| 3 | $1080.09 | +0.084 | $949.20 | -13.7% | 106 | **-0.023** | 9/42/12/43 |
| 4 | $1005.02 | +0.026 | $1104.57 | -7.4% | 85 | **+0.148** | 10/32/14/29 |
| 5 | $919.38 | -0.088 | $917.29 | -13.9% | 154 | **-0.030** | 13/61/19/61 |
| 6 | $948.32 | -0.014 | $1218.10 | -13.7% | 179 | **+0.143** | 19/55/27/78 |

| geometri | `null` momR | `trend` momR | trend − null | halt (null/trend) |
|---|--:|--:|--:|--:|
| baseline (SL 2.25, TMO 96) | −0.016 | −0.040 | −0.017 (t −0.37) | 4 / 5 |
| **wide3** (SL 6.75, TMO 288) | **+0.049** (±0.032) | **+0.207** (±0.093) | **+0.123** (t 1.25) | 0 / 1 |

Karar kuralı "null ≤ +0.05R": havuz +0.049 geçiyor, tohum ortalaması +0.052
geçmiyor — **sınırda**. Kaynağı biliniyor ve sızıntı değil: `null` **log**
fiyatta martingale (log drift 0), dolayısıyla aritmetik getiri +σ²/2 per bar.
Uzun-yalnız bir sistem bundan tutma süresiyle orantılı yararlanır: 8h'te
≈ +0.04%/0.76% SL ≈ +0.05R (baseline'ın ücret tabanı −0.115'in üstünde
−0.016'da çıkmasını açıklar), 24h'te ≈ +0.08%/2.3% ≈ +0.03R (wide3'ün TIMEOUT
%40'ı tam süre tutuyor). Harness kusuru olarak kaydedildi: uzun-yalnız sistem
için doğru null **fiyatta** martingale (`drift = −σ²/2`). Eklenecek ve
`null_mart` × baseline / wide3 koşulacak; o zamana kadar temiz metrik
**trend − null** farkı: baseline −0.02 (piyasayı görmüyor), wide3 +0.12
(görüyor; 6 tohumla t=1.25 — yönü kesin, büyüklüğü gürültülü).

**H10.1 kararı: KABUL — mekanizma düzeyinde.** Aynı giriş sinyali, çıkış
geometrisi tutma ufkunun gürültüsüne ölçeklenince (SL ≈ 0.8× 8h SD, timeout
24h) sentetik trendi hasat ediyor (+0.21R, eşleştirilmiş +0.23R t=2.95) ve
sonucu ilk kez piyasaya bağlı hale geliyor (çıkış dağılımı null ile trend
arasında ayrışıyor). Tur 9'un teşhisi doğrulandı: kod değil, ölçek.

**Bu bir deploy kararı DEĞİL.** H10.2 aynı gün ölçtü: gerçek veride bu ufukta
(4h → 8–24h) hasat edilecek şey yok — naif kural OOS'ta −0.04R, in-sample'da
−0.07R. Makineyi wide3 ile gerçek veriye koşmak, boş bir tarlada daha iyi bir
orakla dolaşmak olur. Sıradaki soru geometri değil: **gerçek veride hangi
ufukta VR > 1 var?** (Tur 4'ün 4h/1g/1h taramasında hiçbiri buy&hold'u
geçmemişti; funding/basis tarafı Tur 6–8'de kapanmıştı.) Cevap "hiçbirinde"
ise, doğru sonuç "daha iyi bot" değil "bu piyasada yönlü bot yok"tur.

`wide4` × `trend` (×4 geometri, 48h) arka planda koşuyor; sonucu ek olarak
işlenecek, kararı değiştirmez.

**Durum: H10.1 ve H10.2 tamamlandı — 14 Eyl 2026.** Çalışan bota değişiklik
veya deploy yok.

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
