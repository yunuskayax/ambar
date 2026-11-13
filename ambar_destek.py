#!/usr/bin/env python3
"""Cihaz -> desteklenen veri alma yöntemleri (Cihazlar sayfası).

Liste üç kaynaktan oluşur: bağlı cihaz, kayıt klasöründeki yedeklerde görülen cihazlar ve marka profilleri.
Yalnızca programda gerçekten uygulanmış yöntemler listelenir; yeni yöntem eklendikçe KURALLAR genişletilir.
"""

# anahtar: (düğme yazısı, kısa açıklama)
YONTEM_KISA = {
    "komple": ("Komple", "Depolama + kayıtlar + adb backup + APK"),
    "agent": ("Android Agent", "Rehber, takvim, MMS ekleri, uygulamalar, onayla tüm dosyalar (ajan uygulaması)"),
    "kayitlar": ("Kayıtlar", "Çağrı, SMS, MMS, hesaplar, SIM geçmişi"),
    "whatsapp": ("WhatsApp", "WhatsApp klasörü ve Oxygen ZIP"),
    "sim": ("SIM bilgisi", "Takılı ve eski SIM kayıtları"),
    "ab": ("Yedekleme", "adb backup (.ab)"),
    "paket": ("Paket", "Tek uygulamanın .ab yedeği"),
    "notlar_yeni": ("MIUI notlar", "Yeni not yedeği alıp çözme"),
    "notlar": ("MIUI not yedeği", "Mevcut not yedeğini çözme"),
    "uicc": ("UICC", "SIM kart dosya sistemi (kart okuyucu)"),
}
ANDROID_GENEL = ["komple", "agent", "kayitlar", "whatsapp", "sim", "ab", "paket"]
XIAOMI = ("xiaomi", "redmi", "poco")

PROFILLER = [
    # (marka, ad, yonga, açıklama)
    ("Android", "Android Generic (Genel)", "", "USB hata ayıklaması açılabilen tüm Android cihazlar"),
    ("Samsung", "Samsung Galaxy (tüm modeller)", "Exynos / Qualcomm / MediaTek", "One UI"),
    ("Xiaomi", "Xiaomi / Redmi / POCO (tüm modeller)", "Qualcomm / MediaTek", "MIUI / HyperOS - not yedeği desteklenir"),
    ("Huawei", "Huawei / Honor (tüm modeller)", "Kirin / Qualcomm / MediaTek", "EMUI / MagicOS (Google servissiz modeller dahil)"),
    ("Oppo", "Oppo / Realme / OnePlus (tüm modeller)", "Qualcomm / MediaTek", "ColorOS / realme UI / OxygenOS"),
    ("Vivo", "Vivo / iQOO (tüm modeller)", "Qualcomm / MediaTek", "Funtouch OS / OriginOS"),
    ("Tecno", "Tecno / Infinix / itel (tüm modeller)", "MediaTek / Unisoc", "HiOS / XOS"),
    ("Google", "Google Pixel (tüm modeller)", "Tensor / Qualcomm", "Stok Android"),
    ("Motorola", "Motorola (tüm modeller)", "Qualcomm / MediaTek", ""),
    ("Nokia", "Nokia / HMD (tüm modeller)", "Qualcomm / MediaTek / Unisoc", "Android One"),
    ("General Mobile", "General Mobile / Casper / Reeder (tüm modeller)", "Qualcomm / MediaTek / Unisoc", "Yerli üretim"),
    ("TCL", "TCL / Alcatel (tüm modeller)", "MediaTek / Qualcomm", ""),
]


def android_surum(s) -> int:
    try:
        return int(str(s).split(".")[0])
    except (TypeError, ValueError):
        return 0


def yontemler(marka: str = "", android: str = "", sim_kart=False) -> list[tuple[str, str]]:
    """-> [(yöntem anahtarı, not)]; not boş değilse yöntem bu cihazda kısıtlıdır."""
    if sim_kart:
        return [("uicc", "")]
    m = (marka or "").lower()
    out = []
    for y in ANDROID_GENEL:
        n = ""
        if y in ("ab", "paket") and android_surum(android) >= 12:
            n = "Android 12+ çoğu uygulama veri vermez"
        if y == "agent" and android and android_surum(android) < 5:
            n = "Android 5.0 veya üstü gerekir"
        out.append((y, n))
    if any(x in m for x in XIAOMI):
        out += [("notlar_yeni", ""), ("notlar", "")]
    return out


def katalog(bagli: dict | None, yedekler: list[dict]) -> list[dict]:
    """bagli: cihaz_ozellikleri() sözlüğü (veya None); yedekler: ambar_incele.cihazlari_grupla() çıktısı."""
    liste, gorulen = [], set()

    def ekle(kaynak, d, ad=None, aciklama=""):
        anahtar = d.get("seri") or d.get("model")
        if anahtar and anahtar in gorulen:
            return
        gorulen.add(anahtar)
        marka = (d.get("marka") or "").title()
        model = d.get("model") or ""
        sim = (d.get("marka") or "") == "SIM kart"
        baslik = ad or (d.get("ticari_ad") and f"{d['ticari_ad']} ({model})") or f"{marka} {model}".strip() or "Bilinmeyen cihaz"
        liste.append({"kaynak": kaynak, "ad": baslik, "marka": marka, "model": model, "yonga": d.get("yonga") or "",
                      "isletim": "SIM / USIM" if sim else (f"Android {d['android']}" if d.get("android") else "Android"),
                      "aciklama": aciklama, "yontemler": yontemler(marka, d.get("android"), sim)})
    if bagli:
        ekle("Bağlı", bagli)
    for g in yedekler:
        ekle("Yedeklerde", g.get("cihaz") or {}, aciklama=f"{len(g.get('cikarmalar') or [])} çıkarma")
    for marka, ad, yonga, ac in PROFILLER:
        liste.append({"kaynak": "Profil", "ad": ad, "marka": marka, "model": "", "yonga": yonga, "isletim": "Android",
                      "aciklama": ac, "yontemler": yontemler(marka)})
    liste.append({"kaynak": "Profil", "ad": "SIM kart (fiziksel SIM / USIM)", "marka": "SIM", "model": "",
                  "yonga": "UICC", "isletim": "SIM / USIM", "aciklama": "PC/SC kart okuyucu gerekir",
                  "yontemler": yontemler(sim_kart=True)})
    return liste
