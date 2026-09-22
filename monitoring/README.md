# Sunucuda 15 dakikalık Codex gözlemi

Bu servis BreakoutBot'un simülasyon state ve log dosyalarını okur, kısa bir
Türkçe AI raporu üretir. Emir göndermez, bot state'ini veya parametrelerini
değiştirmez; botu başlatmaz/durdurmaz. Rapor hiçbir işlem motoruna bağlı değildir.

Zamanlama sunucunun `systemd` servisine aittir; Mac'in veya Codex masaüstü
uygulamasının açık olması gerekmez. Masaüstü Scheduled ekranında görünmez.
Sunucunun çalışması, internet erişimi ve geçerli Codex oturumu gerekir.

## Düzen

- Kod: `/opt/breakoutbot-observer/`
- Kaynak: `/root/BreakoutBot-test/state_paper.json`, `paper_bb.log`
- Son rapor: `/var/lib/breakoutbot-observer/latest.md`
- Makine kaydı: `/var/lib/breakoutbot-observer/latest.json`
- Geçmiş: `/var/lib/breakoutbot-observer/history.jsonl`, `runs/`
- Zamanlayıcı: `breakoutbot-observer.timer`
- Çalıştırıcı: `breakoutbot-observer.service`
- Model/binary ayarı: `/etc/breakoutbot-observer.conf`

Her saat :00, :15, :30, :45'in 30. saniyesinde tetiklenir. Böylece botun
5 dakikalık mum kapanışından sonraki yazımına zaman bırakılır. Bir çalışma
en fazla 180 saniye sürer; systemd 240 saniyelik dış sınır uygular. Çakışan
çalışmalar kilitle engellenir. Sunucu yeniden açıldığında timer geri gelir;
kaçırılan tüm turlar art arda model çağrısına dönüştürülmez.

Her tur önce sınırlı bir snapshot kaydeder ve hesap kotasını okur. Beş saatlik
veya haftalık kotada kalan pay %10 veya altındaysa model çağrısı yapılmaz.
Kota okunamıyorsa da çağrı yapılmaz. Sonraki 15 dakikalık tur tekrar kontrol
eder; yeterli pay oluşunca değerlendirme kendiliğinden devam eder. Kullanıcı
ve diğer Codex görevleri aynı kotayı tükettiği için %10 kesin bir rezerv
garantisi değildir; kontrol ile çağrı arasında tüketim değişebilir.

Codex mevcut ChatGPT oturumunu kullanır. Ayrı API anahtarı, kredi alımı veya
limit sıfırlaması kurulmaz. Varsayılan model `gpt-6-astra`, muhakeme `low`;
her tur yeni ve kısa bağlam kullanılır. Shell, uygulama bağlantıları,
tarayıcı, eklentiler ve alt ajanlar kapalıdır. Model yalnız verilen snapshot'ı
değerlendirir. Çıktı şeması ayrıca Python tarafından doğrulanır.

## Sonuçları okuma ve kontrol

Mac'ten:

```bash
ssh breakoutbot 'cat /var/lib/breakoutbot-observer/latest.md'
ssh breakoutbot 'systemctl list-timers breakoutbot-observer.timer --no-pager'
ssh breakoutbot 'journalctl -u breakoutbot-observer.service -n 20 --no-pager'
```

Sunucudaki Codex sohbetinden de şu dosyanın okunması istenebilir:
`/var/lib/breakoutbot-observer/latest.md`.

`completed` geçerli AI değerlendirmesi; `paused_quota` kullanım payı az;
`paused_quota_unknown` kota doğrulanamadı demektir. Başarısız bir değerlendirme
önceki başarılı raporun güncelmiş gibi gösterilmesine yol açmaz. Ayrı arşivde
eski sonuçlar korunur. Her turun zaman damgası, girdi ve sürüm hash'leri,
modeli ve varsa token tüketimi kaydedilir.

Bu sürüm harici mesaj veya bildirim göndermez. Son rapor ve geçmiş sunucuda
okunur; normal koşullarda 96 günlük bildirim üretmez.

## Ölçüm sınırları

State güncel olsa bile fiyatların tazeliği doğrulanmış sayılmaz. Mevcut state
mark fiyatı, spread, funding, haber veya henüz yürütülmemiş sinyal adaylarını
içermiyor. Geçmiş işlem bacaklarından işlem öncesi tahmin başarısı çıkarılmaz.
Bu ilk sürüm operasyonel denetim ve açık pozisyonlarda tutarlılık incelemesidir.
AI giriş filtresini ölçmek için ayrıca sinyal anında veri kaydı gerekir;
15 dakikalık kontrol her 5 dakikalık giriş kararına yetişmez.

AI'ın önerileri, kârın arttığına dair kanıt veya pozisyon kapatma talimatı
değildir. Giriş/çıkış stratejisinde değişiklik yapılacaksa projenin 240g ve
665g deney disiplini ve ileriye dönük doğrulama geçerlidir.

## Doğrulama ve durdurma

```bash
python3.12 -m pytest tests/test_monitor_snapshot.py tests/test_monitor_runner.py
python3.12 -m ruff check monitoring tests/test_monitor_snapshot.py tests/test_monitor_runner.py
python3.12 -m mypy monitoring --ignore-missing-imports
```

Yalnız gözlemi durdurmak için:

```bash
ssh breakoutbot 'systemctl disable --now breakoutbot-observer.timer'
ssh breakoutbot 'systemctl stop breakoutbot-observer.service'
```

Bu komutlar `breakoutbot-test` servisini etkilemez. Arşiv korunur.

## Dağıtım

`snapshot.py`, `runner.py`, `prompt.md`, `assessment.schema.json` dosyaları
`/opt/breakoutbot-observer/` içine; iki unit dosyası `/etc/systemd/system/`
içine kopyalanır. Kod manifest'i SHA-256 içerir. Config dosyası sunucudaki
doğrulanmış Codex binary yolunu `OBSERVER_CODEX_BIN`, modeli `OBSERVER_MODEL`
olarak tanımlar. `/var/lib/breakoutbot-observer` önceden oluşturulur; dizin ve
raporlar yalnız root tarafından okunur. Bir servis çalışması doğrulandıktan
sonra timer etkinleştirilir.

Çalışan botun deploy betiği kullanılmaz; bot kodu, state ve unit dosyası bu
kurulumun kapsamı dışındadır. Var olan gözlemci güncellenirken timer geçici
durdurulur, önceki kod ve unit dosyaları yedeklenir; smoke test başarısızsa
eski sürüme dönülür.
