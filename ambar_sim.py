#!/usr/bin/env python3
"""
SIM kart (UICC) imajı - USB akıllı kart okuyucu (PC/SC) üzerinden.

Windows'un kendi WinSCard arayüzü kullanılır (ek sürücü / kütüphane gerekmez; CCID uyumlu her okuyucu çalışır).
Kart yalnızca OKUNUR: SELECT / GET RESPONSE / READ BINARY / READ RECORD / STATUS. Tek yazma sayılabilecek komut,
kullanıcı PIN girerse yapılan VERIFY'dır; kalan deneme hakkı 1 ise PIN gönderilmez (kart kilitlenmesin).

Çıktı: <hedef>/SIM-<ICCID>_uicc_<zaman>/
    uicc.json       çözümlenmiş içerik + her dosyanın ham (hex) verisi
    uicc_apdu.txt   karta gönderilen tüm komutlar ve yanıtları (PIN maskelenir)
    ambar_bilgi.json, SHA256_tum_dosyalar.txt
"""
import ctypes as C
import datetime
import json
from pathlib import Path

# ----------------------------------------------------------------- PC/SC (winscard.dll)
SCARD_SCOPE_USER = 0
SCARD_SHARE_SHARED = 2
SCARD_PROTOCOL_T0, SCARD_PROTOCOL_T1 = 1, 2
SCARD_LEAVE_CARD = 0
HATALAR = {
    0x8010002E: "Bilgisayara bağlı akıllı kart okuyucu bulunamadı.",
    0x8010001D: "Akıllı kart hizmeti (SCardSvr) çalışmıyor.",
    0x8010000C: "Okuyucuda kart yok. SIM kartı okuyucuya takın.",
    0x80100069: "Okuyucuda kart yok. SIM kartı okuyucuya takın.",
    0x80100066: "Kart yanıt vermiyor. Kartı çıkarıp yeniden takın.",
    0x8010000B: "Kart başka bir program tarafından kullanılıyor.",
    0x8010000F: "Kart protokolü desteklenmiyor.",
    0x80100017: "Okuyucu kullanılamıyor (çıkarılmış olabilir).",
}


class SimHatasi(RuntimeError):
    pass


class _IO(C.Structure):
    _fields_ = [("dwProtocol", C.c_uint32), ("cbPciLength", C.c_uint32)]


class PCSC:
    """Tek okuyucuya bağlantı. transmit(apdu: bytes) -> bytes (veri + SW1 SW2)."""

    def __init__(self, okuyucu: str | None = None):
        self.w = C.windll.winscard
        self.ctx = C.c_void_p()
        self._k(self.w.SCardEstablishContext(SCARD_SCOPE_USER, None, None, C.byref(self.ctx)))
        okuyucular = okuyucu_listesi(self)
        if not okuyucular:
            raise SimHatasi(HATALAR[0x8010002E])
        self.okuyucu = okuyucu if okuyucu in okuyucular else okuyucular[0]
        self.kart = C.c_void_p()
        self.protokol = C.c_uint32()
        self._k(self.w.SCardConnectW(self.ctx, C.c_wchar_p(self.okuyucu), SCARD_SHARE_SHARED,
                                     SCARD_PROTOCOL_T0 | SCARD_PROTOCOL_T1, C.byref(self.kart), C.byref(self.protokol)))
        self.atr = self._atr()

    def _k(self, r):
        r &= 0xFFFFFFFF
        if r:
            raise SimHatasi(HATALAR.get(r, f"Akıllı kart hatası 0x{r:08X}"))

    def _atr(self):
        ad_uz, durum, prot, atr_uz = C.c_uint32(0), C.c_uint32(), C.c_uint32(), C.c_uint32(36)
        atr = (C.c_ubyte * 36)()
        self.w.SCardStatusW(self.kart, None, C.byref(ad_uz), C.byref(durum), C.byref(prot), atr, C.byref(atr_uz))
        return bytes(atr[:atr_uz.value])

    def transmit(self, apdu: bytes) -> bytes:
        pci = _IO(self.protokol.value, C.sizeof(_IO))
        yanit = (C.c_ubyte * 260)()
        uz = C.c_uint32(260)
        self._k(self.w.SCardTransmit(self.kart, C.byref(pci), (C.c_ubyte * len(apdu))(*apdu), len(apdu),
                                     None, yanit, C.byref(uz)))
        return bytes(yanit[:uz.value])

    def kapat(self):
        try:
            self.w.SCardDisconnect(self.kart, SCARD_LEAVE_CARD)
        finally:
            self.w.SCardReleaseContext(self.ctx)


def okuyucu_listesi(baglanti=None) -> list[str]:
    """Bağlı PC/SC okuyucuların adları (okuyucu yoksa boş liste)."""
    try:
        w = C.windll.winscard
    except (AttributeError, OSError):
        return []
    ctx = baglanti.ctx if baglanti else C.c_void_p()
    if not baglanti and w.SCardEstablishContext(SCARD_SCOPE_USER, None, None, C.byref(ctx)):
        return []
    try:
        n = C.c_uint32(0)
        if w.SCardListReadersW(ctx, None, None, C.byref(n)) or not n.value:
            return []
        b = C.create_unicode_buffer(n.value)
        if w.SCardListReadersW(ctx, None, b, C.byref(n)):
            return []
        return [x for x in b[:n.value].split("\0") if x]
    finally:
        if not baglanti:
            w.SCardReleaseContext(ctx)


# ----------------------------------------------------------------- dosya tanımları
# (yol, ad, açıklama, çözücü)
MF = "3F00"
TELECOM, GSM, USIM = "3F00/7F10", "3F00/7F20", "ADF"
DOSYALAR = [
    (f"{MF}/2FE2", "EF_ICCID", "Kart seri numarası (ICCID)", "iccid"),
    (f"{MF}/2F05", "EF_PL", "Tercih edilen diller", "dil"),
    (f"{MF}/2F00", "EF_DIR", "Uygulama listesi (USIM/ISIM)", "dir"),
    (f"{TELECOM}/6F3A", "EF_ADN", "Rehber (kısaltılmış arama numaraları)", "adn"),
    (f"{TELECOM}/6F3B", "EF_FDN", "Sabit arama numaraları", "fdn"),
    (f"{TELECOM}/6F49", "EF_SDN", "Servis numaraları", "sdn"),
    (f"{TELECOM}/6F44", "EF_LND", "Son aranan numaralar", "lnd"),
    (f"{TELECOM}/6F40", "EF_MSISDN", "Kendi telefon numarası", "msisdn"),
    (f"{TELECOM}/6F3C", "EF_SMS", "SIM'de saklanan SMS'ler", "sms"),
    (f"{TELECOM}/6F42", "EF_SMSP", "SMS parametreleri (mesaj merkezi)", "smsp"),
    (f"{TELECOM}/6F4A", "EF_EXT1", "Numara uzantıları", None),
    (f"{TELECOM}/5F3A/4F30", "EF_PBR", "USIM telefon defteri kayıt tablosu", None),
    (f"{GSM}/6F07", "EF_IMSI", "Abone kimliği (IMSI)", "imsi"),
    (f"{GSM}/6F46", "EF_SPN", "Servis sağlayıcı adı", "spn"),
    (f"{GSM}/6FAD", "EF_AD", "Yönetim verisi (MNC uzunluğu)", "ad"),
    (f"{GSM}/6F7E", "EF_LOCI", "Son konum bilgisi (TMSI, LAI)", "loci"),
    (f"{GSM}/6F53", "EF_LOCIGPRS", "Son GPRS konum bilgisi (P-TMSI, RAI)", "psloci"),
    (f"{GSM}/6F7B", "EF_FPLMN", "Yasaklı ağlar", "plmn3"),
    (f"{GSM}/6F30", "EF_PLMNsel", "Tercih edilen ağlar", "plmn3"),
    (f"{GSM}/6F31", "EF_HPPLMN", "Ev ağı arama aralığı", None),
    (f"{GSM}/6F38", "EF_SST", "SIM hizmet tablosu", None),
    (f"{GSM}/6F78", "EF_ACC", "Erişim sınıfı", None),
    (f"{GSM}/6F74", "EF_BCCH", "Son kullanılan yayın kanalları", None),
    (f"{USIM}/6F07", "EF_IMSI", "Abone kimliği (IMSI)", "imsi"),
    (f"{USIM}/6F46", "EF_SPN", "Servis sağlayıcı adı", "spn"),
    (f"{USIM}/6FAD", "EF_AD", "Yönetim verisi (MNC uzunluğu)", "ad"),
    (f"{USIM}/6F40", "EF_MSISDN", "Kendi telefon numarası", "msisdn"),
    (f"{USIM}/6F3C", "EF_SMS", "SIM'de saklanan SMS'ler", "sms"),
    (f"{USIM}/6F42", "EF_SMSP", "SMS parametreleri (mesaj merkezi)", "smsp"),
    (f"{USIM}/6F3B", "EF_FDN", "Sabit arama numaraları", "fdn"),
    (f"{USIM}/6F49", "EF_SDN", "Servis numaraları", "sdn"),
    (f"{USIM}/6F7E", "EF_LOCI", "Son konum bilgisi (TMSI, LAI)", "loci"),
    (f"{USIM}/6F73", "EF_PSLOCI", "Son paket veri konumu (P-TMSI, RAI)", "psloci"),
    (f"{USIM}/6FE3", "EF_EPSLOCI", "Son 4G konumu (GUTI, TAI)", "epsloci"),
    (f"{USIM}/6F7B", "EF_FPLMN", "Yasaklı ağlar", "plmn3"),
    (f"{USIM}/6F60", "EF_PLMNwAcT", "Kullanıcının tercih ettiği ağlar", "plmn5"),
    (f"{USIM}/6F61", "EF_OPLMNwAcT", "Operatörün tercih ettiği ağlar", "plmn5"),
    (f"{USIM}/6F62", "EF_HPLMNwAcT", "Ev ağı", "plmn5"),
    (f"{USIM}/6F31", "EF_HPPLMN", "Ev ağı arama aralığı", None),
    (f"{USIM}/6F38", "EF_UST", "USIM hizmet tablosu", None),
    (f"{USIM}/6F56", "EF_EST", "Etkin hizmetler", None),
    (f"{USIM}/6F78", "EF_ACC", "Erişim sınıfı", None),
    (f"{USIM}/6F80", "EF_ICI", "Gelen çağrı bilgisi", None),
    (f"{USIM}/6F81", "EF_OCI", "Giden çağrı bilgisi", None),
    (f"{USIM}/6FC4", "EF_NETPAR", "Ağ parametreleri", None),
    (f"{USIM}/6F5B", "EF_START-HFN", "Başlangıç HFN", None),
]

# ----------------------------------------------------------------- çözücüler
GSM7 = ("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
GSM7_EK = {0x0A: "\f", 0x14: "^", 0x28: "{", 0x29: "}", 0x2F: "\\", 0x3C: "[", 0x3D: "~", 0x3E: "]",
           0x40: "|", 0x65: "€"}


def _bos(b: bytes) -> bool:
    return not b or all(x == 0xFF for x in b)


def bcd(b: bytes) -> str:
    """Yarım bayt yer değiştirmeli BCD (ICCID, numara). A=*, B=#, C=p."""
    out = []
    for x in b:
        for n in (x & 0x0F, x >> 4):
            if n == 0x0F:
                return "".join(out)
            out.append("0123456789*#pwe"[n] if n > 9 else str(n))
    return "".join(out)


def _septetler(b: bytes, adet: int, kayma: int = 0) -> list[int]:
    bit = int.from_bytes(b, "little")
    return [(bit >> (kayma + 7 * i)) & 0x7F for i in range(adet)]


def gsm7_metin(kodlar) -> str:
    out, kac = [], False
    for c in kodlar:
        if kac:
            out.append(GSM7_EK.get(c, "?")); kac = False
        elif c == 0x1B:
            kac = True
        else:
            out.append(GSM7[c] if c < len(GSM7) else "?")
    return "".join(out)


def alfa(b: bytes) -> str:
    """SIM alfa etiketi (TS 31.102 Ek A): GSM 8 bit, 0x80 UCS2, 0x81 / 0x82 sıkıştırılmış UCS2."""
    if _bos(b):
        return ""
    if b[0] == 0x80:
        v = b[1:]
        n = len(v) - len(v) % 2
        s = v[:n].decode("utf-16-be", "replace")
        return s.split("￿")[0].rstrip("\x00")
    if b[0] in (0x81, 0x82) and len(b) > 3:
        uz = b[1]
        taban, i = (b[2] << 7, 3) if b[0] == 0x81 else ((b[2] << 8) | b[3], 4)
        return "".join(chr(taban + (x & 0x7F)) if x & 0x80 else gsm7_metin([x]) for x in b[i:i + uz])
    return gsm7_metin([x for x in b.split(b"\xff")[0]]).rstrip()


def adres(b: bytes) -> str:
    """[uzunluk][TON/NPI][BCD...] -> '+90532...'"""
    if _bos(b) or b[0] in (0, 0xFF) or len(b) < 2:
        return ""
    n = bcd(b[2:1 + b[0]])
    return ("+" if (b[1] & 0x70) == 0x10 and n else "") + n


def plmn(b: bytes) -> str:
    """3 bayt -> 'MCC-MNC'."""
    if _bos(b):
        return ""
    mcc = f"{b[0] & 0xF}{b[0] >> 4}{b[1] & 0xF}"
    mnc = f"{b[2] & 0xF}{b[2] >> 4}" + ("" if b[1] >> 4 == 0xF else str(b[1] >> 4))
    return f"{mcc}-{mnc}"


def numara_kaydi(r: bytes) -> dict | None:
    """ADN/FDN/SDN/LND/MSISDN kaydı: alfa(X) + uzunluk + TON/NPI + 10 bayt BCD + CCP + EXT."""
    if _bos(r) or len(r) < 14:
        return None
    x = len(r) - 14
    ad, num = alfa(r[:x]), adres(r[x:x + 12])
    return {"ad": ad, "numara": num} if ad or num else None


def _scts(b: bytes):
    """SMS zaman damgası (7 yarım bayt çevrik) -> ms (yerel saat dilimi bilgisiyle)."""
    try:
        d = [int(f"{x & 0xF}{x >> 4}") for x in b[:6]]
        tz = b[6]
        dk = int(f"{tz & 0x7}{tz >> 4}") * 15 * (-1 if tz & 0x08 else 1)
        t = datetime.datetime(2000 + d[0], d[1], d[2], d[3], d[4], d[5],
                              tzinfo=datetime.timezone(datetime.timedelta(minutes=dk)))
        return int(t.timestamp() * 1000)
    except (ValueError, IndexError):
        return None


def _oa(b: bytes, i: int):
    """TP-OA/TP-DA: [hane sayısı][TON][...]; alfanümerik gönderen (TON 101) GSM 7 bit."""
    hane, ton = b[i], b[i + 1]
    uz = (hane + 1) // 2
    ham = b[i + 2:i + 2 + uz]
    if (ton & 0x70) == 0x50:
        n = gsm7_metin(_septetler(ham, hane * 4 // 7))
    else:
        n = ("+" if (ton & 0x70) == 0x10 else "") + bcd(ham)[:hane]
    return n, i + 2 + uz


def sms_coz(r: bytes) -> dict | None:
    """EF_SMS kaydı: durum + SMSC adresi + TPDU (SMS-DELIVER / SMS-SUBMIT)."""
    if not r or r[0] & 0x01 == 0 or r[0] == 0xFF:     # 0 / FF: boş kayıt
        return None
    durum = r[0] & 0x07
    try:
        i = 1 + 1 + r[1]                       # SMSC
        ilk = r[i]; i += 1
        mti = ilk & 0x03
        tarih = None
        if mti == 0:                           # SMS-DELIVER (gelen)
            numara, i = _oa(r, i)
            pid, dcs = r[i], r[i + 1]; i += 2
            tarih = _scts(r[i:i + 7]); i += 7
            yon = "Gelen"
        else:                                  # SMS-SUBMIT (giden)
            i += 1                             # TP-MR
            numara, i = _oa(r, i)
            pid, dcs = r[i], r[i + 1]; i += 2
            vpf = (ilk >> 3) & 0x03
            i += {0: 0, 2: 1}.get(vpf, 7)
            yon = "Giden" if durum == 5 else "Gönderilmemiş"
        udl = r[i]; i += 1
        ud = r[i:]
        bas = 0
        if ilk & 0x40 and ud:                  # TP-UDHI: kullanıcı veri başlığı (birleştirilmiş SMS vb.)
            bas = ud[0] + 1
        alfabe = (dcs >> 2) & 0x03 if dcs & 0xC0 in (0x00, 0x40) else (2 if (dcs & 0xF0) == 0xE0 else 0)
        if alfabe == 2:
            metin = ud[bas:udl].decode("utf-16-be", "replace")
        elif alfabe == 1:
            metin = ud[bas:udl].decode("latin-1")
        else:
            kayma = (7 - bas * 8 % 7) % 7 if bas else 0
            atla = (bas * 8 + kayma) // 7
            metin = gsm7_metin(_septetler(ud[bas:], udl - atla, kayma))
        return {"yon": yon, "numara": numara, "metin": metin, "tarih": tarih,
                "okundu": {1: "Evet", 3: "Hayır"}.get(durum), "durum": durum}
    except IndexError:
        return {"yon": "?", "numara": "", "metin": "(çözümlenemedi)", "tarih": None, "okundu": None, "durum": durum}


ICCID_VEREN = {"899001": "Turkcell", "899002": "Vodafone TR", "899003": "Türk Telekom"}


def iccid_operatoru(iccid: str | None) -> str | None:
    """ICCID'nin veren kurum kodundan operatör (89 + ülke 90 + kurum); SPN boş kartlar için."""
    return ICCID_VEREN.get((iccid or "")[:6])


def cozumle(cozucu: str, veri, kayitlar: list[bytes] | None):
    """Dosya içeriğini okunabilir metne çevirir."""
    try:
        if cozucu == "iccid":
            return bcd(veri)
        if cozucu == "imsi":
            if _bos(veri) or veri[0] in (0, 0xFF):
                return ""
            return bcd(veri[1:1 + veri[0]])[1:]
        if cozucu == "dir":
            out = []
            for r in kayitlar or []:
                t = _tlv(r[2:] if r[:1] == b"\x61" else r)
                if t.get(0x4F):
                    out.append(f"{t.get(0x50, b'').decode('ascii', 'replace') or '?'} (AID {t[0x4F].hex().upper()})")
            return ", ".join(out)
        if cozucu == "spn":
            return alfa(veri[1:])
        if cozucu == "ad":
            return f"MNC uzunluğu: {veri[3] & 0x0F}" if len(veri) > 3 else ""
        if cozucu == "dil":
            return ", ".join(veri[i:i + 2].decode("ascii", "replace") for i in range(0, len(veri) - 1, 2)
                             if not _bos(veri[i:i + 2]))
        if cozucu == "loci":
            if len(veri) < 11:
                return ""
            durum = {0: "güncel", 1: "güncel değil", 2: "PLMN yasaklı", 3: "konum alanı yasaklı"}.get(veri[10] & 0x07, "?")
            return (f"TMSI {veri[:4].hex().upper()} · LAI {plmn(veri[4:7])} LAC {int.from_bytes(veri[7:9], 'big')} "
                    f"(0x{veri[7:9].hex().upper()}) · durum: {durum}")
        if cozucu == "psloci":
            if len(veri) < 14:
                return ""
            return (f"P-TMSI {veri[:4].hex().upper()} · RAI {plmn(veri[7:10])} LAC {int.from_bytes(veri[10:12], 'big')} "
                    f"RAC {veri[12]} · durum: {veri[13] & 0x07}")
        if cozucu == "epsloci":
            if len(veri) < 18:
                return ""
            g = veri[:12]
            return (f"GUTI {plmn(g[2:5])} MME grup {int.from_bytes(g[5:7], 'big')} kod {g[7]} M-TMSI {g[8:12].hex().upper()}"
                    f" · TAI {plmn(veri[12:15])} TAC {int.from_bytes(veri[15:17], 'big')} · durum: {veri[17]}")
        if cozucu in ("plmn3", "plmn5"):
            adim = 3 if cozucu == "plmn3" else 5
            return ", ".join(p for p in (plmn(veri[i:i + 3]) for i in range(0, len(veri) - 2, adim)) if p)
        if cozucu in ("adn", "fdn", "sdn", "lnd", "msisdn"):
            k = [numara_kaydi(r) for r in kayitlar or []]
            k = [x for x in k if x]
            return "; ".join(f"{x['ad']} {x['numara']}".strip() for x in k[:20]) + (f" … (+{len(k) - 20})" if len(k) > 20 else "")
        if cozucu == "smsp":
            out = []
            for r in kayitlar or []:
                y = len(r) - 28
                if y >= 0 and not r[y] & 0x02:          # SC adresi var
                    out.append(adres(r[y + 13:y + 25]))
            return "Mesaj merkezi: " + ", ".join(x for x in out if x) if any(out) else ""
        if cozucu == "sms":
            return f"{sum(1 for r in kayitlar or [] if r and r[0] & 1 and r[0] != 0xFF)} / {len(kayitlar or [])} kayıt dolu"
    except Exception as e:      # çözümlenemeyen içerik ham veriyle yine kaydedilir
        return f"(çözümlenemedi: {e})"
    return ""


# ----------------------------------------------------------------- kart erişimi
class Kart:
    """APDU düzeyi okuma. iletim: bytes -> bytes (veri+SW). gunluk: her komut/yanıt satırı."""

    def __init__(self, iletim, gunluk=None):
        self.iletim = iletim
        self.gunluk = gunluk if gunluk is not None else []
        self.cla = 0x00
        self.usim_aid = None

    def gonder(self, apdu: bytes, gizli=False):
        y = self.iletim(apdu)
        sw = y[-2:]
        if sw[0] == 0x6C:                                   # yanlış Le: doğrusuyla tekrar
            apdu = apdu[:4] + bytes([sw[1]])
            y = self.iletim(apdu); sw = y[-2:]
        veri = y[:-2]
        while sw[0] in (0x61, 0x9F):                       # yanıt bekliyor: GET RESPONSE
            g = self.iletim(bytes([self.cla, 0xC0, 0, 0, sw[1]]))
            veri += g[:-2]; sw = g[-2:]
        self.gunluk.append(f"> {(apdu[:5].hex() + ' ' + '**' * (len(apdu) - 5)) if gizli else apdu.hex()}"
                           f"\n< {veri.hex()} {sw.hex()}")
        return veri, (sw[0] << 8) | sw[1]

    # -- seçim
    def sec_fid(self, fid: str):
        f = bytes.fromhex(fid)
        if self.cla == 0x00:
            return self.gonder(bytes([0x00, 0xA4, 0x00, 0x04, 2]) + f)
        return self.gonder(bytes([0xA0, 0xA4, 0x00, 0x00, 2]) + f)

    def sec_yol(self, yol: str):
        """'3F00/7F10/6F3A' veya 'ADF/6F07' -> (fcp, sw)."""
        parca = yol.split("/")
        if parca[0] == "ADF":
            if not self.usim_aid:
                return b"", 0x6A82
            v, sw = self.gonder(bytes([0x00, 0xA4, 0x04, 0x04, len(self.usim_aid)]) + self.usim_aid)
            if sw != 0x9000:
                return v, sw
            parca = parca[1:]
        v, sw = b"", 0x9000
        for fid in parca:
            v, sw = self.sec_fid(fid)
            if sw != 0x9000:
                return v, sw
        return v, sw

    # -- dosya başlığı
    def bilgi(self, fcp: bytes) -> dict:
        """-> {yapi: 'saydam'|'dogrusal'|'dongusel'|'klasor', boyut, kayit_uz, kayit_say}"""
        if self.cla == 0x00:
            tlv = _tlv(fcp[2:] if fcp[:1] == b"\x62" else fcp)
            fd = tlv.get(0x82, b"")
            if not fd or fd[0] & 0x38 == 0x38:
                return {"yapi": "klasor"}
            yapi = {1: "saydam", 2: "dogrusal", 6: "dongusel"}.get(fd[0] & 0x07, "saydam")
            boyut = int.from_bytes(tlv.get(0x80, b"") or b"\0", "big")
            if yapi == "saydam":
                return {"yapi": yapi, "boyut": boyut}
            kuz = int.from_bytes(fd[2:4], "big") if len(fd) >= 4 else 0
            ksay = fd[4] if len(fd) >= 5 else (boyut // kuz if kuz else 0)
            return {"yapi": yapi, "boyut": boyut or kuz * ksay, "kayit_uz": kuz, "kayit_say": ksay}
        # 2G (GSM 11.11) yanıtı
        if len(fcp) < 14 or fcp[6] != 0x04:
            return {"yapi": "klasor"}
        boyut = int.from_bytes(fcp[2:4], "big")
        yapi = {0: "saydam", 1: "dogrusal", 3: "dongusel"}.get(fcp[13], "saydam")
        if yapi == "saydam":
            return {"yapi": yapi, "boyut": boyut}
        kuz = fcp[14] if len(fcp) > 14 else 0
        return {"yapi": yapi, "boyut": boyut, "kayit_uz": kuz, "kayit_say": boyut // kuz if kuz else 0}

    # -- okuma
    def oku_saydam(self, boyut: int):
        veri = b""
        while len(veri) < boyut:
            n = min(255, boyut - len(veri))
            v, sw = self.gonder(bytes([self.cla, 0xB0, len(veri) >> 8, len(veri) & 0xFF, n]))
            if sw != 0x9000:
                return veri, sw
            veri += v
            if not v:
                break
        return veri, 0x9000

    def oku_kayitlar(self, kuz: int, ksay: int):
        out, sw = [], 0x9000
        for i in range(1, ksay + 1):
            v, sw = self.gonder(bytes([self.cla, 0xB2, i, 0x04, kuz]))
            if sw != 0x9000:
                break
            out.append(v)
        return out, sw

    # -- PIN
    def pin_durumu(self):
        """-> (kalan_deneme | None, pin_gerekli). 3G: VERIFY verisiz; 2G: DF_GSM yanıtı."""
        if self.cla == 0x00:
            _, sw = self.gonder(bytes([0x00, 0x20, 0x00, 0x01]))
            if sw == 0x9000 or sw == 0x6984:      # doğrulanmış / PIN kapalı
                return None, False
            if sw & 0xFFF0 == 0x63C0:
                return sw & 0x0F, True
            if sw == 0x6983:
                return 0, True
            return None, False
        v, sw = self.sec_yol(GSM)
        if sw != 0x9000 or len(v) < 19:
            return None, False
        kapali = bool(v[13] & 0x80)
        return v[18] & 0x0F, not kapali

    def pin_dogrula(self, pin: str):
        p = pin.encode("ascii").ljust(8, b"\xff")
        _, sw = self.gonder(bytes([self.cla, 0x20, 0x00, 0x01, 8]) + p, gizli=True)
        return sw

    # -- uygulamalar
    def kur(self):
        """Kart tipini belirler (3G USIM / 2G SIM) ve USIM AID'ini bulur."""
        self.cla = 0x00
        _, sw = self.sec_fid("3F00")
        if sw != 0x9000:
            self.cla = 0xA0
            _, sw = self.sec_fid("3F00")
            if sw != 0x9000:
                raise SimHatasi(f"Kart SIM/USIM komutlarına yanıt vermiyor (SW {sw:04X}). Bu bir SIM kart olmayabilir.")
            return "SIM (2G)"
        fcp, sw = self.sec_yol("3F00/2F00")
        if sw == 0x9000:
            b = self.bilgi(fcp)
            kayitlar, _ = self.oku_kayitlar(b.get("kayit_uz", 0), b.get("kayit_say", 0))
            for r in kayitlar:
                t = _tlv(r[2:] if r[:1] == b"\x61" else r)
                aid = t.get(0x4F, b"")
                if aid[:7] == bytes.fromhex("A0000000871002"):
                    self.usim_aid = aid
                    break
        return "USIM (3G/4G)" if self.usim_aid else "UICC (USIM uygulaması yok)"


def _tlv(b: bytes) -> dict:
    out, i = {}, 0
    while i + 1 < len(b):
        t, n = b[i], b[i + 1]
        if t in (0x00, 0xFF):
            break
        out.setdefault(t, b[i + 2:i + 2 + n])
        i += 2 + n
    return out


# ----------------------------------------------------------------- imaj alma
def imaj_al(kart: Kart, pin: str | None = None, ilerleme=None) -> dict:
    """Tüm bilinen dosyaları okur -> uicc.json içeriği."""
    bildir = ilerleme or (lambda y, m: None)
    tip = kart.kur()
    bildir(5, f"Kart türü: {tip}")
    kalan, gerekli = kart.pin_durumu()
    pin_bilgi = "PIN kapalı / doğrulanmış" if not gerekli else f"PIN gerekli (kalan deneme {kalan})"
    if gerekli:
        if not pin:
            pin_bilgi += " - PIN girilmedi, korumalı dosyalar okunamadı"
        elif kalan is not None and kalan <= 1:
            pin_bilgi += " - kalan hak 1 olduğu için PIN gönderilmedi (kart kilitlenmesin)"
        else:
            sw = kart.pin_dogrula(pin)
            pin_bilgi = "PIN doğrulandı" if sw == 0x9000 else f"PIN YANLIŞ (SW {sw:04X}) - korumalı dosyalar okunamadı"
            if sw != 0x9000:
                kalan2, _ = kart.pin_durumu()
                pin_bilgi += f", kalan deneme {kalan2}"

    dosyalar, rehber, sms = [], [], []
    tanimlar = [d for d in DOSYALAR if not (d[0].startswith("ADF") and not kart.usim_aid)]
    # USIM telefon defteri: EF_PBR'deki ADN dosyalarını listeye ekle
    for i, (yol, ad, aciklama, coz) in enumerate(tanimlar):
        bildir(10 + 85 * i / len(tanimlar), f"Okunuyor: {ad} ({yol})")
        fcp, sw = kart.sec_yol(yol)
        if sw != 0x9000:
            continue
        b = kart.bilgi(fcp)
        if b["yapi"] == "klasor":
            continue
        kayit_listesi = None
        if b["yapi"] == "saydam":
            veri, sw = kart.oku_saydam(b.get("boyut", 0))
        else:
            kayit_listesi, sw = kart.oku_kayitlar(b.get("kayit_uz", 0), b.get("kayit_say", 0))
            veri = b"".join(kayit_listesi)
        d = {"yol": yol, "ad": ad, "aciklama": aciklama, "yapi": b["yapi"], "boyut": b.get("boyut"),
             "kayit_uz": b.get("kayit_uz"), "kayit_say": b.get("kayit_say"),
             "durum": "tamam" if sw == 0x9000 else f"erişim yok (SW {sw:04X})", "ham": veri.hex().upper()}
        if coz and veri:
            d["deger"] = cozumle(coz, veri, kayit_listesi)
        dosyalar.append(d)
        if ad == "EF_PBR" and kayit_listesi:
            for r in kayit_listesi:
                a8 = _tlv(r).get(0xA8, b"")
                c0 = _tlv(a8).get(0xC0, b"")
                if len(c0) >= 2:
                    tanimlar.append((f"{TELECOM}/5F3A/{c0[:2].hex().upper()}", "EF_ADN (USIM)",
                                     "USIM telefon defteri", "adn"))
        if coz in ("adn", "fdn", "sdn", "lnd", "msisdn") and kayit_listesi:
            for n, r in enumerate(kayit_listesi, 1):
                k = numara_kaydi(r)
                if k:
                    rehber.append({"liste": coz, "kayit": n, "dosya": yol, **k})
        if coz == "sms" and kayit_listesi:
            for n, r in enumerate(kayit_listesi, 1):
                m = sms_coz(r)
                if m:
                    sms.append({"kayit": n, "dosya": yol, **m})

    # aynı içerik hem 2G hem USIM yolundan okunmuş olabilir: tekille
    def tekil(liste, anahtar):
        g, out = set(), []
        for x in liste:
            k = anahtar(x)
            if k not in g:
                g.add(k); out.append(x)
        return out
    rehber = tekil(rehber, lambda x: (x["liste"], x["ad"], x["numara"]))
    sms = tekil(sms, lambda x: (x["numara"], x["tarih"], x["metin"]))
    deger = lambda ad: next((d.get("deger") for d in dosyalar if d["ad"] == ad and d.get("deger")), None)
    msisdn = next((r["numara"] for r in rehber if r["liste"] == "msisdn" and r["numara"]), None)
    imsi = deger("EF_IMSI")
    ad_d = next((d for d in dosyalar if d["ad"] == "EF_AD" and d.get("ham")), None)
    mnc_uz = (bytes.fromhex(ad_d["ham"])[3] & 0x0F) if ad_d and len(ad_d["ham"]) >= 8 else 2
    bildir(100, "Kart okundu")
    return {
        "tip": tip, "pin": pin_bilgi,
        "ozet": {"iccid": deger("EF_ICCID"), "imsi": imsi, "msisdn": msisdn,
                 "spn": deger("EF_SPN") or iccid_operatoru(deger("EF_ICCID")),
                 "mcc_mnc": f"{imsi[:3]}-{imsi[3:3 + (mnc_uz if mnc_uz in (2, 3) else 2)]}" if imsi else None,
                 "dil": deger("EF_PL"), "konum": deger("EF_LOCI"), "konum_4g": deger("EF_EPSLOCI")},
        "dosyalar": dosyalar, "rehber": rehber, "sms": sms,
    }


def uicc_cek(hedef: Path, pin: str | None = None, okuyucu: str | None = None, iletim=None, atr=b"", okuyucu_adi=None):
    """Kart okuyucudaki SIM'in imajını alır. iletim verilirse (test) PC/SC kullanılmaz."""
    import ambar as core
    bas = core.simdi()
    baglanti = None
    if iletim is None:
        baglanti = PCSC(okuyucu)
        iletim, atr, okuyucu_adi = baglanti.transmit, baglanti.atr, baglanti.okuyucu
    print(f"Okuyucu : {okuyucu_adi}")
    print(f"ATR     : {atr.hex(' ').upper()}")
    kart = Kart(iletim)
    try:
        sonuc = imaj_al(kart, pin, lambda y, m: (core.ilerleme(y, m), print("   ", m)))
    finally:
        if baglanti:
            baglanti.kapat()
    o = sonuc["ozet"]
    iccid = o.get("iccid") or "bilinmeyen"
    oturum = hedef / f"SIM-{core.guvenli_ad(iccid)}_uicc_{core.zaman_damgasi()}"
    oturum.mkdir(parents=True, exist_ok=True)
    sonuc.update(okuyucu=okuyucu_adi, atr=atr.hex().upper(), alinma=core.simdi())
    (oturum / "uicc.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    (oturum / "uicc_apdu.txt").write_text("\n".join(kart.gunluk) + "\n", encoding="utf-8")
    cihaz = {"marka": "SIM kart", "model": o.get("spn") or sonuc["tip"],
             "ticari_ad": f"SIM kart {o.get('spn') or ''} ({iccid})".replace("  ", " "), "seri": iccid}
    core.manifest_yaz(oturum, "uicc", {"cihaz": cihaz, "kart_okuyucu": okuyucu_adi, "atr": atr.hex().upper(),
                                       "kayit_sayilari": {"dosya": len(sonuc["dosyalar"]), "rehber": len(sonuc["rehber"]),
                                                          "sms": len(sonuc["sms"])}}, baslangic=bas)
    core.manifest_bitir(oturum)
    core.sha_listesi(oturum)
    print(f"\n    Kart türü : {sonuc['tip']}  ·  {sonuc['pin']}")
    print(f"    ICCID     : {iccid}\n    IMSI      : {o.get('imsi') or '-'}\n    Numara    : {o.get('msisdn') or '-'}")
    print(f"    Operatör  : {o.get('spn') or '-'}")
    print(f"    Dosya {len(sonuc['dosyalar'])} · rehber {len(sonuc['rehber'])} · SMS {len(sonuc['sms'])}")
    print(f"\nTAMAM: {oturum}")
    return oturum
