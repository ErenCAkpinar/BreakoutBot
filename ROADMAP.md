# BreakoutBot — Çok-Rejim Evrim Yol Haritası

> **Durum: TASARIM / SİNDİRME aşaması.** Henüz kod yazılmıyor. Bu döküman; mevcut kod analizini, iki araştırma agent'ının bulgularını ve önümüzdeki upgrade'leri tek yerde toplar. Amaç: başlamadan önce her şeyi netleştirmek.
>
> Tarih: 2026-06-02 · Yazan: Eren + Claude ortak çalışması

---

## 0. Tek cümlede amaç

Kanıtlanmış **long momentum edge'ini koruyarak**, bota **rejim-farkındalığı** kazandırmak: yükselişte long, yatayda (range) mean-reversion, düşüşte ise long'u kapat + (dikkatli, opsiyonel) short. Hedef: "para makinesi" değil, **kötü piyasada kanamayı durduran, iyi piyasada kazanan** dengeli bir sistem.

---

## 1. Şu an neredeyiz?

**Strateji:** Wave 11 MathEngine sinyali (composite 0-100) → `test ($20) → 1-bar onay → full ($900 notional ×3x) → TP1/TP2/TRAIL` state machine. 23 token. Pratikte **long-only**.

**Canlı paper sonucu (Mayıs 30 – Haziran 1):**
- 4 full pozisyon: 1 kazanç / 3 kayıp, WR %25, R:R 0.62
- Net −%3.04 ($1000 → $969), MaxDD −%3.04 (kontrollü)
- Sebep: **choppy → bearish** rejim; long-only strateji tape'e karşı

**Altyapı:**
- Hetzner sunucu (Helsinki) — systemd ile 7/24, **eski/güvenli kod** çalışıyor
- `dashboard.py` — go/no-go checklist panosu
- Gerçek market verisi (production klines) + testnet emir

---

## 2. Bu oturumda tamamlananlar ✅

| # | Değişiklik | Durum |
|---|---|---|
| 1 | **Forming-bar bug fix** — `fetch_recent` artık kapanmış bar kullanıyor (vol_ratio≈0 sorunu çözüldü, onaylar başladı) | ✅ canlıda |
| 2 | **Gerçekçi maliyetler** — fee %0.04→%0.05 (gerçek taker) + slippage %0.025/yön (23 coin'den ölçülen) = round-trip %0.15 | ✅ canlıda |
| 3 | **Risk-based sizing** — sabit $900 yerine `notional = RISK_$ / sl_frac`; her full SL ~$10 (coin ATR'ından bağımsız) | ✅ backtest doğrulandı, **henüz deploy edilmedi** |

> Not: #3 backtest'te SOL/INJ/FET 90g → 3'ü de kârlı, MaxDD −4/−5.6%, WR ~%73. Araştırma da doğruladı (vol-targeting = ~%25 daha az DD).

---

## 3. Kod analizi — review bulguları

| Öncelik | Bulgu | Durum |
|---|---|---|
| 🔴 Yüksek | **Sabit $900 sizing** → volatil coin (FET −$14) sakin coinden (NEAR −$9) çok kaybediyor, aynı 1.5×ATR stop'ta | ✅ #3 ile düzeltildi |
| 🟡 Orta | **Pratikte long-only + kaba BTC gate** → düşüşte tüm girişleri bloklar (short da açamaz) | ⬜ Faz 1-3 |
| 🟡 Orta | **R:R 0.62** — kazançlar (TP1+trail) küçük, kayıplar (full SL) büyük; ara sıra büyük TP2'ye muhtaç | ⬜ Faz 2 (range) + exit ayarı |
| 🟢 Düşük | Daily SL freeze mesajı yanıltıcı ("FETUSDT freezing" ama tüm sembolleri donduruyor) | ⬜ kozmetik |
| ✅ İyi | Daily SL freeze tetikleniyor, BTC gate çalışıyor, test-confirm filtresi ucuz eliyor, DD kontrollü | — |

---

## 4. Araştırma sentezi (2 yeni agent + önceki 4-agent)

### ⚡ EN KRİTİK BAĞLAM: Şu an bear market
- BTC Ekim 2025'te ~$126K zirve → ~%50 düştü (Şubat 2026 <$64K)
- Alt'lar (bizim 23 coin) **late-2024'ten beri** düşüşte
- **Sonuç:** long-only bot ~18 aydır tape'e karşı savaşıyor. Mevcut gate, short'un tek edge olduğu rejimde bizi dışarıda tutuyor. Logdaki `🔴GATE-LONG` yağmuru tam bu.

### İki agent'ın UZLAŞTIĞI ilkeler
1. **#1 değişiklik = yapısal directional rejim gate** (200-MA tabanlı), kaba −%1.5 tek-eşik değil. İkisi de bunu birinci sıraya koydu.
2. **33→40 eşik gevşetme = HAYIR.** Agent #2: *"aktif reddedeceğim tek değişiklik."* Düşük-kaliteli sinyalleri sınırsız-kayıp tarafına ekler = küçük-hesap aşırı-işlem hatası. Gerekirse short eşiği **daha sıkı** (≤30).
3. **Short = opsiyonel, ayrı/sıkı mantık** (bear rejim + yükselen OI + lower-high), **küçük boyut** (0.5-0.7×), likit-only, **aylarca paper-first**. Eşik mirror'lamak DEĞİL.
4. **Vol-targeted sizing** = ikisi de öneriyor (✅ yaptık).
5. **Range (chop) = mean-reversion** (RSI/Bollinger). Chop'ta MR Sharpe ~2.3, momentum'dan çok iyi. "Boş bekleme" açığını doldurur.
6. **Düşüş için short ŞART değil.** "Long/flat" (düşüşte nakde geç) kanıtlanmış bedava koruma (Grayscale, QuantPedia) — short'un sınırsız riskine girmeden.

### Önceki 4-agent araştırmasıyla örtüşme (plan arşivinde)
- ATR-scaled sizing ✅, asymmetric exits, drawdown kill-switches, regime filter, funding-rate farkındalığı, delta-neutral sleeve fikri. Hepsi aynı yöne işaret ediyor: **rejim disiplini + drawdown kontrolü.**

### Verifiye gerçekler (sindirilecek)
- Kazanan trader'lar **delta-nötr / market-maker** (HLP vault: ~%450/2.4yıl, düşük DD). Yönlü "kahramanlar" (James Wynn: 40x long → 1 haftada $100M→$23) patlıyor.
- **Ders: kaldıraç öldürür, yön ikincil.** Bizim 3x makul — short sleeve'i asla daha yükseğe çekme.
- Negatif funding (2026) → short tutmak "para kazandırıyor" (nüans), ama short'un risk-yönetimi sorunu (sınırsız kayıp, squeeze, ADL) kalıcı.

---

## 5. SİNDİRİLECEK ANA İLKELER (kararların temeli)

1. **Edge'i çoğaltma yolu = frekans değil, rejim isabeti.** Aynı sinyali doğru rejimde kullanmak, daha çok sinyal üretmekten iyi.
2. **Drawdown kontrolü kâr peşinden koşmaktan önce gelir.** Hayatta kalan sistem, en çok kazanan değil, en az batan.
3. **Sample size kutsal.** ~1 trade/gün → bir değişikliği doğrulamak ya 200-500+ trade ya da çok-rejimli backtest ister. Backtest'ler maliyetsiz fiyatlanırsa abartır.
4. **Her değişiklik tek tek + backtest'le.** Bundle yığma yok; hangi değişiklik neyi bozdu görmek için incremental.
5. **Short = savunma/tamamlama upgrade'i, money-printer değil.** Beklenti dürüst: en büyük kazanç gate fix (long'u da iyileştirir).
6. **Asla validate olmadan deploy yok.** Canlı bot her zaman doğrulanmış kodla.

---

## 6. UPGRADE PLANI — Fazlar (detaylı)

> Sıralama: önce temel (gate), sonra range motoru, en son short. Her faz: **ne / neden / dokunulan dosyalar / kabul kriteri / risk.**

### FAZ 1 — Directional Yapısal Rejim Gate 🟢 (ilk, en yüksek değer)

**Ne:** `config.BTC_GATE_RETURN = −0.015` tek-eşiğini kaldır → BTC'nin yapısal rejimini hesapla ve yöne göre davran.

**Tasarım (karar verilecek detaylar §9'da):**
- Rejim tanımı: `BTC fiyat vs 200-period MA (4h ya da 1d) + MA eğimi`
  - `BULL`: fiyat > MA **ve** eğim > 0
  - `BEAR`: fiyat < MA **ve** eğim < 0
  - `NEUTRAL`: arası
- Davranış:
  - `BULL` → long aç (mevcut), short blok
  - `BEAR` → long **blok** (+ Faz 3'te short izinli), açık long'larda trail sıkılaştır
  - `NEUTRAL` → her ikisi azaltılmış boyutla, ya da beklemede

**Neden:** İki agent de #1. −%1.5 "uçurum" eşiği whipsaw üretiyor + overfit kokuyor (tek mum tetikliyor). Yapısal 200-MA hem long'u korur (bear tape'e breakout almayı keser) hem short'un kapısını açar. **Short hiç ateşlemese bile long'u iyileştirir.**

**Dosyalar:** `indicators.py` (BTC 200-MA + eğim hesabı; yeni TF fetch gerekebilir), `paper_bb.py` + `backtest.py` (gate mantığı), `config.py` (yeni rejim parametreleri).

**Kabul kriteri:** Backtest'te bull+bear+chop dönemlerinde (özellikle 2022 + 2025 bear) eski gate vs yeni gate → MaxDD ↓, bear dönemlerde gereksiz long kaybı ↓. Parametre küçük değişimine duyarlı OLMAMALI (overfit kontrolü).

**Risk:** Düşük-orta. Sadece giriş filtresi; sinyal/execution mantığına dokunmaz.

---

### FAZ 2 — Range için Mean-Reversion Motoru 🟡

**Ne:** Sadece `RANGE` rejiminde (ADX<25 + düz EMA-200) ateşleyen ayrı bir mean-reversion sinyal yolu.

**Tasarım:**
- Tetik: `RSI(14) < 30` long / `> 70` short-exit; Bollinger(20,2) alt banda dokunuş
- Sıkı gate: ADX < 25 **ve** EMA-200 düz (trend'e karşı asla)
- Küçük hedef (kısa TP), sıkı stop, **düşük boyut** (momentum'un ~yarısı)
- Momentum composite'inden **bağımsız** — ayrı kanal

**Neden:** Tam "chop'ta boş bekleme" açığı (−%3'lük haftamız). Chop'ta MR edge'i momentum'dan belirgin yüksek (Sharpe ~2.3 vs ~1.0). Range'i ölü zamandan küçük pozitif carry'ye çevirir.

**Dosyalar:** Yeni `mean_reversion.py` (ya da `strategy.py`'a 2. mod), `config.py` (MR parametreleri), `backtest.py` + `paper_bb.py` (rejime göre hangi motor).

**Kabul kriteri:** Backtest'te range dönemlerinde MR pozitif katkı; trend dönemlerinde MR **devre dışı** (yanlışlıkla trend'e karşı işlem yapmamalı). Momentum WR'sini bozmamalı.

**Risk:** Orta. Yeni sinyal mantığı + rejim entegrasyonu. İki motor birbirine karışmamalı.

---

### FAZ 3 — Short Sleeve (ayrı, sıkı, paper-first) 🔴 (en son, en dikkatli)

**Ne:** Sadece `BEAR` rejimde, **ayrı ve daha sıkı** short mantığı. Eşik gevşetme DEĞİL.

**Tasarım (agent #2 reçetesi):**
- Şartlar (hepsi): `BEAR` rejim **+** teyitli breakdown (**yükselen OI**) **+** lower-high/lower-low yapı
- Boyut: long'un **0.5-0.7×**'i (fat right tail telafisi)
- ATR stop ama short'ta daha sıkı time-stop (squeeze hızlı)
- Sadece **en likit** coin'ler (low-float/yeni-listing hariç — squeeze silahı)
- Funding: teyit filtresi (aşırı-pozitif funding + breakdown → short bias; derin-negatif funding → short azalt/kaçın)
- Weekend/gece girişlerini blokla/azalt (ince likidite = squeeze)
- **Aylarca paper-first**, canlı sermaye ancak kanıttan sonra

**Yeni veri ihtiyacı:** **Open Interest (OI)** şu an snapshot'ta yok. Binance OI endpoint'i eklemek gerekir (yeni bağımlılık).

**Neden:** Bear market'teyiz; long-only mismatch. Disiplinli short = rejim-bütünlüğü. AMA sınırsız risk + ADL + düşük trade sayısı → dürüst beklenti **mütevazı**.

**Dosyalar:** `indicators.py` (OI fetch + lower-high tespiti), `math_engine.py`/`strategy.py` (short mantığı + boyut), `config.py` (short parametreleri), tüm exit mantığı (short tarafı zaten `mult=-1` ile var).

**Kabul kriteri:** Backtest'te bear dönemlerde (2022, 2025) short pozitif/nötr; bull dönemlerde short **hiç ateşlememeli**; squeeze senaryolarında kontrollü kayıp. **Statistical power uyarısı:** short <1 trade/gün → backtest anlamlılığına alçakgönüllü yaklaş.

**Risk:** Yüksek. Sınırsız kayıp tarafı + yeni veri + en düşük örneklem. En son, en yavaş, en küçük.

---

### CROSS-CUTTING — Sentiment freni: Fear & Greed Index 🟢 (Eren önerisi)

**Ne:** Market-geneli Fear & Greed (0-100) — **trend sinyali olarak DEĞİL** (BTC 200-MA ile redundant). **Kontrarian aşırı-uç freni** olarak:
- **Aşırı Greed (>80)** → yeni **LONG azalt/durdur** (tepe/"zirveyi alma" riski) → **Faz 1 eklentisi**
- **Aşırı Fear (<20)** → yeni **SHORT azalt/durdur** (dip/squeeze/"dibi shortlama" riski) → **Faz 3 güvenlik freni**

**Neden:** Botun en pahalı 2 hatasını (tepede long, dipte short) doğrudan hedefler. Squeeze'ler aşırı-fear'da olur — agent #2'nin short riski uyarısının tam panzehiri.
**Veri:** CMC API (key gerekir, + global metrics) **veya** alternative.me (anahtarsız/ücretsiz, orijinal indeks). Günde 1 değer, cache'lenir.
**Param disiplini:** sadece 2 eşik (80/20) — ≤5 kuralı korunur. Mütevazı filtre, ana sürücü değil.

### Diğer dış-veri sinyalleri — SEÇİLİ (çok veri ≠ iyi)
**Disiplin:** her ekleme = +param + API + overfit riski. Sadece **orthogonal (tekrarlamayan)** olanları ekle, her birini ayrı backtest'le doğrula.

| Sinyal | Değer | Kaynak | Faz |
|---|---|---|---|
| **Funding rate** (per-coin) | Aşırı-pozitif=kalabalık long=short setup; derin-negatif=squeeze riski | **Binance** (per-coin, ücretsiz) | Faz 3 short teyidi |
| **BTC Dominance / Alt-Season** (biri) | BTC-liderli mi alt-liderli mi → rejim blend ağırlığını **dinamik** ayarla | CMC | Faz 1.5 rafine |
| **Liquidations** | Büyük long-liq = kapitülasyon → short açma freni | CMC/Coinglass | Faz 3 short freni |
| **Exchange in/out flows** | Borsaya akış = satış baskısı (opsiyonel makro teyit) | CMC | opsiyonel |

**SKIP (redundant/gürültü):** RSI/MACD (kendimiz hesaplıyoruz), ETF flows (yavaş/redundant), CMC20/100 (BTC redundant), trending/gainers/top-traders/community-sentiment (social gürültü), news/AI-chatbot (veri değil).

---

## 7. Doğrulama disiplini

- **Backtest:** her faz, SOL + birkaç coin, **90+ gün**, mümkünse bull+bear+chop kapsayan dönem (2022 bear, 2025 chop/bear out-of-sample).
- **Maliyet dahil:** fee %0.05 + slippage %0.025 (zaten kodda).
- **Walk-forward:** eşikleri sadece geçmiş veriyle aylık recalibrate (overfit kontrolü).
- **Parametre sayısı ≤5 çekirdek:** küçük değişime duyarlılık = overfit kırmızı bayrağı.
- **Sample:** anlamlı sonuç için 200-500+ trade. Short sleeve için aylarca paper.
- **Unit testler:** her yeni mantık için (rejim sınıflandırma, MR tetik, short şartları).

---

## 8. Gerçekçi beklentiler

| Sermaye | Sürdürülebilir aylık | Notlar |
|---|---|---|
| $1,000 | %3-5 | Sadece momentum + rejim |
| $1,000 + range motoru | %4-6 | Chop ölü zamanı azalır |
| $5,000+ | %5-8 | Multi-regime tam |

- **%10+/ay sürdürülebilir DEĞİL** (4 agent + şampiyon araştırması uzlaştı).
- En büyük tek kazanç = **gate fix** (long'u da iyileştirir, short hiç olmasa bile).
- Bu bir "rejim-bütünlüğü/savunma" upgrade'i — felaketi önler, jackpot vermez.

---

## 9. KARARLAR (kilitlendi — 2026-06-02)

Tüm açık sorular cevaplandı. Final tasarım:

| Soru | Karar |
|---|---|
| **Rejim TF** | **4h, 200-MA + eğim** (~33 gün; hızlı tepki) |
| **Rejim kaynağı** | **Blend: her coin kendi trendi + BTC makro**, BTC ağırlığı ~**%57.5** (55-60 ortası): `regime = 0.575×BTC + 0.425×coin` |
| **NEUTRAL + BEAR davranışı** | Long'u **%35 boyuta** düşür + **short'u aç** (BEAR'da short ağırlıklı). BULL'da long %100, short kapalı |
| **Short mantığı** | **Ayrı kanal** (composite'i bypass eder): fiyat **kırılımı + OI artışı + lower-high** |
| **OI verisi** | **Eklenecek** (Binance/CMC OI endpoint) — short kalitesi için |
| **Test ortamı** | **Aynı sunucuda 2. servis** (`breakoutbot-test`), canlıya dokunmadan A/B |
| **Mean-reversion kapsamı** | **23 coin'in hepsi**, sadece RANGE rejiminde |
| **Risk/mod** | Claude belirledi — aşağıdaki matris |

### Rejim × yön × boyut matrisi (final)

| Rejim | Long | Short | Mean-reversion (range) |
|---|---|---|---|
| **BULL** | %100 → **$10** | kapalı | kapalı (trend var) |
| **NEUTRAL** | %35 → **$3.5** | açık, sıkı, **küçük** → **$5** | açık → **$5** |
| **BEAR** | %35 → **$3.5** | açık + **ağırlıklı**, sıkı, **orta** → **$8** | kapalı (trend aşağı) |

> **Risk gerekçesi:** Trend long %1 (ana edge). Throttled long %0.35 (Eren'in "%35" kararı). **Short: BEAR'da %0.8 ORTA boyut** (kendi rejimi, yüksek-konviksiyon — Eren'in tercihi) / **NEUTRAL'da %0.5 küçük** (daha belirsiz). Mean-reversion %0.5. Hepsi 3× kaldıraç + MAX_NOTIONAL tavanına tabi.

### ⚠️ Tek dürüst uyarı (short ağırlığı)
"BEAR'da ağırlıklı short" tercihi makul (rejim-uygun), **ama** araştırma short'un sınırsız-kayıp + squeeze + ADL riskine dikkat çekti. Uzlaşma: **short'ları ağırlıklı yap (yön olarak) ama her short'u disiplinli tut** — sıkı mantık (kırılım+OI+lower-high), küçük boyut ($6.5), sadece likit coin, **test servisinde aylarca paper**. "Ağırlıklı" = bear'da ana yön; "disiplinli" = her işlem küçük+kaliteli. İkisi çelişmez.

---

## 10. Deploy / Ops prosedürü (validate'ten SONRA)

```bash
# 1. (Mac) doğrulanmış dosyaları sunucuya gönder
scp config.py strategy.py indicators.py paper_bb.py root@<vm-ip>:~/BreakoutBot/

# 2. (Sunucu SSH) servisi yeniden başlat
systemctl restart breakoutbot

# 3. (Sunucu) doğrula
journalctl -u breakoutbot -f
```
- **Strateji değişikliği = temiz başlangıç** (state'i $1000/bar #1'e resetle) ki yeni sistem temiz değerlendirilsin.
- Tüm değişiklikler tek seferde (sizing + Faz 1 + …) → tek reset.
- **Asla validate olmadan deploy yok.** Sunucu her zaman güvenli kodla döner.

---

## 11. Önerilen genel sıralama (özet)

```
✅ Faz 0  : forming-bar + maliyetler + risk-sizing            [TAMAM, deploy bekliyor]
⬜ Faz 1  : directional rejim gate (200-MA)                   [SIRADAKI — en yüksek değer]
⬜ Faz 2  : range mean-reversion motoru                       [chop açığı]
⬜ Faz 3  : short sleeve (ayrı/sıkı/paper-first/OI)           [en son, en dikkatli]
   ↓ her fazdan sonra: backtest → doğrula → (hepsi bitince) tek deploy + temiz başlangıç
```

---

## 12. Kaynaklar (araştırma)
- Grayscale — *The Trend is Your Friend* (BTC MA-crossover, to-cash downside koruma)
- QuantPedia — Trend-following vs Mean-reversion in Bitcoin (bear'da trend hayatta, MR negatif)
- QuantInsti — Regime-adaptive trading (HMM, Sharpe 1.76 vs 1.16)
- ADX rejim filtresi, EMA 20/50/200 yapısı, Hurst exponent
- Vol-targeting sizing (Concretum, LuxAlgo) — ~%25 DD azalması
- Hyperliquid on-chain: HLP vault (delta-nötr kazanan) vs James Wynn (yönlü patlama)
- Funding economics (2026 negatif streak), short asymmetric risk (Bybit, ADL: insights4vc)
- Bear market 2026 teyidi (CoinDesk/Pantera, KuCoin)

> Tam URL'ler agent raporlarında; gerekirse eklerim.

---

*Bu döküman canlı — sindirdikçe, karar verdikçe güncellenecek. Kod yazımı, §9'daki kararlar netleşince Faz 1'den başlar.*
