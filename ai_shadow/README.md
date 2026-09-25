# AI gölge vetosu (DEFTER Tur 15)

Botun açtığı her **tam pozisyonu** (`FULL OPEN`) Claude Opus 5.5'e değerlendirtir
ve cevabı — `ALLOW` / `VETO_RECOMMENDED` / `NO_DATA` — yalnızca kayda geçirir.
Bot bu servisten habersizdir: kodu, env'i, unit'i değişmez, hiçbir işlem
cevabı beklemez. Hipotez, karar kuralları ve fazlar: `experiments/DEFTER.md` → Tur 15.

## Nasıl çalışır

1. `breakoutbot-ai-shadow.timer` her 5 dakikalık barın 25. saniyesinde tetikler.
2. İşçi `state_paper.json` → `trade_log`'da henüz değerlendirilmemiş `FULL OPEN`
   bacaklarını bulur (yalnız `SHADOW_SINCE` sonrası; her sinyal **bir kez**).
3. Girişin bar kapanışı = **veri kesimi**. Binance'ten coin ve BTC için 5m/1h/4h
   mumları `endTime = kesim − 1` ile çeker; kesimden sonra kapanan mum (ör. henüz
   dolmamış 1h/4h barı) atılır ve ayrıca assert edilir. İhlal → `NO_DATA`
   (`lookahead_guard`).
4. Modele tek çağrıda şunlar gider: mum tabloları, koddan hesaplanan özellikler
   (getiri, ATR, oynaklık, aralık konumu, hacim oranı, SMA farkları, BTC
   korelasyonu), botun girişteki seviyeleri (SL/TP/notional — trail ve TP1
   ilerlemesi *gelecek bilgisi* olduğu için hariç), kesim anındaki pozisyon defteri.
5. Cevap şemayla zorlanır (`decision.schema.json`) ve yerelde yeniden doğrulanır.
6. Her sonuç `decisions.jsonl`'a eklenir; girdi ve ham çıktı `runs/` altında saklanır.

Başarısızlık hiçbir zaman sessiz değildir: hata, zaman aşımı, ret, kredi bitmesi,
bütçe tavanı, bayat birikim hepsi nedenli bir `NO_DATA` kaydıdır. Yedek model
kullanılmaz — reddedilen bir isteği başka modelin cevaplaması kolu değiştirir.

## Sunucuda düzen

| ne | nerede |
|---|---|
| kod | `/opt/breakoutbot-ai/ai_shadow/` (+ `DEPLOYED_SHA`) |
| Python | `/opt/breakoutbot-ai/.venv` (`anthropic==1.8.0`; botun venv'ine dokunmaz) |
| API anahtarı | `/etc/breakoutbot-ai/anthropic.env` (600, root) — **asla `cat` etme** |
| ayarlar | `/etc/breakoutbot-ai/shadow.conf` (örnek: `shadow.conf.example`) |
| kayıtlar | `/var/lib/breakoutbot-ai-shadow/decisions.jsonl`, `runs/` |
| unit'ler | `breakoutbot-ai-shadow.service` + `.timer` |

Unit kısıtlı çalışır: dosya sistemi salt-okunur (yalnız kayıt dizini yazılabilir);
`/root` boş bir tmpfs'tir ve içine yalnız `/root/BreakoutBot-test` salt-okunur bağlanır —
botun dizini dışında `/root` altındaki hiçbir şey görünmez.

## Okuma

```bash
ssh breakoutbot 'cd /opt/breakoutbot-ai && .venv/bin/python -m ai_shadow.worker --summary'
ssh breakoutbot 'tail -3 /var/lib/breakoutbot-ai-shadow/decisions.jsonl'
ssh breakoutbot 'journalctl -u breakoutbot-ai-shadow.service -n 20 --no-pager'
```

`--summary` Faz 0 ölçütlerini verir (geçerli öneri payı ≥ %95, medyan gecikme
< 120 s, çağrı başına ≤ $0.30, look-ahead ihlali 0). Bu bir veto değerlendirmesi
**değildir** — o Faz 1'de, kapanmış pozisyonlarla yapılır.

## Maliyet ve bütçe

Tam giriş başına ~$0.18 (≈15K girdi + 6K çıktı; $4/$20 per MTok). Console'da
aylık limit $20 ve auto-reload kapalı; işçi kendi harcamasını ayrıca sayar ve
takvim ayı içinde `SHADOW_BUDGET_USD`'yi (18) aşacaksa çağrı yapmaz (`budget`).

## Dağıtım

```bash
./ai_shadow/deploy.sh
```

Commit edilmemiş değişiklikle çalışmaz. Kod, venv ve unit'leri kurar; **timer'ı
açmaz**. İlk kurulumda `shadow.conf` elle yazılır, bir duman testi geçtikten
sonra timer açılır:

```bash
ssh breakoutbot 'systemctl enable --now breakoutbot-ai-shadow.timer'
```

## Durdurma

```bash
ssh breakoutbot 'systemctl disable --now breakoutbot-ai-shadow.timer'
```

Bot etkilenmez; kayıtlar korunur.

## Doğrulama

```bash
python3.12 -m pytest tests/test_ai_shadow.py
python3.12 -m ruff check ai_shadow tests/test_ai_shadow.py
python3.12 -m mypy ai_shadow --ignore-missing-imports
```
