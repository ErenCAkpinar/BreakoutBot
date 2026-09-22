# BreakoutBot

Rejim-farkındalıklı kripto breakout + mean-reversion botu. **Saf simülasyon** — gerçek
Binance Futures fiyat verisi, yerel sanal cüzdan, borsaya emir gitmiyor. Emekli v1
sürümü testnet'e gerçek emir gönderiyordu; çalışan bot göndermiyor — API anahtarı
yüklü bile değil. "testnet" kelimesi bu projede yalnızca o emekli sürümü anlatır.

Faz geçmişi, deney sonuçları ve kürasyon kararları `BENCHMARKS.md`'de; her karar
orada gerekçesiyle kayıtlı.

Canlı sistem sunucuda `breakoutbot-test` servisi olarak koşuyor (4 coin:
UNI/INJ/ADA/NEAR — POL 2026-09-22'de sahip kararıyla çıkarıldı, `config.py` TOKENS
bloğu ve `BENCHMARKS.md` Faz 9'da gerekçesi ve karşı-kanıtı kayıtlı).

## Parametreler env'de DEĞİL, config.py'de (2026-08-27'den beri)

Doğrulanan çıkış seti artık `config.py` **varsayılanı** (`BENCHMARKS.md` Faz 8):
`SL_FULL_ATR=2.25 · TP1=3.0 · TP2=6.0 · TRAIL=3.75 · TIMEOUT_BARS=96 ·
TP1_CLOSE_FRAC=0.0 · MR_ENABLED=False`. Env'siz koşmak **doğru** sistemi test eder.

Systemd unit'inde **X_\* override yok** (2026-09-22'de doğrulandı: E6'dan kalma
`X_TP1_CLOSE_FRAC` / `X_TRAIL_ATR` satırları yorumlanmış, `DEPLOYED_SHA` deploy'u
gösterir). Öyle kalmalı — bir X_* bayrağı ancak `experiments/DEFTER.md`'de onu hak
eden bir kol varsa eklenir. `python3.12 backtest.py` açılışta env sapmasını yazdırır.

## Deney disiplini

Parametre değişiklikleri `experiments/` üzerinden gider, elle değil:
`./experiments/run_arm.sh <kol> <gün> ENV=VAL…` → `python3.12 experiments/ledger.py`.
**Bir kol ancak 240g VE 665g'de birden baseline'ı geçerse alınır** — tek pencere
kanıt değil (n≈120'de SE ≈0.16R). Her hipotez ve sonucu `experiments/DEFTER.md`'de.

## Health Stack

- typecheck: mypy . --ignore-missing-imports --exclude 'office/'
- lint: ruff check .
- test: pytest
- deadcode: ruff check . --select F401,F841

Notlar:
- `shellcheck` kurulu değil → shell lint atlanıyor (`deploy_test.sh` denetlenmiyor).
- Sistem `python3` (3.14) bu projenin bağımlılıklarına sahip değil; ccxt/pandas/numpy
  **`python3.12`** altında. Betikleri `python3.12 ...` ile çalıştır.
- `office/` kişisel içerik, `.gitignore`'da — denetim dışı.
