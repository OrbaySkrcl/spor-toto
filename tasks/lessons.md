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

## 13. İndirilen veriyi kullanmayı unutma

`ingest` yaklaşan maçların bahis oranlarını indiriyordu; blend'de piyasanın
ağırlığı 0,83'tü. Ama kullanıcı kupon listesini yapıştırdığında oran hiç
aranmıyordu — en güçlü sinyal, elde olmasına rağmen çöpe gidiyordu.
Aynı şekilde `predictions` tablosu şemada vardı ama hiçbir yerden yazılmıyordu.

**Ders:** "Veriyi topladım" ile "veriyi kullandım" arasında sessiz bir boşluk
oluşabilir. Her veri kaynağı için "bu, hangi kod yolunda okunuyor?" sorusunu
sor; cevabı yoksa ya bağla ya da toplamayı bırak. Şemaya tablo eklemek özellik
değildir.

## 14. Doğru amacı optimize ettiğinden emin ol

Sistem P(15/15)'i maksimize ediyordu, oysa Spor Toto 12'den itibaren ödüyor.
Kullanıcının gerçekten ulaşabileceği sonuç 12-13 iken, optimizasyon ulaşılamaz
bir eşiğe göre dağıtım yapıyordu.

**Ders:** Amaç fonksiyonu bir varsayımdır ve gözden geçirilmelidir. "Matematik
doğru" ile "doğru matematiği yapıyoruz" aynı şey değil. Ölçtüğümde fark küçük
çıktı (%0,5 puan) — bunu da dürüstçe söylemek, özelliği abartmaktan iyidir.

## 15. Yerel arama her başlangıç noktasına uygulanmalı

P(≥k) araması, tam DP'den gelen çözümü aday olarak alıyor ama ona yerel arama
uygulamıyordu. 240 senaryonun 2'sinde optimumu kaçırma sebebi buydu: doğru
cevap, DP çözümünün tek bir takas komşusundaydı.

**Ders:** Bir sezgisel aramada "elimdeki iyi çözüm" de diğerleri gibi bir
başlangıç noktasıdır. Onu iyileştirme adımından muaf tutma.

## 16. Ölçtüğün iyileştirmenin işe yaramadığını söylemek de sonuçtur

Beraberlik kalibrasyonu yazıldı, test edildi, çalıştı — ve gerçek veride
"kazanç yok" deyip kendini kapattı. Model zaten kalibreymiş (beraberlik %24,8
vs gerçek %25,2). Oran hareketi düzeltmesi de aynı korumaya sahip.

Cazip olan, bir özellik yazdıktan sonra onun faydalı olduğunu varsaymaktı.
Doğru olan, faydayı **tutulan veride** ölçüp yoksa uygulamamak.

**Ders:** Her istatistiksel düzeltmeye bir "kazanç kapısı" koy ve kapıyı fit
edilen veride değil tutulan veride aç. Serbest parametre sayısı arttıkça
gürültüden kazanç uydurmak kolaylaşır. Özelliğin devreye girmemesi başarısızlık
değil; yanlışlıkla devreye girmesi başarısızlıktır.

## 17. Eksik veriyi kapatamıyorsan görünür kıl

TFF 1. Lig için ücretsiz kaynak aradım; football-data.co.uk'un kendi belgesi
Türkiye için yalnızca Süper Lig yayınladığını söylüyor, GitHub aynalarında da
yok. Boşluk kapanmıyor.

Yapılabilecek en kötü şey, kapanmayan boşluğu sessizce taşımaktı: kullanıcı
"neden bu maçlar hep zayıf" diye sorup cevap bulamazdı.

**Ders:** Kapatamadığın eksiği ürünün görünür bir parçası yap. `/kapsam`
komutu, ingest'te eksik lig takibi ve maç bazında "sınırlı veri" işareti,
boşluğu bir sürprizden bilinen bir kısıta çevirdi.

## 18. Arayüz testi ağ gerektirmez

Telegram botunun menü akışı (kalıcı tuş takımı, inline butonlar, callback
işleme) 20 satırlık sahte bir API nesnesiyle uçtan uca test edildi. Gerçek bir
jeton veya ağ erişimi olmadan buton tıklamaları, durum değişiklikleri ve
mesaj biçimleri doğrulandı.

İlk testte gerçek bir hata çıktı: `/ayarlar` tuş takımında vardı ama Telegram
komut menüsüne eklenmemişti.

**Ders:** Dış servisle konuşan katmanı test edilemez sayma. Çağrıları kaydeden
küçük bir taklit nesne, entegrasyonun kendi mantığını tamamen doğrular.
