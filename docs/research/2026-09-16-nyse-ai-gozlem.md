# NYSE reposu ve BreakoutBot için AI gözlem katmanı

16 Eylül 2026. İncelenen NYSE sürümü: `823254e1a9424136aa7c921c74741ebbe0674506`.
Bu çalışma kaynak kodu ve belgelerin incelemesidir. Dış repo çalıştırılmadı;
sunucuya bağlanılmadı, strateji değiştirilmedi, otomasyon kurulmadı.

**Karar:** NYSE'nin sinyal–gerekçe–sonuç panosu örnek alınabilir. Mevcut kod,
kanıtlanmış net getiri veya sürekli LLM işlem yönetimi sunmuyor. BreakoutBot için
ilk aday, 15 dakikalık genel denetim ve yeni sinyal oluştuğunda yapılan ayrı AI
değerlendirmesi. İlk değerlendirmeler yalnızca kayda girmeli; getiriyi artırdığı
ölçülmeden giriş veya çıkış kurallarını değiştirmemeli.

**NYSE gerçekte ne yapıyor?**

Robinhood Stock Token havuz fiyatını referans fiyatla karşılaştırıp yüzde fark
hesaplıyor. Eşik aşılırsa sinyal açıyor, fark daralırsa başarılı sayıyor.
README para taşıma/emir yürütme yapmadığını açıkça belirtiyor; Minara üzerinden
işlem yürütme yol haritasında. Bu, kripto breakout stratejimizden farklı bir
piyasa ve hipotez. [README](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/README.md)

- **Çalışma sırasında GPT kararı yok:** karar eşiklerden oluşuyor. Minara isteğe
  bağlı fiyat sağlayıcısı olarak kullanılıyor. GPT ile geliştirilmiş olması,
  GPT'nin her sinyali değerlendirdiğini göstermiyor.
  [gap.py](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/gap.py#L50-L57),
  [oracle.py](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/oracle.py#L28-L32)
- **Başarı oranı net kâr değil:** skor farkın ±%0,5 içine dönmesini sayıyor.
  Pozisyon büyüklüğü, gerçekleşen işlemler, komisyon, kayma, fonlama ve borçlanma
  maliyeti üzerinden PnL hesaplanmıyor. Açık kaynak ağacında paylaşımdaki başarıyı
  bağımsız doğrulayan işlem defteri bulunmadı. Canlı sitenin verisi bu incelemede
  alınamadı; sosyal medya iddiaları doğrulanmış sonuç olarak kullanılmadı.
  [Kayıt ve skor](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/gap.py#L23-L85)
- **Likidite tanımı tutarsız:** README %2 fiyat aralığında $50 bin derinlik
  anlatırken kod toplam rezervi derinlik sayıp $25 bin eşik uyguluyor. Toplam
  rezervden belirli miktarın hangi fiyattan gerçekleşeceği çıkmaz.
  [Veri okuma](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/gecko.py#L13-L26),
  [Eşik](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/gap.py#L6-L9)
- **Eksik veri skoru bozabilir:** fiyat bacaklarından biri eksikse açık sinyal
  güncellenmiyor; sekiz saatlik süre aşımı da o turda değerlendirilmiyor.
  `generatedAt` okunuyor ancak kararın veri tazeliği kontrolünde kullanılmıyor.
  [Döngü](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/desk.py#L79-L109),
  [Zaman damgası](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/nyse/robinhood.py#L34-L44)
- **Gösterilen stop farklı:** kayıtlı stop giriş farkının iki katıyken Telegram
  görünümü güncel farkın iki katını yazıyor. Giriş %1,5 ve güncel fark %2 ise
  kayıtlı stop %3, gösterilen stop %4 oluyor.
  [Telegram](https://github.com/slash1sol1/nyse/blob/823254e1a9424136aa7c921c74741ebbe0674506/bot.py#L110-L125)

Fiyat farkının kapanması uygulanabilir arbitrajı tek başına kanıtlamaz: iki
bacağın da eşzamanlı işlem görebilmesi, short için erişim/borç bulunması ve tüm
maliyetler önemlidir. Örneğin kapanmış piyasadan kalan $100 referansa karşı
$103 token fiyatı, $100'den gerçekten alım yapabileceğimiz anlamına gelmez.
Robinhood belgeleri varlık bazında seans uygunluğunun kontrol edilmesini ve
eski oracle verisinin reddedilmesini istiyor.
[Seanslar](https://docs.robinhood.com/chain/stock-tokens/),
[Oracle kuralları](https://docs.robinhood.com/chain/oracles-and-price-feeds/)

**Bizim deneylerin söylediği**

BreakoutBot gerçek Binance Futures verisiyle sanal cüzdanda çalışıyor; borsaya
emir göndermiyor. Beş coin, 5 dakikalık kapanmış mum ve bir mum sonraki probe
teyidi kullanılıyor. Bu nedenle 15 dakikada bir uyanan asistan bazı girişlerin
öncesine yetişemez. [config.py](../../config.py), [strategy.py](../../strategy.py),
[paper_bb.py](../../paper_bb.py)

| Deney | Sonuç | Tasarıma etkisi |
|---|---|---|
| Coin'in kendi trendini zorunlu tutmak | 23 net kârlı giriş elendi; son bakiye $1370,04 → $1295,95 | Kötü görünen işlemleri elemek toplam getiriyi düşürebilir |
| Minimum stop genişliği %0,75 | Ortalama R yükseldi, son bakiye $1370,04 → $1278,72 düştü | İşlem başına kalite tek başına yeterli ölçüt değil |
| TP2'yi kaldırmak | 240g +$105,12 iyileşme; 665g −$16,33 kötüleşme ve DD 8,01 puan artış | Kazananı daha uzun tutma fikri de iki pencerede sınanmalı |

Kaynak: [Deney defteri, Tur 2 ve N1](../../experiments/DEFTER.md). N1'in 665 günlük
karşılaştırması operatör restart'ı varsayımı içerir. Sonraki Tur 10–14 araştırması
da mevcut sinyallerin ek yön tahmin gücünü güvenilir biçimde kanıtlamadı; AI'ın
bunu düzelteceği şu an yalnızca yeni bir hipotezdir.

**Önerilen çalışma düzeni**

| Tetik | Görev | İlk aşamada yetki |
|---|---|---|
| Her 5m mum | Mevcut botun sinyal, pozisyon ve risk döngüsü | Mevcut simülasyon davranışı |
| Her yeni işlem adayı | Zaman damgalı veriyle AI değerlendirmesi | Yalnız öneri kaydı |
| Her 15m | Veri tazeliği, bot sağlığı, rejim ve pozisyon tutarlılığı denetimi | Rapor ve uyarı |
| Günlük | AI önerileriyle sonradan oluşan sonuçların karşılaştırılması | Analiz |

Genel kontrolde geciken veri, aynı sinyalin tekrarı, açıklanamayan state değişimi,
konfigürasyon sapması ve maliyet hesabı tutarsızlığı araştırılabilir. Haber veya
takvim kullanılacaksa olay ve yayın zamanı, kaynak ve varlıkla ilişki kaydedilmeli;
doğrulanamayan haber işlem gerekçesi sayılmamalı. Spread/fonlama gibi mevcut state'te
bulunmayan girdiler ayrıca ölçülmeli; model tarafından tahmin edilmemeli.

Yeni sinyal anındaki entegrasyon ayrıca geliştirilecek bir sunucu olayı/API işidir;
masaüstü sohbetinin kendiliğinden bot sinyallerini dinlediği varsayılmamalı.
AI gecikmesi veya yokluğu mevcut SL/TP/trail mekanizmasını bekletmemeli.

**Gölge deney taslağı**

1. Sonuç oluşmadan `signal_id`, veri kesim zamanı, fiyat/veri kaynakları, girdi
   özeti/hash'i, model/prompt sürümü, gerekçe, öneri (`ALLOW`, `VETO_RECOMMENDED`,
   `NO_DATA`), geçerlilik süresi ve gecikme kaydedilir. Eksik/geç kararlar ayrıca
   sayılır. Modelin güven puanı kalibre edilmiş olasılık kabul edilmez.
2. Mevcut baseline bütün işlemlerini sürdürür. AI giriş vetosu ve AI çıkış
   önerisi ayrı hipotezlerdir; ilk deney giriş vetosuyla sınırlı tutulur.
3. Baseline, basit sayısal filtre ve AI filtresi aynı maliyetlerle karşılaştırılır.
   Ana ölçüler net hesap getirisi, maksimum düşüş, önlenen zarar, kaçırılan
   kazananların kârı ve değerlendirme maliyetidir. Sadece başarı oranı raporlanmaz.
4. Portföy replay'i boşalan pozisyon yuvalarını, değişen bakiye/risk kısıtlarını ve
   sonraki girişleri yeniden hesaplar; yalnız veto edilen işlemleri defterden
   silmek yeterli değildir.
5. Tarihsel kural için 240g ve 665g koşulu korunur. Geçmiş haberler bugünün model
   bilgisiyle yeniden uydurulmaz. Noktasal tarihsel veri olsa bile LLM'in eğitim
   verisi sonuçları biliyor olabilir; ileriye dönük, dokunulmamış veri esastır.
   İki pencereyi geçmek tek başına istatistiksel kanıt değildir.
6. İlk birkaç hafta bağlantı ve kayıt kalitesi ölçülür. Yaklaşık 240 günde 123
   tam pozisyon üreten tarihsel tempoda birkaç haftanın kârlılığı kanıtlaması
   beklenmez. Örneklem belirsizliği raporlanır, sonuç görüldükçe eşik oynatılmaz.

**Buradan zamanlamak mümkün mü?**

Mevcut sohbet bağlamında dakika aralıklı görevler destekleniyor; 5 ve 15 dakika
kurulu uygulama tarafından desteklenen aralıklardır. Yerel dosyalara erişen
görev için bilgisayar açık ve uygulama çalışır olmalı. Zamanlamanın ertelenmesi
mümkündür; kritik işlem korumasını buna bağlamamak gerekir.
[OpenAI zamanlanmış görev belgeleri](https://learn.chatgpt.com/docs/automations?surface=app)

Tek toplu değerlendirme varsayımıyla 5m = 288/gün, 15m = 96/gün; 30 günde 8640
ve 2880 tetik. Her tetik gerçekleşmeyebilir. Kullanım normal hesap limitlerinden
tüketir; ayrı API servisinin faturalandırması ayrıdır. Kesin tüketim model,
bağlam ve araç yükü ölçülmeden hesaplanamaz.
[OpenAI fiyatlandırma ve kullanım](https://learn.chatgpt.com/docs/pricing)

İlk uygulama öncesinde sunucudan salt okunur, güncel bir snapshot alınabildiği
doğrulanmalı. Yerel dosya sunucunun güncel durumunun kanıtı değildir. Ayrıca
AGENTS.md eski env override'ları konusunda uyarırken deney defteri 27 Ağustos'ta
bunların kaldırıldığını kaydediyor. Güncel unit okunmadan hangi durumun geçerli
olduğu varsayılmamalı; bu araştırmada canlı durum doğrulanmadı.
