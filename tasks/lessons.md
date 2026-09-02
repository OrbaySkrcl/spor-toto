# Dersler

Bu dosya geliştirme sırasında öğrenilen ve tekrarlanmaması gereken şeyleri
tutar.

## 1. Sınıf sırası sözleşmesi tek yerde tanımlanmalı

`EloOrdinal.fit` sonuçları `(2, 0, 1)` sırasıyla, `predict` ise `(1, 0, 2)`
sırasıyla ele alıyordu. Optimizasyon β'nın işaretini çevirerek uyum sağladı;
model yakınsadı, hata vermedi, ama **her tahmini ters üretiyordu.**

**Ders:** Sıralı/kategorik çıktı üreten her modelde sınıf sırası tek bir yerde
tanımlanmalı (`pipeline.OUTCOME_ORDER`) ve *monotonluk testi* yazılmalı —
"girdi artarken çıktı da artmalı". Yakınsama doğruluk kanıtı değildir.

## 2. Maskelenmiş parametreler serbest kalır

Log havuz birleştirmede, veri setinde hiç görünmeyen bir bileşenin ağırlığı
log-loss'u etkilemez. Optimizasyon onu keyfî bir değerde bırakır. O bileşen
üretimde ortaya çıktığında, **hiç doğrulanmamış bir ağırlıkla** devreye girer.

**Ders:** Fit edilen her parametrenin veri tarafından *gerçekten* kısıtlandığını
kontrol et. Kısıtlanmıyorsa parametreyi modelden çıkar, varsayılana sabitle
veya kapsama oranını raporla.

## 3. Pencere sınırları iki uçtan da uygulanmalı

`walk_forward` bloklar hâlinde ilerlerken son bloğun `stop`'u `end`'i aşıyordu.
Sonuç: backtest'in kalibrasyon penceresi değerlendirme penceresine taşıyordu.
Bu tür sızıntılar skorları *iyileştirdiği* için fark edilmesi zordur.

**Ders:** Zaman pencereli döngülerde alt sınır kadar üst sınırı da filtrede
uygula. Ve sızıntıyı davranışsal olarak test et: geleceği boz, geçmiş
değişmemeli.

## 4. Aynı veriyi iki kez normalize etme

`budget_frontier`, `_normalize` çıktısını tekrar `optimize_coupon`'a veriyordu;
o da yeniden normalize etmeye çalışıp `TypeError` atıyordu. Manuel denemelerde
yakalanmadı çünkü o yolu elle çalıştırmamıştım.

**Ders:** Normalizasyon/doğrulama yapan genel giriş noktalarından ayrı bir
"çekirdek" fonksiyon tut; iç çağrılar çekirdeği kullansın. Ve her genel
fonksiyonun en az bir testi olsun — elle denenmeyen kod yol demektir.

## 5. Şema yazmak özelliği uygulamak değildir

`adjustments` tablosu, `add_adjustment`, `active_adjustments` ve
`DixonColesFit.rates(adjustments=...)` yazılmıştı — ama hiçbir çağrı yolu
düzeltmeleri gerçekten geçirmiyordu. Özellik "var" görünüyordu, çalışmıyordu.

**Ders:** Bir özelliği ancak *uçtan uca* bir test onu doğruladıysa
tamamlanmış say. "Düzeltme girildi → tahmin değişti" testi olmadan bu tablo
dokümantasyonda yalan olurdu.

## 6. Sınırsız kalibrasyon parametreleri patlar

Blend sıcaklığı serbestken dejenere doğrulama verisinde 0,011'e indi; bu
olasılıkları 0/1'e iter ve kupon optimizasyonunu tamamen bozar.

**Ders:** Kalibrasyon parametrelerini fiziksel olarak anlamlı bir aralığa
sıkıştır (burada `tanh` ile `[0,6 – 1,8]`). Optimizasyon, bulabilirse aşırı
değere kaçar.

## 7. Geliştirme ortamının ağ kısıtı mimariyi belirlemeli, engellememeli

Bu ortamın egress politikası birincil veri kaynağını (`football-data.co.uk`)
engelledi. Çözüm, kaynağı değiştirmek değil, **kaynak katmanını takılabilir
yapmak** oldu: birincil (oranlı), ayna (gerçek veri, oransız), yerel ve
sentetik. Model gerçek 19.763 maçla doğrulandı, oran yolu sentetik veriyle.

**Ders:** Dış bağımlılığı arayüzün arkasına al. Kısıt, tasarımı iyileştiren bir
zorlama olabilir — sistem artık kapalı ağlarda da çalışıyor.

## 8. "Doğruluk" yanlış metrik

İlk raporlama isabet oranına odaklanıyordu. Ama kupon optimizasyonu doğrudan
olasılıkların üzerine kurulu: kalibre olmayan bir model yüksek isabetle bile
kötü kupon üretir. RPS ve kalibrasyon tablosu eklendi.

**Ders:** Metriği son hedefe göre seç. Hedef 15/15 ise, ölçülecek şey
"olasılıkların ne kadar dürüst olduğu"dur, "kaç tane bildiği" değil.
