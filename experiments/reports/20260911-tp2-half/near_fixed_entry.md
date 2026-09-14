# NEAR: aynı altı girişte TP2'de yarısını kapatma deneyi

Bu çalışma **sabit girişlerle çıkış teşhisidir; portföy backtest'i değildir**. Saatler UTC. Gerçek girişler ile önceki incelemenin ATR, büyüklük ve riskleri korundu. Tek giriş ücreti ve her çıkışın orantılı yürütme maliyeti dahil; probe işlemleri hariçtir.

TP2'de %50 kapanıyor; kalan %50 aynı 3,75 ATR trail ve mevcut fazın süre sınırıyla taşınıyor. TP2'de süre sıfırlanmıyor. Kısmi çıkıştan sonra hesap, kalan pozisyon kapanıncaya kadar devam ediyor.

| Giriş (UTC) | Mevcut net | TP2 yok net | TP2'de yarısı net | Fark | Kalanın kapanışı (UTC) |
|---|---:|---:|---:|---:|---|
| 04 Eyl 06:40 | $+4.08 | $+4.08 | $+4.08 | $+0.00 | 04 Eyl 07:55 TRAIL |
| 04 Eyl 09:00 | $+12.60 | $+4.83 | $+8.72 | $-3.88 | 04 Eyl 10:15 TRAIL |
| 04 Eyl 19:20 | $+12.83 | $+11.41 | $+12.12 | $-0.71 | 04 Eyl 20:10 TRAIL |
| 06 Eyl 05:25 | $+12.88 | $+10.07 | $+11.47 | $-1.41 | 06 Eyl 08:30 TRAIL |
| 06 Eyl 09:05 | $+25.98 | $+11.75 | $+18.87 | $-7.11 | 06 Eyl 14:25 TRAIL |
| 06 Eyl 22:35 | $-10.73 | $-10.73 | $-10.73 | $+0.00 | 07 Eyl 00:55 SL |
| **Toplam** | **$+57.63** | **$+31.41** | **$+44.52** | **$-13.11** | |

Bu girişlerde yarım satış, mevcut TP2 ile hedefi tümüyle kaldırma sonucunun tam ortasında kaldı. Bu eşitlik, girişler/büyüklükler sabit ve maliyetler orantılı olduğu için oluşur. Portföyde geciken kapanışlar yeni sinyalleri, beklemeyi, risk büyüklüğünü ve diğer coinlerle yer paylaşımını değiştirebilir; portföy sonuçlarının bu ortalamaya eşit olması beklenmez.

Altı mevcut çıkış ve altı TP2'siz kontrolün zaman, fiyat, çıkış türü ve net PnL'si önceki arşivle eşleşti. Her üç varyantın ücretleri uzlaştırıldı. Gerçek kaydın dört ondalık yuvarlaması ile tekrar oynatma farkı $0,00011'den küçük.

Tekrar üretim: `python3.12 experiments/reports/20260911-tp2-half/replay_near_fixed_entries.py`. [Ayrıntılı JSON ve kaynak hash'leri](near_fixed_entry.json).
