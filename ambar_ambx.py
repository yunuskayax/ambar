#!/usr/bin/env python3
"""
.ambx - Ambar tek dosya rapor biçimi.

Düzen (tüm sayılar little-endian):
  [0..16)    başlık   : b"AMBARX" + sürüm(2 bayt) + 8 bayt ayrılmış
  [16..)     öğeler   : her öğenin baytları art arda (dosyalar sıkıştırılmaz -> doğrudan rastgele erişim)
  [dizin]    zlib(JSON): {"surum", "olusturma", "ogeler": [{"y": yol, "o": konum, "b": boyut, "h": sha256,
                                                             "z": 1 ise zlib'li, "ab": açık boyut, "m": mtime ms}]}
  [son 64]   kuyruk   : b"AMBXSON1" + dizin konumu(8) + dizin uzunluğu(8) + sha256(dizin)(32) + 8 bayt ayrılmış

Dosya baştan sona tek geçişte yazılır; bu sırada tüm dosyanın SHA-256 ve MD5 özetleri hesaplanıp yan dosyalara
(<ad>.ambx.sha256 / .md5, sha256sum biçimi - TeraCopy vb. ile doğrulanabilir) yazılır.
"""
import hashlib
import io
import json
import os
import struct
import zlib
from pathlib import Path

BASLIK = b"AMBARX" + struct.pack("<H", 1) + b"\x00" * 8
KUYRUK_IMZA = b"AMBXSON1"
KUYRUK = 64
UZANTI = ".ambx"


class AmbxYazici:
    def __init__(self, yol: Path):
        self.yol = Path(yol)
        self.gecici = self.yol.with_name(self.yol.name + ".part")
        self.f = open(self.gecici, "wb")
        self.sha = hashlib.sha256()
        self.md5 = hashlib.md5()
        self.ogeler = []
        self.konum = 0
        self._yaz(BASLIK)

    def _yaz(self, b: bytes):
        self.f.write(b)
        self.sha.update(b)
        self.md5.update(b)
        self.konum += len(b)

    def ekle_bayt(self, ad: str, veri: bytes, sikistir=False, mtime=None):
        h = hashlib.sha256(veri).hexdigest()
        govde = zlib.compress(veri, 6) if sikistir else veri
        o = {"y": ad, "o": self.konum, "b": len(govde), "h": h}
        if sikistir:
            o.update(z=1, ab=len(veri))
        if mtime:
            o["m"] = int(mtime)
        self._yaz(govde)
        self.ogeler.append(o)

    def ekle_akis(self, ad: str, kaynak, mtime=None, ilerleme=None) -> str:
        """Dosya nesnesinden (sıkıştırmadan) kopyalar; öğenin SHA-256'sını döndürür."""
        h = hashlib.sha256()
        bas = self.konum
        for parca in iter(lambda: kaynak.read(1024 * 1024), b""):
            h.update(parca)
            self._yaz(parca)
            if ilerleme:
                ilerleme(len(parca))
        o = {"y": ad, "o": bas, "b": self.konum - bas, "h": h.hexdigest()}
        if mtime:
            o["m"] = int(mtime)
        self.ogeler.append(o)
        return o["h"]

    def kapat(self, ek: dict | None = None) -> dict:
        import datetime
        dizin = zlib.compress(json.dumps({"surum": 1, "olusturma": datetime.datetime.now().isoformat(timespec="seconds"),
                                          **(ek or {}), "ogeler": self.ogeler}, ensure_ascii=False).encode("utf-8"), 9)
        konum = self.konum
        self._yaz(dizin)
        self._yaz(KUYRUK_IMZA + struct.pack("<QQ", konum, len(dizin)) + hashlib.sha256(dizin).digest() + b"\x00" * 8)
        self.f.close()
        self.gecici.replace(self.yol)
        sha, md5 = self.sha.hexdigest(), self.md5.hexdigest()
        (self.yol.parent / (self.yol.name + ".sha256")).write_text(f"{sha} *{self.yol.name}\n", encoding="utf-8")
        (self.yol.parent / (self.yol.name + ".md5")).write_text(f"{md5} *{self.yol.name}\n", encoding="utf-8")
        return {"sha256": sha, "md5": md5, "boyut": self.konum, "oge": len(self.ogeler)}

    def iptal(self):
        try:
            self.f.close()
        finally:
            self.gecici.unlink(missing_ok=True)


class _Pencere(io.RawIOBase):
    """Kap dosyasının bir bölümünü ayrı bir dosya gibi okutur (seek/read)."""

    def __init__(self, yol, konum, boyut):
        super().__init__()
        self.f = open(yol, "rb")
        self.bas, self.boyut, self.poz = konum, boyut, 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.poz

    def seek(self, n, nereden=0):
        self.poz = max(0, min(self.boyut, n if nereden == 0 else self.poz + n if nereden == 1 else self.boyut + n))
        return self.poz

    def read(self, n=-1):
        kalan = self.boyut - self.poz
        n = kalan if n is None or n < 0 or n > kalan else n
        if n <= 0:
            return b""
        self.f.seek(self.bas + self.poz)
        b = self.f.read(n)
        self.poz += len(b)
        return b

    def readinto(self, tampon):
        b = self.read(len(tampon))
        tampon[:len(b)] = b
        return len(b)

    def close(self):
        self.f.close()
        super().close()


class AmbxOkuyucu:
    def __init__(self, yol: Path):
        self.yol = Path(yol)
        with open(self.yol, "rb") as f:
            if f.read(6) != BASLIK[:6]:
                raise ValueError("Bu dosya bir Ambar raporu (.ambx) değil.")
            f.seek(-KUYRUK, os.SEEK_END)
            k = f.read(KUYRUK)
            if k[:8] != KUYRUK_IMZA:
                raise ValueError("Rapor dosyası eksik veya bozuk (kuyruk bulunamadı). Kopyalama tamamlanmamış olabilir.")
            konum, uzunluk = struct.unpack("<QQ", k[8:24])
            f.seek(konum)
            ham = f.read(uzunluk)
        if hashlib.sha256(ham).digest() != k[24:56]:
            raise ValueError("Rapor dizini bozuk (SHA-256 uyuşmuyor).")
        self.dizin = json.loads(zlib.decompress(ham))
        self.ogeler = {o["y"]: o for o in self.dizin["ogeler"]}

    def var(self, ad: str) -> bool:
        return ad in self.ogeler

    def oku(self, ad: str) -> bytes:
        o = self.ogeler[ad]
        with open(self.yol, "rb") as f:
            f.seek(o["o"])
            b = f.read(o["b"])
        return zlib.decompress(b) if o.get("z") else b

    def ac(self, ad: str):
        """(dosya nesnesi, boyut, mtime_ms) - sıkıştırılmamış öğeler için."""
        o = self.ogeler.get(ad)
        if not o:
            raise FileNotFoundError(ad)
        if o.get("z"):
            return io.BytesIO(self.oku(ad)), o["ab"], o.get("m")
        return _Pencere(self.yol, o["o"], o["b"]), o["b"], o.get("m")

    def dogrula(self, ilerleme=None) -> dict:
        """Her öğenin SHA-256 özetini dizindekiyle ve (varsa) yan .sha256 dosyasını tüm dosyayla karşılaştırır."""
        hatali = []
        toplam = sum(o["b"] for o in self.ogeler.values()) or 1
        biten = 0
        for ad, o in self.ogeler.items():
            h = hashlib.sha256()
            with open(self.yol, "rb") as f:
                f.seek(o["o"])
                kalan = o["b"]
                govde = io.BytesIO() if o.get("z") else None
                while kalan > 0:
                    p = f.read(min(1024 * 1024, kalan))
                    if not p:
                        break
                    kalan -= len(p)
                    biten += len(p)
                    if govde is not None:
                        govde.write(p)
                    else:
                        h.update(p)
                    if ilerleme:
                        ilerleme(biten / toplam * 100)
            if govde is not None:
                h.update(zlib.decompress(govde.getvalue()))
            if h.hexdigest() != o["h"]:
                hatali.append(ad)
        tum = None
        yan = self.yol.parent / (self.yol.name + ".sha256")
        if yan.exists():
            beklenen = yan.read_text(encoding="utf-8").split()[0].lower()
            h = hashlib.sha256()
            with open(self.yol, "rb") as f:
                for p in iter(lambda: f.read(4 * 1024 * 1024), b""):
                    h.update(p)
            tum = h.hexdigest() == beklenen
        return {"oge": len(self.ogeler), "hatali": hatali, "tum_dosya": tum}
