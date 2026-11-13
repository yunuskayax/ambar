#!/usr/bin/env python3
"""
Ambar - Android Veri Alma Aracı (yapımcı: Yunus Kaya, https://yunuskaya.com.tr)
- adb backup ile telefon yedeği (.ab) alır
- Telefondaki com.whatsapp klasörünü bilgisayara çeker (adb pull)
- .ab dosyasını .tar'a çevirir (şifresiz yedekler için)

Kullanım:
    python ambar.py                 -> etkileşimli menü
    python ambar.py backup          -> tam adb yedeği
    python ambar.py backup --paket com.whatsapp
    python ambar.py whatsapp        -> com.whatsapp klasörünü otomatik bul ve çek
    python ambar.py whatsapp --kaynak /sdcard/Yedek/com.whatsapp
    python ambar.py ab2tar yedek.ab
    python ambar.py oxygen yedekler\\<klasör>   -> hesap başına Oxygen uyumlu ZIP üret
    python ambar.py notlar          -> Xiaomi Notlar yedeğini çek, notlar.txt'ye çevir
    python ambar.py notlar --yeni   -> telefonda güncel not yedeği aldır, sonra çek
    python ambar.py komple          -> adb backup + tüm /sdcard + APK'lar + SHA-256
    python ambar.py notlar --dosya "Notlar(com.miui.notes).bak"
"""

import argparse
import datetime
import os
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

import threading
import time

UYGULAMA_ADI = "Ambar"
ALT_BASLIK = "Android Veri Alma Aracı"
SURUM = "2.0.0"
YAPIMCI = "Yunus Kaya"
WEB = "https://yunuskaya.com.tr"
GITHUB = "https://github.com/yunuskayax/ambar"
GITHUB_API_SON_SURUM = "https://api.github.com/repos/yunuskayax/ambar/releases/latest"


def _surum_tuple(s: str):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", s)[:3]) or (0,)


def guncelleme_denetle(zaman_asimi=8):
    """GitHub'daki son sürümü sorgular. (yeni_var_mi, son_surum, sayfa_url) döndürür."""
    import json
    import urllib.request
    istek = urllib.request.Request(GITHUB_API_SON_SURUM, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"Ambar/{SURUM}"})
    with urllib.request.urlopen(istek, timeout=zaman_asimi) as r:
        veri = json.load(r)
    son = veri.get("tag_name", "").lstrip("vV")
    return _surum_tuple(son) > _surum_tuple(SURUM), son, veri.get("html_url", GITHUB + "/releases")

if getattr(sys, "frozen", False):          # PyInstaller ile paketlenmiş .exe
    SCRIPT_DIR = Path(sys.executable).resolve().parent
    PAKET_DIR = Path(getattr(sys, "_MEIPASS", SCRIPT_DIR))
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    PAKET_DIR = SCRIPT_DIR
VARSAYILAN_HEDEF = SCRIPT_DIR / "yedekler"

# Windows'ta alt süreçlerin konsol penceresi açmasını engelle
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# İlerleme bildirimi: GUI bunu kendi fonksiyonuyla değiştirir. f(yuzde 0-100 veya None, metin)
ILERLEME = None


def ilerleme(yuzde, metin=""):
    if ILERLEME:
        ILERLEME(yuzde, metin)

# Telefonda com.whatsapp klasörünün bulunabileceği olası yerler
OLASI_WHATSAPP_YOLLARI = [
    "/sdcard/Android/media/com.whatsapp",
    "/sdcard/com.whatsapp",
    "/sdcard/WhatsApp",
    "/storage/emulated/0/Android/media/com.whatsapp",
]


# ----------------------------------------------------------------- ADB yardımcıları
def adb_bul() -> str:
    """adb.exe'yi PATH'te, betik klasöründe veya bilinen yerlerde arar."""
    exe = "adb.exe" if os.name == "nt" else "adb"
    gomulu = PAKET_DIR / "platform-tools"
    if getattr(sys, "frozen", False) and (gomulu / exe).is_file():
        # .exe içine gömülü adb'yi kalıcı bir yere kopyala (geçici klasör kilitlenmesin)
        kalici = Path(os.environ.get("LOCALAPPDATA", SCRIPT_DIR)) / "Ambar" / "platform-tools"
        try:
            kalici.mkdir(parents=True, exist_ok=True)
            for f in gomulu.iterdir():
                h = kalici / f.name
                if not h.exists() or h.stat().st_size != f.stat().st_size:
                    shutil.copy2(f, h)
            return str(kalici / exe)
        except OSError:
            return str(gomulu / exe)
    yol = shutil.which("adb")
    if yol:
        return yol
    adaylar = [
        PAKET_DIR / "platform-tools" / exe,
        SCRIPT_DIR / exe,
        SCRIPT_DIR / "platform-tools" / exe,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Android" / "Sdk" / "platform-tools" / exe,
        Path(os.environ.get("ANDROID_HOME", "")) / "platform-tools" / exe,
        Path("C:/platform-tools") / exe,
        Path("C:/adb") / exe,
    ]
    for a in adaylar:
        if a.is_file():
            return str(a)
    sys.exit(
        "HATA: adb bulunamadı.\n"
        "Android SDK Platform-Tools'u indirin:\n"
        "  https://developer.android.com/tools/releases/platform-tools\n"
        "ve 'platform-tools' klasörünü bu betiğin yanına koyun ya da PATH'e ekleyin."
    )


ADB = None


def adb(*args, yakala=True, kontrol=False):
    global ADB
    if ADB is None:
        ADB = adb_bul()
    cmd = [ADB, *args]
    if yakala:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    else:
        # Çıktıyı satır satır print'e aktar (GUI'de de görünsün)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        for satir in iter(p.stdout.readline, b""):
            s = satir.decode("utf-8", errors="replace").rstrip()
            if s:
                print(s.split("\r")[-1])
        p.wait()
        r = subprocess.CompletedProcess(cmd, p.returncode)
    if kontrol and r.returncode != 0:
        hata = (r.stderr or r.stdout or "").strip() if yakala else ""
        raise RuntimeError(f"adb {' '.join(args)} başarısız: {hata}")
    return r


def cihaz_kontrol():
    """Bağlı ve yetkili tek cihaz olduğundan emin olur."""
    adb("start-server")
    satirlar = adb("devices").stdout.strip().splitlines()[1:]
    cihazlar = [s.split() for s in satirlar if s.strip()]
    if not cihazlar:
        sys.exit("HATA: Bağlı cihaz yok. USB hata ayıklamanın açık olduğundan ve kablonun takılı olduğundan emin olun.")
    for seri, durum, *_ in cihazlar:
        if durum == "unauthorized":
            sys.exit(f"HATA: {seri} yetkili değil. Telefonda çıkan 'USB hata ayıklamaya izin ver' penceresini onaylayın.")
        if durum != "device":
            sys.exit(f"HATA: {seri} durumu: {durum}")
    if len(cihazlar) > 1:
        print("UYARI: Birden fazla cihaz bağlı; ANDROID_SERIAL ortam değişkeni ile birini seçin.")
        print("  " + ", ".join(c[0] for c in cihazlar))
        if "ANDROID_SERIAL" not in os.environ:
            sys.exit(1)
    model = adb("shell", "getprop", "ro.product.model").stdout.strip()
    surum = adb("shell", "getprop", "ro.build.version.release").stdout.strip()
    print(f"Cihaz: {model} (Android {surum}) - seri: {cihazlar[0][0]}")
    CIHAZ.clear()
    CIHAZ.update({"model": model, "android": surum, "seri": os.environ.get("ANDROID_SERIAL", cihazlar[0][0])})
    return model or "cihaz"


# Son cihaz_kontrol() çağrısında okunan cihaz bilgisi (manifest için)
CIHAZ: dict = {}


def _luhn(n: str) -> bool:
    t = 0
    for i, ch in enumerate(reversed(n)):
        d = int(ch) * (2 if i % 2 else 1)
        t += d - 9 if d > 9 else d
    return t % 10 == 0


def _parcel_metin(cikti: str):
    """'service call' çıktısındaki Parcel'den String16 değeri (hata/boşsa None)."""
    import re
    kelimeler = [w for h in re.findall(r"0x[0-9a-f]+: ((?:[0-9a-f]{8} ?)+)", cikti or "") for w in h.split()]
    b = b"".join(bytes.fromhex(w)[::-1] for w in kelimeler)
    if len(b) < 8 or int.from_bytes(b[:4], "little", signed=True) != 0:
        return None
    n = int.from_bytes(b[4:8], "little", signed=True)
    return b[8:8 + 2 * n].decode("utf-16-le", "replace") if 0 < n < 200 else None


def imei_al(ekran=True) -> dict:
    """Bağlı cihazın IMEI numaraları. 1) IPhoneSubInfo (yalnızca okuma yapan 'get' arayüzü; Android sürümüne
    göre işlem numarası değişir), 2) olmazsa çevirici ekranında *#06# açılıp ekran metni uiautomator ile okunur.
    Yalnızca 15 haneli ve Luhn denetimi geçen değerler IMEI kabul edilir."""
    import re, time
    bulunan, kaynak = [], None

    def ekle(v, k):
        nonlocal kaynak
        for n in re.findall(r"(?<!\d)\d{15}(?!\d)", v or ""):
            if _luhn(n) and n not in bulunan and n != "0" * 15:
                bulunan.append(n)
                kaynak = kaynak or k
    for yuva in (0, 1, 2):              # getDeviceIdForPhone(phoneId, callingPackage[, featureId])
        for kod in (4, 3, 5):
            v = _parcel_metin(adb("shell", f"service call iphonesubinfo {kod} i32 {yuva} s16 com.android.shell s16 null").stdout)
            if v:
                ekle(v, "iphonesubinfo")
                break
    for kod in (1, 2):                  # getDeviceId(callingPackage) - varsayılan yuva
        ekle(_parcel_metin(adb("shell", f"service call iphonesubinfo {kod} s16 com.android.shell").stdout), "iphonesubinfo")
    if not bulunan and ekran:
        try:
            adb("shell", "input keyevent KEYCODE_WAKEUP")
            adb("shell", "am start -a android.intent.action.DIAL")
            time.sleep(1.5)
            adb("shell", "input text '*#06#'")
            time.sleep(2.5)
            adb("shell", "uiautomator dump /sdcard/ambar_ui.xml")
            ekle(adb("shell", "cat /sdcard/ambar_ui.xml").stdout, "*#06# ekranı")
            adb("shell", "rm -f /sdcard/ambar_ui.xml")
            for _ in range(3):
                adb("shell", "input keyevent KEYCODE_BACK")
            adb("shell", "input keyevent KEYCODE_HOME")
        except Exception:
            pass
    return {"imei": bulunan, "kaynak": kaynak}


def cihaz_ozellikleri(ekran=True) -> dict:
    """Cihazın kimlik bilgilerini okur (inceleme programındaki 'Cihaz' paneli için)."""
    p = lambda k: adb("shell", "getprop", k).stdout.strip()
    d = {
        "marka": p("ro.product.manufacturer"),
        "model": p("ro.product.model"),
        "ticari_ad": p("ro.product.marketname") or p("ro.product.vendor.marketname"),
        "cihaz_kodu": p("ro.product.device"),
        "android": p("ro.build.version.release"),
        "sdk": p("ro.build.version.sdk"),
        "guvenlik_yamasi": p("ro.build.version.security_patch"),
        "yapi": p("ro.build.display.id"),
        "parmak_izi": p("ro.build.fingerprint"),
        "saat_dilimi": p("persist.sys.timezone"),
        "seri": CIHAZ.get("seri", ""),
        "android_id": adb("shell", "settings get secure android_id").stdout.strip(),
        "cihaz_adi": adb("shell", "settings get global device_name").stdout.strip(),
        "yonga": " ".join(x for x in (p("ro.soc.manufacturer"), p("ro.soc.model")) if x) or p("ro.board.platform")
        or p("ro.hardware.chipname") or p("ro.hardware"),
    }
    # Veri alırken işlem başına bir kez (gerekirse *#06# ekranı açılır); ekran=False: yalnızca servis yolu
    try:
        if ekran:
            if "imei" not in CIHAZ:
                CIHAZ["imei"] = imei_al()
            im = CIHAZ["imei"]
        else:
            im = imei_al(ekran=False)
    except Exception:
        im = {"imei": [], "kaynak": None}
    if im["imei"]:
        d["imei"] = " / ".join(im["imei"])
        d["imei_kaynak"] = im["kaynak"]
    return {k: v for k, v in d.items() if v and v != "null"}


def simdi() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def manifest_yaz(oturum: Path, tur: str, ek: dict | None = None, baslangic: str | None = None):
    """Yedek klasörüne kimlik dosyası (ambar_bilgi.json) yazar; inceleme programı bunu okur.
    baslangic: veri almanın başladığı an (verilmezse şimdi). Bitiş manifest_bitir() ile eklenir."""
    import json
    baslangic = baslangic or simdi()
    veri = {
        "ambar_surum": SURUM,
        "tur": tur,
        "tarih": baslangic,
        "baslangic": baslangic,
        "cihaz": {},
    }
    try:
        veri["cihaz"] = cihaz_ozellikleri() if CIHAZ else {}
    except Exception as e:   # cihaz bağlı değilse (örn. yerel dosyadan işlem)
        veri["cihaz_hatasi"] = str(e)
    if ek:
        veri.update(ek)
    (oturum / "ambar_bilgi.json").write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")


def manifest_bitir(oturum: Path, ek: dict | None = None):
    """Veri almanın bittiği anı ve süresini manifeste ekler.
    SHA-256 listesinden ÖNCE çağrılmalı; aksi halde ambar_bilgi.json'un özeti değişir."""
    import json
    yol = oturum / "ambar_bilgi.json"
    try:
        veri = json.loads(yol.read_text(encoding="utf-8"))
    except Exception:
        return
    veri["bitis"] = simdi()
    try:
        veri["sure_sn"] = int((datetime.datetime.fromisoformat(veri["bitis"]) -
                               datetime.datetime.fromisoformat(veri["baslangic"])).total_seconds())
    except (KeyError, ValueError):
        pass
    if ek:
        veri.update(ek)
    yol.write_text(json.dumps(veri, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------- Çağrı / SMS / MMS kayıtları
def content_sorgu(uri: str, kolonlar: list[str], where: str | None = None):
    """
    'adb shell content query' çıktısını sözlük listesine çevirir.
    Değerlerde virgül ve satır sonu olabildiği için kolonlar bilinen sırayla eşleştirilir.
    Erişim izni yoksa None döndürür.
    """
    import re
    komut = f"content query --uri {_q(uri)} --projection {':'.join(kolonlar)}"
    if where:
        komut += f' --where "{where}"'
    r = adb("shell", komut)
    cikti = (r.stdout or "").replace("\r\n", "\n")
    if "Error while accessing provider" in cikti or "SecurityException" in cikti:
        return None
    desen = re.compile("^" + ", ".join(f"{re.escape(k)}=(.*?)" for k in kolonlar) + r"\s*$", re.S)
    satirlar = []
    for parca in re.split(r"(?:^|\n)Row: \d+ ", cikti)[1:]:
        m = desen.match(parca)
        if m:
            satirlar.append({k: (None if v == "NULL" else v) for k, v in zip(kolonlar, m.groups())})
    return satirlar


UYGULAMA_KOMUTLARI = [
    ("paketler_tum.txt", "pm list packages -f -i -U"),
    ("paketler_3parti.txt", "pm list packages -3"),
    ("paketler_surum.txt", "pm list packages -i -U --show-versioncode"),
    # firstInstallTime / lastUpdateTime / versionName (tek komutta tüm paketler, ~0,5 sn)
    ("paketler_detay.txt", "dumpsys package packages"),
]


def uygulamalari_al(hedef: Path):
    for ad, komut in UYGULAMA_KOMUTLARI:
        (hedef / ad).write_text(adb("shell", komut).stdout, encoding="utf-8")


def simgeleri_al(hedef: Path):
    """Uygulama simgeleri -> hedef/simgeler/<paket>.svg|png|webp (APK'ların tamamı çekilmez)."""
    import ambar_simge
    ilerleme(0, "Uygulama simgeleri okunuyor")
    try:
        r = ambar_simge.telefondan_simgeler(hedef / "simgeler", ADB, ilerleme=ilerleme)
        print(f"    {'simgeler':<12} {sum(r.values())} / {len(r)} uygulama (diğerlerinin simgesi yok)")
    except Exception as e:      # simge alınamazsa diğer veriler etkilenmesin
        print(f"    {'simgeler':<12} alınamadı: {e}")


def kayitlari_al(klasor: Path, uygulamalar: bool = True) -> dict:
    """Çağrı kaydı, SMS ve MMS'i telefondan okuyup klasor/kayitlar/*.json olarak kaydeder.
    uygulamalar=True ise uygulama envanteri de kayitlar/ altına yazılır (komple yedek bunu cihaz_bilgisi'ne yazar)."""
    import json
    hedef = klasor / "kayitlar"
    hedef.mkdir(parents=True, exist_ok=True)
    sayilar = {}

    def yaz(ad, veri):
        (hedef / f"{ad}.json").write_text(json.dumps(veri, ensure_ascii=False, indent=1), encoding="utf-8")
        sayilar[ad] = len(veri) if veri is not None else None
        durum = f"{len(veri)} kayıt" if veri is not None else "ERİŞİM İZNİ YOK"
        print(f"    {ad:<12} {durum}")

    ilerleme(None, "Çağrı kaydı okunuyor")
    yaz("cagrilar", content_sorgu("content://call_log/calls",
        ["_id", "date", "duration", "type", "subscription_id", "countryiso", "geocoded_location", "number", "name"]))
    ilerleme(None, "SMS okunuyor")
    sms_kolon = ["_id", "thread_id", "date", "date_sent", "type", "read", "address", "body"]
    sms = content_sorgu("content://sms", sms_kolon + ["sub_id"])     # sub_id: hangi SIM (eski ROM'larda yok)
    yaz("sms", sms if sms is not None else content_sorgu("content://sms", sms_kolon))

    ilerleme(None, "MMS okunuyor")
    mms = content_sorgu("content://mms", ["_id", "thread_id", "date", "msg_box", "read", "sub"])
    if mms:
        metinler = {}
        for p in content_sorgu("content://mms/part", ["mid", "ct", "text"]) or []:
            if p.get("ct") == "text/plain" and p.get("text"):
                metinler.setdefault(p["mid"], []).append(p["text"])
        # Adresler: her MMS için ayrı sorgu çok yavaş (MMS başına ~2 sn). Bunun yerine
        # konuşma (thread) -> alıcı kimlikleri -> adres eşlemesi iki toplu sorguyla yapılır.
        adresler = {a["_id"]: a["address"] for a in
                    content_sorgu("content://mms-sms/canonical-addresses", ["_id", "address"]) or []}
        konusmalar = {k["_id"]: k.get("recipient_ids") or "" for k in
                      content_sorgu("content://mms-sms/conversations?simple=true", ["_id", "recipient_ids"]) or []}
        for m in mms:
            m["body"] = "\n".join(metinler.get(m["_id"], []))
            ids = konusmalar.get(m.get("thread_id"), "").split()
            m["address"] = ", ".join(adresler[i] for i in ids if adresler.get(i)) or None
    yaz("mms", mms)

    (hedef / "kullanicilar.txt").write_text(adb("shell", "pm list users").stdout, encoding="utf-8")
    if uygulamalar:
        ilerleme(None, "Uygulama listesi okunuyor")
        uygulamalari_al(hedef)
        print(f"    {'uygulamalar':<12} {(hedef / 'paketler_tum.txt').read_text(encoding='utf-8').count('package:')} paket")
        simgeleri_al(hedef)
    ilerleme(None, "Hesaplar okunuyor")
    print(f"    {'hesaplar':<12} {hesaplari_al(hedef)} hesap")
    ilerleme(None, "SIM kart okunuyor")
    try:
        s = sim_al(hedef)
        sayilar.update(s)
        print(f"    {'sim kart':<12} {s['sim_kart']} kayıtlı SIM, {s['sim_rehber']} rehber, {s['sim_sms']} SMS")
    except Exception as e:      # SIM okunamazsa diğer kayıtlar etkilenmesin
        print(f"    {'sim kart':<12} okunamadı: {e}")
    print("    Not: Rehber (kişiler) adb ile okunamaz (READ_CONTACTS izni gerekir);")
    print("         inceleme programı kişileri çağrı ve mesaj kayıtlarından türetir.")
    return sayilar


# Bazı ROM'lar (ör. MIUI) 'dumpsys account' çıktısında hesap adlarını maskeler (ahmet***5@gmail.com).
# Aynı adresler başka sistem dökümlerinde açık halde geçer; inceleme programı maskeyi bunlarla çözer.
HESAP_KAYNAKLARI = [
    "dumpsys content",
    "dumpsys activity service com.google.android.gms",
    "dumpsys jobscheduler",
    "dumpsys usagestats",
    "dumpsys backup",
    "dumpsys package com.google.android.gms",
]
_EPOSTA = r"[A-Za-z0-9._%+\-*]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"


def hesaplari_al(hedef: Path) -> int:
    """hesaplar.txt (dumpsys account, ham) + hesap_kaynaklari.txt (diğer dökümlerdeki e-posta satırları)."""
    import re
    ham = adb("shell", "dumpsys account").stdout
    (hedef / "hesaplar.txt").write_text(ham, encoding="utf-8")
    satirlar = []
    for komut in HESAP_KAYNAKLARI:
        try:
            cikti = adb("shell", komut).stdout or ""
        except Exception:
            continue
        bulunan = sorted({s.strip() for s in cikti.splitlines() if re.search(_EPOSTA, s)})
        if bulunan:
            satirlar.append(f"### {komut}")
            satirlar += bulunan[:2000]
    (hedef / "hesap_kaynaklari.txt").write_text("\n".join(satirlar) + "\n", encoding="utf-8")
    return len(set(re.findall(r"Account \{name=([^,]+), type=([^}]+)\}", ham)))


# ----------------------------------------------------------------- SIM kart
# 'dumpsys isub' takılı SIM'lerle birlikte cihaza daha önce takılmış tüm SIM'lerin kaydını verir
# (operatör, ICCID, IMSI, numara, ülke...). Android 14+ ICCID/IMSI/numaranın sonunu maskeler ([****]);
# tam değerler yalnızca root veya operatör yetkisiyle okunabilir.
SIM_KOMUTLARI = [
    ("sim_abonelikler.txt", "dumpsys isub"),
    ("sim_telefon.txt", "dumpsys telephony.registry"),
    ("sim_sms_servisi.txt", "dumpsys isms"),
]
SIM_LISTELERI = ["adn", "fdn", "sdn"]       # rehber, sabit arama, servis numaraları


def content_ham(uri: str):
    """'content query' (projeksiyonsuz) -> (ham çıktı, satır listesi | erişim yoksa None).
    Kolon adlarını bilmeden ayrıştırır (SIM sağlayıcılarının kolonları üreticiye göre değişir)."""
    import re
    r = adb("shell", f"content query --uri {_q(uri)}")
    cikti = ((r.stdout or "") + (r.stderr or "")).replace("\r\n", "\n")
    if "Error while accessing provider" in cikti or "Exception" in cikti[:400]:
        return cikti, None
    satirlar = []
    for parca in re.split(r"(?:^|\n)Row: \d+ ", cikti)[1:]:
        r = {}
        for alan in re.split(r", (?=[A-Za-z_][A-Za-z0-9_]*=)", parca.rstrip("\n")):
            k, _, v = alan.partition("=")
            r[k] = None if v == "NULL" else v
        satirlar.append(r)
    return cikti, satirlar


def sim_al(hedef: Path) -> dict:
    """SIM abonelik geçmişi, SIM durumu, SIM rehberi (ADN/FDN/SDN) ve SIM'deki SMS'ler -> hedef/sim_*.txt + sim.json."""
    import json, re
    for ad, komut in SIM_KOMUTLARI:
        (hedef / ad).write_text(adb("shell", komut).stdout or "", encoding="utf-8")
    ozellik = adb("shell", "getprop").stdout or ""
    (hedef / "sim_durum.txt").write_text("\n".join(
        s for s in ozellik.splitlines()
        if re.match(r"\[(gsm\.|ril\.|persist\.radio\.|ro\.telephony\.|persist\.sys\.(sim|dual)|telephony\.)", s)) + "\n",
        encoding="utf-8")
    isub = (hedef / "sim_abonelikler.txt").read_text(encoding="utf-8")
    idler = sorted(set(re.findall(r"(?:SubscriptionInfoInternal: |\{)id=(\d+)", isub)), key=int)

    ham, rehber, sms, sorgular = [], [], [], {}

    def sorgula(uri, sid, liste=None):
        metin, satir = content_ham(uri)
        ham.append(f"### {uri}\n{metin.strip()}\n")
        sorgular[uri] = None if satir is None else len(satir)
        for r in satir or []:
            (rehber if liste else sms).append({"sub_id": sid, **({"liste": liste} if liste else {}), **r})

    for liste in SIM_LISTELERI:
        sorgula(f"content://icc/{liste}", None, liste)
        for sid in idler:
            sorgula(f"content://icc/{liste}/subId/{sid}", sid, liste)
    sorgula("content://sms/icc", None)
    for sid in idler:
        sorgula(f"content://sms/icc_subId/{sid}", sid)

    # Varsayılan URI, varsayılan SIM'in kayıtlarını tekrar döndürür: abonelik numarası bilinen kopya kalsın
    def tekille(liste, anahtar):
        bilinen = {anahtar(r) for r in liste if r.get("sub_id")}
        return [r for r in liste if r.get("sub_id") or anahtar(r) not in bilinen]
    rehber = tekille(rehber, lambda r: (r.get("liste"), r.get("name"), r.get("number")))
    sms = tekille(sms, lambda r: (r.get("address"), r.get("date"), r.get("body")))

    (hedef / "sim_sorgular.txt").write_text("\n".join(ham), encoding="utf-8")
    (hedef / "sim.json").write_text(json.dumps({"alinma": simdi(), "abonelik_idleri": idler, "rehber": rehber,
                                                "sms": sms, "sorgular": sorgular}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    durum = re.search(r"\[gsm\.sim\.state\]: \[([^\]]*)\]", ozellik)
    return {"sim_kart": len(idler), "sim_rehber": len(rehber), "sim_sms": len(sms),
            "sim_durum": durum.group(1) if durum else ""}


def sim_cek(hedef: Path):
    """Sadece SIM kart bilgilerini alır."""
    bas = simdi()
    model = cihaz_kontrol()
    oturum = hedef / f"{guvenli_ad(model)}_sim_{zaman_damgasi()}"
    k = oturum / "kayitlar"
    k.mkdir(parents=True, exist_ok=True)
    print("\n>>> SIM kart bilgileri okunuyor...")
    ilerleme(None, "SIM kart okunuyor")
    s = sim_al(k)
    print(f"    SIM durumu     : {s['sim_durum'] or '-'}")
    print(f"    Kayıtlı SIM    : {s['sim_kart']} (takılı ve daha önce takılmış)")
    print(f"    SIM rehberi    : {s['sim_rehber']} kayıt")
    print(f"    SIM'deki SMS   : {s['sim_sms']}")
    manifest_yaz(oturum, "sim", {"kayit_sayilari": s}, baslangic=bas)
    manifest_bitir(oturum)
    sha_listesi(oturum)
    print(f"\nTAMAM: {oturum}")
    return oturum


def kayitlar_cek(hedef: Path):
    """Sadece çağrı / SMS / MMS kayıtlarını alır."""
    bas = simdi()
    model = cihaz_kontrol()
    oturum = hedef / f"{guvenli_ad(model)}_kayitlar_{zaman_damgasi()}"
    oturum.mkdir(parents=True, exist_ok=True)
    print("\n>>> Çağrı ve mesaj kayıtları okunuyor...")
    sayilar = kayitlari_al(oturum)
    manifest_yaz(oturum, "kayitlar", {"kayit_sayilari": sayilar}, baslangic=bas)
    manifest_bitir(oturum)
    sha_listesi(oturum)
    print(f"\nTAMAM: {oturum}")
    return oturum


def zaman_damgasi():
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S")


def guvenli_ad(s):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def boyut_yaz(n):
    for birim in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {birim}"
        n /= 1024
    return f"{n:.1f} PB"


def klasor_istatistik(p: Path):
    adet, toplam = 0, 0
    for kok, _, dosyalar in os.walk(p):
        for d in dosyalar:
            adet += 1
            try:
                toplam += (Path(kok) / d).stat().st_size
            except OSError:
                pass
    return adet, toplam


def _q(s: str) -> str:
    """Android shell için tek tırnaklı güvenli argüman."""
    return "'" + s.replace("'", "'\\''") + "'"


def guvenli_pull(uzak_dir: str, yerel_dir: Path, hatalar: list, derinlik=0):
    """
    Klasörü çeker. adb pull ilk okunamayan dosyada tümden durduğu için, hata olursa
    klasörün içine inip alt klasörleri ve dosyaları tek tek çeker; çekilemeyenleri 'hatalar'a ekler.
    """
    uzak_dir = uzak_dir.rstrip("/")
    yerel_dir.mkdir(parents=True, exist_ok=True)
    # Kök ve korumalı olduğu bilinen klasörleri doğrudan böl (tekrar kopyalamayı önler)
    bol = derinlik == 0 or Path(uzak_dir).name in ("Android", "data", "obb")
    if not bol:
        r = adb("pull", "-a", f"{uzak_dir}/.", str(yerel_dir))
        if r.returncode == 0:
            return True
        print(f"    Okunamayan dosya var, tek tek çekiliyor: {uzak_dir}")
    else:
        r = subprocess.CompletedProcess([], 1, "", "")
    kok = _q(uzak_dir + "/")   # sondaki / : /sdcard gibi kısayolların içine girilsin
    klasorler = adb("shell", f"find {kok} -mindepth 1 -maxdepth 1 -type d 2>/dev/null").stdout
    dosyalar = adb("shell", f"find {kok} -mindepth 1 -maxdepth 1 -type f 2>/dev/null").stdout
    # find, 'kok/' verildiğinde '/sdcard//x' döndürebilir; çift eğik çizgiyi temizle
    klasorler = "\n".join(s.replace("//", "/") for s in klasorler.splitlines())
    dosyalar = "\n".join(s.replace("//", "/") for s in dosyalar.splitlines())
    klasorler = [s for s in klasorler.splitlines() if s.strip()]
    dosyalar = [s for s in dosyalar.splitlines() if s.strip()]
    if not klasorler and not dosyalar:
        hatalar.append(f"{uzak_dir}/  ({(r.stderr or r.stdout).strip().splitlines()[-1:] or ['erişilemedi']})")
        return False
    tamam = True
    for d in dosyalar:
        rr = adb("pull", "-a", d, str(yerel_dir / Path(d).name))
        if rr.returncode != 0:
            tamam = False
            sebep = (rr.stderr or rr.stdout).strip().splitlines()
            hatalar.append(f"{d}  ({sebep[-1] if sebep else 'hata'})")
    for k in klasorler:
        tamam &= guvenli_pull(k, yerel_dir / Path(k).name, hatalar, derinlik + 1)
    return tamam


def izlemeli(fn, yerel: Path, toplam_bayt: int | None, etiket=""):
    """fn() çalışırken yerel klasör boyutunu izleyip ilerleme yüzdesi bildirir."""
    bitti = threading.Event()

    def izle():
        while not bitti.wait(2):
            _, simdi = klasor_istatistik(yerel)
            if toplam_bayt:
                y = min(99.0, simdi * 100 / toplam_bayt)
                ilerleme(y, f"{etiket} {boyut_yaz(simdi)} / {boyut_yaz(toplam_bayt)}")
            else:
                ilerleme(None, f"{etiket} {boyut_yaz(simdi)}")

    t = threading.Thread(target=izle, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        bitti.set()
        ilerleme(100, f"{etiket} tamamlandı")


def izlemeli_pull(uzak: str, yerel: Path, toplam_bayt: int | None, etiket=""):
    """Hatalarda durmayan, ilerleme bildiren klasör çekme. (sonuc_ok, hatalar) döndürür."""
    hatalar = []
    ok = izlemeli(lambda: guvenli_pull(uzak.removesuffix("/."), yerel, hatalar),
                  yerel, toplam_bayt, etiket)
    if hatalar:
        print(f"    UYARI: {len(hatalar)} öğe çekilemedi (izin yok). Liste: cekilemeyenler.txt")
        with open(yerel.parent / "cekilemeyenler.txt", "a", encoding="utf-8") as f:
            f.write("\n".join(hatalar) + "\n")
    return subprocess.CompletedProcess([], 0 if ok else 1)


# ----------------------------------------------------------------- 1) adb backup
def adb_backup(hedef: Path, paket: str | None, apk: bool, paylasilan: bool, sistem: bool):
    model = cihaz_kontrol()
    hedef.mkdir(parents=True, exist_ok=True)
    ad = f"{guvenli_ad(model)}_{paket or 'tam'}_{zaman_damgasi()}.ab"
    dosya = hedef / ad

    args = ["backup", "-f", str(dosya)]
    args.append("-apk" if apk else "-noapk")
    if paket:
        args.append(paket)
    else:
        args.append("-shared" if paylasilan else "-noshared")
        args.append("-system" if sistem else "-nosystem")
        args.append("-all")

    print("\n>>> Telefonun ekranını açın ve 'Verilerimi yedekle' düğmesine basın.")
    print("    (Şifre girmezseniz yedek şifresiz olur ve .tar'a çevrilebilir.)")
    print(f"    Komut: adb {' '.join(args)}\n")
    adb(*args, yakala=False)

    if not dosya.exists() or dosya.stat().st_size <= 100:
        print("UYARI: Yedek dosyası boş veya çok küçük.")
        print("  - Telefonda onayı vermemiş olabilirsiniz.")
        print("  - Uygulama yedeklemeye izin vermiyor olabilir (WhatsApp 'allowBackup=false' kullanır;")
        print("    bu yüzden WhatsApp verileri için klasör çekme seçeneğini kullanın).")
        print("  - Android 12+ sürümlerde adb backup çoğu uygulama için artık veri içermez.")
    else:
        print(f"\nTAMAM: {dosya}  ({boyut_yaz(dosya.stat().st_size)})")
    return dosya


# ----------------------------------------------------------------- 2) com.whatsapp klasörü
def uzak_var_mi(yol: str) -> bool:
    r = adb("shell", f'[ -d "{yol}" ] && echo VAR')
    return "VAR" in r.stdout


def whatsapp_klasoru_bul() -> list[str]:
    bulunan = [y for y in OLASI_WHATSAPP_YOLLARI if uzak_var_mi(y)]
    # Kullanıcının kendi oluşturduğu com.whatsapp klasörlerini de ara
    r = adb("shell", "find /sdcard/ -maxdepth 4 -type d -name com.whatsapp 2>/dev/null")
    for satir in r.stdout.splitlines():
        satir = satir.strip().rstrip("/")
        if satir and satir not in bulunan and not satir.startswith("/sdcard/Android/data"):
            bulunan.append(satir)
    # /storage/emulated/0 ile /sdcard aynı yer; tekrarı kaldır
    sonuc, gorulen = [], set()
    for y in bulunan:
        anahtar = y.replace("/storage/emulated/0", "/sdcard")
        if anahtar not in gorulen:
            gorulen.add(anahtar)
            sonuc.append(y)
    return sonuc


def uzak_istatistik(yol: str):
    yol = yol.rstrip("/") + "/"   # /sdcard gibi kısayolların içine girilsin
    adet = adb("shell", f"find {_q(yol)} -type f 2>/dev/null | wc -l").stdout.strip()
    boyut = adb("shell", f"du -sk {_q(yol)} 2>/dev/null").stdout.split()
    try:
        return int(adet), int(boyut[0]) * 1024 if boyut else 0
    except ValueError:
        return None, None


def whatsapp_cek(hedef: Path, kaynak: str | None, sor=True, secici=None):
    """secici: birden fazla klasör bulunursa çağrılır, list[str] alır ve seçilenleri döndürür (GUI)."""
    model = cihaz_kontrol()

    if kaynak:
        if not uzak_var_mi(kaynak):
            sys.exit(f"HATA: Telefonda klasör bulunamadı: {kaynak}")
        kaynaklar = [kaynak]
    else:
        print("com.whatsapp klasörü aranıyor...")
        bulunan = whatsapp_klasoru_bul()
        if not bulunan:
            sys.exit("HATA: com.whatsapp klasörü bulunamadı. --kaynak ile yolu elle verin.")
        print("\nBulunan klasörler:")
        for i, y in enumerate(bulunan, 1):
            adet, byt = uzak_istatistik(y)
            bilgi = f"{adet} dosya, {boyut_yaz(byt)}" if adet is not None else "?"
            print(f"  {i}) {y}   [{bilgi}]")
        if secici and len(bulunan) > 1:
            kaynaklar = secici(bulunan)
            if not kaynaklar:
                print("İptal edildi.")
                return None
        elif sor and len(bulunan) > 1:
            secim = input("\nHangisi çekilsin? (numara, virgülle birden fazla, 'h' = hepsi) [1]: ").strip().lower()
            if secim in ("h", "hepsi", "a", "all"):
                kaynaklar = bulunan
            else:
                secim = secim or "1"
                kaynaklar = [bulunan[int(s) - 1] for s in secim.split(",") if s.strip().isdigit()
                             and 0 < int(s) <= len(bulunan)]
        else:
            kaynaklar = bulunan[:1]

    bas = simdi()      # klasör seçimi (kullanıcı bekleme) sonrası, çekme başlarken
    oturum = hedef / f"{guvenli_ad(model)}_whatsapp_{zaman_damgasi()}"
    oturum.mkdir(parents=True, exist_ok=True)
    manifest_yaz(oturum, "whatsapp", {"kaynaklar": kaynaklar}, baslangic=bas)

    for k in kaynaklar:
        alt = oturum / guvenli_ad(k.strip("/").replace("/", "_"))
        alt.mkdir(parents=True, exist_ok=True)
        uzak_adet, uzak_boyut = uzak_istatistik(k)
        print(f"\n>>> Çekiliyor: {k}")
        if uzak_adet is not None:
            print(f"    Telefonda: {uzak_adet} dosya, {boyut_yaz(uzak_boyut)}")
        # "kaynak/." -> klasörün içeriğini doğrudan 'alt' içine kopyalar; -a zaman damgalarını korur
        r = izlemeli_pull(f"{k.rstrip('/')}/.", alt, uzak_boyut, "WhatsApp:")
        yerel_adet, yerel_boyut = klasor_istatistik(alt)
        print(f"    Bilgisayarda: {yerel_adet} dosya, {boyut_yaz(yerel_boyut)}")
        if r.returncode != 0:
            print("    UYARI: adb pull hata ile bitti (bazı dosyalar atlanmış olabilir).")
        elif uzak_adet is not None and yerel_adet < uzak_adet:
            print(f"    UYARI: {uzak_adet - yerel_adet} dosya eksik görünüyor.")
        else:
            print("    TAMAM")

    print(f"\nYedek klasörü: {oturum}")
    manifest_bitir(oturum)
    oxygen_ziple(oturum)
    return oturum


# ----------------------------------------------------------------- 5) Komple yedek
def sdcard_cek(oturum: Path):
    hedef_sd = oturum / "sdcard"
    hedef_sd.mkdir(parents=True, exist_ok=True)
    (oturum / "cekilemeyenler.txt").unlink(missing_ok=True)
    uzak_adet, uzak_boyut = uzak_istatistik("/sdcard/")
    print(f"    Telefonda: {uzak_adet} dosya, {boyut_yaz(uzak_boyut or 0)}")
    izlemeli_pull("/sdcard/.", hedef_sd, uzak_boyut, "Dahili depolama:")
    yerel_adet, yerel_boyut = klasor_istatistik(hedef_sd)
    print(f"    Bilgisayarda: {yerel_adet} dosya, {boyut_yaz(yerel_boyut)}")
    if uzak_adet and yerel_adet < uzak_adet:
        print(f"    UYARI: {uzak_adet - yerel_adet} dosya eksik (Android 11+ 'Android/data' ve "
              "'Android/obb' içini korur; root olmadan alınamaz).")
    return hedef_sd


def sha_listesi(oturum: Path):
    print("\nSHA-256 özetleri hesaplanıyor...")
    liste = oturum / "SHA256_tum_dosyalar.txt"
    tum = [Path(k) / d for k, _, ds in os.walk(oturum) for d in sorted(ds)]
    tum = [p for p in tum if p != liste]
    adet = 0
    with open(liste, "w", encoding="utf-8") as f:
        for i, p in enumerate(tum, 1):
            try:
                f.write(f"{_sha256(p)}  {p.relative_to(oturum).as_posix()}\n")
                adet += 1
            except OSError as e:
                f.write(f"HATA  {p.relative_to(oturum).as_posix()}  ({e})\n")
            if i % 50 == 0 or i == len(tum):
                ilerleme(i * 100 / max(1, len(tum)), f"SHA-256 {i}/{len(tum)}")
    print(f"    {adet} dosya -> {liste.name}")
    return liste
def komple_yedek(hedef: Path, ab_al=True, sdcard=True, apk=True, ozet=True):
    bas = simdi()
    model = cihaz_kontrol()
    oturum = hedef / f"{guvenli_ad(model)}_komple_{zaman_damgasi()}"
    oturum.mkdir(parents=True, exist_ok=True)
    print(f"Komple yedek klasörü: {oturum}")

    # 0) Cihaz bilgileri
    bilgi = oturum / "cihaz_bilgisi"
    bilgi.mkdir(exist_ok=True)
    for ad, komut in [
        ("getprop.txt", "getprop"),
        *UYGULAMA_KOMUTLARI,
        ("hesaplar.txt", "dumpsys account"),
        ("kullanicilar.txt", "pm list users"),
        ("depolama.txt", "df -h"),
        ("tarih.txt", "date"),
    ]:
        (bilgi / ad).write_text(adb("shell", komut).stdout, encoding="utf-8")
    simgeleri_al(bilgi)
    print("[1/4] Cihaz bilgileri kaydedildi.")
    print("      Çağrı ve mesaj kayıtları:")
    kayit_sayilari = kayitlari_al(oturum, uygulamalar=False)
    manifest_yaz(oturum, "komple", {"kayit_sayilari": kayit_sayilari}, baslangic=bas)

    # 1) adb backup
    if ab_al:
        print("\n[2/4] adb backup (telefonda onay gerekir)")
        adb_backup(oturum, None, apk=True, paylasilan=False, sistem=False)

    # 2) Dahili depolama
    if sdcard:
        print("\n[3/4] Dahili depolama (/sdcard) çekiliyor... (GB'larca veri, uzun sürebilir)")
        sdcard_cek(oturum)

    # 3) APK'lar
    if apk:
        print("\n[4/4] Uygulama APK'ları çekiliyor...")
        apk_kok = oturum / "apk"
        paketler = [s.split(":", 1)[1].strip() for s in
                    adb("shell", "pm list packages -3").stdout.splitlines() if ":" in s]
        for i, p in enumerate(sorted(paketler), 1):
            yollar = [s.split(":", 1)[1].strip() for s in
                      adb("shell", f"pm path {p}").stdout.splitlines() if s.startswith("package:")]
            d = apk_kok / p
            d.mkdir(parents=True, exist_ok=True)
            tamam = sum(adb("pull", y, str(d / Path(y).name)).returncode == 0 for y in yollar)
            ilerleme(i * 100 / len(paketler), f"APK {i}/{len(paketler)}")
            print(f"    {i}/{len(paketler)} {p}: {tamam}/{len(yollar)} apk")

    # 4) SHA-256 özetleri (bitiş zamanı önce yazılır ki manifestin özeti doğru kalsın)
    manifest_bitir(oturum)
    if ozet:
        sha_listesi(oturum)

    adet, toplam = klasor_istatistik(oturum)
    print(f"\nKOMPLE YEDEK BİTTİ: {oturum}\n    {adet} dosya, {boyut_yaz(toplam)}")
    return oturum


# ----------------------------------------------------------------- Oxygen için ZIP
def _dosya_listesi(kok: Path, haric_ust=()):
    """kok altındaki dosyaları {göreli_yol: Path} olarak döndürür."""
    sonuc = {}
    for yol, klasorler, dosyalar in os.walk(kok):
        if Path(yol) == kok:
            klasorler[:] = [k for k in klasorler if k not in haric_ust]
        for d in dosyalar:
            p = Path(yol) / d
            sonuc[p.relative_to(kok).as_posix()] = p
    return sonuc


def _sha256(p: Path):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while parca := f.read(4 * 1024 * 1024):
            h.update(parca)
    return h.hexdigest()


def oxygen_ziple(oturum: Path):
    """
    Çok hesaplı WhatsApp'ta ek hesapların verileri WhatsApp/accounts/<id>/ altında durur ve Oxygen
    bunları görmez. Her hesap için ayrı ZIP üretir:
      - ana:        WhatsApp/ kökündeki dosyalar (accounts klasörü hariç)
      - hesap_<id>: YALNIZCA accounts/<id>/ içindeki dosyalar, ZIP'te WhatsApp/ köküne yerleştirilir
    Hesapların verileri birbirine karıştırılmaz. Çekilen orijinal klasöre dokunulmaz.
    SHA256.txt'de kaydı olan (tamamlanmış) ZIP'ler yeniden üretilmez.
    """
    import zipfile

    # Zaten sıkıştırılmış / şifreli dosyalar yeniden sıkıştırılmaz (çok daha hızlı)
    SIKISMIS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".mp4", ".3gp", ".mkv", ".mov", ".opus",
                ".mp3", ".m4a", ".aac", ".ogg", ".zip", ".pdf", ".crypt12", ".crypt14", ".crypt15", ".webm"}
    sha_dosyasi = oturum / "SHA256.txt"
    tamamlanan = set()
    if sha_dosyasi.exists():
        tamamlanan = {s.split("  ", 1)[1].strip() for s in sha_dosyasi.read_text(encoding="utf-8").splitlines()
                      if "  " in s}

    wa_klasorleri = [p for p in oturum.rglob("WhatsApp")
                     if p.is_dir() and p.parent.name != "accounts" and "accounts" not in p.parts]
    if not wa_klasorleri:
        print("UYARI: Çekilen yedekte 'WhatsApp' klasörü bulunamadı, ZIP oluşturulmadı.")
        return []

    uretilen = []
    for wa in wa_klasorleri:
        ana = _dosya_listesi(wa, haric_ust=("accounts",))
        hesap_kok = wa / "accounts"
        hesaplar = sorted(p for p in hesap_kok.iterdir() if p.is_dir()) if hesap_kok.is_dir() else []

        paketler = []  # (etiket, {arc: kaynak})
        ana_msgstore = any("msgstore" in k for k in ana)
        if not hesaplar or ana_msgstore:
            paketler.append(("ana", ana))
        for h in hesaplar:
            # Yalnızca bu hesabın dosyaları: ana hesabın verileri bu pakete KARIŞTIRILMAZ
            paketler.append((f"hesap_{h.name}", _dosya_listesi(h)))
        if hesaplar:
            print(f"\n{len(hesaplar)} ek hesap bulundu: {', '.join(h.name for h in hesaplar)}")
            if not ana_msgstore:
                print("  Ana klasörde mesaj yedeği (msgstore) yok, 'ana' ZIP'i atlandı.")

        for etiket, dosyalar in paketler:
            zip_yolu = oturum / f"{oturum.name}_{etiket}.zip"
            if zip_yolu.name in tamamlanan and zip_yolu.exists():
                print(f"\n>>> ZIP zaten tamamlanmış, atlandı: {zip_yolu.name}")
                uretilen.append(zip_yolu)
                continue
            msg = sorted(k for k in dosyalar if "msgstore" in k)
            toplam = sum(p.stat().st_size for p in dosyalar.values()) or 1
            print(f"\n>>> ZIP: {zip_yolu.name}  ({len(dosyalar)} dosya, {boyut_yaz(toplam)})")
            for m in msg:
                print(f"    WhatsApp/{m}")
            if not msg:
                print("    UYARI: Bu pakette msgstore veritabanı yok.")
            gecici = zip_yolu.with_suffix(".zip.part")
            yazilan = 0
            with zipfile.ZipFile(gecici, "w", zipfile.ZIP_DEFLATED, allowZip64=True,
                                 strict_timestamps=False) as z:
                for i, (arc, kaynak) in enumerate(sorted(dosyalar.items()), 1):
                    tip = zipfile.ZIP_STORED if kaynak.suffix.lower() in SIKISMIS else zipfile.ZIP_DEFLATED
                    z.write(kaynak, f"WhatsApp/{arc}", compress_type=tip)
                    yazilan += kaynak.stat().st_size
                    if i % 200 == 0 or i == len(dosyalar):
                        ilerleme(yazilan * 100 / toplam, f"ZIP ({etiket}): {boyut_yaz(yazilan)} / {boyut_yaz(toplam)}")
            gecici.replace(zip_yolu)   # yarım kalan ZIP asla tamamlanmış gibi görünmesin
            ozet = _sha256(zip_yolu)
            with open(sha_dosyasi, "a", encoding="utf-8") as f:
                f.write(f"{ozet}  {zip_yolu.name}\n")
            print(f"    {boyut_yaz(zip_yolu.stat().st_size)}  SHA256: {ozet}")
            uretilen.append(zip_yolu)
    return uretilen


# ----------------------------------------------------------------- 3) .ab -> .tar
def ab_to_tar(ab_dosya: Path, tar_dosya: Path | None = None):
    tar_dosya = tar_dosya or ab_dosya.with_suffix(".tar")
    with open(ab_dosya, "rb") as f:
        if f.readline().strip() != b"ANDROID BACKUP":
            sys.exit("HATA: Geçerli bir Android yedek (.ab) dosyası değil.")
        surum = f.readline().strip()
        sikistirilmis = f.readline().strip() == b"1"
        sifreleme = f.readline().strip()
        if sifreleme != b"none":
            sys.exit(f"HATA: Yedek şifreli ({sifreleme.decode()}). Bu araç yalnızca şifresiz yedekleri çevirir.")
        print(f"Yedek sürümü: {surum.decode()}, sıkıştırma: {'evet' if sikistirilmis else 'hayır'}")
        d = zlib.decompressobj() if sikistirilmis else None
        with open(tar_dosya, "wb") as out:
            while parca := f.read(1024 * 1024):
                out.write(d.decompress(parca) if d else parca)
            if d:
                out.write(d.flush())
    print(f"TAMAM: {tar_dosya}  ({boyut_yaz(tar_dosya.stat().st_size)})")
    print("Açmak için: tar -xf \"%s\"" % tar_dosya)
    return tar_dosya


# ----------------------------------------------------------------- 4) MIUI Notlar
MIUI_YEDEK_KOK = "/sdcard/MIUI/backup/AllBackup"


def _pb_varint(b, i):
    sonuc = kay = 0
    while True:
        if i >= len(b):
            raise ValueError("varint taşması")
        c = b[i]
        i += 1
        sonuc |= (c & 0x7F) << kay
        if not c & 0x80:
            return sonuc, i
        kay += 7
        if kay > 70:
            raise ValueError("varint çok uzun")


def _pb_coz(b):
    """Basit protobuf çözücü: [(alan_no, tip, değer)] döndürür; geçersizse ValueError."""
    alanlar, i = [], 0
    while i < len(b):
        anahtar, i = _pb_varint(b, i)
        alan, tip = anahtar >> 3, anahtar & 7
        if alan == 0:
            raise ValueError("geçersiz alan")
        if tip == 0:
            v, i = _pb_varint(b, i)
        elif tip == 2:
            n, i = _pb_varint(b, i)
            if i + n > len(b):
                raise ValueError("uzunluk taşması")
            v, i = b[i:i + n], i + n
        elif tip == 1:
            v, i = b[i:i + 8], i + 8
        elif tip == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError("bilinmeyen tip")
        alanlar.append((alan, tip, v))
    return alanlar


def _pb_dene(b):
    try:
        return _pb_coz(b) if b else None
    except ValueError:
        return None


def _not_metni(xml_bayt):
    import html
    import re
    s = xml_bayt.decode("utf-8", errors="replace")
    s = re.sub(r"</text>\s*", "\n", s)
    s = re.sub(r"<input[^>]*checked=\"true\"[^>]*/?>", "[x] ", s)
    s = re.sub(r"<input[^>]*/?>", "[ ] ", s)
    s = re.sub(r"<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def _ms_tarih(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%d.%m.%Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return "?"


def miui_notlari_coz(bak_yolu: Path):
    """MIUI 'Notlar(com.miui.notes).bak' dosyasından notları çıkarır."""
    import io
    import tarfile

    veri = bak_yolu.read_bytes()
    i = veri.find(b"ANDROID BACKUP")
    if i < 0:
        raise ValueError("Dosyada ANDROID BACKUP başlığı yok")
    hdr = veri[i:].split(b"\n", 4)
    if hdr[3].strip() != b"none":
        raise ValueError("Yedek şifreli; MIUI yedeğini şifresiz alın")
    yuk = zlib.decompress(hdr[4]) if hdr[2].strip() == b"1" else hdr[4]

    notlar = []
    with tarfile.open(fileobj=io.BytesIO(yuk)) as tf:
        for uye in tf.getmembers():
            if not uye.isfile() or "miui_bak" not in uye.name:
                continue
            ham = tf.extractfile(uye).read()
            gorulen = set()

            def gez(mesaj):
                # Not kaydı: 5=oluşturma, 6=değiştirme (ms), 8=özet XML, 9=veri (mime + içerik)
                d = {}
                for alan, tip, v in mesaj:
                    d.setdefault((alan, tip), []).append(v)
                ozet = d.get((8, 2), [b""])[0]
                if (5, 0) in d and (6, 0) in d and ozet.lstrip().startswith(b"<"):
                    icerik = []
                    for veri_b in d.get((9, 2), []):
                        alt = _pb_dene(veri_b) or []
                        mime = next((v for a, t, v in alt if a == 1 and t == 2), b"")
                        if b"text_note" in mime:
                            icerik += [v for a, t, v in alt if a == 4 and t == 2]
                    metin = _not_metni(icerik[0] if icerik else ozet)
                    anahtar = (d[(5, 0)][0], metin)
                    if anahtar not in gorulen:
                        gorulen.add(anahtar)
                        notlar.append({
                            "olusturma": _ms_tarih(d[(5, 0)][0]),
                            "degistirme": _ms_tarih(d[(6, 0)][0]),
                            "_sira": d[(6, 0)][0],
                            "metin": metin,
                        })
                    return
                for alan, tip, v in mesaj:
                    if tip == 2:
                        alt = _pb_dene(v)
                        if alt:
                            gez(alt)

            kok = _pb_dene(ham)
            if kok:
                gez(kok)
    notlar.sort(key=lambda n: n["_sira"], reverse=True)
    return notlar


def notlari_yaz(notlar, cikti: Path):
    import json
    with open(cikti.with_suffix(".txt"), "w", encoding="utf-8") as f:
        for n, kayit in enumerate(notlar, 1):
            f.write(f"===== Not {n}  (oluşturma: {kayit['olusturma']}, "
                    f"son değişiklik: {kayit['degistirme']}) =====\n")
            f.write(kayit["metin"] + "\n\n")
    with open(cikti.with_suffix(".json"), "w", encoding="utf-8") as f:
        json.dump([{k: v for k, v in x.items() if k != "_sira"} for x in notlar], f,
                  ensure_ascii=False, indent=2)


def _not_yedekleri():
    """Telefondaki not yedeklerini {yol: boyut} olarak döndürür."""
    r = adb("shell", f"stat -c '%s|%n' {MIUI_YEDEK_KOK}/*/*com.miui.notes*.bak 2>/dev/null")
    sonuc = {}
    for s in r.stdout.splitlines():
        boyut, _, yol = s.strip().partition("|")
        if yol.endswith(".bak") and boyut.isdigit():
            sonuc[yol] = int(boyut)
    return sonuc


def yeni_not_yedegi_bekle(sure_sn=900):
    """Telefonda MIUI yedekleme ekranını açar, yeni not yedeği oluşana kadar bekler."""
    import time
    onceki = _not_yedekleri()
    adb("shell", "input keyevent KEYCODE_WAKEUP")
    r = adb("shell", "am start -n com.miui.backup/.local.LocalHomeActivity")
    if "Error" in r.stdout + r.stderr:
        print("UYARI: Yedekleme ekranı açılamadı, telefonda elle açın.")
    print("\n>>> Telefonda yedekleme ekranı açıldı. Şunları yapın:")
    print("    1) 'Yedekle' düğmesine basın")
    print("    2) Sadece 'Notlar'ı seçin (diğerlerinin işaretini kaldırın)")
    print("    3) Şifre KOYMAYIN, yedeklemeyi başlatın")
    print(f"    Yeni yedek bekleniyor (en fazla {sure_sn // 60} dk, iptal için Ctrl+C)...")
    bitis = time.time() + sure_sn
    son_boyut, sabit = {}, 0
    while time.time() < bitis:
        time.sleep(3)
        yeni = {y: b for y, b in _not_yedekleri().items() if y not in onceki}
        if yeni:
            # Dosya yazımı bitsin: boyut iki ölçümde aynı kalmalı
            sabit = sabit + 1 if yeni == son_boyut else 0
            son_boyut = yeni
            if sabit >= 2:
                yol = sorted(yeni)[-1]
                print(f"Yeni yedek oluştu: {yol} ({boyut_yaz(yeni[yol])})")
                return yol
    sys.exit("HATA: Süre doldu, yeni not yedeği oluşmadı.")


def notlari_cek(hedef: Path, yerel_bak: Path | None = None, yeni: bool = False):
    bas = simdi()
    if yerel_bak:
        CIHAZ.clear()
        oturum = hedef / f"notlar_{zaman_damgasi()}"
    else:
        model = cihaz_kontrol()
        oturum = hedef / f"{guvenli_ad(model)}_notlar_{zaman_damgasi()}"
    oturum.mkdir(parents=True, exist_ok=True)
    if yerel_bak:
        bak = yerel_bak
    else:
        if yeni:
            bulunan = [yeni_not_yedegi_bekle()]
        else:
            r = adb("shell", f"ls -t {MIUI_YEDEK_KOK}/*/*com.miui.notes*.bak 2>/dev/null")
            bulunan = [s.strip() for s in r.stdout.splitlines() if s.strip().endswith(".bak")]
        if not bulunan:
            sys.exit("HATA: Telefonda MIUI not yedeği yok.\n"
                     "Telefonda: Ayarlar > Ek ayarlar (veya Telefon hakkında) > Yedekle ve geri yükle >\n"
                     "Mobil cihaz > Yedekle -> sadece 'Notlar'ı seçip yedek alın, sonra tekrar çalıştırın.")
        uzak = bulunan[0]
        print(f"En yeni not yedeği: {uzak}")
        bak = oturum / Path(uzak).name
        adb("pull", "-a", uzak, str(bak), kontrol=True)
    notlar = miui_notlari_coz(bak)
    notlari_yaz(notlar, oturum / "notlar")
    manifest_yaz(oturum, "notlar", {"not_sayisi": len(notlar)}, baslangic=bas)
    manifest_bitir(oturum)
    print(f"\n{len(notlar)} not bulundu:")
    for n, k in enumerate(notlar, 1):
        print(f"  {n}) son değişiklik {k['degistirme']}  ({len(k['metin'])} karakter)")
    print(f"\nKaydedildi: {oturum / 'notlar.txt'}  ve  notlar.json")
    return oturum


# ----------------------------------------------------------------- menü
def menu(hedef: Path):
    while True:
        print("\n========== ADB YEDEKLEME ==========")
        print(f"Hedef klasör: {hedef}")
        print("1) Tam adb yedeği al (.ab)")
        print("2) Belirli bir paketin adb yedeğini al")
        print("3) com.whatsapp klasörünü bilgisayara çek")
        print("4) com.whatsapp klasörünü elle girilen yoldan çek")
        print("5) .ab dosyasını .tar'a çevir")
        print("6) Hepsi: tam yedek + com.whatsapp klasörü")
        print("7) Notları (Xiaomi Notlar) çek ve metne çevir")
        print("8) Daha önce çekilmiş WhatsApp yedeğinden Oxygen ZIP'i üret")
        print("9) KOMPLE yedek (adb backup + tüm dahili depolama + APK'lar + SHA-256)")
        print("s) SIM kart bilgisi (telefondan)")
        print("u) SIM kart imajı (USB kart okuyucudan)")
        print("0) Çıkış")
        s = input("Seçim: ").strip()
        try:
            if s == "1":
                adb_backup(hedef, None, apk=True, paylasilan=True, sistem=False)
            elif s == "2":
                p = input("Paket adı [com.whatsapp]: ").strip() or "com.whatsapp"
                adb_backup(hedef, p, apk=True, paylasilan=False, sistem=False)
            elif s == "3":
                whatsapp_cek(hedef, None)
            elif s == "4":
                yol = input("Telefondaki yol (örn. /sdcard/Yedek/com.whatsapp): ").strip()
                if yol:
                    whatsapp_cek(hedef, yol)
            elif s == "5":
                yol = input(".ab dosya yolu: ").strip().strip('"')
                if yol:
                    ab_to_tar(Path(yol))
            elif s == "6":
                adb_backup(hedef, None, apk=True, paylasilan=True, sistem=False)
                whatsapp_cek(hedef, None)
            elif s == "7":
                c = input("Telefonda yeni (güncel) not yedeği alınsın mı? [E/h]: ").strip().lower()
                notlari_cek(hedef, yeni=c not in ("h", "hayir", "hayır", "n"))
            elif s == "9":
                komple_yedek(hedef)
            elif s == "s":
                sim_cek(hedef)
            elif s == "u":
                import ambar_sim, getpass
                pin = getpass.getpass("SIM PIN (gerekmiyorsa boş bırakın): ").strip() or None
                ambar_sim.uicc_cek(hedef, pin=pin)
            elif s == "8":
                yol = input("Yedek klasörü (örn. yedekler\\23117RA68G_whatsapp_...): ").strip().strip('"')
                if yol:
                    oxygen_ziple(Path(yol))
            elif s == "0":
                return
        except SystemExit as e:
            if e.code not in (0, None):
                print(e.code if isinstance(e.code, str) else "İşlem iptal edildi.")
        except (RuntimeError, ValueError, IndexError) as e:
            print(f"HATA: {e}")


def main():
    global ADB
    ap = argparse.ArgumentParser(prog="ambar", description=f"{UYGULAMA_ADI} v{SURUM} - {ALT_BASLIK}")
    ap.add_argument("-o", "--hedef", type=Path, default=VARSAYILAN_HEDEF, help="Yedeklerin kaydedileceği klasör")
    alt = ap.add_subparsers(dest="komut")

    b = alt.add_parser("backup", help="adb backup ile yedek al")
    b.add_argument("--paket", help="Sadece bu paketi yedekle (örn. com.whatsapp)")
    b.add_argument("--noapk", action="store_true", help="APK dosyalarını dahil etme")
    b.add_argument("--noshared", action="store_true", help="Paylaşılan depolamayı (sdcard) dahil etme")
    b.add_argument("--system", action="store_true", help="Sistem uygulamalarını da dahil et")

    w = alt.add_parser("whatsapp", help="com.whatsapp klasörünü bilgisayara çek")
    w.add_argument("--kaynak", help="Telefondaki klasör yolu (verilmezse otomatik aranır)")

    t = alt.add_parser("ab2tar", help=".ab dosyasını .tar'a çevir")
    t.add_argument("dosya", type=Path)
    t.add_argument("--cikti", type=Path)

    alt.add_parser("kayitlar", help="Çağrı kaydı, SMS ve MMS'i al")
    g = alt.add_parser("agent", help="Ambar Agent ile çıkarma (rehber, takvim, MMS ekleri, uygulamalar)")
    g.add_argument("--dosyalar", action="store_true",
                   help="Telefondaki tüm dosyaları da çek (Android 11+: telefonda izin onayı gerekir)")
    i = alt.add_parser("incele", help="Yedekleri inceleme programını aç (tarayıcıda)")
    i.add_argument("--port", type=int, default=0)
    n = alt.add_parser("notlar", help="Xiaomi Notlar yedeğini çek ve metne çevir")
    n.add_argument("--dosya", type=Path, help="Telefondan çekmek yerine yereldeki .bak dosyasını çöz")
    n.add_argument("--yeni", action="store_true",
                   help="Telefonda yedekleme ekranını aç, yeni (güncel) not yedeği alınmasını bekle")

    k = alt.add_parser("komple", help="adb backup + tüm /sdcard + APK'lar + SHA-256 listesi")
    k.add_argument("--ab-yok", action="store_true", help="adb backup adımını atla")
    k.add_argument("--sdcard-yok", action="store_true", help="Dahili depolamayı çekme")
    k.add_argument("--apk-yok", action="store_true", help="APK'ları çekme")
    k.add_argument("--ozet-yok", action="store_true", help="SHA-256 listesi oluşturma")

    z = alt.add_parser("oxygen", help="Çekilmiş WhatsApp yedeğinden hesap başına Oxygen ZIP'i üret")
    z.add_argument("klasor", type=Path, help="whatsapp komutunun oluşturduğu yedek klasörü")

    a = ap.parse_args()

    if a.komut == "incele":
        import ambar_incele
        ambar_incele.calistir(a.hedef, port=a.port)
        return
    if a.komut == "oxygen":
        oxygen_ziple(a.klasor)
        return
    if a.komut == "ab2tar":
        ab_to_tar(a.dosya, a.cikti)
        return
    if a.komut == "notlar" and a.dosya:
        notlari_cek(a.hedef, a.dosya)
        return

    ADB = adb_bul()
    print(f"adb: {ADB}")
    if a.komut == "backup":
        adb_backup(a.hedef, a.paket, apk=not a.noapk, paylasilan=not a.noshared, sistem=a.system)
    elif a.komut == "whatsapp":
        whatsapp_cek(a.hedef, a.kaynak)
    elif a.komut == "notlar":
        notlari_cek(a.hedef, yeni=a.yeni)
    elif a.komut == "kayitlar":
        kayitlar_cek(a.hedef)
    elif a.komut == "agent":
        import ambar_agent
        ambar_agent.agent_cek(a.hedef, dosyalar=a.dosyalar)
    elif a.komut == "komple":
        komple_yedek(a.hedef, ab_al=not a.ab_yok, sdcard=not a.sdcard_yok,
                     apk=not a.apk_yok, ozet=not a.ozet_yok)
    else:
        menu(a.hedef)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nİptal edildi.")
