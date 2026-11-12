#!/usr/bin/env python3
"""
Android Agent - telefona geçici olarak kurulan Ambar Agent uygulamasıyla veri çıkarma.

adb'nin kendi yetkisiyle okunamayan verileri (rehber, takvim, kişi fotoğrafları, MMS ekleri, tüm uygulamaların
gerçek adları ve simgeleri, eşleşmiş Bluetooth cihazları) uygulama izniyle okur.

Akış:
  1. adb install -r -g ambar_agent.apk   (-g: izinler kurulumda verilir, telefonda tek tek onay gerekmez)
  2. am start ... --es anahtar <rastgele>  (telefonda "Ambar Agent - veri aktarılıyor" ekranı görünür)
  3. adb forward tcp:N localabstract:ambar_agent  -> yalnızca USB üzerinden, oturum anahtarıyla bağlantı
  4. veriler okunur: ham satırlar agent/ham/*.jsonl, inceleme programının biçimi kayitlar/*.json
  5. agent kapatılır ve kaldırılır (pm uninstall)

Ajanın internet izni yoktur; veri yalnızca USB kablosu üzerinden çıkar. Telefondaki hiçbir veri değiştirilmez.
"""
import json
import secrets
import socket
import struct
import time
from pathlib import Path

import ambar as core

PAKET = "com.ambar.agent"
ETKINLIK = f"{PAKET}/.Ana"
SOKET = "ambar_agent"
IZINLER = ["READ_CONTACTS", "READ_CALL_LOG", "READ_SMS", "READ_CALENDAR", "GET_ACCOUNTS", "READ_PHONE_STATE",
           "READ_PHONE_NUMBERS", "READ_EXTERNAL_STORAGE", "READ_MEDIA_IMAGES", "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO",
           "ACCESS_MEDIA_LOCATION", "BLUETOOTH_CONNECT"]


class AgentHatasi(RuntimeError):
    pass


def apk_yolu() -> Path | None:
    for p in (core.PAKET_DIR / "agent" / "ambar_agent.apk", core.SCRIPT_DIR / "agent" / "ambar_agent.apk"):
        if p.exists():
            return p
    return None


KURULUM_HATALARI = {
    "INSTALL_FAILED_USER_RESTRICTED": "Telefon USB üzerinden uygulama kurulmasına izin vermiyor. Xiaomi/Redmi/POCO: "
                                      "Geliştirici seçenekleri > 'USB ile yükle' açılmalı (Mi hesabı gerekir). "
                                      "Diğer markalar: telefonda çıkan kurulum onayını kabul edin.",
    "INSTALL_FAILED_VERIFICATION_FAILURE": "Google Play Protect kurulumu engelledi. Telefonda çıkan uyarıda "
                                           "'Yine de yükle'ye basın veya Play Protect'te 'Uygulamaları tara'yı geçici kapatın.",
    "INSTALL_FAILED_OLDER_SDK": "Telefonun Android sürümü çok eski (Android 5.0 veya üstü gerekir).",
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "Telefonda farklı imzalı eski bir Ambar Agent var; önce kaldırılıyor.",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": "Telefonda yeterli boş alan yok.",
}


class Agent:
    def __init__(self):
        self.sok = None
        self.port = None
        self.anahtar = secrets.token_hex(16)

    # -- kurulum
    def kur(self):
        apk = apk_yolu()
        if not apk:
            raise AgentHatasi("ambar_agent.apk bulunamadı (agent/derle.py ile derlenmeli).")
        for deneme in range(2):
            r = core.adb("install", "-r", "-g", str(apk))
            cikti = (r.stdout or "") + (r.stderr or "")
            if "Success" in cikti:
                break
            kod = next((k for k in KURULUM_HATALARI if k in cikti), None)
            if kod == "INSTALL_FAILED_UPDATE_INCOMPATIBLE" and deneme == 0:
                core.adb("uninstall", PAKET)
                continue
            raise AgentHatasi(KURULUM_HATALARI.get(kod, f"Ambar Agent kurulamadı:\n{cikti.strip()[-400:]}"))
        # -g bazı ROM'larda tüm izinleri vermeyebilir: tek tek de ver (olmayan izinler sessizce geçilir)
        for izin in IZINLER:
            core.adb("shell", f"pm grant {PAKET} android.permission.{izin}")

    def baslat(self):
        core.adb("shell", "input keyevent KEYCODE_WAKEUP")
        r = core.adb("shell", f"am start -W -n {ETKINLIK} --es anahtar {self.anahtar}")
        if "Error" in (r.stdout or "") + (r.stderr or ""):
            raise AgentHatasi(f"Ambar Agent başlatılamadı:\n{(r.stdout or '') + (r.stderr or '')}")
        self.port = int((core.adb("forward", "tcp:0", f"localabstract:{SOKET}").stdout or "0").strip() or 0)
        if not self.port:
            raise AgentHatasi("adb yönlendirmesi kurulamadı.")
        son = None
        for _ in range(40):                 # ajan soketi açana kadar bekle
            try:
                s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
                s.settimeout(300)
                s.sendall((self.anahtar + "\n").encode())
                self.sok = s
                bilgi = self.json("bilgi")
                if bilgi and "_hata" not in bilgi[0]:
                    return bilgi[0]
                son = bilgi
                s.close()
            except (OSError, AgentHatasi) as e:
                son = e
            time.sleep(0.5)
        raise AgentHatasi(f"Ambar Agent'a bağlanılamadı ({son}). Telefonun kilidi açık mı?")

    # -- protokol
    def _oku(self, n):
        b = bytearray()
        while len(b) < n:
            p = self.sok.recv(n - len(b))
            if not p:
                raise AgentHatasi("Bağlantı koptu (kablo çıkmış veya telefon kilitlenmiş olabilir).")
            b += p
        return bytes(b)

    def cerceveler(self, komut):
        self.sok.sendall((komut + "\n").encode("utf-8"))
        while True:
            n = struct.unpack(">i", self._oku(4))[0]
            if n == 0:
                return
            yield self._oku(n)

    def json(self, komut):
        return [json.loads(c) for c in self.cerceveler(komut)]

    def akis(self, uri) -> bytes:
        parca = list(self.cerceveler(f"akis {uri}"))
        if len(parca) == 1 and parca[0][:9] == b'{"_hata":':
            return b""
        return b"".join(parca)

    def bitir(self):
        try:
            if self.sok:
                list(self.cerceveler("bitir"))
                self.sok.close()
        except Exception:
            pass
        if self.port:
            core.adb("forward", "--remove", f"tcp:{self.port}")
        core.adb("shell", f"am force-stop {PAKET}")
        r = core.adb("uninstall", PAKET)
        return "Success" in (r.stdout or "")


# ----------------------------------------------------------------- çıkarma
def _jsonl(yol: Path, satirlar):
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        for r in satirlar:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _hata_mi(satirlar):
    return len(satirlar) == 1 and "_hata" in satirlar[0]


def _b64(v):
    import base64
    return base64.b64decode(v[7:]) if isinstance(v, str) and v.startswith("base64:") else None


def agent_cek(hedef: Path):
    """Android Agent ile çıkarma. Çıktı: <model>_agent_<zaman>/ (kayitlar/, agent/ham/, rehber_fotograflari/, mms_ekleri/)."""
    bas = core.simdi()
    model = core.cihaz_kontrol()
    oturum = hedef / f"{core.guvenli_ad(model)}_agent_{core.zaman_damgasi()}"
    k = oturum / "kayitlar"
    ham = oturum / "agent" / "ham"
    k.mkdir(parents=True, exist_ok=True)
    ham.mkdir(parents=True, exist_ok=True)
    sayilar, notlar = {}, []
    ag = Agent()
    print(">>> Ambar Agent telefona kuruluyor...")
    core.ilerleme(None, "Ambar Agent kuruluyor")
    ag.kur()
    try:
        print(">>> Ambar Agent başlatılıyor (telefonun kilidi açık olmalı)...")
        bilgi = ag.baslat()
        (oturum / "agent" / "bilgi.json").write_text(json.dumps(bilgi, ensure_ascii=False, indent=1), encoding="utf-8")
        verilmeyen = [p.split(".")[-1] for p, v in bilgi.get("izinler", {}).items() if not v]
        print(f"    Bağlandı: {bilgi.get('marka')} {bilgi.get('model')} (Android {bilgi.get('android')})")
        if verilmeyen:
            print(f"    UYARI: verilmeyen izinler: {', '.join(verilmeyen)}")

        def sorgu(ad, uri, yuzde):
            core.ilerleme(yuzde, f"Agent: {ad}")
            s = ag.json(f"sorgu {uri}")
            if _hata_mi(s):
                notlar.append(f"{ad}: {s[0]['_hata']}")
                print(f"    {ad:<14} OKUNAMADI ({s[0]['_hata'][:80]})")
                return None
            _jsonl(ham / f"{ad}.jsonl", s)
            print(f"    {ad:<14} {len(s)} kayıt")
            return s

        # --- rehber
        veri = sorgu("rehber_veri", "content://com.android.contacts/data", 5)
        ham_kisi = sorgu("rehber_ham", "content://com.android.contacts/raw_contacts", 10)
        if veri is not None:
            rehber = rehber_olustur(veri, ham_kisi or [], oturum, ag)
            (k / "rehber.json").write_text(json.dumps(rehber, ensure_ascii=False, indent=1), encoding="utf-8")
            sayilar["rehber"] = len(rehber)
            print(f"    {'rehber':<14} {len(rehber)} kişi")
        # --- takvim
        takvimler = sorgu("takvimler", "content://com.android.calendar/calendars", 25)
        etkinlik = sorgu("etkinlikler", "content://com.android.calendar/events", 28)
        katilimci = sorgu("katilimcilar", "content://com.android.calendar/attendees", 31)
        hatirlatma = sorgu("hatirlatmalar", "content://com.android.calendar/reminders", 33)
        if etkinlik is not None:
            t = takvim_olustur(takvimler or [], etkinlik, katilimci or [], hatirlatma or [])
            (k / "takvim.json").write_text(json.dumps(t, ensure_ascii=False, indent=1), encoding="utf-8")
            sayilar["takvim"] = len(t)
        # --- çağrı / SMS / MMS (adb'deki ile aynı alan adları; inceleme programı doğrudan okur)
        for ad, uri, dosya, y in (("cagrilar", "content://call_log/calls", "cagrilar.json", 40),
                                  ("sms", "content://sms", "sms.json", 50)):
            s = sorgu(ad, uri, y)
            if s is not None:
                (k / dosya).write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
                sayilar[ad] = len(s)
        mms = sorgu("mms", "content://mms", 60)
        parcalar = sorgu("mms_parcalar", "content://mms/part", 62)
        if mms is not None:
            m = mms_olustur(mms, parcalar or [], oturum, ag)
            (k / "mms.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
            sayilar["mms"] = len(m)
        # --- uygulamalar (gerçek ad + simge), hesaplar, bluetooth
        core.ilerleme(80, "Agent: uygulamalar")
        uyg = ag.json("uygulamalar")
        simge = k / "simgeler"
        simge.mkdir(exist_ok=True)
        etiket = {}
        for u in uyg:
            b = _b64(u.pop("simge", None))
            if b:
                (simge / f"{u['paket']}.png").write_bytes(b)
            if u.get("ad"):
                etiket[u["paket"]] = u["ad"]
        (simge / "etiketler.json").write_text(json.dumps(etiket, ensure_ascii=False, indent=1), encoding="utf-8")
        _jsonl(ham / "uygulamalar.jsonl", uyg)
        print(f"    {'uygulamalar':<14} {len(uyg)} paket (ad ve simge)")
        core.uygulamalari_al(k)                         # inceleme tablosu için pm listeleri ve kurulum tarihleri
        for ad in ("hesaplar", "bluetooth"):
            s = ag.json(ad)
            if not _hata_mi(s):
                (oturum / "agent" / f"{ad}.json").write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
                print(f"    {ad:<14} {len(s)}")
        core.hesaplari_al(k)
    finally:
        core.ilerleme(95, "Ambar Agent kaldırılıyor")
        kaldirildi = ag.bitir()
        print(f">>> Ambar Agent {'telefondan kaldırıldı' if kaldirildi else 'KALDIRILAMADI - telefondan elle kaldırın'}.")
    if notlar:
        (oturum / "agent" / "okunamayanlar.txt").write_text("\n".join(notlar) + "\n", encoding="utf-8")
    core.manifest_yaz(oturum, "agent", {"kayit_sayilari": sayilar, "agent_kaldirildi": kaldirildi}, baslangic=bas)
    core.manifest_bitir(oturum)
    core.sha_listesi(oturum)
    print(f"\nTAMAM: {oturum}")
    return oturum


MIME = "vnd.android.cursor.item/"
HESAP_TURU = {"com.google": "Google", "vnd.sec.contact.phone": "Telefon", "vnd.sec.contact.sim": "SIM",
              "vnd.sec.contact.sim2": "SIM 2", "com.android.localphone": "Telefon", "com.android.contacts.sim": "SIM",
              "com.xiaomi": "Xiaomi", "com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business",
              "org.telegram.messenger": "Telegram", "com.viber.voip": "Viber", "com.samsung.android.coreapps": "Samsung",
              "com.osp.app.signin": "Samsung hesabı", "com.huawei.hwid": "Huawei", "": "Telefon"}


def rehber_olustur(veri, ham_kisi, oturum: Path, ag: Agent):
    """ContactsContract.Data satırları -> kişi listesi (numaralar, e-postalar, kurum, adres, not, fotoğraf...)."""
    hesap = {r.get("_id"): (r.get("account_type") or "", r.get("account_name") or "") for r in ham_kisi}
    silinmis = {r.get("_id") for r in ham_kisi if r.get("deleted") == "1"}
    kisiler = {}
    foto_klasor = oturum / "rehber_fotograflari"
    for r in veri:
        cid = r.get("contact_id") or r.get("raw_contact_id")
        k = kisiler.setdefault(cid, {"id": cid, "ad": r.get("display_name") or "", "numaralar": [], "epostalar": [],
                                     "kurum": "", "adres": [], "not": "", "dogum_gunu": "", "web": [],
                                     "hesaplar": set(), "favori": r.get("starred") == "1",
                                     "son_iletisim": r.get("last_time_contacted"), "iletisim_sayisi": r.get("times_contacted"),
                                     "silinmis": False, "foto": None})
        t = (r.get("mimetype") or "").replace(MIME, "")
        if r.get("raw_contact_id") in hesap:
            ht, ha = hesap[r["raw_contact_id"]]
            tur = HESAP_TURU.get(ht, ht)
            k["hesaplar"].add(f"{ha} ({tur})" if ha and ha != ht and "contact" not in ha else tur or "Telefon")
        if r.get("raw_contact_id") in silinmis:
            k["silinmis"] = True
        if t == "phone_v2" and r.get("data1"):
            if r["data1"] not in k["numaralar"]:
                k["numaralar"].append(r["data1"])
        elif t == "email_v2" and r.get("data1"):
            k["epostalar"].append(r["data1"])
        elif t == "organization":
            k["kurum"] = " - ".join(x for x in (r.get("data1"), r.get("data4")) if x)
        elif t == "postal-address_v2" and r.get("data1"):
            k["adres"].append(r["data1"])
        elif t == "note" and r.get("data1"):
            k["not"] = r["data1"]
        elif t == "website" and r.get("data1"):
            k["web"].append(r["data1"])
        elif t == "contact_event" and r.get("data1") and r.get("data2") == "3":
            k["dogum_gunu"] = r["data1"]
        elif t == "photo" and not k["foto"]:
            b = None
            try:
                b = ag.akis(f"content://com.android.contacts/contacts/{cid}/display_photo")     # tam boy
            except Exception:
                pass
            b = b or _b64(r.get("data15"))                                                       # küçük resim
            if b:
                foto_klasor.mkdir(parents=True, exist_ok=True)
                (foto_klasor / f"{cid}.jpg").write_bytes(b)
                k["foto"] = f"rehber_fotograflari/{cid}.jpg"
    out = []
    for k in kisiler.values():
        k["hesaplar"] = sorted(k["hesaplar"])
        out.append(k)
    return sorted(out, key=lambda x: (x["ad"] or "").casefold())


def takvim_olustur(takvimler, etkinlikler, katilimcilar, hatirlatmalar):
    ad = {r.get("_id"): (r.get("calendar_displayName") or r.get("name") or "", r.get("account_name") or "")
          for r in takvimler}
    kat, hat = {}, {}
    for r in katilimcilar:
        kat.setdefault(r.get("event_id"), []).append(
            " ".join(x for x in (r.get("attendeeName"), f"<{r['attendeeEmail']}>" if r.get("attendeeEmail") else None) if x))
    for r in hatirlatmalar:
        hat.setdefault(r.get("event_id"), []).append(f"{r.get('minutes')} dk önce")
    out = []
    for r in etkinlikler:
        t, h = ad.get(r.get("calendar_id"), ("", ""))
        out.append({"id": r.get("_id"), "baslik": r.get("title"), "baslangic": r.get("dtstart"), "bitis": r.get("dtend"),
                    "tum_gun": r.get("allDay") == "1", "konum": r.get("eventLocation"), "aciklama": r.get("description"),
                    "takvim": f"{t} ({h})" if h and h != t else t, "duzenleyen": r.get("organizer"),
                    "tekrar": r.get("rrule"), "silinmis": r.get("deleted") == "1",
                    "katilimcilar": kat.get(r.get("_id"), []), "hatirlatmalar": hat.get(r.get("_id"), [])})
    return out


def mms_olustur(mms, parcalar, oturum: Path, ag: Agent):
    """MMS + metin parçaları + ekler (mms_ekleri/ klasörüne) + adresler (gönderen/alıcılar)."""
    metin, ekler = {}, {}
    klasor = oturum / "mms_ekleri"
    for p in parcalar:
        mid, ct = p.get("mid"), (p.get("ct") or "").lower()
        if ct == "text/plain":
            if p.get("text"):
                metin.setdefault(mid, []).append(p["text"])
        elif ct != "application/smil" and p.get("_id"):
            b = ag.akis(f"content://mms/part/{p['_id']}")
            if b:
                ad = core.guvenli_ad(p.get("name") or p.get("cl") or p.get("fn") or f"ek_{p['_id']}")
                klasor.mkdir(parents=True, exist_ok=True)
                hedef = klasor / f"{mid}_{p['_id']}_{ad}"
                if "." not in ad:
                    uz = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "video/mp4": ".mp4",
                          "audio/amr": ".amr", "text/x-vcard": ".vcf"}.get(ct, "")
                    hedef = hedef.with_name(hedef.name + uz)
                hedef.write_bytes(b)
                ekler.setdefault(mid, []).append(hedef.relative_to(oturum).as_posix())
    out = []
    for m in mms:
        mid = m.get("_id")
        adres = ag.json(f"sorgu content://mms/{mid}/addr")
        kimden = [a.get("address") for a in adres if a.get("type") == "137" and a.get("address") != "insert-address-token"]
        kime = [a.get("address") for a in adres if a.get("type") in ("151", "130", "129")]
        m["body"] = "\n".join(metin.get(mid, []))
        m["address"] = ", ".join(x for x in (kimden if m.get("msg_box") == "1" else kime) if x) or None
        m["ekler"] = ekler.get(mid, [])
        out.append(m)
    return out
