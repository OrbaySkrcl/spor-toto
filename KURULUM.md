# Kurulum ve Kullanım Rehberi

Bu rehber kod bilmeyenler için yazıldı. Terminal kullanmanıza gerek yok —
her şey tarayıcıdan ve Telegram'dan yapılıyor.

**Toplam süre: ~10 dakika.**

---

## Bölüm 1 — Şu anki hatanın çözümü

Railway'de servisiniz **"Crashed"** görünüyor ve loglarda şu satır tekrarlanıyor:

```
Bot jetonu yok. TELEGRAM_BOT_TOKEN ortam değişkenini ayarlayın
```

Sebebi basit: **hiçbir değişken kaydedilmemiş.** Railway'deki o listede
gördüğünüz değişkenler henüz *öneri* — kodunuzda geçtikleri için Railway
bunları bulup göstermiş, ama siz **Add** butonuna basmadığınız için hiçbiri
kaydedilmemiş durumda.

Sadece **bir tanesini** eklemeniz yeterli: `TELEGRAM_BOT_TOKEN`.
Diğerlerinin hepsinin makul bir varsayılanı var.

> Not: Bu güncellemeyle birlikte bot artık jeton yokken çökmüyor; logda tek bir
> açık yönerge basıp bekliyor. Yani aşağıdaki adımları rahatça yapabilirsiniz.

---

## Bölüm 2 — Telegram botunu oluşturun (3 dakika)

1. Telegram'ı açın, arama kutusuna **`@BotFather`** yazın ve ona tıklayın
   (mavi tikli olan).
2. **Start** deyin, sonra şunu yazın:
   ```
   /newbot
   ```
3. Botunuza bir **isim** sorar → istediğinizi yazın, ör. `Spor Toto Tahmin`
4. Bir **kullanıcı adı** sorar → **`bot` ile bitmek zorunda**,
   ör. `benim_sportoto_bot`
5. BotFather size şuna benzeyen uzun bir satır verir:

   ```
   8123456789:AAHk3pQr7XyZ...
   ```

   **Bu sizin jetonunuz.** Uzun basıp kopyalayın.

   ⚠️ Bu jeton şifreniz gibidir — kimseyle paylaşmayın, ekran görüntüsüne
   koymayın. Yanlışlıkla paylaştıysanız BotFather'da `/revoke` ile
   iptal edip yenisini alın.

---

## Bölüm 3 — Railway'e jetonu ekleyin (2 dakika)

1. [railway.app](https://railway.app) → projeniz → **spor-toto** servisine tıklayın.
2. Üstteki **Variables** sekmesine geçin.
3. Sağ üstteki mor **+ New Variable** butonuna basın.
4. İki kutu çıkar:

   | Kutu | Yazacağınız |
   |---|---|
   | İsim (sol) | `TELEGRAM_BOT_TOKEN` |
   | Değer (sağ) | BotFather'ın verdiği uzun jeton |

5. **Add** deyin.

Railway servisi kendiliğinden yeniden başlatır. Bu kadar.

### İsteğe bağlı: kolon fiyatını ayarlayın

Spor Toto'nun **güncel kolon bedelini** biliyorsanız onu da ekleyin —
bütçe hesabı buna bağlıdır:

| İsim | Değer |
|---|---|
| `SPORTOTO_COLUMN_PRICE` | ör. `5` |

Girmezseniz 5 TL varsayılır. Kupon bedelleri buna göre hesaplanır; yanlışsa
sadece TL rakamları yanlış olur, tahminler etkilenmez.

### Kalıcı disk (zaten yaptınız)

`/data` volume'ünü bağlamışsınız — doğru. Bot artık bunu **kendiliğinden
buluyor**, `SPORTOTO_DATA_DIR` eklemenize gerek yok. Bu sayede indirilen
veri her yeniden dağıtımda sıfırdan inmez.

---

## Bölüm 4 — İlk çalıştırma (5 dakika bekleyin)

Railway → **Deployments** sekmesinden logları izleyin. Sırayla göreceksiniz:

```
Bot çalışıyor: @benim_sportoto_bot
Veritabanı boş; ilk veri indirmesi başlıyor
footballdata: 45231 maç çekildi ...
Blend kalibre edildi (...) : ağırlıklar: market=0.52, dc=0.28, ...
Model hazır
```

İlk açılışta **5–10 dakika** sürer: 16 ligin 8 sezonluk arşivini indirip
modeli eğitiyor. Bir daha bu kadar sürmez — veri `/data`'da kalır ve günde
bir kez sessizce güncellenir.

Bu sırada Telegram'da botunuzu açıp `/start` yazabilirsiniz; model hazır
değilse "⏳ Model yükleniyor" der ve hazır olunca cevap verir.

---

## Bölüm 5 — Kullanım

Telegram'da botunuzu açın ve şunları yazın.

### 🔹 Haftanın tahminleri — `/hafta`

En çok kullanacağınız komut. Önümüzdeki günlerde oynanacak tüm maçları
bulur ve her biri için olasılık verir:

```
▸ 5 Eylül Cumartesi
  Maç                                    1     0     2         Tahmin
  ──────────────────────────────────────────────────────────────────
  Barcelona - Girona                   81%   12%    7%   1 · %81 çok güçlü
  Real Madrid - Getafe                 71%   19%   10%   1 · %71 güçlü
  Ath Madrid - Sevilla                 68%   19%   13%   1 · %68 güçlü

════════════════════════════════════════════════════════════════════
Hepsini favoriye oynarsanız beklenen doğru sayısı: 11.9 / 18 (%66)
```

Okuma kılavuzu:

| Ne görüyorsunuz | Ne demek |
|---|---|
| `1 / 0 / 2` sütunları | Ev galibiyeti / beraberlik / deplasman galibiyeti olasılığı |
| `1 · %81 çok güçlü` | En olası sonuç `1`, olasılığı %81 |
| `belirsiz` | Model bu maçta kararsız — sistemde ikili/üçlü yapmaya aday |
| Beklenen doğru sayısı | Hepsini favoriye oynarsanız ortalama kaçını tutturursunuz |

`/hafta 14` yazarsanız 14 gün ileriye bakar.

### 🔹 Otomatik kupon — `/otomatik`

Yaklaşan maçlar arasından **en tahmin edilebilir 15 tanesini** seçer ve
bütçenize göre sistem kuponunu kurar:

```
/butce 1500
/otomatik
```

⚠️ Bu **resmî Spor Toto listesi değildir.** Resmî 15 maçlık liste ücretsiz
bir veri kaynağında yayınlanmıyor. Resmî listeyi oynayacaksanız aşağıdaki
yöntemi kullanın.

### 🔹 Kendi kuponunuz (resmî liste)

Bayiden veya Spor Toto sitesinden 15 maçlık listeyi alın, **alt alta**
botun mesaj kutusuna yapıştırın:

```
1. Galatasaray - Fenerbahçe
2. Beşiktaş - Trabzonspor
3. Konyaspor - Alanyaspor
...
15. Kasımpaşa - Göztepe
```

Takım adlarını nasıl yazdığınızın önemi yok — bot `Fatih Karagümrük`,
`Karagumruk`, `RAMS Başakşehir`, `Manchester United`, `Man Utd` gibi
varyantları tanır. Numaralar, tarihler ve saatler otomatik ayıklanır.

Bot iki mesaj döner: olasılık listesi ve **ne işaretleyeceğinizi söyleyen**
kupon talimatı:

```
🎫 KUPONUNUZ
Kupon üzerinde aşağıdaki işaretleri yapın.

1. Arsenal - Chelsea
   ➜ işaretle: 1 — tutma %67

2. Everton - Manchester United
   ➜ işaretle: 1-2  (çift) — tutma %72
...
━━━━━━━━━━━━━━━━━━━━
📋 8 tek · 5 çift · 2 üçlü
💰 288 kolon = 1.440,00 TL

🎯 TUTMA ŞANSINIZ
   15/15        %0,14
   13 ve üzeri  %6,60
   12 ve üzeri  %19,14
```

**"tutma %"** = o maçta işaretlediklerinizden birinin çıkma olasılığı.
Çift/üçlü işaretlerde bu oran yükselir ama kolon sayısı da artar.

### 🔹 Bütçe ayarlama

```
/butce 2000        ← 2000 TL'ye kadar
/kolon 576         ← ya da doğrudan kolon sınırı
```

Bütçe hafızada kalır; her kupon için tekrar yazmanıza gerek yok.

### 🔹 Bütçe / şans eğrisi — `/egri`

Bir kupon gönderdikten sonra `/egri` yazın. "Şu kadar para koysam şansım ne
olur" sorusunun tam cevabı:

```
   Kolon           Bedel    P(15/15)     P(≥14)     P(≥13)
       1         5.00 TL     0.0049%     0.077%     0.565%
     486     2.430.00 TL     0.6082%     4.798%    17.556%
   3.888    19.440.00 TL     2.1469%    12.498%    34.498%
```

Bu tablo dürüst olsun diye var. Bütçeyi 100 katına çıkarmak şansı 100 kat
değil, yaklaşık 10–15 kat artırır.

### 🔹 Hedef seçimi — `/hedef 13`

Spor Toto **12'den itibaren** ödeme yapar. Varsayılan olarak sistem 15/15'i
maksimize eder, ama makul bütçelerde bu gerçekçi değildir:

```
/hedef 13
```

Bundan sonra kupon, "hepsini tutturma" yerine "en az 13 tutturma" şansını
maksimize edecek şekilde dağıtılır. Fark küçüktür ama bedavadır.
`/hedef 15` ile eski davranışa dönersiniz.

### 🔹 Gerçek karne — `/gecmis`

Sistem yaptığı her tahmini kaydeder. Maçlar oynanıp veri güncellendikçe
kendi karnesini çıkarır:

```
📈 GERÇEK KARNE
Sonuçlanan tahmin: 240
Favori tutturma: %53,3

Güven bandına göre:
   güçlü          82 maç · iddia %68 · gerçek %70
   orta           95 maç · iddia %52 · gerçek %51
   belirsiz/zayıf 63 maç · iddia %39 · gerçek %37
```

`/basari` geçmiş veriyle yapılan simülasyondur; `/gecmis` ise **size verdiği
gerçek tahminlerin** karnesidir. "Güçlü" derken gerçekten haklı mı, burada
görürsünüz. İlk haftalarda boş olur, doldukça anlamlanır.

### 🔹 Modelin başarısı — `/basari`

```
Favori tutturma: %52.7
Referans (hep aynı sonucu oynamak): %43.2
```

Bu rakam **ölçülmüş**tür: model, hiç görmediği maçlar üzerinde test edilir.
"Favori tutturma %52,7" demek, tek maç bazında en olası sonucun 100 maçın
~53'ünde tuttuğu anlamına gelir. 15 maçın 15'ini bilme oranı **değildir**.

### 🔹 Haftalık otomatik gönderim — `/abone`

Her Perşembe öğlen tahminler kendiliğinden gelsin isterseniz `/abone` yazın.
İptal: `/abonelikiptal`

Günü/saati değiştirmek isterseniz Railway'de değişken ekleyin:
`SPORTOTO_WEEKLY_DAY` (0=Pazartesi … 6=Pazar) ve `SPORTOTO_WEEKLY_HOUR`
(UTC saati; Türkiye saati için 3 çıkarın — 12:00 TR = `9`).

### 🔹 Sakatlık bildirme — `/eksik`

Model sakatlık verisini otomatik alamıyor (ücretsiz güvenilir kaynağı yok).
Ama siz biliyorsanız söyleyebilirsiniz:

```
/eksik Galatasaray -0.25 golcü sakat
```

Sayı, takımın gol beklentisini ne kadar düşüreceğinizdir:

| Durum | Yazacağınız sayı |
|---|---|
| Önemli bir oyuncu yok | `-0.15` |
| Golcü / yıldız oyuncu yok | `-0.25` |
| Birkaç kilit oyuncu birden yok | `-0.40` |
| Takım güçlendi (yıldız döndü) | `+0.15` |

14 gün geçerli olur. Listelemek için: `/eksik liste`

### 🔹 Diğer komutlar

| Komut | Ne yapar |
|---|---|
| `/durum` | Kaç maç var, veri ne kadar güncel |
| `/hedef 13` | Kaç doğruyu hedefleyelim |
| `/gecmis` | Gerçek karne |
| `/tahmin Galatasaray - Fenerbahçe` | Tek maç tahmini |
| `/tablo` | Kolon adedi ve kupon bedeli tabloları |
| `/guncelle` | Veriyi hemen tazele ve modeli yeniden eğit |
| `/yardim` | Komut listesi |

---

## Bölüm 6 — Sorun giderme

### "Crashed" yazıyor, loglar `TELEGRAM_BOT_TOKEN` diyor
Jeton eklenmemiş. Bölüm 3'e dönün. Yeni sürümde bot çökmez, bekler —
eski dağıtım çalışıyorsa Railway'de **Redeploy** deyin.

### Bot cevap vermiyor
- Railway → Deployments → loglara bakın. `Bot çalışıyor: @...` satırı var mı?
- İlk açılışta model eğitimi 5–10 dakika sürer, bu sırada sessiz kalabilir.
- Jetonu yanlış kopyalamış olabilirsiniz (başında/sonunda boşluk olmasın).

### `/hafta` "Yaklaşan maç bulunamadı" diyor
Veri kaynağı fikstürleri genelde maçtan birkaç gün önce yayınlar. Ayrıca
sezon aralarında (Haziran–Temmuz) hiç maç olmayabilir. `/guncelle` deneyin.

### "⚠️ sınırlı veri" veya "takım tanınmadı" uyarısı

Bot tanımadığı bir takımı **rastgele başka bir takıma bağlamaz** — o maçı
açıkça işaretler:

| Uyarı | Anlamı | Ne yapmalı |
|---|---|---|
| `takım tanınmadı` | Bu takım veritabanında yok | Adı kaynaktaki hâline yakın yazmayı deneyin |
| `⚠️ sınırlı veri` | Tahmin var ama dayanağı zayıf | O maça sistemde çift/üçlü vermeyi düşünün |
| `veri yok` | Hiçbir model uygulanamadı | Tahmin üretilmedi, sayıları dikkate almayın |

Optimizasyon bu maçları zaten daha riskli sayar ve önce onlara çift/üçlü verir.

Kapsam: Süper Lig, TFF 1. Lig (kaynakta varsa) ve büyük Avrupa ligleri.
Alt ligler ve amatör kategoriler ücretsiz kaynakta yoktur.

### Railway'de ücret uyarısı
Bot 7/24 çalışır ve Railway'in ücretsiz kredisini zamanla tüketir.
Sürekli çalışmasına gerek yoksa kullanmadığınızda servisi durdurabilir,
kupon zamanı tekrar başlatabilirsiniz — veri `/data`'da durur, kaybolmaz.

---

## Bölüm 7 — Gerçekçi beklenti

Bu sistem size şunu **vaat eder**:

- ✅ Her hafta, oynanacak maçlar için kalibre edilmiş 1/0/2 olasılıkları
- ✅ Ölçülmüş ve dürüstçe raporlanan isabet oranı (~%52–53, referans %43)
- ✅ Verilen bütçe için **matematiksel olarak en iyi** sistem dağılımı
- ✅ Her bütçenin gerçekte ne kadar şans getirdiğinin açık tablosu

Şunu **vaat etmez**:

- ❌ 15/15 garantisi. Optimize edilmiş 2.430 TL'lik bir kuponda bile
  15/15 olasılığı tipik bir haftada yaklaşık **%0,6**'dır.
- ❌ Kâr. Sistem ikramiye havuzunu bilmediği için beklenen getiri
  hesaplayamaz; olasılık maksimize eder, kâr değil.

Makul bütçelerde gerçekçi hedef 15/15 değil, **12–13 tutturmaktır** — kupon
çıktısındaki "Beklenen isabet dağılımı" bunu her seferinde gösterir.

Kaybetmeyi göze alamayacağınız parayla oynamayın.
