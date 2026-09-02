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

## 9. Konum tabanlı satır eşlemesi kırılgandır

`prepare_frame` tarihe göre sıralama yapıyordu ve bir kuponun 15 maçı aynı
tarihi paylaşıyordu. `sort_values` varsayılan olarak **kararsız** (quicksort)
olduğu için bekleyen satırların göreli sırası bozuluyor, `.tail(n)` ile
okunduğunda **15 maçın 14'ü yanlış maçın verisiyle** tahmin ediliyordu.
Fenerbahçe–Beşiktaş'a Erzurumspor–Konyaspor'un rakamları atanıyordu.

Sessiz bir hataydı: çıktı makul görünüyordu, hiçbir test düşmüyordu, yalnızca
tahminler yanlış maçlara aitti.

**Ders:** Bir veri çerçevesinden satır seçerken asla konuma güvenme. Açık bir
kimlik taşı ve onunla geri eşle. Sıralamayı kararlı yapmak yardımcı olur ama
tek başına yeterli bir güvence değildir — asıl güvence kimliktir.

## 10. Kalibrasyon ile kullanım koşulları ayrışabilir

Blend ağırlıkları, oranların **hep mevcut olduğu** geçmiş veride öğreniliyordu.
Piyasa diğer bileşenleri sıfıra eziyordu. Kullanıcı kupon yapıştırdığında oran
yoktu; geriye ağırlığı sıfır bileşenler kalıyor ve model **her maça
%33/%33/%33** diyordu. Yani sistem, tam da kullanıldığı senaryoda çalışmıyordu.

**Ders:** "Modeli hangi veriyle eğitiyorum" ile "hangi veriyle kullanacağım"
aynı olmayabilir. Bir girdinin eğitimde her zaman, üretimde hiç bulunmadığı
durumu ayrıca kestir ve test et. Burada çözüm, her bileşen kombinasyonu için
ayrı profil fit etmek oldu.

## 11. Bulanık arama "en yakın"ı döndürür, "doğru"yu değil

Çözümleyici `"Zzz Kulubu XYZ"` sorgusuna %12 benzerlikle `"Team B"` cevabını
veriyor, sistem de o takımın gücüyle güvenli görünen bir tahmin üretiyordu.
Kullanıcı bunu fark edemezdi.

**Ders:** Bulanık eşleştirmeye bir **kabul eşiği** koy. Eşiğin altında
"bulamadım" de. Yanlış tanımak, tanımamaktan kötüdür; çünkü ilkinde kullanıcı
uyarılmaz.

## 12. Geniş tablolar telefonda okunmaz

Terminal için tasarlanmış 76 karakterlik tablolar Telegram'da satır sarmasıyla
okunamaz hâle geliyordu. Kullanıcı çıktıyı hiç anlamadı — hesap doğru olsa bile
işe yaramıyordu.

**Ders:** Çıktının hedef ortamını tasarım kısıtı say. Aynı veri için terminal
(geniş, hizalı) ve telefon (dar, sarmalı, kalın vurgulu) ayrı biçimlendirici
hak eder. Doğru hesap, okunmayan bir biçimde teslim edilirse teslim edilmemiş
sayılır.
