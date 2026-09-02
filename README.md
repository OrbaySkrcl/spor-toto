# Spor Toto — 1/0/2 Olasılık Modeli ve Sistem Kuponu Optimizasyonu

15 maçın her biri için kalibre edilmiş `P(1) / P(0) / P(2)` olasılıkları üretir ve
verilen bütçe altında **P(15/15)'i matematiksel olarak maksimize eden** sistem
dağılımını (hangi maça tekli, hangisine ikili, hangisine üçlü) hesaplar.

Telegram botu ve Railway dağıtımı dahil, **tamamen ücretsiz** kaynaklarla çalışır:
API anahtarı gerekmez.

> 👉 **Kod bilmiyorsanız [KURULUM.md](KURULUM.md) dosyasını okuyun** — adım adım,
> terminal kullanmadan kurulum ve kullanım rehberi.

```
15 maçlık liste ──► olasılık modeli ──► P(1),P(0),P(2) ──► bütçe kısıtlı
                    (DC + Elo + oran)                      optimizasyon ──► kupon
```

---

## Önce gerçekler

Bu bir kazanç aracı değil, bir **olasılık ve bütçe optimizasyonu** aracıdır.
Aşağıdaki tablo, modelin gerçek bir haftada (5 Avrupa ligi, Mayıs 2026) ürettiği
olasılıklarla hesaplanmış **gerçek** rakamlardır — örnek değil, çıktı:

| Kolon | Bedel (kolon 5 TL) | P(15/15) | P(≥14) | P(≥13) |
|------:|-------------------:|---------:|-------:|-------:|
| 1 | 5 TL | 0,0049 % | 0,08 % | 0,57 % |
| 24 | 120 TL | 0,067 % | 0,74 % | 3,8 % |
| 486 | 2.430 TL | 0,61 % | 4,8 % | 17,6 % |
| 2.916 | 14.580 TL | 1,9 % | 11,8 % | 33,9 % |
| 19.683 | 98.415 TL | 6,5 % | 29,4 % | 62,6 % |
| 177.147 | 885.735 TL | 20,8 % | 61,6 % | 90,4 % |

Üç şey buradan okunur:

1. **15/15 nadir bir olaydır.** En iyi model bile bunu değiştirmez; sadece
   olasılığı 2–3 kat iyileştirir. Bir haftada 15 maçın 15'ini bilmek, her maçta
   ortalama ~%53 isabetle, tek kolonda ~20.000'de 1'dir.
2. **Kolon maliyeti doğrusal, kazanma şansı değil.** Bütçeyi 100 katına
   çıkarmak şansı ~100 kat değil ~10–15 kat artırır (tablodaki "TL/kat" sütunu
   bunu ölçer). Azalan verim gerçektir.
3. **Asıl kazanç 13/14'te.** Spor Toto 12'den itibaren ödeme yapar; makul
   bütçelerde beklenebilecek sonuç 15/15 değil, ≥13'tür. Sistem bu dağılımı da
   her kupon için raporlar.

Bu araç bahis tavsiyesi vermez ve beklenen getiriyi (EV) hesaplamaz — bunun için
ödül havuzu/ikramiye verisi gerekir ve ücretsiz kaynaklarda yoktur. Yaptığı şey,
**verdiğiniz bütçeyi olabilecek en verimli şekilde dağıtmaktır.**

---

## Hızlı başlangıç

```bash
git clone <bu-depo> && cd spor-toto
pip install -r requirements.txt

python -m sportoto ingest                 # veriyi indir (ilk sefer birkaç dakika)
python -m sportoto train                  # modeli eğit ve kalibre et

cat > kupon.txt <<'EOF'
1. Galatasaray - Fenerbahçe
2. Beşiktaş - Trabzonspor
...  (15 satır)
EOF

python -m sportoto coupon kupon.txt --budget 2000 --frontier
```

Örnek çıktı:

```
OPTİMİZE SİSTEM KUPONU
────────────────────────────────────────────────────────────────────────────
 # Maç                                 İşaret   Kapsama     Risk
────────────────────────────────────────────────────────────────────────────
 1 Manchester City - Luton                  1     70.8%   29.2%
 2 Arsenal - Everton                    1-0-2    100.0%    0.0% ▪▪
 3 Liverpool - Brighton                     1     80.7%   19.3%
...
────────────────────────────────────────────────────────────────────────────
Dağılım : 9 tekli · 2 ikili · 4 üçlü
Kolon   : 324   Bedel: 1.620,00 TL
P(15/15) : 8.1485%

Beklenen isabet dağılımı:
   15/15:  8.149%   (en az 15: 8.149%)
   14/15: 23.412%   (en az 14: 31.560%)
   13/15: 29.969%   (en az 13: 61.529%)
```

### Komutlar

| Komut | Ne yapar |
|---|---|
| `ingest` | Veri kaynağından maçları indirir (tekrar çalıştırmak güvenli) |
| `train` | Modeli eğitir, blend ağırlıklarını kalibre eder, `data/blend.json`'a yazar |
| `hafta` | Yaklaşan maçlar için haftalık tahminler (`--coupon` ile otomatik kupon) |
| `basari` | Modelin ölçülmüş örnek dışı isabet oranı |
| `predict dosya.txt` | Maç listesi için 1/0/2 olasılıkları |
| `coupon dosya.txt --budget 2000` | Kuponu optimize eder |
| `backtest --start 2024-08-01` | Sızıntısız geçmişe dönük değerlendirme |
| `tables` | Kolon adedi ve kupon bedeli tabloları |
| `leagues` | Desteklenen ligler |
| `stats` | Veritabanı durumu |
| `adjust` | Sakatlık/ceza için manuel takım düzeltmesi |
| `bot` | Telegram botunu çalıştırır |

Faydalı bayraklar: `--components` (hangi bileşen ne diyor), `--frontier`
(bütçe/şans eğrisi), `--columns 576` (bütçe yerine kolon sınırı),
`--max-triples 4`, `--source mirror`.

### Sakatlık ve ceza bilgisini elle beslemek

Sakatlık verisinin ücretsiz ve güvenilir bir kaynağı yok, ama bilgi sizde
olabilir. Takım gücüne doğrudan düzeltme girebilirsiniz:

```bash
python -m sportoto adjust "Galatasaray" --attack -0.25 --days 14 --note "golcü sakat"
python -m sportoto adjust --list
```

Düzeltme **logaritmik ölçektedir**: `-0.25` gol beklentisini `exp(-0.25) ≈ %78`'e
düşürür. Kaba bir başlangıç: takımın gol üretiminin %X'ini oluşturan oyuncu
yoksa `attack ≈ ln(1 - X)` (ör. %25 → `-0.29`). `--defense` negatif verilirse
savunma zayıflar. Düzeltme yalnızca geçerlilik aralığındaki maçlara uygulanır ve
Dixon-Coles gol beklentilerine girer; modeli yeniden eğitmek gerekmez.

Telegram'da aynısı: `/eksik Galatasaray -0.25 golcü sakat`, listelemek için
`/eksik liste`.

---

## Telegram + Railway ile ücretsiz kurulum

### 1. Telegram botu oluşturun
Telegram'da [@BotFather](https://t.me/BotFather) → `/newbot` → adı ve kullanıcı
adını verin → size bir **jeton** (token) verir.

### 2. Railway'e dağıtın
1. Bu depoyu GitHub'a itin.
2. [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**.
3. **Variables** sekmesinde **tek zorunlu** değişkeni ekleyin:

   | Değişken | Değer |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | BotFather'dan aldığınız jeton |

   İsteğe bağlı: `SPORTOTO_COLUMN_PRICE` (güncel kolon bedeli, varsayılan 5).
   Diğer değişkenlerin hepsinin makul varsayılanı vardır.

4. **Settings → Volumes** → yeni volume, mount path `/data`.
   Bot bu diski **kendiliğinden bulur** (`SPORTOTO_DATA_DIR` gerekmez).
   Bu adımı atlarsanız bot her yeniden dağıtımda veriyi baştan indirir.
5. Deploy. Bot ilk açılışta veriyi indirip modeli eğitir (birkaç dakika),
   sonra günde bir kez kendini otomatik günceller.

`railway.json`, `Procfile` ve `nixpacks.toml` hazır; başlangıç komutu
`python -m sportoto bot`. Bot **uzun yoklama** kullanır, yani alan adı,
TLS sertifikası veya açık port gerekmez — Railway'de en ucuz çalışma biçimi budur.

### 3. Botu kullanın

```
/butce 2000                  ← bütçenizi ayarlayın
1. Galatasaray - Fenerbahçe  ← 15 maçı alt alta yapıştırın
2. Beşiktaş - Trabzonspor
...
```

Bot olasılık tablosunu ve optimize edilmiş kuponu döndürür.

Haftalık akış için resmî listeyi beklemenize gerek yok:

| Komut | Ne yapar |
|---|---|
| `/hafta` | Önümüzdeki maçları bulur, hepsi için 1/0/2 olasılığı ve güven yüzdesi verir |
| `/otomatik` | En tahmin edilebilir 15 maçtan sistem kuponu kurar |
| `/basari` | Modelin ölçülmüş isabet oranını gösterir |
| `/abone` | Haftalık tahminleri otomatik gönderir (varsayılan: Perşembe) |

Diğerleri: `/durum`, `/tahmin A - B`, `/egri`, `/tablo`, `/eksik`, `/guncelle`, `/yardim`.

---

## Veri kaynakları

| Kaynak | Anahtar | Kapsam | Oran | Kullanım |
|---|---|---|:---:|---|
| **football-data.co.uk** | gerekmez | 30+ lig, Türkiye Süper Lig dahil, 1993'ten beri | ✅ | varsayılan |
| GitHub aynası | gerekmez | 5 büyük Avrupa ligi, 1993'ten beri | ❌ | yedek / kapalı ağ |
| Yerel CSV | — | ne koyarsanız | ✅ | elle indirme |
| Sentetik | — | üretilmiş lig | ✅ | test |

Birincil kaynak **bahis oranlarını da** verdiği için önemlidir: piyasa, modelin
en güçlü tek girdisidir (aşağıya bakın). Kaynak `--source` veya
`SPORTOTO_SOURCE` ile seçilir.

> **Not:** Kurumsal ağlar veya bazı bulut ortamları `football-data.co.uk` alan
> adını engelleyebilir. O durumda `--source mirror` (oransız, 5 lig) veya
> `--source local` kullanın. Bu depo geliştirilirken kullanılan ortam da bu alan
> adını engellediği için model doğrulaması ayna verisiyle yapılmıştır.

---

## Nasıl çalışıyor

### Olasılık üretimi — dört bileşenin birleşimi

| Bileşen | Ne modelliyor | Neden var |
|---|---|---|
| **Dixon–Coles** | Takım başına atak/defans, zaman ağırlıklı Poisson gol modeli + düşük skor düzeltmesi | Beraberlik olasılığını doğru verir; Spor Toto'da kritik |
| **Elo (sıralı logit)** | Sonuç dizisinden türeyen güç derecesi | Farklı hata yapısı → bağımsız katkı |
| **Piyasa** | Bahis oranlarından marj temizlenmiş olasılık | Milyonlarca bahisçinin ve profesyonel modellerin bilgisi |
| **Form** | Son 5 maç, iç/deplasman ayrımı, dinlenme, fikstür yükü, zaman ağırlıklı H2H | Modelin göremediği kısa vadeli sinyaller |

Bileşenler **logaritmik görüş havuzunda** birleştirilir (`p ∝ Π p_k^{w_k}`).
Ağırlıklar sabit değil; ayrı bir doğrulama penceresinde log-loss minimize
edilerek öğrenilir. Bir bileşen eksikse (ör. oran yoksa) ağırlıklar mevcutlar
üzerinde yeniden normalize edilir — sistem tek bileşen kaybında durmaz.

İki koruma önemli:
* Kalibrasyonda **neredeyse hiç görünmeyen bir bileşene ağırlık atanmaz.** Aksi
  hâlde optimizasyon o ağırlığı serbest bırakır (maskelendiği için log-loss'u
  değiştirmez) ve üretimde doğrulanmamış bir ağırlıkla devreye girer.
* Sıcaklık parametresi `[0,6 – 1,8]` aralığına sıkıştırılmıştır; serbest
  bırakılırsa dejenere veride olasılıkları 0/1'e itip kupon optimizasyonunu bozar.

### Kupon optimizasyonu — sezgisel değil, **tam çözüm**

Problem şu: `Σ log P(sonuç ∈ S_i)` değerini `Π|S_i| = 2^a·3^b ≤ bütçe` kısıtı
altında maksimize etmek.

İki gözlem bunu küçük bir dinamik programa indirger:

1. `|S_i| = k` sabitken en iyi küme, o maçın **en yüksek k olasılıklı**
   sonucudur.
2. Maliyet yalnızca kaç ikili/üçlü seçtiğimize bağlıdır, hangi maçların ikili
   olduğuna değil.

Böylece durum uzayı `(maç, ikili sayısı, üçlü sayısı)` = 15×16×16 olur ve DP
**küresel optimumu garanti eder**. Yaygın "en belirsiz maçlara ikili ver"
sezgiseli bu optimumu genelde ıskalar. Optimizer, 4–9 maçlık senaryolarda
kaba kuvvetle (tüm 3ⁿ atama) birebir karşılaştırılarak doğrulanmıştır
(`tests/test_optimizer.py`).

Doğru bilinen maç sayısının tam dağılımı (Poisson-binom) da hesaplanır; bu
sayede `P(15/15)` kadar `P(≥13)` de raporlanabilir.

### Haftalık fikstür akışı

`ingest`, sonuçların yanında **oynanmamış maçları da** (güncel oranlarıyla)
indirip veritabanına yazar. `hafta` / `/hafta` bunları okuyup tahmin eder;
maç oynandığında aynı satır sonuçla güncellenir ve eğitim verisine katılır.
Yani sistem elle müdahale olmadan haftadan haftaya kendini besler.

Modelin isabet oranı her eğitimde **örnek dışı** ölçülür: kalibrasyon
penceresi zamana göre 70/30 bölünür, ağırlıklar ilk parçada öğrenilir, başarı
son parçada ölçülür, sonra ağırlıklar tüm pencereyle yeniden kestirilir.
Kullanıcıya gösterilen yüzde bu ölçümden gelir.

### Eksik bileşene dayanıklılık — "profiller"

Ağırlıklar, bahis oranlarının **her zaman mevcut olduğu** geçmiş veride
öğrenilir; piyasa en güçlü sinyal olduğu için diğerlerini neredeyse sıfıra
ezer. Kullanıcı kupon listesini elle yapıştırdığında ise oran yoktur — geriye
ağırlığı sıfıra yakın bileşenler kalır ve havuz "hiçbir bilgim yok" diyerek
düzgün dağılıma (%33/%33/%33) düşer. Model, aslında bildiği maçlarda bile susar.

Çözüm: tek ağırlık vektörü yerine **her bileşen kombinasyonu için ayrı bir
profil** kestirilir. "Yalnızca dc+elo mevcut" profili tam da o iki bileşenle,
gerçek sonuçlar üzerinde fit edilir; tahmin anında her maç kendi profilini
kullanır. Ek güvence olarak ağırlıklara bir taban uygulanır.

Gerçek veride etkisi: oran varken `market=0.83`, oran yokken aynı model
`dc=0.84, elo=0.14` ağırlıklarına geçer.

### Lig grupları

Spor Toto listeleri aynı ülkenin farklı seviyelerini sürekli karıştırır
(Süper Lig + 1. Lig). Takım güçleri yalnızca birbiriyle maç yapmış takımlar
arasında karşılaştırılabilir; bu yüzden gol modeli lig başına değil **ülke
piramidi başına** kestirilir (TR, EN, ES, IT, DE, FR, SC). Küme düşme/çıkma
seviyeleri bağladığı için bu küme tutarlıdır. Lig başına kestirimde
"Süper Lig takımı - 1. Lig takımı" maçlarında gol modeli hiçbir şey söyleyemezdi.

### Bilinmeyen takım politikası

Bulanık arama her zaman bir "en yakın" aday döndürür. %12 benzerlikle bulunan
takımı kabul etmek, modelin tamamen alakasız bir takımın gücüyle güvenli
görünen bir tahmin üretmesi demektir. Bu yüzden 0,45 altındaki eşleşmeler
reddedilir; maç `sınırlı veri` olarak işaretlenir ve kullanıcıya söylenir.
Tanımamak, yanlış tanımaktan iyidir.

### Sızıntı yok

Bir maçın tahmininde kullanılan hiçbir şey o maçtan sonra bilinemez:

* Elo ve form özellikleri tek geçişte, yalnızca önceki maçlardan üretilir.
* Model parametreleri `--refit-days` aralığıyla, yalnızca o ana kadarki veriyle
  yeniden kestirilir.
* Blend ağırlıkları, **değerlendirme penceresinden önceki** ayrı bir dönemde
  kalibre edilir.

Bu iddia test edilmiştir: `tests/test_pipeline.py` kesim tarihinden sonraki tüm
skorları bozar ve kesim öncesi özelliklerin ile tahminlerin **bit düzeyinde
değişmediğini** doğrular.

---

## Backtest sonuçları

5 Avrupa ligi, 3.504 maç (Ağu 2024 – May 2026), 21 günde bir yeniden kestirim,
oran verisi **olmadan** (ayna kaynağı):

```
Model             n   İsabet   LogLoss      RPS    Brier
--------------------------------------------------------
dc            3.430    0.520    0.9885   0.2016   0.5893
elo           3.504    0.526    0.9908   0.2019   0.5909
form          3.504    0.459    1.0491   0.2216   0.6317
--------------------------------------------------------
BLEND         3.504    0.527    0.9881   0.2012   0.5891
```

Referans: düzgün dağılımın log-loss'u `ln 3 = 1,0986`; sabit taban oranı 1,0746.
Kalibrasyon sapması ana kovalarda ±%2'nin altında.

Oranlı veriyle (sentetik lig, piyasa bileşeni aktif) blend piyasanın kendisini
de geçiyor — log-loss 0,9742 vs 0,9764, RPS 0,1995 vs 0,2000. Fark küçüktür ve
küçük olması beklenir: **bahis piyasasını anlamlı biçimde yenmek zordur**;
modelin işi onu yenmek değil, ona kendi bağımsız bilgisini eklemektir.

`RPS` (Ranked Probability Score) burada log-loss'tan daha anlamlı bir metriktir:
1/0/2 sıralı olduğu için "1 derken 2 çıkması" "1 derken 0 çıkmasından" daha
büyük bir hatadır ve RPS bunu ayırt eder.

Kendiniz çalıştırın:

```bash
python -m sportoto backtest --start 2024-08-01 --calibration --out sonuclar.csv
```

---

## İstenenlerin durumu

Projenin hedef listesinde olup **uygulananlar**: takım gücü (rakip kalitesine
göre düzeltilmiş), güncel form ve zaman ağırlığı, iç saha/deplasman ayrımı,
fikstür yoğunluğu ve dinlenme günü, zaman ağırlıklı H2H, bahis piyasası verisi
ve marj temizleme, walk-forward backtest, 15/15–14/15–13/15 ölçümü ve bütçe
optimizasyonu.

**Uygulanmayanlar ve nedenleri** — bunları gizlemek yerine açıkça yazıyorum:

| İstenen | Durum | Neden |
|---|---|---|
| xG / xGA / PPDA / big chances | ❌ | Ücretsiz ve yasal toplu API'si yok. FBref/Understat kazıma ToS ihlali. Şu an yerine **isabetli şut hâkimiyeti** kullanılıyor (zayıf bir vekil). |
| Sakatlık / ceza / muhtemel ilk 11 | ⚠️ manuel | Ücretsiz güvenilir kaynak yok. `sportoto adjust` / `/eksik` ile elle girilir; düzeltme doğrudan Dixon-Coles gol beklentisine uygulanır (yukarıya bakın). |
| Motivasyon (küme düşme, şampiyonluk) | ⚠️ vekil | `motivation_proxy` puan durumundan türetir; modele henüz bağlı değil (backtest'te anlamlı katkı vermedi). |
| Oran hareketi (tick düzeyi) | ⚠️ kısmi | Kaynak açılış + kapanış oranı verir; ikisinin farkı hesaplanabilir ama saniyelik hareket yoktur. |
| Farklı kitapçılar arası konsensüs | ✅ kısmi | Kaynağın piyasa ortalaması (`Avg*`) sütunları kullanılıyor. |

xG veya sakatlık verisine ücretli bir API ile erişirseniz, mimari bunları
almaya hazır: `sportoto/sources/` altına yeni bir kaynak, `features/form.py`
altına yeni sütunlar eklemek yeterli — blend, katkı verip vermediğine
kendiliğinden karar verir.

---

## Yapılandırma

Tüm ayarlar ortam değişkeniyle ezilebilir (`.env.example` dosyasına bakın):

| Değişken | Varsayılan | Açıklama |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Bot jetonu (bot için zorunlu) |
| `SPORTOTO_DATA_DIR` | `data` | Veritabanı ve önbellek klasörü |
| `SPORTOTO_SOURCE` | `footballdata` | `footballdata` / `mirror` / `local` / `synthetic` |
| `SPORTOTO_LEAGUES` | 16 lig | İndirilecek lig kodları |
| `SPORTOTO_SEASONS` | `8` | Kaç sezon geriye |
| `SPORTOTO_COLUMN_PRICE` | `5` | **Kolon birim fiyatı — güncel değeri siz girin** |
| `SPORTOTO_BUDGET` | `500` | Varsayılan kupon bütçesi (TL) |
| `SPORTOTO_AUTO_UPDATE_HOURS` | `24` | Botun otomatik tazeleme aralığı (`0` = kapalı) |
| `SPORTOTO_DC_XI` | `0.0018` | Dixon-Coles zaman ağırlığı (yarı ömür ≈ 385 gün) |
| `SPORTOTO_MARGIN` | `power` | Marj temizleme: `power` / `shin` / `basic` |

`SPORTOTO_COLUMN_PRICE` özellikle önemli: Spor Toto kolon bedelini zaman zaman
günceller ve doğru bütçe hesabı buna bağlıdır. Varsayılan 5 TL bir yer
tutucudur — güncel değeri kontrol edip ayarlayın.

---

## Geliştirme

```bash
pip install -r requirements.txt pytest
python -m pytest tests/ -q          # 195 test
```

Ağ olmadan çalışmak için sentetik kaynak yeterlidir:

```bash
python -m sportoto --source synthetic --leagues SYN ingest
python -m sportoto --source synthetic backtest --start 2021-01-01
```

Proje düzeni:

```
sportoto/
├── config.py            lig tanımları, hiperparametreler
├── storage.py           SQLite şeması ve erişim
├── teams.py             takım adı normalizasyonu + bulanık eşleştirme
├── sources/             veri kaynakları (footballdata, mirror, local, synthetic)
├── features/            elo.py, form.py  (nedensel özellik üretimi)
├── models/              dixon_coles, elo_model, market, form_model, blend
├── pipeline.py          sızıntısız walk-forward tahmin üretimi
├── predictor.py         üst katman API (eğit / tahmin et)
├── coupon/              pricing.py, optimizer.py  (bütçe kısıtlı DP)
├── backtest.py          metrikler + kupon simülasyonu
├── report.py            metin çıktıları
├── cli.py               komut satırı
└── bot/telegram_bot.py  Telegram botu
```

---

## Sorumluluk reddi

Bu yazılım eğitim ve analiz amaçlıdır. Şans oyunlarında kayıp riski gerçektir ve
hiçbir model bunu ortadan kaldırmaz. Yukarıdaki tablolar, iyi kalibre edilmiş bir
modelle bile 15/15 olasılığının düşük kaldığını açıkça göstermektedir.
Kaybetmeyi göze alamayacağınız parayla oynamayın.
