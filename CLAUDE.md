# BreakoutBot

Rejim-farkındalıklı kripto breakout + mean-reversion botu. Binance Futures **testnet**
(sim mode) — gerçek para yok. Faz geçmişi, deney sonuçları ve kürasyon kararları
`BENCHMARKS.md`'de; her karar orada gerekçesiyle kayıtlı.

Canlı sistem sunucuda `breakoutbot-test` servisi olarak koşuyor (5 coin, E6 çıkış
parametreleri env'den: `X_TP1_CLOSE_FRAC=0.0 X_TRAIL_ATR=2.5`).

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
