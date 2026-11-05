<div align="center">

<img src="icon.png" width="96" alt="Ambar simgesi">

# Ambar

**Android Veri Alma Aracı**

Android telefonlardan ADB ile tek tıkla veri alın: telefonun içindeki her şey düzenli, doğrulanabilir ve dokunulmamış halde ambarınızda.

Dahili depolama · WhatsApp (çoklu hesap, Oxygen uyumlu) · Xiaomi Notlar · APK'lar · SHA-256

![Sürüm](https://img.shields.io/badge/sürüm-1.0.0-f59e0b)
![Platform](https://img.shields.io/badge/platform-Windows-3b82f6)
![Python](https://img.shields.io/badge/python-3.10%2B-3776ab)

[yunuskaya.com.tr](https://yunuskaya.com.tr)

<br>

### [⬇ Ambar.exe'yi indir](https://github.com/yunuskayax/ambar/releases/latest/download/Ambar.exe)

Windows 10 / 11 · Kurulum gerekmez · Tek dosya

</div>

---

<p align="center">
  <img src="docs/ekran_goruntusu.png" width="760" alt="Ana ekran">
</p>

## Özellikler

| İşlem | Açıklama |
|---|---|
| **Komple Yedek** | Cihaz bilgileri, `adb backup`, tüm dahili depolama (`/sdcard`), yüklü uygulamaların APK'ları ve tüm dosyaların SHA-256 özet listesi |
| **WhatsApp Yedeği** | `com.whatsapp` klasörünü otomatik bulur ve dosya tarihlerini koruyarak çeker. Çoklu hesapta her hesap için ayrı, **Oxygen Forensic** ile açılabilen ZIP üretir |
| **Güncel Notlar** | Telefonda MIUI yedekleme ekranını açar, yeni not yedeğini otomatik algılar ve notları `notlar.txt` / `notlar.json` olarak kaydeder |
| **Mevcut Not Yedeği** | Telefondaki en son MIUI not yedeğini (`Notlar(com.miui.notes).bak`) çeker ve çözer |
| **adb backup** | Klasik `.ab` yedeği; tam cihaz veya tek paket |
| **Oxygen ZIP Üret** | Daha önce çekilmiş bir WhatsApp klasöründen hesap başına ZIP oluşturur |
| **.ab → .tar** | Şifresiz `.ab` dosyasını açılabilir `.tar` arşivine çevirir |

Ek olarak:

- **Hatalarda durmayan kopyalama:** `adb pull` normalde okunamayan ilk dosyada tamamen durur. Bu araç sorunlu klasöre inip okunabilen her dosyayı alır, alınamayanları `cekilemeyenler.txt` dosyasına yazar.
- **Canlı ilerleme:** Kopyalanan boyut ve yüzde ekranda görünür.
- **Cihaz durumu:** Bağlı, izin bekliyor veya bağlı değil durumları renkle gösterilir.
- **Dokunulmayan orijinal kopya:** Çekilen klasörler hiç değiştirilmez. Oxygen için gereken düzenleme yalnızca ZIP'in içinde yapılır.

## Kurulum

### Hazır program (önerilen)

1. **[Ambar.exe'yi indirin](https://github.com/yunuskayax/ambar/releases/latest/download/Ambar.exe).** Eski sürümler [Releases](../../releases) sayfasındadır.
2. İndirdiğiniz dosyayı bir klasöre (örneğin `Belgeler\Ambar`) taşıyın ve çift tıklayın.

Python, Android SDK veya adb kurmanız gerekmez; hepsi programın içindedir. Yedekler exe'nin bulunduğu klasördeki `yedekler\` klasörüne kaydedilir. Kayıt yerini arayüzden değiştirebilirsiniz.

> **"Windows bilgisayarınızı korudu" uyarısı:** Program dijital olarak imzalanmadığı için Windows SmartScreen ilk açılışta bu uyarıyı gösterebilir. **Ek bilgi → Yine de çalıştır** seçeneğine tıklayın. Dosyanın değiştirilmediğini, Releases sayfasındaki `Ambar.exe.sha256` özetiyle karşılaştırarak doğrulayabilirsiniz:
>
> ```powershell
> Get-FileHash .\Ambar.exe -Algorithm SHA256
> ```

### Kaynak koddan

```bash
git clone https://github.com/yunuskayax/ambar.git
cd ambar
```

[Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) paketini indirin ve `platform-tools` klasörünü proje klasörüne koyun ya da adb'yi PATH'e ekleyin.

```bash
python ambar_gui.py      # grafik arayüz
python ambar.py          # komut satırı menüsü
```

Ek Python paketi gerekmez; yalnızca standart kütüphane (tkinter dahil) kullanılır.

## Telefonun hazırlanması

1. **Ayarlar → Telefon hakkında** bölümünde **Yapım numarası**'na (Xiaomi'de **MIUI / HyperOS sürümü**) 7 kez dokunun. Geliştirici seçenekleri açılır.
2. **Ek ayarlar → Geliştirici seçenekleri** bölümünde **USB hata ayıklama**'yı açın.
3. Telefonu USB kablosuyla bağlayın, USB modunu **Dosya aktarımı** yapın.
4. Telefonda çıkan **"USB hata ayıklamaya izin verilsin mi?"** penceresini onaylayın.

Programın sağ üst köşesindeki nokta yeşile döndüğünde telefon hazırdır.

## Komut satırı kullanımı

```bash
python ambar.py komple                 # adb backup + tüm /sdcard + APK'lar + SHA-256
python ambar.py whatsapp               # com.whatsapp klasörünü bul, çek, Oxygen ZIP üret
python ambar.py whatsapp --kaynak /sdcard/Yedek/com.whatsapp
python ambar.py notlar --yeni          # telefonda güncel not yedeği aldır, sonra çek
python ambar.py notlar                 # mevcut son not yedeğini çek
python ambar.py notlar --dosya "Notlar(com.miui.notes).bak"
python ambar.py backup [--paket com.whatsapp]
python ambar.py oxygen yedekler/<klasör>
python ambar.py ab2tar yedek.ab
python ambar.py -o D:/Yedekler komple  # farklı kayıt klasörü
```

`komple` komutu için `--ab-yok`, `--sdcard-yok`, `--apk-yok` ve `--ozet-yok` seçenekleriyle adım atlanabilir.

## Çıktı yapısı

```text
yedekler/
├── <model>_komple_<tarih>/
│   ├── cihaz_bilgisi/            getprop, paket listesi, hesaplar, depolama
│   ├── sdcard/                   dahili depolamanın kopyası
│   ├── apk/<paket>/*.apk
│   ├── <model>_tam_<tarih>.ab
│   ├── cekilemeyenler.txt        izin nedeniyle alınamayan dosyalar
│   └── SHA256_tum_dosyalar.txt
├── <model>_whatsapp_<tarih>/
│   ├── sdcard_Android_media_com.whatsapp/   orijinal kopya
│   ├── <...>_hesap_1001.zip                 Oxygen'e verilecek dosya
│   └── SHA256.txt
└── notlar_<tarih>/
    ├── notlar.txt
    ├── notlar.json
    └── Notlar(com.miui.notes).bak
```

## WhatsApp çoklu hesap ve Oxygen Forensic

WhatsApp'ta birden fazla hesap varsa ek hesabın verileri `WhatsApp/accounts/<id>/` altına yazılır. Oxygen Forensic veritabanını (`msgstore.db.crypt15`) bu konumda bulamaz. Program her hesap için ayrı bir ZIP üretir ve hesabın `Backups`, `Databases` ve `Media` klasörlerini ZIP içinde `WhatsApp/` köküne yerleştirir. Hesapların verileri birbirine karışmaz.

## Bilinen sınırlamalar

- **`adb backup`** Android 12 ve sonrasında çoğu uygulamanın verisini içermez. WhatsApp ise bu yöntemle yedeklenmeye hiçbir sürümde izin vermez. WhatsApp için klasör yedeğini kullanın.
- **`Android/data` ve `Android/obb`** Android 11 ve sonrasında korunur; root olmadan içerikleri alınamaz.
- **WhatsApp mesaj yedekleri** (`.crypt14` / `.crypt15`) şifrelidir. Çözmek için anahtar dosyası veya uçtan uca yedek parolası gerekir; bu araç dosyaları yalnızca kopyalar.
- **Not yedekleri** MIUI / HyperOS yerel yedekleme biçimine göre çözülür. Başka marka not uygulamaları desteklenmez.
- Şifreli `.ab` ve MIUI yedekleri çözülmez; yedeği alırken şifre koymayın.

## Exe derleme

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Betik, adb yoksa Google'dan indirir, PyInstaller'ı kurar, sürüm numarasını `ambar.py` içindeki `SURUM` değerinden alır ve `dist\Ambar.exe` ile `dist\Ambar.exe.sha256` dosyalarını üretir. Gömülü adb, programın ilk açılışında `%LOCALAPPDATA%\Ambar\platform-tools` klasörüne kopyalanır.

### Yeni sürüm yayınlama

Sürümler GitHub Actions ile otomatik derlenir ve yayınlanır:

1. `ambar.py` içindeki `SURUM` değerini artırın (örneğin `1.1.0`).
2. Değişikliği gönderin ve aynı numarayla bir etiket oluşturun:

   ```bash
   git tag v1.1.0
   git push origin v1.1.0
   ```

3. [`.github/workflows/release.yml`](.github/workflows/release.yml) exe'yi Windows sunucusunda derler ve Releases sayfasına yükler. README'deki indirme bağlantısı her zaman en son sürümü gösterir.

Simge `tools/make_icon.ps1` ile üretilir.

## Proje yapısı

| Dosya | Görev |
|---|---|
| `ambar.py` | Tüm veri alma işlemleri ve komut satırı arayüzü |
| `ambar_gui.py` | tkinter grafik arayüz |
| `build.ps1` | Tek komutla exe derleme |
| `.github/workflows/release.yml` | Etiket gönderildiğinde otomatik derleme ve yayın |
| `icon.ico`, `icon.png`, `tools/make_icon.ps1` | Uygulama simgesi ve üreteci |
| `docs/` | README görselleri |

## Yasal uyarı

Bu aracı yalnızca **sahibi olduğunuz** veya **incelemeye yasal olarak yetkili olduğunuz** cihazlarda kullanın. Yedekler kişisel ve hassas veriler içerir; güvenli bir yerde saklayın ve yetkisiz kişilerle paylaşmayın.

## Yapımcı

**Yunus Kaya** · [yunuskaya.com.tr](https://yunuskaya.com.tr) · [GitHub](https://github.com/yunuskayax/ambar)

Hata bildirimi ve öneriler için [Issues](https://github.com/yunuskayax/ambar/issues) sayfasını kullanabilirsiniz.

<p align="center">
  <img src="docs/hakkinda.png" width="360" alt="Hakkında penceresi">
</p>

© 2026 Yunus Kaya. Tüm hakları saklıdır.
