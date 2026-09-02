# Spor Toto 15/15 Tahmin Sistemi — Görev Planı

## Görev Tanımı (Anladığım Kadarıyla)

Spor Toto kuponundaki **15 maçın 15'ini birden bilme olasılığını maksimize eden** bir sistem.
Bu tek başına bir "tahmin modeli" değil, iki parçalı bir problem:

1. **Olasılık üretimi** — her maç için kalibre edilmiş `P(1), P(0), P(2)`.
2. **Sistem kuponu optimizasyonu** — verilen kolon/bütçe kısıtı altında
   `P(15/15) = Π P(maç i'nin işaretlenen kümesi)` değerini **maksimize eden**
   tekli/ikili/üçlü dağılımını bulmak.

İkinci parça birincisi kadar önemli. Aynı olasılıklarla, kolonları doğru
maçlara dağıtmak 15/15 şansını kat kat değiştirir.

### Kısıtlar
- **Ücretsiz olmalı** (API anahtarı gerektirmeyen veri kaynakları).
- Kullanıcının **Telegram** ve **Railway** hesapları var → dağıtım hedefi bunlar.

## Ortam Analizi (doğrulandı)

| Kontrol | Sonuç |
|---|---|
| Depo durumu | Tamamen boş, ilk commit |
| Python | 3.11.15, numpy/pandas/scipy kurulabiliyor |
| `football-data.co.uk` erişimi | **Bu geliştirme oturumunda egress politikası ile 403** (Railway'de engel yok) |
| `raw.githubusercontent.com` | 200 — erişilebilir |
| GitHub aynası (`datasets/football-datasets`) | 5 lig × 11 sezon (1516–2526) gerçek veri, **oran sütunu yok** |
| `api.football-data.org`, `fbref`, `openligadb` | Engelli |

**Sonuç:** Veri katmanı **pluggable** olmalı. Birincil kaynak oranları da içeren
football-data.co.uk; geliştirme/doğrulama için GitHub aynası; testler için
deterministik sentetik üretici.

## Mimari Kararlar

| Karar | Seçim | Gerekçe |
|---|---|---|
| Birincil veri | football-data.co.uk CSV | Anahtarsız, ücretsiz, **bahis oranları dahil**, Türkiye T1/T2 + tüm Avrupa ligleri, 1993'ten beri |
| Yedek veri | GitHub aynası + yerel CSV | Sandbox/offline doğrulama |
| Skor modeli | Dixon–Coles + zaman ağırlığı (exp decay) | Beraberlik olasılığını doğru modelleyen standart; düşük skorlarda `rho` düzeltmesi |
| Güç modeli | Elo (gol farkı çarpanlı) + ordinal logit | Farklı hata yapısı → ensemble'a katkı |
| Piyasa modeli | Oran → olasılık (power/Shin marj temizleme) | Piyasa en güçlü tek sinyal |
| Birleştirme | Log-opinion-pool, ağırlıklar log-loss ile fit | Eksik kaynağa dayanıklı (oran yoksa yeniden normalize) |
| Kupon optimizasyonu | **Tam DP** (durum: çift sayısı, üçlü sayısı) | Kolon = 2^a·3^b olduğu için durum uzayı 16×16 → exact optimum, greedy değil |
| Depolama | SQLite | Ücretsiz, Railway volume'a sığar |
| Dağıtım | Railway worker + Telegram polling | Webhook/domain gerekmez |

## Yapılacaklar

### Faz 1 — İskelet ve veri katmanı
- [x] Depo/branch kurulumu, dizin yapısı
- [x] `tasks/todo.md` planı
- [x] `sportoto/config.py` — ayarlar, lig tanımları
- [x] `sportoto/storage.py` — SQLite şeması (matches, teams, predictions, coupons)
- [x] `sportoto/sources/` — footballdata_uk, github_mirror, local, synthetic
- [x] `sportoto/ingest.py` — indir/normalize/yaz, idempotent
- [x] `sportoto/teams.py` — takım adı normalizasyonu + bulanık eşleştirme (TR isimleri dahil)

### Faz 2 — Özellikler ve modeller
- [x] `sportoto/features/elo.py` — gol farkı çarpanlı Elo, iç saha avantajı
- [x] `sportoto/features/form.py` — son N maç, iç/deplasman ayrımı, dinlenme günü, H2H
- [x] `sportoto/models/dixon_coles.py` — zaman ağırlıklı MLE
- [x] `sportoto/models/market.py` — overround temizleme (basic/power/shin)
- [x] `sportoto/models/elo_model.py` — ordinal logit
- [x] `sportoto/models/blend.py` — log-pool, ağırlık fit
- [x] `sportoto/models/predictor.py` — tek giriş noktası

### Faz 3 — Kupon optimizasyonu (asıl hedef)
- [x] `sportoto/coupon/pricing.py` — kolon adedi & bedel tabloları
- [x] `sportoto/coupon/optimizer.py` — bütçe kısıtlı exact DP
- [x] Doğru sayı dağılımı (Poisson-binom DP) → P(15/15), P(14/15), P(13/15)...
- [x] Bütçe frontier tablosu

### Faz 4 — Backtest
- [x] `sportoto/backtest.py` — walk-forward, sızıntısız (yalnızca t öncesi veri)
- [x] Metrikler: accuracy, log-loss, **RPS**, Brier, kalibrasyon
- [x] Kupon simülasyonu: 15/15, 14/15, 13/15 sayıları, TL başına başarı

### Faz 5 — Arayüzler
- [x] `sportoto/cli.py` — ingest / train / predict / coupon / backtest
- [x] `sportoto/bot/telegram_bot.py` — /kupon, /tahmin, /guncelle, /backtest
- [x] Railway dağıtım dosyaları (Procfile, railway.json, runtime)
- [x] README — kurulum, ücretsiz dağıtım rehberi, **dürüst beklenti matematiği**

### Faz 6 — Doğrulama
- [x] Birim testleri (optimizer optimalliği brute-force ile, market marj, DC toplamı 1)
- [x] Gerçek veriyle uçtan uca backtest çalıştırma ve sonuç raporu
- [x] `tasks/lessons.md`

## Kapsam Dışı / Neden

Kullanıcının paylaştığı listede olup **uygulamayacağım** ve nedeni:

| İstenen | Durum | Neden |
|---|---|---|
| xG / xGA / PPDA / big chances | **Stub + manuel override** | Ücretsiz ve yasal toplu API yok (fbref scraping ToS ihlali + engelli). Model bunları alacak şekilde tasarlanacak. |
| Sakatlık / ceza / ilk 11 | **Manuel override arayüzü** | Ücretsiz güvenilir kaynak yok. Bot üzerinden `/eksik` ile takım gücüne elle düzeltme girilebilir. |
| Motivasyon (küme düşme vs.) | **Türetilmiş proxy** | Puan durumundan otomatik türetilecek (sezon sonu + sıralama), elle etiket yok. |
| Canlı oran hareketi | **Kısmi** | football-data.co.uk açılış+kapanış oranı verir (hareket hesaplanabilir), tick-level yok. |


---

# Sonuçlar / Review

## Teslim edilenler

Boş depodan çalışan bir sisteme: **31 kaynak dosyası (~4.900 satır), 160 test, hepsi geçiyor.**

| Katman | Durum | Doğrulama |
|---|---|---|
| Veri (4 kaynak) | ✅ | 19.763 gerçek maç indirildi (5 lig, 2015–2026) |
| Takım eşleştirme | ✅ | 23 Türkçe/Avrupa isim varyantı, %100 isabet |
| Dixon–Coles | ✅ | Analitik gradyan sayısal gradyanla doğrulandı (hata < 1e-4) |
| Elo (sıralı logit) | ✅ | Monotonluk testi; örnek dışı log-loss 0,991 (taban 1,075) |
| Piyasa (marj temizleme) | ✅ | power/shin/basic; favori-longshot düzeltmesi test edildi |
| Blend (log havuz) | ✅ | Kapsama koruması + sıcaklık sınırı |
| **Kupon optimizasyonu** | ✅ | **144 senaryoda kaba kuvvetle birebir eşleşti** |
| Backtest | ✅ | 3.504 maç, sızıntı testi ile kanıtlandı |
| CLI + Telegram + Railway | ✅ | Uçtan uca çalıştırıldı |

## Ölçülen performans

Gerçek veri (5 Avrupa ligi, 3.504 maç, Ağu 2024 – May 2026, **oransız**):
isabet %52,7 · log-loss 0,9881 · RPS 0,2012 · kalibrasyon sapması ±%2 içinde.
Referans: düzgün dağılım 1,0986, sabit taban oranı 1,0746.

Oranlı yol (sentetik lig): blend piyasanın kendisini de geçti
(log-loss 0,9742 vs 0,9764).

## Geliştirme sırasında bulunan ve düzeltilen 5 gerçek hata

1. **Elo sıralı modelde sınıf sırası uyuşmazlığı** — `fit` (2,0,1) sırasıyla,
   `predict` (1,0,2) sırasıyla çalışıyordu. Optimizasyon β işaretini çevirerek
   uyum sağladığı için model *sessizce ters olasılık* üretiyordu. Monotonluk
   testi eklendi.
2. **`walk_forward` `end` sınırını aşıyordu** — son yeniden kestirim bloğu
   `end`'i taşıyordu. Backtest'te kalibrasyon penceresinin değerlendirme
   penceresine sızması demekti; yani raporlanan skorlar tam örnek dışı değildi.
3. **Blend, hiç görülmemiş bileşene ağırlık atıyordu** — oran verisi olmayan
   veri setinde `market` bileşeni 0,38 ağırlık aldı (maskelendiği için
   log-loss'u değiştirmiyordu). Oranlar devreye girdiğinde doğrulanmamış bir
   ağırlıkla çalışacaktı. Kapsama eşiği eklendi.
4. **`budget_frontier` çöküyordu** — normalize edilmiş satırları tekrar
   `_normalize`'a veriyordu. Test yakaladı; çekirdek ayrıldı.
5. **`adjustments` tablosu ölü koddu** — sakatlık düzeltmeleri yazılıyor ama
   tahmin yoluna hiç ulaşmıyordu. Bağlandı, CLI + bot komutu ve test eklendi.

## Bilinçli kapsam kararları

* **xG verisi yok.** Ücretsiz ve yasal toplu API'si yok; FBref/Understat kazımak
  ToS ihlali olurdu. Yerine isabetli şut hâkimiyeti (zayıf vekil) kullanıldı.
  Mimari yeni özellik eklemeye açık: blend katkı verip vermediğine kendisi
  karar veriyor.
* **Motivasyon vekili yazıldı ama bağlanmadı.** `motivation_proxy` mevcut;
  backtest'te anlamlı katkı vermediği için boru hattına dahil edilmedi.
* **Beklenen getiri (EV) hesaplanmıyor.** İkramiye havuzu verisi ücretsiz
  kaynaklarda yok. Araç olasılık maksimize eder, kâr değil.
* **Kolon birim fiyatı yapılandırılabilir bırakıldı** (varsayılan 5 TL).
  Spor Toto bunu güncellediği için koda gömmek yanlış olurdu.

## Sonraki adımlar (isteğe bağlı)

* Süper Lig'i de kapsayan bir doğrulama: `--source footballdata --leagues T1`
  ile (bu ortamda ağ engeli nedeniyle çalıştırılamadı, kullanıcının makinesinde
  veya Railway'de çalışacaktır).
* TFF 1. Lig kapsanmıyor — football-data.co.uk'ta yok. Spor Toto listesinde
  çıkarsa o maçlar için yalnızca form bileşeni çalışır.
* İkramiye havuzu verisi bulunursa `optimize_coupon`'a EV modu eklenebilir
  (olasılık yerine beklenen getiri maksimizasyonu).
