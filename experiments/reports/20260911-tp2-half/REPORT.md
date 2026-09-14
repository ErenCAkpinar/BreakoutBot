# N2 — TP2'de %50 kapat, kalan %50'yi taşı

**Sonuç: mevcut sisteme alınmıyor.** Tek oran olarak %50/%50 denendi. Mevcut
sisteme göre 240 günde net kâr $73,16 arttı, 665 günde $26,52 azaldı. Her iki
pencerede de mevcut sistemi geçme şartı sağlanmadı. Çalışan simülasyon botuna
değişiklik veya deploy yapılmadı.

## Deneyin tam tanımı

TP2, giriş ATR'sinin 6 katındaki hedef olarak kaldı. Hedefe gelince pozisyonun
yarısı kapatıldı. Kalan yarı aynı 3,75 ATR iz süren stop ve mevcut 96 bar faz
sınırıyla devam etti. TP2'de süre yeniden başlatılmadı. TP1'de satış yok;
TP1 mevcut sistemdeki gibi iz süren stopu başlatıyor ve faz sayacını sıfırlıyor.
Dolayısıyla 96 bar, işlemin bütün ömrüne ait tek bir sınır değil.

İlk risk, SL=2,25 ATR, sinyaller, yeniden giriş beklemesi ve hesap risk frenleri
korundu. Kalan parça açıkken aynı sembolde yeni pozisyon açılmadı ve iki
pozisyonluk portföy limitinde yer tutmaya devam etti. Araştırma beş coinin
tamamına uygulandı; yalnız NEAR için seçilmiş bir kural değil.

Mevcut fill sırası korundu: eski trail ve TP2 aynı mumda görülürse trail önce
çalışır. Yeni mumun yüksek/düşük değeriyle trail, ancak o mumdan sağ çıkıldıktan
sonra güncellenir. TP2 ile süre sınırı aynı mumda gelirse yarım TP2 satışı ve
kalan için mum kapanışında TIMEOUT oluşur. Her parçanın çıkış maliyeti kendi
büyüklüğünden alınır; kalan parçaya ikinci bir giriş ücreti yazılmaz.

## Beş coin, tek $1.000 hesap

Tüm kârlar probe işlemleri, giriş ücretleri ve modellenen çıkış maliyetleri
dahil **hesap netidir**. Maksimum düşüş gerçekleşmiş nakit bakiyesinden
hesaplanır; açık pozisyonların mum içi değer kaybını içermez.

| Pencere | Kural | Net kâr | Son bakiye | Maks. düşüş | Hard stop | Ana pozisyon |
|---|---|---:|---:|---:|---:|---:|
| 240 gün | Mevcut: TP2'de tamamı | +$435,46 | $1.435,46 | %6,14 | 0 | 114 |
| 240 gün | N1: TP2 yok | +$540,58 | $1.540,58 | %6,23 | 0 | 114 |
| 240 gün | **N2: TP2'de yarısı** | **+$508,62** | **$1.508,62** | **%6,18** | **0** | **114** |
| 665 gün | Mevcut: TP2'de tamamı | +$85,22 | $1.085,22 | %37,56 | 3 | 396 |
| 665 gün | N1: TP2 yok | +$68,89 | $1.068,89 | %45,57 | 4 | 387 |
| 665 gün | **N2: TP2'de yarısı** | **+$58,71** | **$1.058,71** | **%37,35** | **3** | **387** |

665 günlük üç koşu da `BT_RESTARTS=1` ile hard stop sonrasında operatörün
yeniden başlattığı varsayımını kullanıyor. Bunlar kesilmeden çalışan bir botun
getirisi değildir. Üç koşu da tüm pencereyi bitirdi; kapanmamış pozisyon yok.

N2'nin 240 günlük kâr iyileşmesine eşlik eden düşüş farkı 0,038 yüzde puan
kötüleşme. 665 günde düşüş yalnız 0,209 yüzde puan iyileşiyor ve hard stop
sayısı mevcut sistemle aynı kalıyor. N1'e göre uzun dönemde düşüş belirgin
azalıyor; ancak N2'nin kârı hem mevcut sistemin hem N1'in altında.

240 günde 35 pozisyonda yarım TP2 satışı oldu; kalanlar 31 TRAIL ve 4 TIMEOUT
ile kapandı. 665 günde 81 yarım TP2 satışı oldu; kalanlar 74 TRAIL ve 7 TIMEOUT
ile kapandı. Bu satışlar ikinci bir ana pozisyon sayılmadı.

## Neden iki uç sonucun ortalaması değil?

Kalanı taşımak, mevcut TP2 kapanışına göre pozisyonu daha uzun açık tutar.
Sonraki sinyaller, yeniden giriş zamanı ve portföy yer paylaşımı değişebilir.
240 günlük N1 ve N2 koşularında girişler ve büyüklükler aynı kaldı; mevcut
sistemin işlem dizisi ise farklı.

665 günlük N1 ve N2 koşularında da giriş anahtarları aynı, fakat 96 pozisyonun
büyüklüğü farklı. Kârın bir kısmının daha erken gerçekleşmesi nakit bakiyesini
ve buna bağlı risk frenlerini değiştiriyor. Bu nedenle bütün portföy sonucunu
iki uç stratejinin kârlarının aritmetik ortalamasıyla tahmin etmek doğru olmaz.

## Ekrandaki NEAR işlemleri

Gerçek altı girişin fiyatı, ATR'si, büyüklüğü ve riski sabit tutularak 4–7 Eylül
verisinde ayrıca tekrar oynatıldı. Bu, portföydeki bütün sonraki sinyalleri
yeniden üreten bir test değil; doğrudan çıkış etkisini ölçüyor. Probe hariç,
tek giriş ve tüm çıkış maliyetleri dahil.

| Kural | Altı girişin toplam neti |
|---|---:|
| Mevcut TP2 | +$57,63 |
| TP2'de yarım satış | +$44,52 |
| TP2 yok | +$31,41 |

Yarım satış bu örnekte **$13,11 daha az** kazandırıyor. Dört TP2 işleminde de
kalan parça hedef fiyatından daha kötü bir trail seviyesinde kapanıyor. Diğer
iki işlem değişmiyor. Girişler ve büyüklükler sabit olduğu için bu dar örnekte
yarım satışın sonucu iki uç sonucun tam ortasında.

[İşlem bazında fiyatlar, saatler ve muhasebe](near_fixed_entry.md).

Tarihsel portföyde NEAR'ın bütün ücretler dahil katkısı N2 ile 240 günde
$56,82 → $69,94; 665 günde −$54,53 → −$33,86 oluyor. Bu, yalnız NEAR'a
uygulandığında aynı sonuç çıkacağının kanıtı değildir: beş coinin çıkışı ve
hesap bakiyesi birlikte değişti. Eylül örneğiyle tarihsel portföyün farklı
sonuç vermesi de tek birkaç kazanan üzerinden karar vermememiz gerektiğini
gösteriyor.

## Veri, kontroller ve karar sınırı

240 günlük işlem penceresi yaklaşık 27 Aralık 2025–24 Ağustos 2026;
665 günlük pencere 28 Ekim 2024–24 Ağustos 2026. Her birinde 40 günlük ısınma
verisi var. Coin bazında birkaç mumluk başlangıç/bitiş farkları kaynak
manifestinde kayıtlı. İki pencere örtüşüyor; iki bağımsız doğrulama örneği
değiller. Eylül'deki altı giriş bu iki pencerenin dışında.

Beş coin ve BTC filtresinin toplam 12 cache dosyasının hash'leri önceki N1
manifestleriyle eşleşti. Kontrol olarak 240 günde yeni doğrulanmış
`N1-control-20260910`, 665 günde mevcut sistemle aynı geometriyi kullanan
`R1-sl225t96` sonucu kullanıldı; 665 günlük kontrol yeniden koşulmadı.
`strategy.py`, `config.py`, `metrics.py`, `backtest.py` benimsenmiş `dded71b`
sürümüyle aynı. `paper_bb.py` çalışma ağacındaki HEAD ile aynı; bu deneyde
değişmedi.

Bağımsız muhasebe kontrolü altı koşudaki her giriş ve çıkışı eşledi, her
çıkışın orantılı maliyetini ve bar sonu nakitten maksimum düşüşü yeniden
hesapladı. Bütün bakiyeler uzlaştı; açıkta kalan ya da iki kez sayılan kısmi
pozisyon yok. **117 test geçti**, bunun 23'ü yeni mekanizmayı kapsıyor. Yeni
araştırma dosyalarının Ruff kontrolü ve strateji/başlatıcının mypy kontrolü
geçti.

Standart `ledger.py`, `R1-baseline` adlı daha eski sistemi seçtiği için N2'ye
orada “AL” yazıyor. Bu çalışmanın referansı o eski sistem değil, benimsenmiş
`R1-sl225t96` geometrisi. Yukarıdaki karşılaştırma doğru referansları açıkça
seçiyor. CLI'nin tek pencereye göre bastığı “DEPLOY” etiketi de deneyin iki
pencere benimseme şartının yerine geçmez.

Funding modellenmiyor; ücret ve kayma mevcut backtest'in sabit maliyet
varsayımını kullanıyor. %50, önceden seçilmiş tek oran; %25/%75 taraması
yapılmadı. **Bu sonuç bütün kısmi çıkış fikirlerini çürütmez, fakat denenmiş
%50/%50 kuralına geçmek için kanıt sağlamaz. Mevcut TP2 ve yeniden giriş
düzenini koruyoruz.**

## Tekrar üretim

```sh
./experiments/run_arm.sh N2-tp2-half-20260911 240 BT_TP2_CLOSE_FRAC=0.5
./experiments/run_arm.sh N2-tp2-half-20260911 665 BT_RESTARTS=1 BT_TP2_CLOSE_FRAC=0.5
python3.12 experiments/ledger.py
python3.12 experiments/reports/20260911-tp2-half/compare_portfolios.py
python3.12 experiments/reports/20260911-tp2-half/replay_near_fixed_entries.py
```

Sonuçlar: [240g ham kayıt](../../results/N2-tp2-half-20260911_240d.json),
[665g ham kayıt](../../results/N2-tp2-half-20260911_665d.json),
[hesap karşılaştırması ve kaynak hash'leri](portfolio_comparison.json),
[NEAR sabit giriş hesapları](near_fixed_entry.json).
