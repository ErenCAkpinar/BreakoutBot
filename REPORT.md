# BreakoutBot — Testnet Raporu ve Postmortem

**Dönem:** 31 Mayıs – 7 Temmuz 2026 · **Ortam:** Binance Futures Testnet (paper, gerçek para yok)
**Veri kaynağı:** `journalctl -u breakoutbot` tam dökümü + `state_paper.json`

---

## 0. Yönetici özeti

- Bot mühendislik olarak tamamlandı ve 7/24 kesintisiz koştu: rejim-farkındalıklı
  sinyal → probe/confirm/full katmanlı giriş → ATR tabanlı çıkışlar → drawdown
  kill-switch'leri → systemd.
- **v1 stratejisinin edge'i negatif çıktı** (45 pozisyon, WR %33, PF 0.52) ve bot
  14 Haziran'da −%20.3 peak drawdown'da **hard stop'a çarparak kendini durdurdu.**
  Bu bir çöküş değil, temiz bir araştırma sonucu: risk sistemi görevini yaptı.
- Hard stop sonrası bir ops bug'ı ortaya çıktı: **systemd servisi ~245 kez restart
  döngüsüne girdi** (§5'te postmortem). Finansal hasar sıfır — bot her restart'ta
  yeniden durdu.
- **v2** (15 Haziran, sıkılaştırılmış filtreler + 8 sembollük küratörlü evren)
  zarar etmiyor ama **3.2 haftada 0 full pozisyon** açtı. Sorun "para kaybediyor"dan
  "sinyal üretmiyor"a dönüştü (overcorrection). Bir sonraki iş: sinyal hunisi
  telemetrisi ile darboğazı ölçmek (§7).

---

## 1. Yöntem ve veri

Metrikler, dönemin tamamını kapsayan `journalctl` dökümünden ve `state_paper.json`
kayıtlarından türetildi. Dökümdeki olay sayıları:

| Olay | Adet | Not |
|---|---|---|
| `TEST OPEN` (probe) | 311 | 308 v1 + 3 v2 |
| `CONF FAIL` | 228 | Probe onay alamadı → iptal |
| `CONFIRMED` | 58 | Probe onaylandı |
| `FULL OPEN` | 45 | Tamamı v1 döneminde |
| `CLOSE FULL` | 60 | 45 pozisyon > 45 event, çünkü TP1 %50 kısmi kapanış (bkz. §4) |
| `Resumed from state` | ~250 | ~245'i 14–15 Haz restart döngüsü (§5) |
| `MR OPEN` / `MR SL` | 1 / 1 | v2'deki tek gerçekleşen trade |

**Sınırlar:** Bakiye ile trade-toplamı mutabakatında küçük fark var (state reset +
resume gürültüsü); yön ve büyüklükler sağlam, kuruş kesinliği iddia edilmiyor.
Tüm rakamlar testnet.

---

## 2. Test düzeneği

| Parametre | v1 (31 May – 14 Haz) | v2 (15 Haz – …) |
|---|---|---|
| Sembol evreni | 23 USDT-M paritesi | 8 küratörlü sembol (Faz 4c) |
| Zaman dilimi | 5m | 5m |
| Kaldıraç / margin | 3× isolated | 3× isolated |
| Full pozisyon riski | ~$10/trade (bakiyenin ~%1'i) | aynı |
| Eş zamanlı full | MAX_OPEN = 2 | aynı |
| Seans | 04–23 UTC | aynı |
| Peak-DD hard stop | **−%20** (o build'de) | güncel kod: **−%15** |
| Başlangıç bakiyesi | $1000 | $1000 (reset) |

> Not — DD limiti: 14 Haziran'da tetiklenen v1 build'i log'a göre −%20 limitle
> koşuyordu (`hit PEAK_DD_LIMIT (-20%)`). Repo'daki güncel `config.py` −%15
> (`PEAK_DD_LIMIT = -0.15`). v2 daha sıkı limitle koşar.

---

## 3. Zaman çizelgesi

| Tarih (2026) | Olay |
|---|---|
| 31 May | v1 canlı testnet'e alındı ($1000) |
| 31 May – 14 Haz | 308 probe, 45 full pozisyon; bakiye kademeli eriyor: $989 → $946 → $891 → $797 |
| **14 Haz 14:10 UTC** | Bakiye $796.88 → peak'ten −%20.3 → **PEAK_DD hard stop**, bot kendini durdurdu |
| 14–15 Haz | systemd, botu ~5 dakikada bir yeniden başlattı (~245 kez); her seferinde bot resume edip yine durdu — **yeni pozisyon açılmadı** |
| 15 Haz | Manuel müdahale: state $1000'a reset, v2 (Faz 4c) deploy |
| 15 Haz 22:30 UTC | v2'nin tek trade'i: INJUSDT MR long → SL, −$5.19 |
| 15 Haz – 7 Tem | 3 probe, 0 CONFIRMED, 0 FULL OPEN; bakiye $994.17 (−%0.6) |

---

## 4. v1 dönemi analizi (31 May – 14 Haz)

**Sonuç: strateji para kaybetti, sistem doğru davrandı.**

### 4.1 Ana metrikler

| Metrik | Değer | Yorum |
|---|---|---|
| Full pozisyon | 45 | Hüküm vermeye yetecek örneklem |
| Win rate | %33 (15/45) | Bu R:R ile başabaş için ~%49 gerekiyordu |
| Ort. kazanç / kayıp | +$10.69 / −$10.34 | R:R ≈ 1:1 → düşük WR ölümcül |
| Profit factor | 0.52 | <1 = kaybeden sistem |
| Expectancy | −$3.33 / pozisyon | Her trade'in beklenen değeri negatif |
| Peak drawdown | −%20.3 | → hard stop (tasarlandığı gibi) |

### 4.2 Çıkış kırılımı — asıl teşhis burada

45 pozisyonun kaderi:

- **30 pozisyon** hiç TP1 görmeden **SL**'e gitti.
- **15 pozisyon** TP1'e ulaştı (%50 kapanış + SL breakeven'a alındı); kalan
  yarımların sadece **2'si TP2**'ye ulaştı, **13'ü trailing** ile kapandı.

Yani breakout'lar *tetikleniyor* ama trend *devam etmiyor*: 30 SL'e karşı yalnızca
2 TP2. Bu, klasik **fake breakout baskınlığı** imzası — sinyal katmanı kırılımı
yakalıyor, ama kırılımların çoğu takip almıyor.

### 4.3 Probe katmanının ekonomisi

308 probe'un net maliyeti **−$7.65**. Karşılığında 228 zayıf sinyal full pozisyona
dönüşmeden elendi. Katmanlı giriş tasarımı sahada kendini kanıtladı: sorun giriş
*mimarisinde* değil, mimariye beslenen *sinyal kalitesinde*.

### 4.4 Sembol dağılımı

Kayıplar düşük kaliteli coin'lerde yoğunlaştı; kazançlar daha likit/temiz isimlerde:

| Kaybettirenler | Kazandıranlar |
|---|---|
| ORDI −$48 · NEAR −$28 · ARB −$16 | AAVE +$8 · LDO +$4 · UNI +$3 |

Bu dağılım v2'deki 8 sembollük budamanın (Faz 4c) ana gerekçesi oldu.

### 4.5 Risk yönetimi doğrulaması

Position sizing hedefi "her full SL ≈ $10 kayıp" idi; gerçekleşen ortalama kayıp
**$10.34**. Sizing mekanizması sahada birebir doğrulandı. Ana sorun risk
yönetiminde **değil**, sinyal kalitesinde.

---

## 5. Olay postmortemi — hard stop + systemd restart döngüsü (14–15 Haz)

### Ne oldu

14 Haziran 14:10 UTC'de bot hard stop koşulunu doğru tespit etti:

```
Jun 14 14:10:33 breakoutbot python[277346]: Balance: $796.88 | MAX_OPEN: 2 | Session: 04–23 UTC
PEAK DD -20.3% hit PEAK_DD_LIMIT (-20%) — hard stop. Saving state.
```

State'i kaydedip çıktı. Ancak systemd botu ~5 dakika sonra yeniden başlattı; bot
`--resume` ile kalktı, DD koşulunu yine gördü, yine durdu — ve bu döngü ~20 saat
boyunca **~245 kez** tekrarlandı (log'da 14:10, 14:15, 14:20… ritmiyle görülüyor).

### Etki

**Finansal hasar: sıfır.** Bot her restart'ta pozisyon açmadan yeniden durdu;
bakiye $796.88'de sabit kaldı. Etki operasyonel: servis "flapping" halindeydi ve
log gürültüsü gerçek sinyali boğdu.

### Kök neden

Servis tanımındaki `RestartPreventExitStatus=1`, botun hard-stop'ta döndürdüğü
exit koduyla **eşleşmiyor**. systemd çıkışı "beklenmedik hata" sayıp
`Restart=` politikasını uyguladı. Kill-switch mantığı doğru; **process'in dünyaya
"bilerek durdum" deme şekli** yanlıştı.

### Ne iyi çalıştı

- Hard stop **insan müdahalesi olmadan** tetiklendi — kill-switch'in var olma sebebi.
- State her seferinde temiz kaydedildi; veri kaybı yok.
- Döngü sırasında tek bir yeni pozisyon bile açılmadı (resume → DD kontrolü → dur
  sıralaması doğru).

### Düzeltme planı (P1)

1. Hard-stop çıkışını deterministik yap: `sys.exit(1)`
   (`RestartPreventExitStatus=1` ile birebir eşleşen kod), **veya**
2. State'e kalıcı `"halted": true` bayrağı yaz + serviste `ExecStartPre` kontrolü —
   bayrak varsa servis hiç başlamasın.
3. Doğrulama ölçütü: fix sonrası tetiklenen bir hard stop'ta `journalctl`'de
   "1 stop, 0 restart" görülmeli.

### Ders

Kill-switch tasarımı iki yarımdır: *karar* (DD'yi tespit et, dur) ve *iletişim*
(orkestratöre bunun kasıtlı olduğunu söyle). İkinci yarım test edilmemişti çünkü
hard stop'un gerçekten tetiklendiği ilk olay buydu. Failure-path'ler de test ister.

---

## 6. v2 dönemi analizi (15 Haz – 7 Tem)

**Sonuç: zarar yok, ama edge hakkında hüküm verecek veri de yok.**

- 15 Haziran'da state $1000'a resetlendi, Faz 4c (8 küratörlü sembol + sıkı
  filtreler) devreye girdi.
- 3.2 haftada: **3 probe, 0 CONFIRMED, 0 FULL OPEN.**
- Tek gerçekleşen trade MR kanalından: INJUSDT long, 15 Haz 22:30 UTC,
  giriş 5.606 → SL 5.5714, **−$5.19**. Mevcut −%0.6'nın tamamı bu.
- Dönem boyunca rejim tablosu: 8 sembolün sadece 2'si BULL (SOL, UNI), gerisi
  BEAR/NEUTRAL — strateji long-momentum ağırlıklı olduğundan doğal bir baskı.

### Teşhis: overcorrection

v1'in kaybettiren sinyallerini kesmek için sıkılan filtreler, sinyal üretimini
tamamen boğdu. Backtest aynı konfigürasyonda trade üretiyordu (90 günde 8/8 kârlı,
PF 3.29 — bkz. [BENCHMARKS.md](BENCHMARKS.md)); canlıda 0 trade. Bu fark iki şeyden
birine işaret ediyor: (a) piyasa rejimi backtest penceresinden gerçekten farklı,
(b) canlı-backtest ayrışması var. **Hangisi olduğunu şu an ölçemiyoruz** — hangi
filtre aşamasında sinyallerin elendiğini gösteren telemetri yok. v2 şu haliyle
kalibrasyonu bilinmeyen bir kara kutu.

---

## 7. Öncelikli işler

### P1 — Restart döngüsü fix'i (bug, ucuz)
§5'teki plan. Küçük iş, ama servis güvenilirliği için ön koşul.

### P2 — Sinyal hunisi telemetrisi (ana iş)
Her barda tek satır sayaç:

```
bar=N | scanned=8 | regime_pass=X | breakout_touch=Y | probe_open=Z | confirmed=W | full=V
```

1–2 hafta veri sonrası darboğaz kendini gösterir:

| Gözlem | Şüpheli |
|---|---|
| `regime_pass` çok düşük | Rejim filtresi fazla katı (en olası — tablo 2/8 BULL) |
| `breakout_touch` var, `probe_open` yok | Breakout eşiği fazla yüksek |
| `confirmed/probe` oranı çok düşük | Confirm katmanı fazla muhafazakâr |

Telemetri olmadan yapılacak her parametre taraması kör atıştır.

### P3 — Sinyal kalitesi (v1'in kök nedeni)
Musluğu telemetri ile açtıktan sonra fake-breakout sorunu geri gelmesin diye:
hacim teyidi (breakout barında hacim > ortalama), ATR'ye göre kırılım gücü şartı,
TP2 oranı düşük kaldıkça TP1/trailing payını artırmak. Her değişiklik: önce
backtest → TEST servisi → MAIN'e promote.

---

## 8. Öğrenilenler

1. **Risk sistemi stratejiden bağımsız test edilebilir ve edilmeli.** v1'in edge'i
   negatifti ama sizing ($10.34 gerçekleşen ≈ $10 hedef) ve kill-switch (−%20.3'te
   otonom stop) birebir spesifikasyona uygun çalıştı.
2. **R:R ~1:1 + WR %33 matematiksel olarak ölüdür** — 45 trade bunu görmek için
   yeterli örneklemdi. Daha uzun süre "belki döner" diye beklemek veri değil
   umut olurdu.
3. **Overcorrection da bir başarısızlık modudur.** Kaybeden sinyalleri kesen
   filtre seti, ölçüm yoksa "hiç sinyal üretmeyen" sisteme dönüşebilir. Gözlem
   (telemetri) kalibrasyondan önce gelir.
4. **Failure-path'ler de deploy'un parçasıdır.** Hard stop ilk kez gerçekten
   tetiklendiğinde, çevresindeki ops zinciri (exit code ↔ systemd sözleşmesi)
   test edilmemiş çıktı.
5. **Canlı iterasyon pahalıdır.** v2 için 3 haftada 0 veri toplandı. Hızlı
   walk-forward backtest altyapısı, kalibrasyon döngüsünü haftalardan dakikalara
   indirir — muhtemel bir sonraki büyük yatırım.

---

## 9. Feragat

Tüm rakamlar Binance Futures **Testnet** üzerindendir (sahte bakiye). Bu doküman
bir mühendislik/araştırma raporudur; yatırım tavsiyesi değildir.
