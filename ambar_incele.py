#!/usr/bin/env python3
"""
Ambar İnceleme - alınan yedekleri tarayıcıda incelemek için yerel sunucu.

- Yedek klasörlerini tarar, cihaza göre gruplar (komple / whatsapp / kayitlar / notlar)
- Verileri SQLite dizinine alır (%LOCALAPPDATA%\\Ambar\\incele). Delil klasörlerine YAZILMAZ.
- Arayüz: incele/index.html (tek sayfa uygulama), yalnızca 127.0.0.1 üzerinden sunulur.
"""
import datetime
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import ambar as core

CALISMA = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Ambar" / "incele"
ARAYUZ = core.PAKET_DIR / "incele" / "index.html"
KILIT = threading.Lock()
DIZIN_SURUM = 6                    # dizin şeması değişince önbellekteki eski dizinler yeniden oluşturulur
RAPOR_DOSYA = "rapor.ambar"        # portable rapor veritabanı
GORUCU_EXE = "AmbarIncele.exe"     # yalnızca inceleme yapan portable program

# ----------------------------------------------------------------- sınıflandırma
KATEGORI = {
    "Görüntü": {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "dng"},
    "Video": {"mp4", "3gp", "mkv", "mov", "avi", "webm", "m4v"},
    "Ses": {"mp3", "m4a", "aac", "opus", "ogg", "wav", "amr", "flac", "3ga"},
    "Belge": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "rtf", "odt", "json", "xml", "html"},
    "Arşiv": {"zip", "rar", "7z", "tar", "gz", "ab", "bak"},
    "Uygulama": {"apk", "apks", "xapk", "split"},
    "Veritabanı": {"db", "sqlite", "crypt12", "crypt14", "crypt15", "wal", "shm"},
}
UZANTI_KAT = {u: k for k, s in KATEGORI.items() for u in s}

UYGULAMA_ADLARI = {
    "com.whatsapp": "WhatsApp Messenger", "com.whatsapp.w4b": "WhatsApp Business",
    "org.telegram.messenger": "Telegram", "com.instagram.android": "Instagram",
    "com.facebook.katana": "Facebook", "com.facebook.orca": "Messenger",
    "com.zhiliaoapp.musically": "TikTok", "com.ss.android.ugc.trill": "TikTok",
    "com.snapchat.android": "Snapchat", "com.twitter.android": "X (Twitter)",
    "com.google.android.gm": "Gmail", "com.google.android.youtube": "YouTube",
    "com.android.chrome": "Chrome", "com.mi.globalbrowser": "Mi Browser",
    "com.sec.android.app.sbrowser": "Samsung Internet", "com.miui.gallery": "Galeri",
    "com.sec.android.gallery3d": "Galeri", "com.lenovo.anyshare.gps": "SHAREit",
    "com.spotify.music": "Spotify", "com.android.vending": "Play Store",
    "com.google.android.apps.photos": "Google Fotoğraflar", "com.google.android.apps.maps": "Haritalar",
    "com.viber.voip": "Viber", "com.skype.raider": "Skype", "us.zoom.videomeetings": "Zoom",
    "com.discord": "Discord", "com.linkedin.android": "LinkedIn", "com.pinterest": "Pinterest",
    "com.sahibinden": "sahibinden.com", "com.trendyol.app": "Trendyol", "com.getir": "Getir",
    "com.google.android.apps.messaging": "Mesajlar", "com.samsung.android.messaging": "Mesajlar",
    "com.android.camera": "Kamera", "com.sec.android.app.camera": "Kamera",
    "com.miui.notes": "Notlar", "com.samsung.android.app.notes": "Samsung Notes",
}
CAGRI_TUR = {"1": "Gelen", "2": "Giden", "3": "Cevapsız", "4": "Sesli mesaj", "5": "Reddedilen",
             "6": "Engellenen", "7": "Başka cihazda cevaplandı"}
SMS_TUR = {"1": "Gelen", "2": "Giden", "3": "Taslak", "4": "Giden kutusu", "5": "Gönderilemedi", "6": "Kuyrukta"}
MMS_KUTU = {"1": "Gelen", "2": "Giden", "3": "Taslak", "4": "Giden kutusu"}
TUR_ADI = {"komple": "Komple yedek", "whatsapp": "WhatsApp yedeği", "kayitlar": "Çağrı ve mesaj kayıtları",
           "notlar": "Notlar yedeği", "sim": "SIM kart (telefondan)", "uicc": "SIM kart imajı (UICC)",
           "agent": "Android Agent"}

AD_DESENI = re.compile(r"^(?:(?P<model>.+?)_)?(?P<tur>komple|whatsapp|kayitlar|notlar|sim|uicc|agent)_(?P<ts>\d{8}_\d{6})$")


def _id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def uygulama_adi(paket: str) -> str:
    if paket in UYGULAMA_ADLARI:
        return UYGULAMA_ADLARI[paket]
    son = paket.split(".")[-1]
    return son[:1].upper() + son[1:]


def numara_anahtari(n: str | None) -> str:
    """Telefon numarasını karşılaştırma için normalleştirir (son 10 hane)."""
    if not n:
        return ""
    rakam = re.sub(r"\D", "", n)
    return rakam[-10:] if len(rakam) >= 10 else (rakam or n.strip().lower())


def _getprop_oku(p: Path) -> dict:
    d = {}
    if p.exists():
        for m in re.finditer(r"^\[([^\]]+)\]: \[(.*)\]$", p.read_text(encoding="utf-8", errors="replace"), re.M):
            d[m.group(1)] = m.group(2)
    return d


def _json(p: Path, varsayilan=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return varsayilan


# ----------------------------------------------------------------- yedek keşfi
def cikarmalari_bul(kok: Path) -> list[dict]:
    """Kök altındaki (2 seviye) Ambar yedek klasörlerini bulur."""
    bulunan = []
    adaylar = []
    if kok.is_dir():
        for p in kok.iterdir():
            if p.is_dir():
                adaylar.append(p)
                if not AD_DESENI.match(p.name) and not (p / "ambar_bilgi.json").exists():
                    adaylar += [q for q in p.iterdir() if q.is_dir()]
    for p in adaylar:
        m = AD_DESENI.match(p.name)
        man = _json(p / "ambar_bilgi.json", {})
        if not m and not man:
            continue
        tur = man.get("tur") or m.group("tur")
        cihaz = dict(man.get("cihaz") or {})
        if tur == "komple" and not cihaz.get("model"):
            gp = _getprop_oku(p / "cihaz_bilgisi" / "getprop.txt")
            cihaz.update({k: v for k, v in {
                "marka": gp.get("ro.product.manufacturer"), "model": gp.get("ro.product.model"),
                "ticari_ad": gp.get("ro.product.marketname"), "cihaz_kodu": gp.get("ro.product.device"),
                "android": gp.get("ro.build.version.release"), "sdk": gp.get("ro.build.version.sdk"),
                "guvenlik_yamasi": gp.get("ro.build.version.security_patch"),
                "yapi": gp.get("ro.build.display.id"), "parmak_izi": gp.get("ro.build.fingerprint"),
                "saat_dilimi": gp.get("persist.sys.timezone"), "seri": gp.get("ro.serialno"),
            }.items() if v})
        if not cihaz.get("model") and m and m.group("model"):
            cihaz["model"] = m.group("model")
        tarih = man.get("baslangic") or man.get("tarih")
        if not tarih and m:
            tarih = datetime.datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S").isoformat()
        bitis, tahmini = man.get("bitis"), False
        if not bitis:   # bitişi kaydetmeyen eski sürümler: yedeğin en son yazılan dosyası (SHA-256 listesi vb.)
            try:
                son = max((q.stat().st_mtime for q in p.iterdir() if q.is_file()), default=None)
            except OSError:
                son = None
            if son:
                bitis, tahmini = datetime.datetime.fromtimestamp(son).isoformat(timespec="seconds"), True
        sure = man.get("sure_sn")
        if sure is None and tarih and bitis:
            try:
                sure = max(0, int((datetime.datetime.fromisoformat(bitis) -
                                   datetime.datetime.fromisoformat(tarih)).total_seconds()))
            except ValueError:
                sure = None
        anahtar = cihaz.get("seri") or cihaz.get("model") or "bilinmeyen"
        bulunan.append({
            "id": _id(str(p.resolve())), "yol": str(p.resolve()), "ad": p.name, "tur": tur,
            "tur_adi": TUR_ADI.get(tur, tur), "tarih": tarih, "bitis": bitis, "bitis_tahmini": tahmini,
            "sure_sn": sure, "cihaz": cihaz, "cihaz_anahtar": anahtar,
            "ambar_surum": man.get("ambar_surum"), "sha256": (p / "SHA256_tum_dosyalar.txt").exists()
            or (p / "SHA256.txt").exists(),
        })
    return sorted(bulunan, key=lambda c: c["tarih"] or "")


def cihazlari_grupla(cikarmalar: list[dict]) -> list[dict]:
    gruplar: dict[str, dict] = {}
    for c in cikarmalar:
        g = gruplar.setdefault(c["cihaz_anahtar"], {"id": _id(c["cihaz_anahtar"]), "cikarmalar": [], "cihaz": {}})
        g["cikarmalar"].append(c)
        for k, v in c["cihaz"].items():      # en dolu bilgi kazanır
            g["cihaz"].setdefault(k, v)
    for g in gruplar.values():
        d = g["cihaz"]
        g["baslik"] = d.get("ticari_ad") or " ".join(x for x in (d.get("marka", "").title(), d.get("model")) if x) \
            or "Bilinmeyen cihaz"
        g["son_tarih"] = max((c["tarih"] or "") for c in g["cikarmalar"])
    return sorted(gruplar.values(), key=lambda g: g["son_tarih"], reverse=True)


# ----------------------------------------------------------------- dizin (SQLite)
SIM_SEMA = """
CREATE TABLE IF NOT EXISTS sim_kartlari(id INTEGER PRIMARY KEY, cikarma TEXT, sub_id TEXT, durum TEXT, operator TEXT,
    gorunen_ad TEXT, numara TEXT, iccid TEXT, imsi TEXT, mcc_mnc TEXT, ulke TEXT, tur TEXT, wifi_arama TEXT,
    kart TEXT, kaynak TEXT);
CREATE TABLE IF NOT EXISTS sim_rehberi(id INTEGER PRIMARY KEY, cikarma TEXT, sim TEXT, liste TEXT, ad TEXT,
    numara TEXT, eposta TEXT, ek_numara TEXT, anahtar TEXT);
CREATE TABLE IF NOT EXISTS simgeler(paket TEXT PRIMARY KEY, tur TEXT, veri BLOB);
CREATE TABLE IF NOT EXISTS rehber(id INTEGER PRIMARY KEY, cikarma TEXT, kisi_id TEXT, ad TEXT, numaralar TEXT,
    epostalar TEXT, kurum TEXT, adres TEXT, notu TEXT, dogum_gunu TEXT, hesap TEXT, favori INTEGER, silinmis INTEGER,
    foto TEXT, son_iletisim INTEGER, iletisim_sayisi INTEGER);
CREATE TABLE IF NOT EXISTS rehber_numara(anahtar TEXT, numara TEXT, ad TEXT);
CREATE TABLE IF NOT EXISTS takvim(id INTEGER PRIMARY KEY, cikarma TEXT, baslik TEXT, baslangic INTEGER, bitis INTEGER,
    tum_gun INTEGER, konum TEXT, aciklama TEXT, takvim TEXT, katilimcilar TEXT, hatirlatma TEXT, tekrar TEXT,
    silinmis INTEGER);
CREATE TABLE IF NOT EXISTS sim_dosyalari(id INTEGER PRIMARY KEY, cikarma TEXT, iccid TEXT, yol TEXT, ad TEXT,
    aciklama TEXT, boyut INTEGER, deger TEXT, ham TEXT);
"""


def sema_tamamla(db) -> bool:
    """Eski dizin / rapor veritabanında SIM tabloları ve mesajlar.sim kolonu yoksa True."""
    tablolar = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    kolon = {r[1] for r in db.execute("PRAGMA table_info(mesajlar)")}
    return not {"sim_kartlari", "sim_rehberi", "sim_dosyalari", "simgeler", "rehber", "rehber_numara",
                "takvim"} <= tablolar or "sim" not in kolon


def sema_ekle(db):
    db.executescript(SIM_SEMA)
    if "sim" not in {r[1] for r in db.execute("PRAGMA table_info(mesajlar)")}:
        db.execute("ALTER TABLE mesajlar ADD COLUMN sim TEXT")


SEMA = SIM_SEMA + """
CREATE TABLE meta(anahtar TEXT PRIMARY KEY, deger TEXT);
CREATE TABLE dosyalar(id INTEGER PRIMARY KEY, cikarma TEXT, yol TEXT, ad TEXT, uzanti TEXT, kategori TEXT,
                      boyut INTEGER, mtime INTEGER, uygulama TEXT, hesap TEXT);
CREATE TABLE cagrilar(id INTEGER PRIMARY KEY, cikarma TEXT, tarih INTEGER, sure INTEGER, tur TEXT, numara TEXT,
                      isim TEXT, konum TEXT, sim TEXT, anahtar TEXT);
CREATE TABLE mesajlar(id INTEGER PRIMARY KEY, cikarma TEXT, kaynak TEXT, konusma TEXT, tarih INTEGER, yon TEXT,
                      okundu TEXT, numara TEXT, metin TEXT, anahtar TEXT, sim TEXT);
CREATE TABLE notlar(id INTEGER PRIMARY KEY, cikarma TEXT, olusturma INTEGER, degistirme INTEGER, metin TEXT);
CREATE TABLE uygulamalar(paket TEXT PRIMARY KEY, ad TEXT, surum TEXT, yukleyen TEXT, uid TEXT, ucuncu INTEGER,
                         apk INTEGER, apk_yol TEXT, surum_adi TEXT, ilk_yukleme INTEGER, guncelleme INTEGER);
CREATE TABLE hesaplar(id INTEGER PRIMARY KEY, ad TEXT, tur TEXT, kaynak TEXT);
CREATE INDEX i_dosya_kat ON dosyalar(kategori);
CREATE UNIQUE INDEX u_sim ON sim_kartlari(COALESCE(sub_id,''), COALESCE(iccid,''));
CREATE UNIQUE INDEX u_simr ON sim_rehberi(liste, COALESCE(ad,''), COALESCE(numara,''));
CREATE INDEX i_dosya_app ON dosyalar(uygulama);
CREATE INDEX i_cagri_t ON cagrilar(tarih);
CREATE INDEX i_mesaj_t ON mesajlar(tarih);
-- Aynı cihazın birden fazla çıkarmasında aynı kayıt tekrar sayılmasın
CREATE UNIQUE INDEX u_cagri ON cagrilar(tarih, COALESCE(numara,''), COALESCE(sure,0), COALESCE(tur,''));
CREATE UNIQUE INDEX u_mesaj ON mesajlar(kaynak, tarih, COALESCE(numara,''), COALESCE(metin,''));
CREATE UNIQUE INDEX u_not ON notlar(COALESCE(degistirme,0), COALESCE(metin,''));
"""


def _ms(s: str | None):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def _not_tarihi(s: str | None):
    try:
        return int(datetime.datetime.strptime(s, "%d.%m.%Y %H:%M").timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _dosya_uygulamasi(rel: str):
    """Dosya yolundan ait olduğu uygulamayı ve WhatsApp hesabını çıkarır."""
    parca = rel.replace("\\", "/").split("/")
    paket, hesap = None, None
    for i, x in enumerate(parca[:-1]):
        if x in ("data", "media", "obb") and i > 0 and parca[i - 1] == "Android" and i + 1 < len(parca) - 1:
            paket = parca[i + 1]
            break
        if x.startswith("sdcard_Android_media_"):
            paket = x[len("sdcard_Android_media_"):]
            break
    else:
        low = rel.lower()
        if "/whatsapp/" in "/" + low:
            paket = "com.whatsapp"
    if paket and "accounts" in parca:
        j = parca.index("accounts")
        if j + 1 < len(parca) - 1:
            hesap = parca[j + 1]
    return paket, hesap


EPOSTA = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
HESAP = re.compile(r"Account \{name=([^,]+), type=([^}]+)\}")


def maske_coz(ad: str, adaylar: dict):
    """'ahmet***5@gmail.com' gibi maskeli adı, başka kaynaklardaki açık adreslerle eşleştirir.
    Tek bir aday eşleşirse (tam adres, kaynak) döner; aksi halde (None, None)."""
    if "*" not in ad:
        return None, None
    desen = re.compile(".+".join(re.escape(x) for x in re.split(r"\*+", ad)), re.I)
    bulunan = {}
    for aday, kaynak in adaylar.items():
        if desen.fullmatch(aday):
            bulunan.setdefault(aday.lower(), (aday, kaynak))
    return next(iter(bulunan.values())) if len(bulunan) == 1 else (None, None)


def _hesaplari_yaz(db, hesaplar: list, adaylar: dict):
    gorulen = set()
    for ad, tur, dosya in hesaplar:
        kaynak = dosya
        if "*" in ad:
            tam, nereden = maske_coz(ad, adaylar)
            if tam:
                ad, kaynak = tam, f"{dosya} (maske çözüldü: {nereden})"
            else:
                kaynak = f"{dosya} (cihaz maskeledi)"
        if (ad.lower(), tur) in gorulen:
            continue
        gorulen.add((ad.lower(), tur))
        db.execute("INSERT INTO hesaplar(ad,tur,kaynak) VALUES(?,?,?)", (ad, tur, kaynak))


def _paket_zamani(s: str | None):
    """'2026-06-12 12:10:10' -> ms. Android ön yüklü (sistem imajı) uygulamalar için 1970 bildirir:
    bu durumda 0 döner (arayüz "Ön yüklü" / "Fabrika sürümü" yazar); alan yoksa None."""
    try:
        t = datetime.datetime.strptime(s.strip(), "%Y-%m-%d %H:%M:%S")
    except (AttributeError, ValueError):
        return None
    return int(t.timestamp() * 1000) if t.year >= 1980 else 0


def paket_detaylari(metin: str) -> dict:
    """'dumpsys package packages' çıktısından paket -> (sürüm adı, ilk yükleme, son güncelleme).
    Android 14+ firstInstallTime'ı 'User 0:' bloğunda verir; eski sürümlerde paket düzeyindedir."""
    metin = metin.split("\nHidden system packages:", 1)[0]
    sonuc = {}
    for parca in re.split(r"\n  Package \[", metin)[1:]:
        paket = parca.split("]", 1)[0]
        bul = lambda k: (re.search(rf"\b{k}=([^\r\n]+)", parca) or [None, None])[1]
        sonuc[paket] = (bul("versionName"), _paket_zamani(bul("firstInstallTime")),
                        _paket_zamani(bul("lastUpdateTime")))
    return sonuc


SIM_LISTE_ADI = {"adn": "Rehber (ADN)", "fdn": "Sabit arama (FDN)", "sdn": "Servis numarası (SDN)",
                 "lnd": "Son aranan (LND)", "msisdn": "Kendi numarası (MSISDN)"}


def sim_abonelikleri(metin: str) -> list[dict]:
    """'dumpsys isub' çıktısındaki abonelik kayıtları (Android 14+: SubscriptionInfoInternal, eski: {id=...})."""
    kayit = {}
    for m in re.finditer(r"(?:SubscriptionInfoInternal: |\{)(id=\d+ [^\n]*?)[\]\}]\s*$", metin, re.M):
        alan = dict(re.findall(r"(\w+)=(.*?)(?= \w+=|$)", m.group(1)))
        for k in ("mcc", "mnc", "countryIso", "simSlotIndex", "isEmbedded"):
            if alan.get(k):
                alan[k] = (alan[k].split() or [""])[0]
        kayit.setdefault(alan["id"], alan)
    return list(kayit.values())


def _sim_satiri(a: dict, kaynak: str):
    bos = lambda v: None if v in (None, "", "null", "-1") else v
    yuva = _ms(a.get("simSlotIndex"))
    maskeli = any("[****]" in (a.get(k) or "") or "***" in (a.get(k) or "") for k in ("iccId", "imsi", "number"))
    return (bos(a.get("id")), f"Takılı · yuva {yuva + 1}" if yuva is not None and yuva >= 0 else "Çıkarılmış (geçmiş kayıt)",
            bos(a.get("carrierName")), bos(a.get("displayName")),
            bos(a.get("number")) or bos(a.get("numberFromIms")) or bos(a.get("numberFromCarrier")),
            bos(a.get("iccId")), bos(a.get("imsi")),
            "-".join(x for x in (a.get("mcc"), a.get("mnc")) if x) or None, bos((a.get("countryIso") or "").upper()),
            "eSIM" if a.get("isEmbedded") in ("1", "true") else "Fiziksel SIM",
            {"1": "Açık", "0": "Kapalı"}.get(a.get("isWifiCallingEnabled")), bos(a.get("cardString")),
            kaynak + (" (Android maskeledi)" if maskeli else ""))


def _sim_yaz(db, cid, kok: Path):
    """Telefondan alınan SIM bilgisi (kayitlar/sim_*) ve kart okuyucudan alınan SIM imajı (uicc.json)."""
    k = kok / "kayitlar"
    if (k / "sim_abonelikler.txt").exists():
        for a in sim_abonelikleri((k / "sim_abonelikler.txt").read_text(encoding="utf-8", errors="replace")):
            db.execute("INSERT OR REPLACE INTO sim_kartlari(cikarma,sub_id,durum,operator,gorunen_ad,numara,iccid,imsi,"
                       "mcc_mnc,ulke,tur,wifi_arama,kart,kaynak) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (cid, *_sim_satiri(a, "kayitlar/sim_abonelikler.txt")))
    sim = _json(k / "sim.json", {}) or {}
    for r in sim.get("rehber") or []:
        db.execute("INSERT OR IGNORE INTO sim_rehberi(cikarma,sim,liste,ad,numara,eposta,ek_numara,anahtar) VALUES(?,?,?,?,?,?,?,?)",
                   (cid, r.get("sub_id"), SIM_LISTE_ADI.get(r.get("liste"), r.get("liste")), r.get("name"),
                    r.get("number"), r.get("emails"), r.get("anrs"), numara_anahtari(r.get("number"))))
    for r in sim.get("sms") or []:
        db.execute("INSERT OR IGNORE INTO mesajlar(cikarma,kaynak,konusma,tarih,yon,okundu,numara,metin,anahtar,sim)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cid, "SIM SMS", None, _ms(r.get("date")), SMS_TUR.get(r.get("type"), r.get("type")),
                    {"1": "Evet", "3": "Hayır"}.get(r.get("status")), r.get("address"), r.get("body"),
                    numara_anahtari(r.get("address")), r.get("sub_id")))
    # Kart okuyucu imajı (ambar_sim.py)
    u = _json(kok / "uicc.json", None)
    if not u:
        return
    o = u.get("ozet") or {}
    iccid = o.get("iccid")
    db.execute("INSERT OR REPLACE INTO sim_kartlari(cikarma,sub_id,durum,operator,gorunen_ad,numara,iccid,imsi,"
               "mcc_mnc,ulke,tur,wifi_arama,kart,kaynak) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (cid, None, "Kart okuyucudan imaj", o.get("spn"), o.get("spn"), o.get("msisdn"), iccid, o.get("imsi"),
                o.get("mcc_mnc"), None, "Fiziksel SIM (" + (u.get("tip") or "SIM") + ")", None, None, "uicc.json"))
    etiket = f"Kart {iccid[-6:]}" if iccid else "Kart"
    for r in u.get("rehber") or []:
        db.execute("INSERT OR IGNORE INTO sim_rehberi(cikarma,sim,liste,ad,numara,eposta,ek_numara,anahtar) VALUES(?,?,?,?,?,?,?,?)",
                   (cid, etiket, SIM_LISTE_ADI.get(r.get("liste"), r.get("liste")), r.get("ad"), r.get("numara"),
                    None, None, numara_anahtari(r.get("numara"))))
    for r in u.get("sms") or []:
        db.execute("INSERT OR IGNORE INTO mesajlar(cikarma,kaynak,konusma,tarih,yon,okundu,numara,metin,anahtar,sim)"
                   " VALUES(?,?,?,?,?,?,?,?,?,?)",
                   (cid, "SIM SMS", None, r.get("tarih"), r.get("yon"), r.get("okundu"), r.get("numara"), r.get("metin"),
                    numara_anahtari(r.get("numara")), etiket))
    for d in u.get("dosyalar") or []:
        db.execute("INSERT INTO sim_dosyalari(cikarma,iccid,yol,ad,aciklama,boyut,deger,ham) VALUES(?,?,?,?,?,?,?,?)",
                   (cid, iccid, d.get("yol"), d.get("ad"), d.get("aciklama"), d.get("boyut"), d.get("deger"), d.get("ham")))


SIMGE_TUR = {"svg": "image/svg+xml", "png": "image/png", "webp": "image/webp", "jpg": "image/jpeg"}


def _simgeleri_yaz(db, kok: Path, durum=None):
    """Veri alırken çıkarılan simgeler (simgeler/) + yedekteki APK'lardan çıkarılabilenler. Yeni çıkarma eskisini ezer.
    Dönüş: paket -> uygulamanın görünen adı (APK'dan okunan)."""
    etiket = {}
    for alt in ("cihaz_bilgisi", "kayitlar"):
        etiket.update(_json(kok / alt / "simgeler" / "etiketler.json", {}) or {})
        for p in (kok / alt / "simgeler").glob("*.*") if (kok / alt / "simgeler").is_dir() else []:
            tur = SIMGE_TUR.get(p.suffix.lower().lstrip("."))
            if tur:
                db.execute("INSERT OR REPLACE INTO simgeler VALUES(?,?,?)", (p.stem, tur, p.read_bytes()))
    apk = kok / "apk"
    if apk.is_dir():
        import ambar_simge
        var = {r[0] for r in db.execute("SELECT paket FROM simgeler")}
        for k in sorted(apk.iterdir()):
            if k.is_dir() and k.name not in var:
                if durum:
                    durum(f"Simge: {k.name}")
                s, e = ambar_simge.yerel_apk_simgesi(sorted(k.glob("*.apk")))
                if s:
                    db.execute("INSERT OR REPLACE INTO simgeler VALUES(?,?,?)", (k.name, s[0], s[1]))
                if e:
                    etiket.setdefault(k.name, e)
    return etiket


def _agent_yaz(db, cid, k: Path):
    for r in _json(k / "rehber.json", []) or []:
        cur = db.execute("INSERT INTO rehber(cikarma,kisi_id,ad,numaralar,epostalar,kurum,adres,notu,dogum_gunu,hesap,"
                         "favori,silinmis,foto,son_iletisim,iletisim_sayisi) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (cid, r.get("id"), r.get("ad"), "\n".join(r.get("numaralar") or []),
                          "\n".join(r.get("epostalar") or []), r.get("kurum"), "\n".join(r.get("adres") or []),
                          r.get("not"), r.get("dogum_gunu"), ", ".join(r.get("hesaplar") or []), int(bool(r.get("favori"))),
                          int(bool(r.get("silinmis"))), r.get("foto"), _ms(r.get("son_iletisim")) or None,
                          _ms(r.get("iletisim_sayisi"))))
        for n in r.get("numaralar") or []:
            db.execute("INSERT INTO rehber_numara VALUES(?,?,?)", (numara_anahtari(n), n, r.get("ad")))
    for r in _json(k / "takvim.json", []) or []:
        db.execute("INSERT INTO takvim(cikarma,baslik,baslangic,bitis,tum_gun,konum,aciklama,takvim,katilimcilar,"
                   "hatirlatma,tekrar,silinmis) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid, r.get("baslik"), _ms(r.get("baslangic")), _ms(r.get("bitis")), int(bool(r.get("tum_gun"))),
                    r.get("konum"), r.get("aciklama"), r.get("takvim"), "\n".join(r.get("katilimcilar") or []),
                    ", ".join(r.get("hatirlatmalar") or []), r.get("tekrar"), int(bool(r.get("silinmis")))))


def _sim_etiketle(db):
    """Çağrı / mesaj / SIM rehberindeki abonelik numarasını 'Operatör (#1)' biçimine çevirir."""
    for sid, op, ad, icc in db.execute("SELECT sub_id, operator, gorunen_ad, iccid FROM sim_kartlari "
                                       "WHERE sub_id IS NOT NULL").fetchall():
        etiket = f"{op or ad or 'SIM'} (#{sid})"
        for tablo in ("cagrilar", "mesajlar", "sim_rehberi"):
            db.execute(f"UPDATE {tablo} SET sim=? WHERE sim=?", (etiket, sid))
            on = (icc or "").split("[")[0]
            if len(on) >= 6:    # eski Android: çağrı kaydında abonelik yerine ICCID
                db.execute(f"UPDATE {tablo} SET sim=? WHERE length(sim) >= 15 AND sim LIKE ?", (etiket, on + "%"))


def dizin_olustur(db: sqlite3.Connection, cikarmalar: list[dict], durum=None):
    db.executescript(SEMA)
    hesaplar, adaylar = [], {}
    for c in cikarmalar:
        kok = Path(c["yol"])
        cid = c["id"]
        if durum:
            durum(f"Taranıyor: {c['ad']}")
        # --- dosyalar (yalnızca veri klasörleri)
        satirlar = []
        for yol, klasorler, dosyalar in os.walk(kok):
            if Path(yol) == kok:   # Ambar'ın ürettiği klasörler telefon dosyası değildir
                klasorler[:] = [k for k in klasorler if k not in ("cihaz_bilgisi", "kayitlar", "agent")]
            for d in dosyalar:
                p = Path(yol) / d
                rel = p.relative_to(kok).as_posix()
                if rel in ("ambar_bilgi.json", "SHA256.txt", "SHA256_tum_dosyalar.txt", "cekilemeyenler.txt",
                           "uicc.json", "uicc_apdu.txt"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                uz = p.suffix.lower().lstrip(".")
                paket, hesap = _dosya_uygulamasi(rel)
                satirlar.append((cid, rel, d, uz, UZANTI_KAT.get(uz, "Diğer"), st.st_size,
                                 int(st.st_mtime * 1000), paket, hesap))
                if len(satirlar) >= 5000:
                    db.executemany("INSERT INTO dosyalar(cikarma,yol,ad,uzanti,kategori,boyut,mtime,uygulama,hesap)"
                                   " VALUES(?,?,?,?,?,?,?,?,?)", satirlar)
                    satirlar = []
        db.executemany("INSERT INTO dosyalar(cikarma,yol,ad,uzanti,kategori,boyut,mtime,uygulama,hesap)"
                       " VALUES(?,?,?,?,?,?,?,?,?)", satirlar)

        # --- çağrılar / mesajlar
        k = kok / "kayitlar"
        for r in _json(k / "cagrilar.json", []) or []:
            db.execute("INSERT OR IGNORE INTO cagrilar(cikarma,tarih,sure,tur,numara,isim,konum,sim,anahtar) VALUES(?,?,?,?,?,?,?,?,?)",
                       (cid, _ms(r.get("date")), _ms(r.get("duration")), CAGRI_TUR.get(r.get("type"), r.get("type")),
                        r.get("number"), r.get("name"), r.get("geocoded_location"), r.get("subscription_id"),
                        numara_anahtari(r.get("number"))))
        for r in _json(k / "sms.json", []) or []:
            db.execute("INSERT OR IGNORE INTO mesajlar(cikarma,kaynak,konusma,tarih,yon,okundu,numara,metin,anahtar,sim) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (cid, "SMS", r.get("thread_id"), _ms(r.get("date")), SMS_TUR.get(r.get("type"), r.get("type")),
                        "Evet" if r.get("read") == "1" else "Hayır", r.get("address"), r.get("body"),
                        numara_anahtari(r.get("address")), r.get("sub_id")))
        for r in _json(k / "mms.json", []) or []:
            t = _ms(r.get("date"))
            db.execute("INSERT OR IGNORE INTO mesajlar(cikarma,kaynak,konusma,tarih,yon,okundu,numara,metin,anahtar) VALUES(?,?,?,?,?,?,?,?,?)",
                       (cid, "MMS", r.get("thread_id"), t * 1000 if t and t < 10**11 else t,
                        MMS_KUTU.get(r.get("msg_box"), r.get("msg_box")), "Evet" if r.get("read") == "1" else "Hayır",
                        r.get("address"), r.get("body") or r.get("sub"), numara_anahtari((r.get("address") or "").split(",")[0])))
        # --- SIM kart (telefondan abonelik geçmişi / kart okuyucudan imaj)
        _sim_yaz(db, cid, kok)
        # --- rehber ve takvim (Android Agent)
        _agent_yaz(db, cid, k)
        # --- hesaplar (dumpsys account) ve maske çözümü için aday adresler
        for alt in ("kayitlar", "cihaz_bilgisi"):
            hp = kok / alt / "hesaplar.txt"
            if hp.exists():
                metin = hp.read_text(encoding="utf-8", errors="replace")
                hesaplar += [(a.strip(), t.strip(), f"{alt}/hesaplar.txt") for a, t in HESAP.findall(metin)]
            klasor = kok / alt
            if klasor.is_dir():
                for tp in klasor.glob("*.txt"):
                    for e in EPOSTA.findall(tp.read_text(encoding="utf-8", errors="replace")):
                        adaylar.setdefault(e, f"{alt}/{tp.name}")
        for (yol,) in db.execute("SELECT yol FROM dosyalar WHERE cikarma=? AND yol LIKE '%@%'", (cid,)):
            for e in EPOSTA.findall(yol.replace("/", " ")):
                adaylar.setdefault(e, "dosya yolu")
        # --- notlar
        for r in _json(kok / "notlar.json", []) or []:
            db.execute("INSERT OR IGNORE INTO notlar(cikarma,olusturma,degistirme,metin) VALUES(?,?,?,?)",
                       (cid, _not_tarihi(r.get("olusturma")), _not_tarihi(r.get("degistirme")), r.get("metin")))
        # --- uygulamalar (komple: cihaz_bilgisi/, çağrı-mesaj yedeği: kayitlar/). Yeni çıkarma eskisini ezer.
        cb = next((d for d in (kok / "cihaz_bilgisi", kok / "kayitlar") if (d / "paketler_tum.txt").exists()), None)
        etiket = _simgeleri_yaz(db, kok, durum)
        if cb:
            detay = {}
            if (cb / "paketler_detay.txt").exists():
                detay = paket_detaylari((cb / "paketler_detay.txt").read_text(encoding="utf-8", errors="replace"))
            ucuncu = set()
            if (cb / "paketler_3parti.txt").exists():
                ucuncu = {s.split(":", 1)[1].strip() for s in
                          (cb / "paketler_3parti.txt").read_text(encoding="utf-8", errors="replace").splitlines() if ":" in s}
            surumler = {}
            if (cb / "paketler_surum.txt").exists():
                for m in re.finditer(r"package:(\S+) versionCode:(\d+)",
                                     (cb / "paketler_surum.txt").read_text(encoding="utf-8", errors="replace")):
                    surumler[m.group(1)] = m.group(2)
            if (cb / "paketler_tum.txt").exists():
                for m in re.finditer(r"package:(\S+)=(\S+)\s+installer=(\S+)(?:\s+uid:(\d+))?",
                                     (cb / "paketler_tum.txt").read_text(encoding="utf-8", errors="replace")):
                    apk_yol, paket, yukleyen, uid = m.groups()
                    apk_klasor = kok / "apk" / paket
                    sad, ilk, gnc = detay.get(paket, (None, None, None))
                    db.execute("INSERT OR REPLACE INTO uygulamalar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (paket, etiket.get(paket) or uygulama_adi(paket), surumler.get(paket), None if yukleyen == "null" else yukleyen,
                                uid, 1 if paket in ucuncu else 0, 1 if apk_klasor.is_dir() else 0, apk_yol,
                                sad, ilk, gnc))
    _hesaplari_yaz(db, hesaplar, adaylar)
    _sim_etiketle(db)
    db.execute("INSERT INTO meta VALUES('olusturma', ?)", (datetime.datetime.now().isoformat(),))
    db.commit()


# ----------------------------------------------------------------- inceleme verisi (etiket / not)
def inceleme_db():
    CALISMA.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CALISMA / "inceleme.sqlite", check_same_thread=False)
    db.executescript("""CREATE TABLE IF NOT EXISTS etiketler(cihaz TEXT, tur TEXT, ref TEXT, baslik TEXT, etiket TEXT,
                        aciklama TEXT, tarih TEXT, PRIMARY KEY(cihaz, tur, ref));
                        CREATE TABLE IF NOT EXISTS cihaz_notlari(cihaz TEXT PRIMARY KEY, metin TEXT, tarih TEXT);
                        CREATE TABLE IF NOT EXISTS ayarlar(anahtar TEXT PRIMARY KEY, deger TEXT);
                        CREATE TABLE IF NOT EXISTS olay_bilgisi(cihaz TEXT PRIMARY KEY, deger TEXT, tarih TEXT);
                        CREATE TABLE IF NOT EXISTS wa_cozulen(cihaz TEXT, cikarma TEXT, yol TEXT, paket TEXT, hesap TEXT,
                            db_yol TEXT, sha256 TEXT, dogrulandi INTEGER, sohbet INTEGER, mesaj INTEGER, tarih TEXT,
                            PRIMARY KEY(cihaz, cikarma, yol));""")
    db.row_factory = sqlite3.Row      # _satirlar() sözlüğe çevirebilsin
    return db


# ----------------------------------------------------------------- uygulama durumu
class Durum:
    def __init__(self, kok: Path):
        self.kok = kok
        self.cihazlar: list[dict] = []
        self.dbler: dict[str, sqlite3.Connection] = {}
        self.ilerleme: dict[str, str] = {}
        self.idb = inceleme_db()
        self.yenile()

    def yenile(self):
        self.cihazlar = cihazlari_grupla(cikarmalari_bul(self.kok))

    def kok_degistir(self, yeni: Path):
        """İnceleme ekranından yedek klasörünü değiştirir (programı yeniden açmaya gerek kalmaz)."""
        yeni = Path(yeni)
        if not yeni.is_dir():
            raise KeyError(f"Klasör bulunamadı: {yeni}")
        with KILIT:
            for db in self.dbler.values():
                db.close()
            self.dbler.clear()
            self.ilerleme.clear()
            if hasattr(self, "_wa_medya"):
                self._wa_medya.clear()
            self.kok = yeni
            self.yenile()

    def cihaz(self, cid: str) -> dict:
        for g in self.cihazlar:
            if g["id"] == cid:
                return g
        raise KeyError("Cihaz bulunamadı")

    def cikarma(self, cid: str, kid: str) -> dict:
        for c in self.cihaz(cid)["cikarmalar"]:
            if c["id"] == kid:
                return c
        raise KeyError("Çıkarma bulunamadı")

    portable = False

    def dosya_koku(self, cid: str, kid: str) -> Path:
        return Path(self.cikarma(cid, kid)["yol"])

    def dosya_ac(self, cid: str, kid: str, rel: str):
        """(dosya nesnesi, boyut, mtime_ms). Yalnızca çıkarma klasörünün içindeki dosyalar (yol kaçışı engellenir)."""
        kok = self.dosya_koku(cid, kid).resolve()
        p = (kok / rel).resolve()
        if not p.is_relative_to(kok) or not p.is_file():
            raise FileNotFoundError(rel)
        st = p.stat()
        return open(p, "rb"), st.st_size, int(st.st_mtime * 1000)

    def db(self, cid: str, yeniden=False) -> sqlite3.Connection:
        with KILIT:
            g = self.cihaz(cid)
            imza = json.dumps([DIZIN_SURUM] + [(c["yol"], os.path.getmtime(c["yol"])) for c in g["cikarmalar"]])
            # Ad kök klasöre de bağlı: aynı cihazın farklı klasörlerdeki yedekleri birbirinin dizinini ezmesin
            yol = CALISMA / f"dizin_{_id(str(self.kok.resolve()) + cid)}.sqlite"
            if not yeniden and cid in self.dbler:
                return self.dbler[cid]
            if not yeniden and yol.exists():
                db = sqlite3.connect(yol, check_same_thread=False)
                try:
                    eski = db.execute("SELECT deger FROM meta WHERE anahtar='imza'").fetchone()
                    if eski and eski[0] == imza:
                        db.row_factory = sqlite3.Row
                        self.dbler[cid] = db
                        return db
                except sqlite3.Error:
                    pass
                db.close()
            if cid in self.dbler:
                self.dbler.pop(cid).close()
            CALISMA.mkdir(parents=True, exist_ok=True)
            gecici = yol.with_name(f"{yol.stem}_{os.getpid()}.tmp")
            gecici.unlink(missing_ok=True)
            db = sqlite3.connect(gecici, check_same_thread=False)
            dizin_olustur(db, g["cikarmalar"], durum=lambda m: self.ilerleme.__setitem__(cid, m))
            db.execute("INSERT INTO meta VALUES('imza', ?)", (imza,))
            db.commit()
            db.close()
            try:
                yol.unlink(missing_ok=True)
            except PermissionError:   # başka bir Ambar penceresi eski dizini açık tutuyor
                yol = yol.with_name(f"{yol.stem}_{os.getpid()}.sqlite")
                yol.unlink(missing_ok=True)
            gecici.replace(yol)
            self.ilerleme.pop(cid, None)
            db = sqlite3.connect(yol, check_same_thread=False)
            db.row_factory = sqlite3.Row
            self.dbler[cid] = db
            return db


class RaporDurum(Durum):
    """Portable rapor (rapor.ambar) üzerinden çalışır: tarama/dizinleme yok, veritabanı salt okunur.
    Etiket ve notlar rapor dosyasını değiştirmemek için yanındaki inceleme.sqlite'a yazılır."""
    portable = True

    def __init__(self, rapor: Path):
        import ambar_ambx
        self.rapor = Path(rapor).resolve()
        self.kok = self.rapor.parent
        self.ilerleme = {}
        self.dbler = {}
        self.kap = None
        if self.rapor.suffix.lower() == ambar_ambx.UZANTI:      # tek dosya biçimi
            self.kap = ambar_ambx.AmbxOkuyucu(self.rapor)
            self._db = sqlite3.connect(":memory:", check_same_thread=False)
            self._db.deserialize(self.kap.oku(RAPOR_DOSYA))
        else:                                                   # eski klasör biçimi (rapor.ambar + dosyalar/)
            self._db = sqlite3.connect(self.rapor.as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        if sema_tamamla(self._db):      # eski rapor: yeni tablolar bellekteki kopyaya eklenir (dosya değişmez)
            if not self.kap:
                bellek = sqlite3.connect(":memory:", check_same_thread=False)
                self._db.backup(bellek)
                self._db.close()
                self._db = bellek
            sema_ekle(self._db)
        self._db.row_factory = sqlite3.Row
        satir = self._db.execute("SELECT deger FROM meta WHERE anahtar='rapor'").fetchone()
        if not satir:
            raise ValueError("Bu dosya bir Ambar raporu değil.")
        self.meta = json.loads(satir[0])
        self.cihazlar = [self.meta["cihaz"]]
        self.idb = self._inceleme_db()

    def _inceleme_db(self):
        sema = """CREATE TABLE IF NOT EXISTS etiketler(cihaz TEXT, tur TEXT, ref TEXT, baslik TEXT, etiket TEXT,
                  aciklama TEXT, tarih TEXT, PRIMARY KEY(cihaz, tur, ref));
                  CREATE TABLE IF NOT EXISTS cihaz_notlari(cihaz TEXT PRIMARY KEY, metin TEXT, tarih TEXT);
                        CREATE TABLE IF NOT EXISTS ayarlar(anahtar TEXT PRIMARY KEY, deger TEXT);
                        CREATE TABLE IF NOT EXISTS olay_bilgisi(cihaz TEXT PRIMARY KEY, deger TEXT, tarih TEXT);
                        CREATE TABLE IF NOT EXISTS wa_cozulen(cihaz TEXT, cikarma TEXT, yol TEXT, paket TEXT, hesap TEXT,
                            db_yol TEXT, sha256 TEXT, dogrulandi INTEGER, sohbet INTEGER, mesaj INTEGER, tarih TEXT,
                            PRIMARY KEY(cihaz, cikarma, yol));"""
        ad = "inceleme.sqlite" if not self.kap else f"{self.rapor.stem}.inceleme.sqlite"
        adaylar = [self.kok / ad,
                   CALISMA / f"rapor_{_id(str(self.rapor))}.sqlite"]       # klasör yazılamıyorsa (CD, salt okunur)
        for yol in adaylar:
            try:
                yeni = not yol.exists()
                yol.parent.mkdir(parents=True, exist_ok=True)
                idb = sqlite3.connect(yol, check_same_thread=False)
                idb.executescript(sema)
                idb.row_factory = sqlite3.Row
                if yeni:   # rapordaki etiket ve notlarla başla
                    for tablo in ("etiketler", "cihaz_notlari", "olay_bilgisi"):
                        try:
                            satirlar = self._db.execute(f"SELECT * FROM {tablo}").fetchall()
                        except sqlite3.Error:
                            continue
                        if satirlar:
                            idb.executemany(f"INSERT OR REPLACE INTO {tablo} VALUES({','.join('?' * len(satirlar[0]))})",
                                            [tuple(r) for r in satirlar])
                    idb.commit()
                return idb
            except (sqlite3.Error, OSError):
                continue
        idb = sqlite3.connect(":memory:", check_same_thread=False)
        idb.executescript(sema)
        idb.row_factory = sqlite3.Row
        return idb

    def yenile(self):
        pass

    def dosya_koku(self, cid: str, kid: str) -> Path:
        self.cikarma(cid, kid)
        return (self.rapor if self.kap else self.kok) / "dosyalar" / kid

    def dosya_ac(self, cid: str, kid: str, rel: str):
        if not self.kap:
            return super().dosya_ac(cid, kid, rel)
        self.cikarma(cid, kid)
        return self.kap.ac(f"dosyalar/{kid}/{rel}")

    def db(self, cid: str, yeniden=False) -> sqlite3.Connection:
        self.cihaz(cid)
        return self._db


# ----------------------------------------------------------------- dışa aktarma yardımcıları (motor: ambar_rapor.py)
def gorucu_exe() -> Path | None:
    adaylar = [core.PAKET_DIR / GORUCU_EXE, core.SCRIPT_DIR / "dist" / GORUCU_EXE]
    if getattr(sys, "frozen", False) and Path(sys.executable).name.lower() == GORUCU_EXE.lower():
        adaylar.insert(0, Path(sys.executable))      # portable görüntüleyicinin kendisi
    return next((a for a in adaylar if a.exists()), None)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for parca in iter(lambda: f.read(1024 * 1024), b""):
            h.update(parca)
    return h.hexdigest()


def kopyala_akis(st, cid, kid, rel, hedef: Path) -> str:
    """Kaynaktaki (klasör veya .ambx) dosyayı hedefe kopyalar, SHA-256 döndürür; değiştirme zamanını korur."""
    f, _, mtime = st.dosya_ac(cid, kid, rel)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with f, open(hedef, "wb") as fo:
        for parca in iter(lambda: f.read(1024 * 1024), b""):
            h.update(parca)
            fo.write(parca)
    if mtime:
        os.utime(hedef, (mtime / 1000, mtime / 1000))
    return h.hexdigest()


def _kopyala_hash(kaynak: Path, hedef: Path) -> str:
    hedef.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    with open(kaynak, "rb") as fi, open(hedef, "wb") as fo:
        for parca in iter(lambda: fi.read(1024 * 1024), b""):
            h.update(parca)
            fo.write(parca)
    shutil.copystat(kaynak, hedef)
    return h.hexdigest()


# ----------------------------------------------------------------- API
def _satirlar(cur):
    return [dict(r) for r in cur.fetchall()]


def kisiler_sorgusu():
    """Rehber adb ile alınamadığı için kişiler çağrı + mesaj kayıtlarından türetilir."""
    return """
    WITH k AS (
      SELECT anahtar, numara, isim, tarih, 1 AS c, 0 AS m FROM cagrilar WHERE anahtar <> ''
      UNION ALL
      SELECT anahtar, numara, NULL, tarih, 0, 1 FROM mesajlar WHERE anahtar <> ''
      UNION ALL
      SELECT anahtar, numara, ad, NULL, 0, 0 FROM sim_rehberi WHERE anahtar <> ''
      UNION ALL
      SELECT anahtar, numara, ad, NULL, 0, 0 FROM rehber_numara WHERE anahtar <> ''
    )
    SELECT anahtar AS id,
           COALESCE(MAX(isim), '') AS isim,
           (SELECT numara FROM k k2 WHERE k2.anahtar = k.anahtar ORDER BY tarih DESC LIMIT 1) AS numara,
           SUM(c) AS cagri, SUM(m) AS mesaj, MIN(tarih) AS ilk, MAX(tarih) AS son
    FROM k GROUP BY anahtar"""


BOLUMLER = {
    # ad: (sql, kolonlar[(alan, başlık, tür)], aranacak alanlar, varsayılan sıralama)
    "cagrilar": ("SELECT id, tarih, tur, numara, isim, sure, konum, sim FROM cagrilar",
                 [("tarih", "Tarih", "tarih"), ("tur", "Tür", "rozet"), ("numara", "Numara", "metin"),
                  ("isim", "Kayıtlı ad", "metin"), ("sure", "Süre", "sure"), ("konum", "Konum", "metin"),
                  ("sim", "SIM", "metin")], ["numara", "isim", "konum", "tur"], "tarih DESC"),
    "mesajlar": ("SELECT id, tarih, kaynak, yon, numara, metin, okundu, sim, konusma, "
                 "COALESCE(NULLIF(anahtar, ''), '_') AS anahtar FROM mesajlar",
                 [("tarih", "Tarih", "tarih"), ("kaynak", "Kaynak", "rozet"), ("yon", "Yön", "rozet"),
                  ("numara", "Numara", "metin"), ("metin", "Mesaj", "uzun"), ("okundu", "Okundu", "metin"),
                  ("sim", "SIM", "metin")],
                 ["numara", "metin", "yon", "kaynak"], "tarih DESC"),
    "kisiler": (kisiler_sorgusu(),
                [("isim", "Ad", "metin"), ("numara", "Numara", "metin"), ("cagri", "Çağrı", "sayi"),
                 ("mesaj", "Mesaj", "sayi"), ("ilk", "İlk iletişim", "tarih"), ("son", "Son iletişim", "tarih")],
                ["isim", "numara"], "son DESC"),
    "dosyalar": ("SELECT id, ad, kategori, boyut, mtime, uygulama, hesap, yol, cikarma FROM dosyalar",
                 [("ad", "Ad", "dosya"), ("kategori", "Tür", "rozet"), ("boyut", "Boyut", "boyut"),
                  ("mtime", "Değiştirme", "tarih"), ("uygulama", "Uygulama", "metin"), ("yol", "Yol", "yol")],
                 ["ad", "yol", "uygulama"], "mtime DESC"),
    "uygulamalar": ("SELECT paket AS id, ad, paket, COALESCE(surum_adi, surum) AS surum, surum AS surum_kodu, "
                    "ilk_yukleme, guncelleme, yukleyen, uid, ucuncu, apk FROM uygulamalar",
                    [("ad", "Ad", "uygulama"), ("paket", "Paket", "metin"), ("surum", "Sürüm", "metin"),
                     ("ilk_yukleme", "İlk yüklenme", "kurulum"), ("guncelleme", "Son güncelleme", "kurulum"),
                     ("yukleyen", "Yükleyen", "metin"), ("ucuncu", "Kullanıcı uygulaması", "evet"),
                     ("apk", "APK alındı", "evet")], ["ad", "paket", "yukleyen"], "ucuncu DESC, ad"),
    "hesaplar": ("SELECT id, ad, tur, kaynak FROM hesaplar",
                 [("ad", "Hesap", "metin"), ("tur", "Tür", "metin"), ("kaynak", "Kaynak", "metin")],
                 ["ad", "tur"], "tur, ad"),
    "rehber": ("SELECT id, ad, numaralar, epostalar, kurum, adres, notu, dogum_gunu, hesap, "
               "CASE WHEN favori THEN 'Evet' END AS favori, CASE WHEN silinmis THEN 'Silinmiş' END AS silinmis, "
               "foto, son_iletisim, iletisim_sayisi, cikarma FROM rehber",
               [("ad", "Ad", "kisi"), ("numaralar", "Numara", "uzun"), ("epostalar", "E-posta", "uzun"),
                ("kurum", "Kurum", "metin"), ("adres", "Adres", "uzun"), ("notu", "Not", "uzun"),
                ("dogum_gunu", "Doğum günü", "metin"), ("hesap", "Hesap", "metin"), ("favori", "Favori", "metin"),
                ("silinmis", "Durum", "rozet"), ("son_iletisim", "Son iletişim", "tarih")],
               ["ad", "numaralar", "epostalar", "kurum", "adres", "notu"], "ad COLLATE NOCASE"),
    "takvim": ("SELECT id, baslangic, bitis, baslik, konum, aciklama, takvim, katilimcilar, hatirlatma, tekrar, "
               "CASE WHEN tum_gun THEN 'Tüm gün' END AS tum_gun, CASE WHEN silinmis THEN 'Silinmiş' END AS silinmis FROM takvim",
               [("baslangic", "Başlangıç", "tarih"), ("bitis", "Bitiş", "tarih"), ("baslik", "Başlık", "metin"),
                ("konum", "Konum", "metin"), ("aciklama", "Açıklama", "uzun"), ("takvim", "Takvim", "metin"),
                ("katilimcilar", "Katılımcılar", "uzun"), ("hatirlatma", "Hatırlatma", "metin"),
                ("tekrar", "Tekrar", "metin"), ("tum_gun", "Tüm gün", "metin"), ("silinmis", "Durum", "rozet")],
               ["baslik", "konum", "aciklama", "katilimcilar"], "baslangic DESC"),
    "sim_kartlari": ("SELECT id, durum, operator, gorunen_ad, numara, iccid, imsi, mcc_mnc, ulke, tur, wifi_arama, "
                     "sub_id, kaynak FROM sim_kartlari",
                     [("durum", "Durum", "rozet"), ("operator", "Operatör", "metin"), ("gorunen_ad", "Görünen ad", "metin"),
                      ("numara", "Numara", "metin"), ("iccid", "ICCID", "metin"), ("imsi", "IMSI", "metin"),
                      ("mcc_mnc", "MCC-MNC", "metin"), ("ulke", "Ülke", "metin"), ("tur", "Tür", "metin"),
                      ("wifi_arama", "Wi-Fi arama", "metin"), ("sub_id", "Abonelik no", "metin"),
                      ("kaynak", "Kaynak", "metin")],
                     ["operator", "gorunen_ad", "numara", "iccid", "imsi"], "durum LIKE 'Takılı%' DESC, durum, sub_id"),
    "sim_rehberi": ("SELECT id, ad, numara, eposta, ek_numara, liste, sim FROM sim_rehberi",
                    [("ad", "Ad", "metin"), ("numara", "Numara", "metin"), ("eposta", "E-posta", "metin"),
                     ("ek_numara", "Ek numara", "metin"), ("liste", "Liste", "rozet"), ("sim", "SIM", "metin")],
                    ["ad", "numara", "eposta"], "liste, ad"),
    "sim_dosyalari": ("SELECT id, yol, ad, aciklama, boyut, deger, ham FROM sim_dosyalari",
                      [("yol", "Yol", "metin"), ("ad", "Dosya", "metin"), ("aciklama", "Açıklama", "metin"),
                       ("boyut", "Boyut", "sayi"), ("deger", "Çözümlenen değer", "uzun"), ("ham", "Ham (hex)", "uzun")],
                      ["yol", "ad", "aciklama", "deger"], "yol"),
    "notlar": ("SELECT id, degistirme, olusturma, metin FROM notlar",
               [("degistirme", "Son değişiklik", "tarih"), ("olusturma", "Oluşturma", "tarih"),
                ("metin", "Not", "uzun")], ["metin"], "degistirme DESC"),
}


def bolum_verisi(st: Durum, cid: str, ad: str, q: dict):
    db = st.db(cid)
    if ad not in BOLUMLER:
        raise KeyError("Bilinmeyen bölüm")
    sql, kolonlar, aranan, sirala = BOLUMLER[ad]
    kosul, param = [], []
    ara = (q.get("q") or "").strip()
    if ara:
        kosul.append("(" + " OR ".join(f"COALESCE({a},'') LIKE ?" for a in aranan) + ")")
        param += [f"%{ara}%"] * len(aranan)
    for alan in ("kategori", "uygulama", "hesap", "tur", "kaynak", "yon", "anahtar", "cikarma"):
        if q.get(alan):
            kosul.append(f"{alan} = ?")
            param.append(q[alan])
    taban = f"SELECT * FROM ({sql})" + (" WHERE " + " AND ".join(kosul) if kosul else "")
    gecerli = {k[0] for k in kolonlar} | {"id"}
    s_alan = q.get("sirala")
    if s_alan in gecerli:
        sirala = f"{s_alan} {'ASC' if q.get('sira_yon') == 'asc' else 'DESC'}"
    toplam = db.execute(f"SELECT COUNT(*) FROM ({taban})", param).fetchone()[0]
    boyut = min(int(q.get("boyut") or 100), 500)
    sayfa = max(int(q.get("sayfa") or 1), 1)
    satir = _satirlar(db.execute(f"{taban} ORDER BY {sirala} LIMIT ? OFFSET ?", param + [boyut, (sayfa - 1) * boyut]))
    etiketli = {r[0] for r in st.idb.execute("SELECT ref FROM etiketler WHERE cihaz=? AND tur=?", (cid, ad))}
    for r in satir:
        r["_etiket"] = str(r.get("id")) in etiketli
    return {"kolonlar": [{"alan": a, "baslik": b, "tur": t} for a, b, t in kolonlar],
            "satirlar": satir, "toplam": toplam, "sayfa": sayfa, "boyut": boyut}


# ----------------------------------------------------------------- mesajlar: görüşme görünümü
GIDEN_YON = ("Giden", "Giden kutusu", "Gönderilemedi", "Kuyrukta", "Taslak", "Gönderilmemiş")
_ANAHTAR = "COALESCE(NULLIF(anahtar, ''), '_')"     # numarası olmayan mesajlar tek grupta


def _kisi_adlari(db) -> dict:
    """numara anahtarı -> kayıtlı ad (çağrı kaydındaki isim, yoksa SIM rehberi)."""
    adlar = {}
    for a, ad in db.execute("SELECT anahtar, ad FROM sim_rehberi WHERE COALESCE(ad,'') <> '' AND anahtar <> ''"):
        adlar[a] = ad
    for a, ad in db.execute("SELECT anahtar, MAX(isim) FROM cagrilar WHERE COALESCE(isim,'') <> '' "
                            "AND anahtar <> '' GROUP BY anahtar"):
        adlar[a] = ad
    for a, ad in db.execute("SELECT anahtar, MAX(ad) FROM rehber_numara WHERE COALESCE(ad,'') <> '' "
                            "AND anahtar <> '' GROUP BY anahtar"):
        adlar[a] = ad           # telefonun rehberi en güvenilir kaynak
    return adlar


def mesaj_konusmalari(st: Durum, cid: str, q: dict):
    db = st.db(cid)
    kosul, p = [], []
    if q.get("kaynak"):
        kosul.append("kaynak = ?"); p.append(q["kaynak"])
    where = ("WHERE " + " AND ".join(kosul)) if kosul else ""
    satir = _satirlar(db.execute(f"""
        SELECT id, numara, son_metin, son, adet, okunmamis FROM (
          SELECT {_ANAHTAR} AS id, numara, metin AS son_metin, tarih AS son,
                 COUNT(*) OVER w AS adet,
                 SUM(CASE WHEN okundu = 'Hayır' AND yon = 'Gelen' THEN 1 ELSE 0 END) OVER w AS okunmamis,
                 ROW_NUMBER() OVER (PARTITION BY {_ANAHTAR} ORDER BY tarih DESC, id DESC) AS sira
          FROM mesajlar {where} WINDOW w AS (PARTITION BY {_ANAHTAR}))
        WHERE sira = 1 ORDER BY son DESC""", p))
    kaynaklar = dict(db.execute(f"SELECT {_ANAHTAR}, group_concat(DISTINCT kaynak) FROM mesajlar {where} "
                                f"GROUP BY {_ANAHTAR}", p).fetchall())
    adlar = _kisi_adlari(db)
    for r in satir:
        r["kaynaklar"] = kaynaklar.get(r["id"])
        r["ad"] = adlar.get(r["id"]) or (r["numara"] if r["id"] != "_" else "Numarasız mesajlar")
    ara = (q.get("q") or "").strip()
    if ara:
        icerik = {x[0] for x in db.execute(f"SELECT DISTINCT {_ANAHTAR} FROM mesajlar WHERE metin LIKE ? OR numara LIKE ?",
                                           (f"%{ara}%", f"%{ara}%"))}
        k = ara.casefold()
        satir = [r for r in satir if r["id"] in icerik or k in (r["ad"] or "").casefold()]
    return {"konusmalar": satir, "toplam": len(satir)}


def mesaj_konusmasi(st: Durum, cid: str, q: dict):
    """Bir kişiyle tüm mesajlar (eskiden yeniye), sayfalı. Sayfa verilmezse son sayfa;
    mesaj=id verilirse o mesajın bulunduğu sayfa."""
    db = st.db(cid)
    anahtar = q.get("id") or "_"
    boyut = min(int(q.get("boyut") or 200), 1000)
    taban = f"FROM mesajlar WHERE {_ANAHTAR} = ?"
    toplam = db.execute(f"SELECT COUNT(*) {taban}", (anahtar,)).fetchone()[0]
    son_sayfa = max(1, -(-toplam // boyut))
    if q.get("mesaj"):
        r = db.execute(f"SELECT tarih, id {taban} AND id = ?", (anahtar, int(q["mesaj"]))).fetchone()
        once = db.execute(f"SELECT COUNT(*) {taban} AND (COALESCE(tarih,0) < ? OR (COALESCE(tarih,0) = ? AND id < ?))",
                          (anahtar, r[0] or 0, r[0] or 0, r[1])).fetchone()[0] if r else toplam
        sayfa = once // boyut + 1
    else:
        sayfa = int(q.get("sayfa") or son_sayfa)
    sayfa = min(max(sayfa, 1), son_sayfa)
    mesajlar = _satirlar(db.execute(f"SELECT id, tarih, yon, kaynak, numara, metin, okundu, sim {taban} "
                                    "ORDER BY COALESCE(tarih,0), id LIMIT ? OFFSET ?", (anahtar, boyut, (sayfa - 1) * boyut)))
    etiketli = {r[0] for r in st.idb.execute("SELECT ref FROM etiketler WHERE cihaz=? AND tur='mesajlar'", (cid,))}
    for m in mesajlar:
        m["ben"] = m["yon"] in GIDEN_YON
        m["_etiket"] = str(m["id"]) in etiketli
    adlar = _kisi_adlari(db)
    numara = db.execute(f"SELECT numara {taban} ORDER BY tarih DESC LIMIT 1", (anahtar,)).fetchone()
    return {"id": anahtar, "ad": adlar.get(anahtar), "numara": numara[0] if numara else None,
            "toplam": toplam, "sayfa": sayfa, "son_sayfa": son_sayfa, "mesajlar": mesajlar}


def ozet(st: Durum, cid: str):
    g = st.cihaz(cid)
    db = st.db(cid)
    say = lambda sql, *p: db.execute(sql, p).fetchone()[0]
    uyg = _satirlar(db.execute("""SELECT uygulama AS paket, hesap, COUNT(*) AS adet, SUM(boyut) AS boyut
                                   FROM dosyalar WHERE uygulama IS NOT NULL GROUP BY uygulama, hesap
                                   ORDER BY adet DESC"""))
    for u in uyg:
        u["ad"] = uygulama_adi(u["paket"]) + (f" (hesap {u['hesap']})" if u["hesap"] else "")
    if say("SELECT COUNT(*) FROM cagrilar"):
        uyg.insert(0, {"paket": "_cagri", "ad": "Çağrı kaydı", "adet": say("SELECT COUNT(*) FROM cagrilar"), "ozel": "cagrilar"})
    if say("SELECT COUNT(*) FROM mesajlar"):
        uyg.insert(1, {"paket": "_sms", "ad": "Mesajlar (SMS/MMS)", "adet": say("SELECT COUNT(*) FROM mesajlar"), "ozel": "mesajlar"})
    not_ = st.idb.execute("SELECT metin FROM cihaz_notlari WHERE cihaz=?", (cid,)).fetchone()
    return {
        "cihaz": g,
        "sayilar": {
            "uygulamalar": say("SELECT COUNT(*) FROM uygulamalar"),
            "cagrilar": say("SELECT COUNT(*) FROM cagrilar"),
            "dosyalar": say("SELECT COUNT(*) FROM dosyalar"),
            "hesaplar": say("SELECT COUNT(*) FROM hesaplar"),
            "kisiler": say(f"SELECT COUNT(*) FROM ({kisiler_sorgusu()})"),
            "mesajlar": say("SELECT COUNT(*) FROM mesajlar"),
            "notlar": say("SELECT COUNT(*) FROM notlar"),
            "rehber": say("SELECT COUNT(*) FROM rehber"),
            "takvim": say("SELECT COUNT(*) FROM takvim"),
            "sim_kartlari": say("SELECT COUNT(*) FROM sim_kartlari"),
            "sim_rehberi": say("SELECT COUNT(*) FROM sim_rehberi"),
            "sim_dosyalari": say("SELECT COUNT(*) FROM sim_dosyalari"),
            "fotograflar": say("SELECT COUNT(*) FROM dosyalar WHERE kategori IN ('Görüntü','Video')"),
            "zaman": say("SELECT (SELECT COUNT(*) FROM cagrilar)+(SELECT COUNT(*) FROM mesajlar)"
                         "+(SELECT COUNT(*) FROM notlar)+(SELECT COUNT(*) FROM dosyalar WHERE kategori IN ('Görüntü','Video'))"),
            "etiketler": st.idb.execute("SELECT COUNT(*) FROM etiketler WHERE cihaz=?", (cid,)).fetchone()[0],
            "toplam_boyut": say("SELECT COALESCE(SUM(boyut),0) FROM dosyalar"),
        },
        "uygulamalar": uyg,
        "simgeli": [r[0] for r in db.execute("SELECT paket FROM simgeler")],
        "not": not_[0] if not_ else "",
        "hesaplar": _satirlar(db.execute("SELECT ad, tur, kaynak FROM hesaplar ORDER BY tur")),
        "sim": _satirlar(db.execute("SELECT durum, operator, gorunen_ad, numara, iccid, imsi, tur, sub_id, kaynak "
                                    "FROM sim_kartlari ORDER BY durum LIKE 'Takılı%' DESC, sub_id")),
        "olay": olay_bilgisi(st, cid),
    }


OLAY_ALANLARI = ["Olay numarası", "Kanıt numarası", "El koyma tarihi", "El koyma yeri", "El koyan",
                 "Teslim eden", "Teslim alan", "Cihaz sahibi", "Mühür / poşet numarası", "Cihazın durumu", "Açıklama"]


def olay_bilgisi(st: Durum, cid: str) -> list[dict]:
    """[{ad, deger, ozel}] - varsayılan alanlar + kullanıcının eklediği alanlar."""
    r = st.idb.execute("SELECT deger FROM olay_bilgisi WHERE cihaz=?", (cid,)).fetchone()
    kayitli = json.loads(r[0]) if r else []
    deger = {a["ad"]: a.get("deger", "") for a in kayitli}
    out = [{"ad": a, "deger": deger.get(a, ""), "ozel": False} for a in OLAY_ALANLARI]
    out += [{"ad": a["ad"], "deger": a.get("deger", ""), "ozel": True} for a in kayitli if a["ad"] not in OLAY_ALANLARI]
    return out


# ----------------------------------------------------------------- WhatsApp şifre çözme
def _wa_db_yolu(st, cid, kid, yol) -> Path:
    return CALISMA / "whatsapp" / f"{_id(str(st.dosya_koku(cid, kid).resolve()) + '|' + yol)}.db"


def wa_cozulen(st, cid, kid, yol):
    r = st.idb.execute("SELECT * FROM wa_cozulen WHERE cihaz=? AND cikarma=? AND yol=?", (cid, kid, yol)).fetchone()
    return dict(r) if r and Path(r["db_yol"]).exists() else None


def wa_medya_haritasi(st, cid, paket, hesap):
    """Dosya adı -> (çıkarma, yol): mesajdaki medya yolunu yedekteki dosyayla eşleştirmek için."""
    anahtar = (cid, paket, hesap)
    if not hasattr(st, "_wa_medya"):
        st._wa_medya = {}
    if anahtar not in st._wa_medya:
        kosul, p = ("hesap IS NULL", [paket]) if not hesap else ("hesap = ?", [paket, hesap])
        st._wa_medya[anahtar] = {Path(r[1]).name: (r[0], r[1]) for r in st.db(cid).execute(
            f"SELECT cikarma, yol FROM dosyalar WHERE uygulama = ? AND {kosul}", p)}
    return st._wa_medya[anahtar]


def istatistik(st: Durum, cid: str):
    db = st.db(cid)
    s = lambda sql: _satirlar(db.execute(sql))
    return {
        "kategoriler": s("SELECT kategori AS ad, COUNT(*) AS adet, SUM(boyut) AS boyut FROM dosyalar GROUP BY kategori ORDER BY boyut DESC"),
        "uygulama_boyut": s("SELECT uygulama AS ad, COUNT(*) AS adet, SUM(boyut) AS boyut FROM dosyalar "
                            "WHERE uygulama IS NOT NULL GROUP BY uygulama ORDER BY boyut DESC LIMIT 10"),
        "cagri_tur": s("SELECT tur AS ad, COUNT(*) AS adet, SUM(sure) AS sure FROM cagrilar GROUP BY tur ORDER BY adet DESC"),
        "mesaj_yon": s("SELECT kaynak || ' · ' || yon AS ad, COUNT(*) AS adet FROM mesajlar GROUP BY kaynak, yon ORDER BY adet DESC"),
        "en_cok": s(f"SELECT isim, numara, cagri, mesaj, cagri + mesaj AS toplam FROM ({kisiler_sorgusu()}) "
                    "ORDER BY toplam DESC LIMIT 10"),
        "aylik": s("""SELECT ay, SUM(c) AS cagri, SUM(m) AS mesaj FROM (
                        SELECT strftime('%Y-%m', tarih/1000, 'unixepoch', 'localtime') AS ay, 1 AS c, 0 AS m FROM cagrilar
                        UNION ALL
                        SELECT strftime('%Y-%m', tarih/1000, 'unixepoch', 'localtime'), 0, 1 FROM mesajlar)
                      WHERE ay IS NOT NULL GROUP BY ay ORDER BY ay DESC LIMIT 18"""),
        "saatlik": s("""SELECT CAST(strftime('%H', tarih/1000, 'unixepoch', 'localtime') AS INTEGER) AS saat, COUNT(*) AS adet
                        FROM (SELECT tarih FROM cagrilar UNION ALL SELECT tarih FROM mesajlar)
                        WHERE tarih IS NOT NULL GROUP BY saat ORDER BY saat"""),
    }


def zaman_cizelgesi(st: Durum, cid: str, q: dict):
    db = st.db(cid)
    turler = set((q.get("turler") or "cagri,mesaj,not,medya").split(","))
    parcalar = []
    if "cagri" in turler:
        parcalar.append("SELECT 'cagri' AS tur, id, tarih, tur || ' çağrı' AS baslik, "
                        "COALESCE(NULLIF(isim,''), numara) AS kim, sure AS ek, NULL AS yol, NULL AS cikarma FROM cagrilar")
    if "mesaj" in turler:
        parcalar.append("SELECT 'mesaj', id, tarih, kaynak || ' · ' || yon, numara, metin, NULL, NULL FROM mesajlar")
    if "not" in turler:
        parcalar.append("SELECT 'not', id, degistirme, 'Not', NULL, metin, NULL, NULL FROM notlar")
    if "medya" in turler:
        parcalar.append("SELECT 'medya', id, mtime, kategori, uygulama, ad, yol, cikarma FROM dosyalar "
                        "WHERE kategori IN ('Görüntü','Video')")
    if not parcalar:
        return {"olaylar": [], "toplam": 0}
    kosul, param = ["tarih IS NOT NULL"], []
    if q.get("bas"):
        kosul.append("tarih >= ?"); param.append(int(q["bas"]))
    if q.get("bit"):
        kosul.append("tarih <= ?"); param.append(int(q["bit"]))
    if q.get("q"):
        kosul.append("(COALESCE(kim,'') LIKE ? OR COALESCE(ek,'') LIKE ?)"); param += [f"%{q['q']}%"] * 2
    taban = f"SELECT * FROM ({' UNION ALL '.join(parcalar)}) WHERE {' AND '.join(kosul)}"
    toplam = db.execute(f"SELECT COUNT(*) FROM ({taban})", param).fetchone()[0]
    sayfa = max(int(q.get("sayfa") or 1), 1)
    olay = _satirlar(db.execute(f"{taban} ORDER BY tarih DESC LIMIT 200 OFFSET ?", param + [(sayfa - 1) * 200]))
    return {"olaylar": olay, "toplam": toplam, "sayfa": sayfa}


def genel_arama(st: Durum, cid: str, ara: str):
    sonuc = {}
    if not ara or len(ara) < 2:
        return sonuc
    for ad in ("kisiler", "rehber", "takvim", "cagrilar", "mesajlar", "notlar", "dosyalar", "uygulamalar", "hesaplar",
               "sim_kartlari", "sim_rehberi", "sim_dosyalari"):
        v = bolum_verisi(st, cid, ad, {"q": ara, "boyut": 25})
        if v["toplam"]:
            sonuc[ad] = v
    return sonuc


def whatsapp_ozeti(st: Durum, cid: str, paket: str):
    """WhatsApp hesaplarının yedek envanteri (mesajlar şifreli olduğu için içerik değil, dosya bazlı)."""
    db = st.db(cid)
    hesaplar = []
    for (hesap,) in db.execute("SELECT DISTINCT hesap FROM dosyalar WHERE uygulama=? ORDER BY hesap IS NOT NULL, hesap", (paket,)):
        kosul, p = ("hesap IS NULL", (paket,)) if hesap is None else ("hesap = ?", (paket, hesap))
        yedekler = _satirlar(db.execute(
            f"SELECT id, ad, boyut, mtime, yol, cikarma FROM dosyalar WHERE uygulama=? AND {kosul} "
            "AND (ad LIKE 'msgstore%' OR ad LIKE 'wa.db%' OR ad LIKE '%.crypt1%') ORDER BY mtime DESC", p))
        medya = _satirlar(db.execute(
            f"""SELECT CASE WHEN yol LIKE '%/Media/%' THEN
                       substr(substr(yol, instr(yol, '/Media/') + 7), 1, instr(substr(yol, instr(yol, '/Media/') + 7) || '/', '/') - 1)
                       ELSE 'Diğer' END AS klasor, COUNT(*) AS adet, SUM(boyut) AS boyut
                FROM dosyalar WHERE uygulama=? AND {kosul} GROUP BY klasor ORDER BY adet DESC""", p))
        for y in yedekler:
            y["cozulen"] = wa_cozulen(st, cid, y["cikarma"], y["yol"])
        hesaplar.append({"hesap": hesap or "ana", "yedekler": yedekler, "medya": medya,
                         "adet": sum(m["adet"] for m in medya), "boyut": sum(m["boyut"] or 0 for m in medya)})
    return {"paket": paket, "ad": uygulama_adi(paket), "hesaplar": hesaplar}


def rapor_html(st: Durum, cid: str) -> str:
    """Yazdırılabilir özet (kayıtlı rapor ayarları: başlık, kurum, logo...)."""
    import ambar_rapor
    return ambar_rapor.ozet_html(st, cid, dosya_url=lambda r: None)


KLASOR_SECICI_CS = r'''
using System; using System.Runtime.InteropServices;
[ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IShellItem {
  void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
  void GetParent(out IShellItem ppsi);
  void GetDisplayName(uint sigdnName, [MarshalAs(UnmanagedType.LPWStr)] out string ppszName);
  void GetAttributes(uint sfgaoMask, out uint psfgaoAttribs);
  void Compare(IShellItem psi, uint hint, out int piOrder);
}
[ComImport, Guid("42f85136-db7e-439c-85f1-e4075d135fc8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAmbarDosyaPenceresi {
  [PreserveSig] int Show(IntPtr parent);
  void SetFileTypes(uint c, IntPtr spec);
  void SetFileTypeIndex(uint i);
  void GetFileTypeIndex(out uint i);
  void Advise(IntPtr p, out uint c);
  void Unadvise(uint c);
  void SetOptions(uint fos);
  void GetOptions(out uint fos);
  void SetDefaultFolder(IShellItem psi);
  void SetFolder(IShellItem psi);
  void GetFolder(out IShellItem psi);
  void GetCurrentSelection(out IShellItem psi);
  void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string s);
  void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string s);
  void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string s);
  void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string s);
  void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string s);
  void GetResult(out IShellItem psi);
}
[ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] public class AmbarDosyaPenceresiSinifi {}
public static class AmbarKlasorSecici {
  [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
  static extern void SHCreateItemFromParsingName(string path, IntPtr pbc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid, out IShellItem item);
  public static string Sec(IntPtr sahip, string baslangic, string baslik) {
    IAmbarDosyaPenceresi d = (IAmbarDosyaPenceresi)new AmbarDosyaPenceresiSinifi();
    uint o; d.GetOptions(out o);
    d.SetOptions(o | 0x20 | 0x40 | 0x800);           // FOS_PICKFOLDERS | FOS_FORCEFILESYSTEM | FOS_PATHMUSTEXIST
    d.SetTitle(baslik);
    d.SetOkButtonLabel("Klasör seç");
    if (!String.IsNullOrEmpty(baslangic)) {
      try { IShellItem k; SHCreateItemFromParsingName(baslangic, IntPtr.Zero, typeof(IShellItem).GUID, out k); d.SetFolder(k); } catch { }
    }
    int hr = d.Show(sahip);
    if (hr != 0 && hr != unchecked((int)0x800704C7) && sahip != IntPtr.Zero) {   // sahip pencere kabul edilmezse sahipsiz dene
      return Sec(IntPtr.Zero, baslangic, baslik);
    }
    if (hr != 0) return "";
    IShellItem r; d.GetResult(out r);
    string p; r.GetDisplayName(0x80058000, out p);    // SIGDN_FILESYSPATH
    return p;
  }
}
'''


def klasor_sec(baslangic: str = "", baslik: str = "Klasör seçin", sahip: int | None = None) -> str:
    """Windows Gezgini görünümlü klasör seçme penceresi (yeni klasör oluşturma, adres çubuğu, gezinti bölmesi).
    Pencere, kullanıcının o an kullandığı pencereye (tarayıcı / Ambar penceresi) bağlı açılır; böylece arkada kalmaz."""
    import subprocess
    if sahip is None:
        try:
            import ctypes
            f = ctypes.windll.user32.GetForegroundWindow
            f.restype = ctypes.c_void_p          # 64 bit pencere tutamacı kesilmesin
            sahip = f() or 0
        except Exception:
            sahip = 0
    yol = Path(baslangic) if baslangic else None
    while yol and not yol.exists() and yol != yol.parent:
        yol = yol.parent
    betik = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
             "Add-Type -TypeDefinition $env:AMBAR_CS\n"
             "[AmbarKlasorSecici]::Sec([IntPtr][long]$env:AMBAR_SAHIP, $env:AMBAR_KLASOR, $env:AMBAR_BASLIK)")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", betik],
                       capture_output=True, timeout=900, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       env={**os.environ, "AMBAR_CS": KLASOR_SECICI_CS, "AMBAR_SAHIP": str(int(sahip or 0)),
                            "AMBAR_KLASOR": str(yol) if yol and yol.exists() else "", "AMBAR_BASLIK": baslik})
    return r.stdout.decode("utf-8", errors="replace").strip().lstrip("﻿")


def tarayici_bul():
    adaylar = []
    for kok in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if kok:
            adaylar += [Path(kok) / "Microsoft/Edge/Application/msedge.exe", Path(kok) / "Google/Chrome/Application/chrome.exe"]
    return next((a for a in adaylar if a.exists()), None)


def pencere_ac(url: str, profil: Path | None = None):
    """İnceleme ekranını tarayıcı sekmesi yerine ayrı bir program penceresinde açar (Edge/Chrome uygulama modu).
    Adres çubuğu ve sekmeler olmaz; görev çubuğunda ayrı pencere olarak görünür. Bulunamazsa varsayılan tarayıcı açılır.
    Dönen değer: tarayıcı süreci (Popen) veya None."""
    import subprocess
    t = tarayici_bul()
    if not t:
        webbrowser.open(url)
        return None
    profil = profil or (CALISMA.parent / "pencere")
    profil.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([str(t), f"--app={url}", f"--user-data-dir={profil}", "--no-first-run",
                             "--no-default-browser-check", "--window-size=1440,920", "--disable-features=Translate",
                             "--lang=tr"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


# ----------------------------------------------------------------- HTTP sunucusu
class Isleyici(BaseHTTPRequestHandler):
    st: Durum = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _gonder(self, kod, govde: bytes, tip="application/json; charset=utf-8", ek=None):
        self.send_response(kod)
        self.send_header("Content-Type", tip)
        self.send_header("Content-Length", str(len(govde)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (ek or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(govde)

    def _json(self, veri, kod=200):
        self._gonder(kod, json.dumps(veri, ensure_ascii=False, default=str).encode("utf-8"))

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        st = self.st
        try:
            if u.path in ("/", "/index.html"):
                return self._gonder(200, ARAYUZ.read_bytes(), "text/html; charset=utf-8")
            if u.path == "/icon.png":
                return self._gonder(200, (core.PAKET_DIR / "icon.png").read_bytes(), "image/png")
            if u.path == "/api/cihazlar":
                if q.get("yenile"):
                    st.yenile()
                rapor = getattr(st, "meta", None)
                return self._json({"kok": str(st.rapor if getattr(st, "kap", None) else st.kok), "cihazlar": st.cihazlar,
                                   "surum": core.SURUM, "ambx": bool(getattr(st, "kap", None)),
                                   "yapimci": core.YAPIMCI, "web": core.WEB, "portable": st.portable,
                                   "rapor": rapor and {k: v for k, v in rapor.items() if k != "cihaz"}})
            if u.path == "/api/dogrula_durum":
                return self._json(getattr(st, "dogrulama", None) or {})
            if u.path == "/api/disa_durum":
                return self._json(getattr(st, "disa", None) or {})
            if u.path == "/api/durum":
                return self._json({"ilerleme": st.ilerleme.get(q.get("cihaz", ""))})
            cid = q.get("cihaz", "")
            if u.path == "/api/ozet":
                if q.get("yeniden"):
                    st.db(cid, yeniden=True)
                return self._json(ozet(st, cid))
            if u.path == "/api/bolum":
                return self._json(bolum_verisi(st, cid, q.get("ad", ""), q))
            if u.path == "/api/istatistik":
                return self._json(istatistik(st, cid))
            if u.path == "/api/zaman":
                return self._json(zaman_cizelgesi(st, cid, q))
            if u.path == "/api/ara":
                return self._json(genel_arama(st, cid, q.get("q", "")))
            if u.path == "/api/whatsapp":
                return self._json(whatsapp_ozeti(st, cid, q.get("paket", "com.whatsapp")))
            if u.path == "/api/sihirbaz":
                import ambar_rapor
                return self._json(ambar_rapor.sihirbaz_bilgisi(st, cid))
            if u.path in ("/api/wa_sohbetler", "/api/wa_mesajlar"):
                import ambar_whatsapp
                kayit = wa_cozulen(st, cid, q.get("c", ""), q.get("yol", ""))
                if not kayit:
                    return self._json({"hata": "Bu yedek henüz çözülmedi"}, 404)
                if u.path == "/api/wa_sohbetler":
                    return self._json({"kayit": kayit, "sohbetler": ambar_whatsapp.sohbetler(Path(kayit["db_yol"]))})
                v = ambar_whatsapp.mesajlar(Path(kayit["db_yol"]), q.get("sohbet", ""), q.get("sayfa"), ara=q.get("q", ""))
                harita = wa_medya_haritasi(st, cid, kayit["paket"], kayit["hesap"])
                for m in v["mesajlar"]:
                    if m.get("dosya"):
                        bul = harita.get(Path(str(m["dosya"]).replace("\\", "/")).name)
                        if bul:
                            m["medya_c"], m["medya_yol"] = bul
                return self._json(v)
            if u.path == "/api/etiketler":
                return self._json(_satirlar(st.idb.execute(
                    "SELECT tur, ref, baslik, etiket, aciklama, tarih FROM etiketler WHERE cihaz=? ORDER BY tarih DESC", (cid,))))
            if u.path == "/rapor":
                import ambar_rapor
                onizleme = (getattr(st, "onizleme", None) or {}).get(cid) if q.get("onizleme") else None
                if onizleme:   # sihirbazdaki ayarlarla canlı önizleme (dosyalar sunucudan gösterilir)
                    r = {**ambar_rapor.VARSAYILAN_RAPOR, **(onizleme.get("rapor") or {}), "_kucuk_resim": onizleme.get("kucuk_resim", True)}
                    a = {**ambar_rapor.VARSAYILAN_DISA, **onizleme}
                    url = lambda x: "/dosya?" + "&".join(f"{k}={quote(str(v))}" for k, v in
                                                         {"cihaz": cid, "c": x["cikarma"], "yol": x["yol"]}.items())
                    govde = ambar_rapor.html_olustur(st, cid, a, r, ambar_rapor.tablolar(st, cid, a), url)
                else:
                    govde = rapor_html(st, cid)
                return self._gonder(200, govde.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/mesaj_konusmalar":
                return self._json(mesaj_konusmalari(st, q.get("cihaz", ""), q))
            if u.path == "/api/mesaj_konusmasi":
                return self._json(mesaj_konusmasi(st, q.get("cihaz", ""), q))
            if u.path == "/api/simge":
                r = st.db(q.get("cihaz", "")).execute("SELECT tur, veri FROM simgeler WHERE paket=?",
                                                      (q.get("paket", ""),)).fetchone()
                if not r:
                    return self._gonder(404, b"", "text/plain")
                return self._gonder(200, bytes(r["veri"]), r["tur"],
                                    {"Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"})
            if u.path == "/dosya":
                return self._dosya(cid, q.get("c", ""), q.get("yol", ""))
            self._json({"hata": "Bulunamadı"}, 404)
        except KeyError as e:
            self._json({"hata": str(e)}, 404)
        except Exception as e:
            self._json({"hata": f"{type(e).__name__}: {e}"}, 500)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try:
            veri = json.loads(self.rfile.read(n) or b"{}")
            st = self.st
            if u.path == "/api/etiket":
                if veri.get("sil"):
                    st.idb.execute("DELETE FROM etiketler WHERE cihaz=? AND tur=? AND ref=?",
                                   (veri["cihaz"], veri["tur"], str(veri["ref"])))
                else:
                    st.idb.execute("INSERT OR REPLACE INTO etiketler VALUES(?,?,?,?,?,?,?)",
                                   (veri["cihaz"], veri["tur"], str(veri["ref"]), veri.get("baslik"),
                                    veri.get("etiket") or "Önemli", veri.get("aciklama"),
                                    datetime.datetime.now().isoformat(timespec="seconds")))
                st.idb.commit()
                return self._json({"tamam": True})
            if u.path == "/api/disa_aktar":
                import ambar_rapor
                if (getattr(st, "disa", None) or {}).get("durum") == "calisiyor":
                    return self._json({"hata": "Bir dışa aktarma zaten sürüyor"}, 409)
                cid = veri.pop("cihaz")
                ambar_rapor.ayar_yaz(st, "rapor", veri.get("rapor") or {})
                ambar_rapor.ayar_yaz(st, "disa", {k: v for k, v in veri.items() if k not in ("rapor", "hedef")})
                st.disa = {"durum": "calisiyor", "yuzde": 0, "mesaj": "Başlıyor"}

                def is_():
                    try:
                        k = ambar_rapor.disa_aktar(st, cid, veri, lambda y, m: st.disa.update(yuzde=round(y, 1), mesaj=m))
                        st.disa.update(durum="bitti", yol=str(k), yuzde=100)
                    except Exception as e:
                        st.disa.update(durum="hata", mesaj=f"{type(e).__name__}: {e}")
                threading.Thread(target=is_, daemon=True).start()
                return self._json({"tamam": True})
            if u.path == "/api/dogrula":
                if not getattr(st, "kap", None):
                    return self._json({"hata": "Yalnızca .ambx raporlarında kullanılabilir"}, 400)
                if (getattr(st, "dogrulama", None) or {}).get("durum") == "calisiyor":
                    return self._json({"tamam": True})
                st.dogrulama = {"durum": "calisiyor", "yuzde": 0}

                def dogrula():
                    try:
                        r = st.kap.dogrula(lambda y: st.dogrulama.update(yuzde=round(y, 1)))
                        st.dogrulama = {"durum": "bitti", "yuzde": 100, **r}
                    except Exception as e:
                        st.dogrulama = {"durum": "hata", "mesaj": f"{type(e).__name__}: {e}"}
                threading.Thread(target=dogrula, daemon=True).start()
                return self._json({"tamam": True})
            if u.path == "/api/wa_coz":
                import base64
                import ambar_whatsapp
                cid, kid, yol = veri["cihaz"], veri["c"], veri["yol"]
                try:
                    f, _, _ = st.dosya_ac(cid, kid, yol)
                    with f:
                        kaynak = f.read()
                except (FileNotFoundError, KeyError):
                    return self._json({"hata": "Yedek dosyası bulunamadı (rapora dahil edilmemiş olabilir)"}, 404)
                satir = st.db(cid).execute("SELECT uygulama, hesap FROM dosyalar WHERE cikarma=? AND yol=?", (kid, yol)).fetchone()
                hedef = _wa_db_yolu(st, cid, kid, yol)
                try:
                    sonuc = ambar_whatsapp.coz(kaynak, hedef, veri.get("anahtar"),
                                               base64.b64decode(veri["dosya_b64"]) if veri.get("dosya_b64") else None)
                except ValueError as e:
                    return self._json({"hata": str(e)}, 400)
                st.idb.execute("INSERT OR REPLACE INTO wa_cozulen VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (cid, kid, yol, satir[0] if satir else "com.whatsapp", satir[1] if satir else None, str(hedef),
                                sonuc["sha256"], int(sonuc["dogrulandi"]), sonuc["sohbet"], sonuc["mesaj"],
                                datetime.datetime.now().isoformat(timespec="seconds")))
                st.idb.commit()
                return self._json({"tamam": True, **sonuc})
            if u.path == "/api/olay":
                alanlar = [{"ad": str(a.get("ad", "")).strip()[:100], "deger": str(a.get("deger", ""))[:5000]}
                           for a in veri.get("alanlar", []) if str(a.get("ad", "")).strip()]
                st.idb.execute("INSERT OR REPLACE INTO olay_bilgisi VALUES(?,?,?)",
                               (veri["cihaz"], json.dumps(alanlar, ensure_ascii=False), datetime.datetime.now().isoformat()))
                st.idb.commit()
                return self._json({"tamam": True})
            if u.path == "/api/kok":
                if st.portable:
                    return self._json({"hata": "Portable raporda klasör değiştirilemez"}, 400)
                yol = Path(veri.get("yol") or "")
                if not str(veri.get("yol") or "").strip() or not yol.is_dir():
                    return self._json({"hata": f"Klasör bulunamadı: {yol}"}, 400)
                st.kok_degistir(yol)
                return self._json({"tamam": True, "kok": str(st.kok), "cihaz": len(st.cihazlar)})
            if u.path == "/api/klasor_sec":
                return self._json({"yol": klasor_sec(veri.get("baslangic") or "", veri.get("baslik") or "Klasör seçin")})
            if u.path == "/api/rapor_onizle":
                import ambar_rapor
                cid = veri.pop("cihaz")
                ambar_rapor.ayar_yaz(st, "rapor", veri.get("rapor") or {})
                if not hasattr(st, "onizleme"):
                    st.onizleme = {}
                st.onizleme[cid] = veri
                return self._json({"url": f"/rapor?cihaz={cid}&onizleme=1"})
            if u.path == "/api/klasor_ac":     # yalnızca son oluşturulan rapor klasörü açılabilir
                yol = (getattr(st, "disa", None) or {}).get("yol")
                if yol and os.path.isdir(yol):
                    os.startfile(yol)
                elif yol and os.path.isfile(yol):      # tek dosya rapor: Gezgin'de dosyayı seçili göster
                    import subprocess
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(yol)])
                return self._json({"tamam": bool(yol)})
            if u.path == "/api/not":
                st.idb.execute("INSERT OR REPLACE INTO cihaz_notlari VALUES(?,?,?)",
                               (veri["cihaz"], veri.get("metin", ""), datetime.datetime.now().isoformat()))
                st.idb.commit()
                return self._json({"tamam": True})
            self._json({"hata": "Bulunamadı"}, 404)
        except Exception as e:
            self._json({"hata": f"{type(e).__name__}: {e}"}, 500)

    def _dosya(self, cid, kid, rel):
        """Yalnızca çıkarma klasörünün İÇİNDEKİ dosyaları sunar (yol kaçışı engellenir)."""
        try:
            f, boyut, _ = self.st.dosya_ac(cid, kid, rel)
        except (FileNotFoundError, KeyError):
            return self._json({"hata": "Dosya bulunamadı"}, 404)
        p = Path(rel)
        tip = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        bas, bit = 0, boyut - 1
        aralik = self.headers.get("Range")
        kod = 200
        if aralik and aralik.startswith("bytes="):
            a, _, b = aralik[6:].partition("-")
            bas = int(a) if a else max(0, boyut - int(b))
            bit = int(b) if a and b else boyut - 1
            bit = min(bit, boyut - 1)
            kod = 206
        uzunluk = bit - bas + 1
        self.send_response(kod)
        self.send_header("Content-Type", tip)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(uzunluk))
        if kod == 206:
            self.send_header("Content-Range", f"bytes {bas}-{bit}/{boyut}")
        if p.suffix.lower() in (".html", ".htm", ".svg", ".xml", ".xhtml", ".js"):
            self.send_header("Content-Security-Policy", "sandbox")
            self.send_header("X-Content-Type-Options", "nosniff")
        if self.path and "indir=1" in self.path:
            self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(p.name)}")
        self.end_headers()
        if self.command == "HEAD":
            f.close()
            return
        with f:
            f.seek(bas)
            kalan = uzunluk
            try:
                while kalan > 0:
                    parca = f.read(min(1024 * 1024, kalan))
                    if not parca:
                        break
                    self.wfile.write(parca)
                    kalan -= len(parca)
            except (ConnectionResetError, BrokenPipeError):
                pass


def sunucu_baslat(kok: Path, port: int = 0):
    Isleyici.st = Durum(Path(kok))
    srv = ThreadingHTTPServer(("127.0.0.1", port), Isleyici)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


def calistir(kok: Path, port: int = 0, tarayici=True):
    srv, url = sunucu_baslat(kok, port)
    print(f"{core.UYGULAMA_ADI} İnceleme: {url}\nYedek klasörü: {kok}\nKapatmak için Ctrl+C")
    if tarayici:
        pencere_ac(url)
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()


def ambx_iliskilendir(exe: Path):
    """.ambx dosyalarını (geçerli kullanıcı için, yönetici izni gerekmeden) bu programla açılacak şekilde kaydeder.
    Kayıtlı program yoksa veya artık mevcut değilse güncellenir."""
    try:
        import winreg
        komut = f'"{exe}" "%1"'
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\Ambar.Rapor\shell\open\command") as k:
                mevcut = winreg.QueryValue(k, None)
            eski = mevcut.split('"')[1] if mevcut.startswith('"') else ""
            if eski and Path(eski).exists():
                return
        except OSError:
            pass
        for anahtar, deger in [(r"Software\Classes\.ambx", "Ambar.Rapor"),
                               (r"Software\Classes\Ambar.Rapor", "Ambar inceleme raporu"),
                               (r"Software\Classes\Ambar.Rapor\DefaultIcon", f'"{exe}",0'),
                               (r"Software\Classes\Ambar.Rapor\shell\open\command", komut)]:
            winreg.SetValue(winreg.HKEY_CURRENT_USER, anahtar, winreg.REG_SZ, deger)
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)     # simgeleri yenile
    except Exception:
        pass


def portable_baslat(argv=None):
    """AmbarIncele.exe: yanındaki (veya verilen) rapor.ambar'ı açar. Yedek alma / adb içermez."""
    import tkinter as tk
    from tkinter import filedialog, messagebox
    argv = sys.argv[1:] if argv is None else argv
    taban = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    if getattr(sys, "frozen", False):
        ambx_iliskilendir(Path(sys.executable))
    yol = Path(argv[0]) if argv else None
    if yol and yol.is_dir():
        taban, yol = yol, None
    if not yol:     # yanındaki tek .ambx raporu (veya eski klasör biçimi)
        bulunan = sorted(taban.glob("*.ambx"))
        yol = bulunan[0] if len(bulunan) == 1 else taban / RAPOR_DOSYA
    kok = tk.Tk()
    kok.withdraw()
    try:
        kok.iconbitmap(default=str(core.PAKET_DIR / "icon.ico"))
    except tk.TclError:
        pass
    if not yol.exists():
        secim = filedialog.askopenfilename(title="Ambar raporu seçin", initialdir=str(taban),
                                           filetypes=[("Ambar raporu", "*.ambx *.ambar"), ("Tümü", "*.*")])
        if not secim:
            return
        yol = Path(secim)
    try:
        Isleyici.st = RaporDurum(yol)
    except Exception as e:
        messagebox.showerror("Ambar İnceleme", f"Rapor açılamadı:\n{yol}\n\n{e}")
        return
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Isleyici)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    # Program penceresi (Edge/Chrome uygulama modu, bu oturuma özel profil). Pencere kapanınca program da kapanır.
    import shutil
    import tempfile
    profil = Path(tempfile.mkdtemp(prefix="ambar_incele_"))
    surec = pencere_ac(url, profil)
    if surec:
        kok.destroy()
        time.sleep(3)
        if surec.poll() is None:          # pencere açık: kapanmasını bekle
            surec.wait()
            srv.shutdown()
            shutil.rmtree(profil, ignore_errors=True)
            return
        kok = tk.Tk()                     # tarayıcı hemen çıktıysa (nadir) denetim penceresine dön
        kok.withdraw()
    else:
        webbrowser.open(url)

    # Küçük denetim penceresi: sunucunun çalıştığını gösterir, kapatınca durur
    m = Isleyici.st.meta
    kok.deiconify()
    kok.title("Ambar İnceleme")
    kok.configure(bg="#ffffff")
    kok.resizable(False, False)
    f = ("Segoe UI", 10)
    tk.Label(kok, text="Ambar İnceleme", bg="#ffffff", fg="#111827", font=("Segoe UI", 15, "bold")).pack(
        anchor="w", padx=22, pady=(18, 0))
    tk.Label(kok, text=f"{m['cihaz'].get('baslik', '')}  ·  rapor {m.get('olusturma', '').replace('T', ' ')}",
             bg="#ffffff", fg="#5b6472", font=f).pack(anchor="w", padx=22)
    tk.Label(kok, text=f"Rapor tarayıcıda açıldı:\n{url}\n\nİncelemeyi bitirince bu pencereyi kapatın.",
             bg="#ffffff", fg="#111827", font=f, justify="left").pack(anchor="w", padx=22, pady=(12, 0))
    alt = tk.Frame(kok, bg="#ffffff")
    alt.pack(fill="x", padx=22, pady=(14, 18))
    tk.Button(alt, text="Kapat", font=f, relief="groove", padx=14, command=kok.destroy).pack(side="right")
    tk.Button(alt, text="Tarayıcıda aç", font=f, relief="groove", padx=14,
              command=lambda: webbrowser.open(url)).pack(side="right", padx=8)
    kok.mainloop()
    srv.shutdown()


if __name__ == "__main__":
    calistir(Path(sys.argv[1]) if len(sys.argv) > 1 else core.VARSAYILAN_HEDEF)
