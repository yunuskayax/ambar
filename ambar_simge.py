#!/usr/bin/env python3
"""
Uygulama simgeleri - APK içindeki ikili AndroidManifest.xml ve resources.arsc çözülerek bulunur.

* Klasik simge (PNG/WebP/JPG): en yüksek yoğunluklu dosya olduğu gibi alınır.
* Uyarlanabilir simge (Android 8+ adaptive-icon): arka plan + ön plan katmanları tek bir SVG'de birleştirilir;
  katman vektör çizim (VectorDrawable) ise SVG'ye çevrilir, resimse SVG içine gömülür.
Kaynak isimleri gizlenmiş (obfuscated) APK'larda da çalışır; dosya adına değil kaynak tablosuna bakılır.

Kaynak: telefondaki APK (adb exec-out unzip -p; APK'nın tamamı çekilmez) veya yedekteki APK dosyaları.
"""
import base64
import html
import re
import struct
import zipfile

# ----------------------------------------------------------------- ikili XML / kaynak tablosu
ATTR_ICON, ATTR_ROUND_ICON, ATTR_DRAWABLE = 0x01010002, 0x0101052C, 0x01010199
T_REF, T_ATTR, T_STRING, T_FLOAT, T_DIM, T_INT_DEC, T_INT_HEX, T_BOOL = 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11, 0x12
T_COLOR_MIN, T_COLOR_MAX = 0x1C, 0x1F
# android.R.color (çerçeve renkleri; uygulamanın tablosunda yoktur)
SISTEM_RENK = {0x0106000B: "#ffffff", 0x0106000C: "#000000", 0x0106000D: "rgba(0,0,0,0)",
               0x01060000: "#000000", 0x01060003: "#ffffff", 0x01060001: "#aaaaaa"}
RASTER = (".png", ".webp", ".jpg", ".jpeg")


class _Havuz:
    """String pool (tembel çözme: büyük tablolarda yalnızca gereken dizgiler okunur)."""

    def __init__(self, b, o):
        _, hs, _ = struct.unpack_from("<HHI", b, o)
        self.n, _, bayrak, bas, _ = struct.unpack_from("<IIIII", b, o + 8)
        self.b, self.utf8 = b, bool(bayrak & 0x100)
        self.ofs = o + hs
        self.bas = o + bas
        self._ont = {}

    def __call__(self, i):
        if i is None or i < 0 or i >= self.n or i == 0xFFFFFFFF:
            return None
        if i in self._ont:
            return self._ont[i]
        b = self.b
        p = self.bas + struct.unpack_from("<I", b, self.ofs + 4 * i)[0]
        if self.utf8:
            p += 2 if b[p] & 0x80 else 1
            n = b[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | b[p + 1]; p += 2
            else:
                p += 1
            s = b[p:p + n].decode("utf-8", "replace")
        else:
            n = struct.unpack_from("<H", b, p)[0]; p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", b, p)[0]; p += 2
            s = b[p:p + 2 * n].decode("utf-16-le", "replace")
        self._ont[i] = s
        return s


def axml_ogeler(b: bytes):
    """İkili XML -> [(derinlik, etiket, {ad: (tür, veri, ham_metin)})] (yalnızca başlangıç etiketleri)."""
    if len(b) < 8 or struct.unpack_from("<H", b, 0)[0] != 0x0003:
        return []
    o, havuz, harita, derinlik, out = 8, None, (), 0, []
    while o + 8 <= len(b):
        t, hs, boy = struct.unpack_from("<HHI", b, o)
        if boy < 8:
            break
        if t == 0x0001:
            havuz = _Havuz(b, o)
        elif t == 0x0180:
            harita = struct.unpack_from(f"<{(boy - hs) // 4}I", b, o + hs)
        elif t == 0x0102 and havuz:
            _, ad = struct.unpack_from("<II", b, o + 16)
            a_bas, a_boy, a_say = struct.unpack_from("<HHH", b, o + 24)
            nit = {}
            for i in range(a_say):
                a = o + 16 + a_bas + i * a_boy
                _, an, ham, _, _, tur, veri = struct.unpack_from("<IIIHBBI", b, a)
                anahtar = harita[an] if an < len(harita) and harita[an] else havuz(an)
                nit[anahtar] = (tur, veri, havuz(ham) if ham != 0xFFFFFFFF else None)
                isim = havuz(an)
                if isim and isim not in nit:
                    nit[isim] = nit[anahtar]
            out.append((derinlik, havuz(ad), nit))
            derinlik += 1
        elif t == 0x0103:
            derinlik -= 1
        o += boy
    return out


class KaynakTablosu:
    """resources.arsc: kaynak kimliği -> [(yoğunluk, tür, veri)]; dizgi değerleri self.dizgi(i)."""

    def __init__(self, b: bytes):
        self.b = b
        self.dizgi = lambda i: None
        self.paketler = {}          # paket id -> (başlangıç, bitiş, başlık uzunluğu)
        t, hs, boy = struct.unpack_from("<HHI", b, 0)
        o = hs
        while o + 8 <= min(boy, len(b)):
            t, h, s = struct.unpack_from("<HHI", b, o)
            if s < 8:
                break
            if t == 0x0001:
                self.dizgi = _Havuz(b, o)
            elif t == 0x0200:
                self.paketler[struct.unpack_from("<I", b, o + 8)[0]] = (o, o + s, h)
            o += s
        self._tip = {}

    def _tip_parcalari(self, pid, tid):
        anahtar = (pid, tid)
        if anahtar not in self._tip:
            liste = []
            if pid in self.paketler:
                bas, bit, h = self.paketler[pid]
                q, b = bas + h, self.b
                while q + 8 <= bit:
                    t, hh, s = struct.unpack_from("<HHI", b, q)
                    if s < 8:
                        break
                    if t == 0x0201 and b[q + 8] == tid:
                        liste.append((q, hh))
                    q += s
            self._tip[anahtar] = liste
        return self._tip[anahtar]

    def coz(self, rid: int, dil=False):
        """-> [(yoğunluk, tür, veri)]; dil=True ise [(dil kodu, tür, veri)]."""
        b, out = self.b, []
        pid, tid, eid = rid >> 24, (rid >> 16) & 0xFF, rid & 0xFFFF
        for q, hh in self._tip_parcalari(pid, tid):
            bayrak = b[q + 9]
            sayi, e_bas = struct.unpack_from("<II", b, q + 12)
            yogunluk = struct.unpack_from("<H", b, q + 20 + 14)[0]
            if dil:
                yogunluk = b[q + 28:q + 30].rstrip(b"\0").decode("ascii", "replace")
            ofs = None
            if bayrak & 0x01:                                  # seyrek
                for i in range(sayi):
                    idx, o4 = struct.unpack_from("<HH", b, q + hh + 4 * i)
                    if idx == eid:
                        ofs = o4 * 4
                        break
                    if idx > eid:
                        break
            elif eid < sayi:
                if bayrak & 0x02:                              # 16 bit konum
                    v = struct.unpack_from("<H", b, q + hh + 2 * eid)[0]
                    ofs = None if v == 0xFFFF else v * 4
                else:
                    v = struct.unpack_from("<I", b, q + hh + 4 * eid)[0]
                    ofs = None if v == 0xFFFFFFFF else v
            if ofs is None:
                continue
            e = q + e_bas + ofs
            e_boy, e_bayrak = struct.unpack_from("<HH", b, e)
            if e_bayrak & 0x08:                                # sıkı kayıt (compact)
                out.append((yogunluk, e_bayrak >> 8, struct.unpack_from("<I", b, e + 4)[0]))
            elif not e_bayrak & 0x01:                          # basit değer (karmaşık/stil değil)
                _, _, tur, veri = struct.unpack_from("<HBBI", b, e + e_boy)
                out.append((yogunluk, tur, veri))
        return out


def _renk(v: int) -> str:
    a, r, g, b = (v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF
    return f"rgba({r},{g},{b},{round(a / 255, 3)})"


def _sayi(tur, veri):
    if tur == T_FLOAT:
        return struct.unpack("<f", struct.pack("<I", veri))[0]
    if tur in (T_INT_DEC, T_INT_HEX):
        return veri - (1 << 32) if veri & 0x80000000 else veri
    if tur == T_DIM:                                           # karmaşık birim: mantis * taban
        mant = (veri >> 8) - (1 << 24) if veri & 0x80000000 else veri >> 8
        return mant * (1.0, 1 / 128, 1 / 32768, 1 / 8388608)[(veri >> 4) & 3]
    return None


# ----------------------------------------------------------------- simge çözücü
class Cozucu:
    """oku(yol) -> bytes | None ile APK içeriğine erişir (telefon veya yerel dosya).
    oku_hepsi(yol) -> [bytes]: bölünmüş (split) APK'larda her parçanın kendi resources.arsc'si vardır
    (ör. yoğunluk dosyaları split_config.xxhdpi.apk'nın tablosundadır); hepsi birlikte aranır."""

    def __init__(self, oku, oku_hepsi=None):
        self.oku = oku
        self.oku_hepsi = oku_hepsi or (lambda y: [b for b in [oku(y)] if b])
        self.tablolar = None
        self.defs = []

    def _tablo(self):
        if self.tablolar is None:
            self.tablolar = []
            for b in self.oku_hepsi("resources.arsc"):
                try:
                    self.tablolar.append(KaynakTablosu(b))
                except struct.error:
                    pass
        return self.tablolar

    def _degerler(self, rid, derin=0):
        """Kaynağı (takma adları izleyerek) [(yoğunluk, tür, veri)] olarak çözer; dizgi değerler metne çevrilir."""
        if derin > 4:
            return []
        out = []
        for t in self._tablo():
            for y, tur, veri in t.coz(rid):
                if tur == T_REF and veri and veri >> 24 not in (0x01, 0x00):
                    out += [(y, a, b) for _, a, b in self._degerler(veri, derin + 1)]
                elif tur == T_STRING:
                    out.append((y, tur, t.dizgi(veri)))
                else:
                    out.append((y, tur, veri))
        return out

    def _dosya(self, rid, raster_once=True):
        """Kaynağın dosya yolu: önce en yüksek yoğunluklu resim, yoksa XML."""
        yollar = []
        for y, tur, veri in self._degerler(rid):
            if tur == T_STRING:
                p = veri
                if p:
                    yogun = 160 if y == 0 else (-1 if y in (0xFFFE, 0xFFFF) else y)
                    yollar.append((p.lower().endswith(RASTER), yogun, p))
        if not yollar:
            return None
        yollar.sort(key=lambda x: (x[0] if raster_once else not x[0], x[1]), reverse=True)
        return yollar[0][2]

    def _renk_coz(self, tur, veri):
        """Renk (rgba) veya renk geçişi (url(#id); tanımı self.defs'e eklenir)."""
        if T_COLOR_MIN <= tur <= T_COLOR_MAX:
            return _renk(veri)
        if tur != T_REF:
            return None
        if veri in SISTEM_RENK:
            return SISTEM_RENK[veri]
        for _, t2, v2 in self._degerler(veri):
            if T_COLOR_MIN <= t2 <= T_COLOR_MAX:
                return _renk(v2)
            if t2 == T_REF and v2 in SISTEM_RENK:
                return SISTEM_RENK[v2]
            if t2 == T_STRING and (v2 or "").endswith(".xml"):
                return self._xml_renk(self.oku(v2) or b"")
        return None

    def _xml_renk(self, x: bytes):
        """<gradient> (linear / radial / sweep) -> SVG tanımı; <selector> -> ilk öğenin rengi."""
        ogeler = axml_ogeler(x)
        if not ogeler:
            return None
        kok_ad, kok = ogeler[0][1], ogeler[0][2]
        if kok_ad == "selector":
            for _, ad, n in ogeler[1:]:
                if ad == "item" and n.get("color"):
                    return self._renk_coz(*n["color"][:2])
            return None
        if kok_ad != "gradient":
            return None
        s = lambda k, v=0.0: (lambda d: _sayi(*d[:2]) if d and _sayi(*d[:2]) is not None else v)(kok.get(k))
        duraklar = []
        for _, ad, n in ogeler[1:]:
            if ad == "item" and n.get("color"):
                r = self._renk_coz(*n["color"][:2])
                if r:
                    duraklar.append((_sayi(*n["offset"][:2]) if n.get("offset") else 0, r))
        if not duraklar:
            for k, ofs in (("startColor", 0), ("centerColor", 0.5), ("endColor", 1)):
                if kok.get(k):
                    r = self._renk_coz(*kok[k][:2])
                    if r:
                        duraklar.append((ofs, r))
        if not duraklar:
            return None
        self._sayac = getattr(self, "_sayac", 0) + 1
        gid = f"g{self._sayac}"
        dur = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in duraklar)
        tur = int(_sayi(*kok["type"][:2])) if kok.get("type") else 0
        if tur == 1:
            tanim = (f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{s("centerX")}" cy="{s("centerY")}" '
                     f'r="{s("gradientRadius", 1)}">{dur}</radialGradient>')
        else:   # doğrusal (sweep yaklaşık olarak doğrusal çizilir)
            tanim = (f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{s("startX")}" y1="{s("startY")}" '
                     f'x2="{s("endX")}" y2="{s("endY")}">{dur}</linearGradient>')
        self.defs.append(tanim)
        return f"url(#{gid})"

    # -- VectorDrawable -> SVG
    # -- çizim (tüm çıktılar 108x108 birimlik alanda SVG parçasıdır)
    def _yollar(self, rid):
        """Kaynağın dosyaları: önce yoğunluktan bağımsız (anydpi, genelde XML), sonra yüksekten düşüğe yoğunluk."""
        out = []
        for y, tur, veri in self._degerler(rid):
            if tur == T_STRING and veri and veri.startswith("res/"):
                out.append((10 ** 6 if y in (0xFFFE, 0xFFFF) else (160 if y == 0 else y), veri))
        return [p for _, p in sorted(set(out), reverse=True)]

    def _ref_ciz(self, rid, derin=0):
        if derin > 6:
            return ""
        renk = SISTEM_RENK.get(rid)
        if renk:
            return f'<rect width="108" height="108" fill="{renk}"/>'
        en_iyi_resim = None
        for yol in self._yollar(rid):
            b = self.oku(yol)
            tur = icerik_turu(b)
            if tur == "xml":
                og = axml_ogeler(b)
                if og:
                    r = self._eleman_ciz(og, 0, derin + 1)
                    if r:
                        return r
            elif tur and not en_iyi_resim:
                en_iyi_resim = (tur, b)
        if en_iyi_resim:
            return (f'<image width="108" height="108" preserveAspectRatio="xMidYMid slice" '
                    f'href="data:{en_iyi_resim[0]};base64,{base64.b64encode(en_iyi_resim[1]).decode()}"/>')
        for _, t2, v2 in self._degerler(rid):                  # renk kaynağı
            if T_COLOR_MIN <= t2 <= T_COLOR_MAX:
                return f'<rect width="108" height="108" fill="{_renk(v2)}"/>'
        return ""

    @staticmethod
    def _cocuklar(og, i):
        d = og[i][0]
        for j in range(i + 1, len(og)):
            if og[j][0] <= d:
                break
            if og[j][0] == d + 1:
                yield j

    def _ic_ciz(self, og, i, derin):
        """Öğenin android:drawable / android:src özniteliği veya içindeki ilk çizim."""
        n = og[i][2]
        d = n.get("drawable") or n.get(ATTR_DRAWABLE) or n.get("src")
        if d:
            renk = self._renk_coz(d[0], d[1])
            if renk:
                return f'<rect width="108" height="108" fill="{renk}"/>'
            if d[0] == T_REF:
                return self._ref_ciz(d[1], derin)
        for j in self._cocuklar(og, i):
            r = self._eleman_ciz(og, j, derin)
            if r:
                return r
        return ""

    def _eleman_ciz(self, og, i, derin=0):
        ad, n = og[i][1], og[i][2]

        def s(k, v=0.0):
            d = n.get(k)
            x = _sayi(*d[:2]) if d else None
            return v if x is None else x

        def kesir(k, v):
            if n.get(k) and n[k][0] == 0x06:          # kesir (%) -> 108 birimlik alan
                return (_sayi(T_DIM, n[k][1]) or 0) * 108
            return s(k, v)
        if ad == "vector":
            v = self._vektor_svg(og, i)
            return f'<svg width="108" height="108" viewBox="0 0 {v[0]} {v[1]}">{v[2]}</svg>' if v else ""
        if ad == "layer-list":
            return "".join(self._ic_ciz(og, j, derin) for j in self._cocuklar(og, i) if og[j][1] == "item")
        if ad == "rotate":
            ic = self._ic_ciz(og, i, derin)
            return (f'<g transform="rotate({s("fromDegrees")} {kesir("pivotX", 54)} {kesir("pivotY", 54)})">{ic}</g>'
                    if ic else "")
        if ad in ("inset", "scale"):
            ic = self._ic_ciz(og, i, derin)
            pay = kesir("inset", 0) if n.get("inset") else 0
            if ic and 0 < pay < 54:
                return f'<g transform="translate({pay} {pay}) scale({(108 - 2 * pay) / 108})">{ic}</g>'
            return ic
        if ad in ("selector", "level-list"):
            for j in self._cocuklar(og, i):
                r = self._ic_ciz(og, j, derin)
                if r:
                    return r
            return ""
        if ad in ("bitmap", "nine-patch", "item", "animated-vector", "clip"):
            return self._ic_ciz(og, i, derin)
        if ad == "color" and n.get("color"):
            r = self._renk_coz(*n["color"][:2])
            return f'<rect width="108" height="108" fill="{r}"/>' if r else ""
        if ad == "shape":
            for j in self._cocuklar(og, i):
                if og[j][1] == "solid" and og[j][2].get("color"):
                    r = self._renk_coz(*og[j][2]["color"][:2])
                    if r:
                        return f'<rect width="108" height="108" fill="{r}"/>'
        return ""

    # -- VectorDrawable -> SVG
    def _vektor_svg(self, og, i=0):
        if not og or og[i][1] != "vector":
            return None
        kok, d_kok = og[i][2], og[i][0]

        def sayi(n, k, v=0):
            d = n.get(k)
            x = _sayi(*d[:2]) if d else None
            return v if x is None else x
        vw, vh = sayi(kok, "viewportWidth", 24) or 24, sayi(kok, "viewportHeight", 24) or 24
        parca, yigin, d0 = [], [], len(self.defs)
        for derin, ad, n in og[i + 1:]:
            if derin <= d_kok:
                break
            while yigin and yigin[-1] >= derin:
                parca.append("</g>"); yigin.pop()
            if ad == "group":
                px, py = sayi(n, "pivotX"), sayi(n, "pivotY")
                d = (f"translate({sayi(n, 'translateX')} {sayi(n, 'translateY')}) translate({px} {py}) "
                     f"rotate({sayi(n, 'rotation')}) scale({sayi(n, 'scaleX', 1)} {sayi(n, 'scaleY', 1)}) "
                     f"translate({-px} {-py})")
                parca.append(f'<g transform="{d}">'); yigin.append(derin)
            elif ad == "path":
                pd = n.get("pathData")
                if not pd or not pd[2]:
                    continue
                dolgu = n.get("fillColor")
                dolgu = self._renk_coz(dolgu[0], dolgu[1]) if dolgu else None
                cizgi = n.get("strokeColor")
                cizgi = self._renk_coz(cizgi[0], cizgi[1]) if cizgi else None
                oz = [f'd="{html.escape(pd[2], quote=True)}"', f'fill="{dolgu or "none"}"']
                if n.get("fillAlpha"):
                    oz.append(f'fill-opacity="{sayi(n, "fillAlpha", 1)}"')
                if n.get("fillType") and n["fillType"][1] == 1:
                    oz.append('fill-rule="evenodd"')
                if cizgi:
                    oz += [f'stroke="{cizgi}"', f'stroke-width="{sayi(n, "strokeWidth", 0)}"']
                parca.append(f"<path {' '.join(oz)}/>")
        parca += ["</g>"] * len(yigin)
        tanim = f"<defs>{''.join(self.defs[d0:])}</defs>" if len(self.defs) > d0 else ""
        return vw, vh, tanim + "".join(parca)

    def etiket(self):
        """Uygulamanın görünen adı (android:label); Türkçe çeviri varsa o, yoksa varsayılan / İngilizce."""
        for _, ad, nit in axml_ogeler(self.oku("AndroidManifest.xml") or b""):
            if ad != "application":
                continue
            d = nit.get(0x01010001) or nit.get("label")
            if not d:
                return None
            if d[0] == T_STRING:
                return d[2]
            if d[0] != T_REF:
                return None
            adaylar = {}
            for t in self._tablo():
                for dil, tur, veri in t.coz(d[1], dil=True):
                    if tur == T_STRING:
                        adaylar.setdefault(dil, t.dizgi(veri))
            for dil in ("tr", "", "en"):
                if adaylar.get(dil):
                    return adaylar[dil]
            return next((v for v in adaylar.values() if v), None)
        return None

    def simge(self):
        """-> (mime, bayt) | None. Uyarlanabilir / vektör simge varsa SVG, yoksa en yüksek yoğunluklu resim."""
        man = self.oku("AndroidManifest.xml")
        rid = None
        for _, ad, nit in axml_ogeler(man or b""):
            if ad == "application":
                for k in (ATTR_ICON, "icon", ATTR_ROUND_ICON, "roundIcon"):
                    if k in nit and nit[k][0] == T_REF and nit[k][1] >> 24 not in (0, 1):
                        rid = nit[k][1]
                        break
                break
        if not rid or not self._tablo():
            return None
        resim = None
        for yol in self._yollar(rid):
            b = self.oku(yol)
            tur = icerik_turu(b)
            if tur == "xml":
                og = axml_ogeler(b)
                if not og:
                    continue
                if og[0][1] == "adaptive-icon":
                    katman = {og[j][1]: j for j in self._cocuklar(og, 0)}
                    arka = self._ic_ciz(og, katman["background"], 1) if "background" in katman else ""
                    on = self._ic_ciz(og, katman["foreground"], 1) if "foreground" in katman else ""
                    if on or arka:
                        arka = arka or '<rect width="108" height="108" fill="#fff"/>'
                        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="18 18 72 72">'
                               '<defs><clipPath id="m"><rect x="18" y="18" width="72" height="72" rx="16"/></clipPath>'
                               f'</defs><g clip-path="url(#m)">{arka}{on}</g></svg>')
                        return "image/svg+xml", svg.encode("utf-8")
                else:
                    r = self._eleman_ciz(og, 0, 1)
                    if r:
                        return "image/svg+xml", (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 108 108">'
                                                 f'{r}</svg>').encode("utf-8")
            elif tur and not resim:
                resim = (tur, b)
        return resim


def icerik_turu(b):
    """Dosya türü içerikten (gizlenmiş APK'larda uzantı yok veya yanıltıcı olabilir)."""
    if not b or len(b) < 12:
        return None
    if b[:4] == b"\x89PNG":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:4] == b"\x03\x00\x08\x00":
        return "xml"
    return None


UZANTI = {"image/svg+xml": "svg", "image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}


# ----------------------------------------------------------------- kaynaklar
def yerel_apk_simgesi(apkler):
    """Yedekteki APK dosyaları (base + split) -> ((mime, bayt) | None, etiket | None)."""
    zipler = []
    for a in apkler:
        try:
            zipler.append(zipfile.ZipFile(a))
        except (OSError, zipfile.BadZipFile):
            pass

    def oku(yol):
        for z in zipler:
            try:
                return z.read(yol)
            except KeyError:
                continue
        return None

    def oku_hepsi(yol):
        out = []
        for z in zipler:
            try:
                out.append(z.read(yol))
            except KeyError:
                pass
        return out
    c = Cozucu(oku, oku_hepsi)
    s = e = None
    try:
        try:
            s = c.simge()
        except Exception:
            pass
        try:
            e = c.etiket()
        except Exception:
            pass
        return s, e
    finally:
        for z in zipler:
            z.close()


def telefondan_simgeler(hedef_klasor, adb_yolu, paketler=None, isci=8, ilerleme=None) -> dict:
    """Bağlı telefondaki uygulamaların simgelerini hedef_klasor/<paket>.<uzantı> olarak yazar.
    APK'ların tamamı çekilmez: 'unzip -p' ile yalnızca manifest, kaynak tablosu ve simge dosyaları okunur."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    gizli = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    hedef = Path(hedef_klasor)
    hedef.mkdir(parents=True, exist_ok=True)
    cikti = subprocess.run([adb_yolu, "shell", "pm list packages -f"], capture_output=True, text=True,
                           creationflags=gizli).stdout
    yollar = {}
    for m in re.finditer(r"package:(.+)=([^\s=]+)\s*$", cikti, re.M):
        yollar[m.group(2)] = m.group(1)
    if paketler:
        yollar = {p: y for p, y in yollar.items() if p in set(paketler)}

    def tek(paket):
        base = yollar[paket]
        apkler = [base]
        if base.endswith("/base.apk"):          # bölünmüş APK parçaları (yoğunluk, dil, özellik)
            klasor = base.rsplit("/", 1)[0]
            ls = subprocess.run([adb_yolu, "shell", f"ls '{klasor}'"], capture_output=True, text=True,
                                creationflags=gizli).stdout.split()
            apkler += [f"{klasor}/{x}" for x in ls if x.endswith(".apk") and x != "base.apk"]

        def cek(apk, ic_yol):
            return subprocess.run([adb_yolu, "exec-out", f"unzip -p '{apk}' '{ic_yol}' 2>/dev/null"],
                                  capture_output=True, creationflags=gizli, timeout=120).stdout or None

        def oku(ic_yol):
            for apk in apkler:
                b = cek(apk, ic_yol)
                if b:
                    return b
            return None
        c = Cozucu(oku, lambda y: [b for b in (cek(a, y) for a in apkler) if b])
        try:
            s = c.simge()
        except Exception:
            s = None
        try:
            etiketler[paket] = c.etiket()
        except Exception:
            pass
        if s:
            (hedef / f"{paket}.{UZANTI[s[0]]}").write_bytes(s[1])
        return paket, bool(s)

    sonuc, biten, etiketler = {}, 0, {}
    with ThreadPoolExecutor(isci) as h:
        for paket, tamam in h.map(tek, sorted(yollar)):
            sonuc[paket] = tamam
            biten += 1
            if ilerleme and biten % 10 == 0:
                ilerleme(biten * 100 / max(1, len(yollar)), f"Uygulama simgeleri ({biten}/{len(yollar)})")
    import json
    (hedef / "etiketler.json").write_text(json.dumps({k: v for k, v in sorted(etiketler.items()) if v},
                                                     ensure_ascii=False, indent=1), encoding="utf-8")
    return sonuc
