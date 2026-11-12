#!/usr/bin/env python3
"""
Ambar dışa aktarma motoru.

Biçimler: Ambar portable rapor (görüntüleyici dahil), HTML (tablo görünümü), PDF (Edge/Chrome ile),
Excel (XLSX), CSV ve JSON. Bölüm / uygulama / dosya türü / tarih / etiket filtreleri ve rapor
düzenleme parametreleri (başlık, kurum, inceleyen, dosya no, logo, renk, üst/alt bilgi) desteklenir.
"""
import csv
import datetime
import html
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote

import ambar as core
import ambar_incele as inc

BICIMLER = {
    "portable": "Ambar portable rapor - tek dosya (.ambx)",
    "html": "HTML - Tablo görünümü",
    "pdf": "PDF",
    "xlsx": "Excel (XLSX)",
    "csv": "CSV (her bölüm ayrı dosya)",
    "json": "JSON",
}
# ad: (başlık, tarih filtresinin uygulanacağı alan)
GENEL = {
    "cihaz": ("Cihaz bilgisi", None),
    "cagrilar": ("Çağrılar", "tarih"),
    "mesajlar": ("Mesajlar", "tarih"),
    "kisiler": ("Kişiler", "son"),
    "rehber": ("Rehber", None),
    "takvim": ("Takvim", "baslangic"),
    "dosyalar": ("Dosyalar", "mtime"),
    "uygulamalar": ("Uygulamalar", None),
    "hesaplar": ("Hesaplar", None),
    "sim_kartlari": ("SIM kartlar", None),
    "sim_rehberi": ("SIM rehberi", None),
    "sim_dosyalari": ("SIM dosya sistemi", None),
    "notlar": ("Notlar", "degistirme"),
}
ANALITIK = {"etiketler": "Etiketli deliller", "zaman": "Zaman çizelgesi", "istatistik": "İstatistik"}
TUM_BOLUMLER = list(GENEL) + list(ANALITIK)
TABLO_ANAHTAR = {"cagrilar": "id", "mesajlar": "id", "notlar": "id", "dosyalar": "id",
                 "uygulamalar": "paket", "hesaplar": "id", "rehber": "id", "takvim": "id", "sim_kartlari": "id", "sim_rehberi": "id",
                 "sim_dosyalari": "id"}

VARSAYILAN_RAPOR = {
    "baslik": "İnceleme Raporu", "kurum": "", "inceleyen": "", "dosya_no": "", "aciklama": "",
    "logo": "", "renk": "#1565c0", "yon": "dikey", "ust_bilgi": "", "alt_bilgi": "",
    "sayfa_no": True, "program_bilgisi": True, "yeni_sayfa": True,
}
VARSAYILAN_DISA = {
    "bicim": "portable", "bolumler": TUM_BOLUMLER, "kategoriler": None, "uygulamalar": None,
    "dosya_disa": True, "kucuk_resim": True, "sadece_etiketli": False, "ayri_dosya": False,
    "tarih_bas": None, "tarih_bit": None, "ad": "Rapor",
}


# ----------------------------------------------------------------- ayarlar (şablon)
def ayar_oku(st, anahtar: str, varsayilan: dict) -> dict:
    try:
        r = st.idb.execute("SELECT deger FROM ayarlar WHERE anahtar=?", (anahtar,)).fetchone()
        return {**varsayilan, **(json.loads(r[0]) if r else {})}
    except Exception:
        return dict(varsayilan)


def ayar_yaz(st, anahtar: str, deger: dict):
    st.idb.execute("INSERT OR REPLACE INTO ayarlar VALUES(?,?)", (anahtar, json.dumps(deger, ensure_ascii=False)))
    st.idb.commit()


# ----------------------------------------------------------------- sihirbaz bilgisi
def sihirbaz_bilgisi(st, cid: str) -> dict:
    db = st.db(cid)
    o = inc.ozet(st, cid)
    n = o["sayilar"]
    say = {"cihaz": len(st.cihaz(cid)["cikarmalar"]), "cagrilar": n["cagrilar"], "mesajlar": n["mesajlar"],
           "kisiler": n["kisiler"], "dosyalar": n["dosyalar"], "uygulamalar": n["uygulamalar"],
           "hesaplar": n["hesaplar"], "rehber": n["rehber"], "takvim": n["takvim"], "sim_kartlari": n["sim_kartlari"], "sim_rehberi": n["sim_rehberi"],
           "sim_dosyalari": n["sim_dosyalari"], "notlar": n["notlar"], "etiketler": n["etiketler"], "zaman": n["zaman"],
           "istatistik": None}
    kat = inc._satirlar(db.execute("SELECT kategori AS ad, COUNT(*) AS adet, COALESCE(SUM(boyut),0) AS boyut "
                                   "FROM dosyalar GROUP BY kategori ORDER BY adet DESC"))
    uyg = inc._satirlar(db.execute("SELECT uygulama AS paket, COUNT(*) AS adet, COALESCE(SUM(boyut),0) AS boyut "
                                   "FROM dosyalar WHERE uygulama IS NOT NULL GROUP BY uygulama ORDER BY adet DESC"))
    for u in uyg:
        u["ad"] = inc.uygulama_adi(u["paket"])
    return {
        "genel": [{"ad": k, "baslik": v[0], "adet": say[k]} for k, v in GENEL.items()],
        "analitik": [{"ad": k, "baslik": v, "adet": say[k]} for k, v in ANALITIK.items()],
        "kategoriler": kat, "uygulamalar": uyg, "bicimler": BICIMLER,
        "varsayilan_hedef": str(st.kok / "Raporlar"), "exe": bool(gorucu_exe()),
        "rapor": ayar_oku(st, "rapor", VARSAYILAN_RAPOR), "disa": ayar_oku(st, "disa", VARSAYILAN_DISA),
        "portable": st.portable,
    }


# ----------------------------------------------------------------- veri toplama
def _kosullar(st, cid, ad, ayar):
    kosul, p = [], []
    alan = GENEL.get(ad, (None, None))[1]
    if alan and ayar.get("tarih_bas"):
        kosul.append(f"{alan} >= ?"); p.append(int(ayar["tarih_bas"]))
    if alan and ayar.get("tarih_bit"):
        kosul.append(f"{alan} <= ?"); p.append(int(ayar["tarih_bit"]))
    if ad == "dosyalar":
        if ayar.get("kategoriler") is not None:
            k = list(ayar["kategoriler"]) or ["-"]
            kosul.append(f"kategori IN ({','.join('?' * len(k))})"); p += k
        if ayar.get("uygulamalar") is not None:
            u = list(ayar["uygulamalar"]) or ["-"]
            kosul.append(f"(uygulama IS NULL OR uygulama IN ({','.join('?' * len(u))}))"); p += u
    if ayar.get("sadece_etiketli"):
        refs = [r[0] for r in st.idb.execute("SELECT ref FROM etiketler WHERE cihaz=? AND tur=?", (cid, ad))] or ["-"]
        kosul.append(f"CAST(id AS TEXT) IN ({','.join('?' * len(refs))})"); p += refs
    return kosul, p


def bolum_tablosu(st, cid, ad, ayar):
    sql, kolonlar, _, sirala = inc.BOLUMLER[ad]
    kosul, p = _kosullar(st, cid, ad, ayar)
    if ad == "dosyalar":
        sql = "SELECT id, ad, kategori, boyut, mtime, uygulama, hesap, yol, cikarma FROM dosyalar"
    q = f"SELECT * FROM ({sql})" + (" WHERE " + " AND ".join(kosul) if kosul else "") + f" ORDER BY {sirala}"
    satir = inc._satirlar(st.db(cid).execute(q, p))
    return {"ad": ad, "baslik": GENEL[ad][0], "kolonlar": list(kolonlar), "satirlar": satir}


def _cihaz_tablolari(st, cid):
    g = st.cihaz(cid)
    d = g["cihaz"]
    etiket = [("marka", "Üretici"), ("model", "Model"), ("ticari_ad", "Ticari ad"), ("cihaz_adi", "Cihaz adı"),
              ("cihaz_kodu", "Cihaz kodu"), ("android", "Android sürümü"), ("sdk", "SDK"),
              ("guvenlik_yamasi", "Güvenlik yaması"), ("yapi", "Yapı"), ("parmak_izi", "Parmak izi"),
              ("seri", "Seri numarası"), ("imei", "IMEI"), ("yonga", "Yonga seti"), ("android_id", "Android ID"), ("saat_dilimi", "Saat dilimi")]
    bilgi = [{"bilgi": b, "deger": d.get(k)} for k, b in etiket if d.get(k)]
    cik = [{"tur": c["tur_adi"], "klasor": c["ad"], "baslangic": c.get("tarih"), "bitis": c.get("bitis"),
            "sure": c.get("sure_sn"),
            "ambar": c.get("ambar_surum") and "v" + c["ambar_surum"], "sha": "Var" if c.get("sha256") else "Yok"}
           for c in g["cikarmalar"]]
    return [
        {"ad": "cihaz", "baslik": "Cihaz bilgisi", "kolonlar": [("bilgi", "Bilgi", "metin"), ("deger", "Değer", "metin")],
         "satirlar": bilgi},
        {"ad": "cikarmalar", "baslik": "Çıkarmalar", "kolonlar": [
            ("tur", "Tür", "metin"), ("klasor", "Klasör", "metin"), ("baslangic", "Başlangıç", "iso"),
            ("bitis", "Bitiş", "iso"), ("sure", "Süre", "sure"),
            ("ambar", "Ambar", "metin"), ("sha", "SHA-256 listesi", "metin")], "satirlar": cik},
    ]


def _zaman_tablosu(st, cid, ayar):
    sql = """SELECT * FROM (
        SELECT tarih, 'Çağrı' AS tur, tur || ' çağrı' AS olay, COALESCE(NULLIF(isim,''), numara) AS kim,
               sure AS ayrinti FROM cagrilar
        UNION ALL SELECT tarih, 'Mesaj', kaynak || ' · ' || yon, numara, metin FROM mesajlar
        UNION ALL SELECT degistirme, 'Not', 'Not', NULL, metin FROM notlar
        UNION ALL SELECT mtime, 'Medya', kategori, uygulama, ad FROM dosyalar WHERE kategori IN ('Görüntü','Video'))
        WHERE tarih IS NOT NULL"""
    p = []
    if ayar.get("tarih_bas"):
        sql += " AND tarih >= ?"; p.append(int(ayar["tarih_bas"]))
    if ayar.get("tarih_bit"):
        sql += " AND tarih <= ?"; p.append(int(ayar["tarih_bit"]))
    return {"ad": "zaman", "baslik": "Zaman çizelgesi", "satirlar": inc._satirlar(st.db(cid).execute(sql + " ORDER BY tarih DESC", p)),
            "kolonlar": [("tarih", "Tarih", "tarih"), ("tur", "Tür", "metin"), ("olay", "Olay", "metin"),
                         ("kim", "Kişi / kaynak", "metin"), ("ayrinti", "Ayrıntı", "uzun")]}


def _etiket_tablosu(st, cid):
    ad = {**{k: v[0] for k, v in GENEL.items()}, "galeri": "Fotoğraf ve video"}
    s = inc._satirlar(st.idb.execute("SELECT tur, baslik, etiket, aciklama, tarih FROM etiketler WHERE cihaz=? ORDER BY tarih",
                                     (cid,)))
    for r in s:
        r["tur"] = ad.get(r["tur"], r["tur"])
    return {"ad": "etiketler", "baslik": "Etiketli deliller", "satirlar": s,
            "kolonlar": [("tarih", "Etiketleme", "iso"), ("tur", "Bölüm", "metin"), ("baslik", "Öğe", "metin"),
                         ("etiket", "Etiket", "metin"), ("aciklama", "Açıklama", "uzun")]}


def _istatistik_tablosu(st, cid):
    n = inc.ozet(st, cid)["sayilar"]
    satir = [{"ad": a, "deger": f"{v:,}".replace(",", ".")} for a, v in [
        ("Uygulamalar", n["uygulamalar"]), ("Çağrılar", n["cagrilar"]), ("Mesajlar (SMS/MMS)", n["mesajlar"]),
        ("Kişiler", n["kisiler"]), ("Rehber", n["rehber"]), ("Takvim", n["takvim"]), ("Dosyalar", n["dosyalar"]), ("Fotoğraf ve video", n["fotograflar"]),
        ("Notlar", n["notlar"]), ("Hesaplar", n["hesaplar"]), ("SIM kartlar", n["sim_kartlari"]),
        ("SIM rehberi", n["sim_rehberi"]), ("Etiketli delil", n["etiketler"])]]
    satir.append({"ad": "Toplam veri", "deger": core.boyut_yaz(n["toplam_boyut"])})
    for k in inc.istatistik(st, cid)["kategoriler"]:
        satir.append({"ad": f"Dosya türü: {k['ad']}", "deger": f"{k['adet']} dosya · {core.boyut_yaz(k['boyut'] or 0)}"})
    return {"ad": "istatistik", "baslik": "İstatistik", "satirlar": satir,
            "kolonlar": [("ad", "Ölçüt", "metin"), ("deger", "Değer", "metin")]}


def tablolar(st, cid, ayar) -> list[dict]:
    secili = set(ayar.get("bolumler") or [])
    out = []
    for ad in TUM_BOLUMLER:
        if ad not in secili:
            continue
        if ad == "cihaz":
            out += _cihaz_tablolari(st, cid)
        elif ad == "zaman":
            out.append(_zaman_tablosu(st, cid, ayar))
        elif ad == "etiketler":
            out.append(_etiket_tablosu(st, cid))
        elif ad == "istatistik":
            out.append(_istatistik_tablosu(st, cid))
        else:
            out.append(bolum_tablosu(st, cid, ad, ayar))
    return out


# ----------------------------------------------------------------- değer biçimlendirme
def _ms_metin(v, saniye=True):
    try:
        return datetime.datetime.fromtimestamp(int(v) / 1000).strftime("%d.%m.%Y %H:%M" + (":%S" if saniye else ""))
    except (TypeError, ValueError, OSError):
        return ""


def bicimle(tur, v, alan=""):
    if v is None or v == "":
        return ""
    if tur == "tarih":
        return _ms_metin(v)
    if tur == "kurulum":
        if v == 0:
            return "Ön yüklü (tarih yok)" if alan == "ilk_yukleme" else "Güncellenmedi (fabrika sürümü)"
        return _ms_metin(v, False)
    if tur == "iso":
        try:
            return datetime.datetime.fromisoformat(str(v)).strftime("%d.%m.%Y %H:%M:%S")
        except ValueError:
            return str(v)
    if tur == "sure":
        v = int(v)
        return f"{v // 3600}:{v % 3600 // 60:02d}:{v % 60:02d}"
    if tur == "boyut":
        return core.boyut_yaz(int(v))
    if tur == "evet":
        return "Evet" if v else "Hayır"
    return str(v)


# ----------------------------------------------------------------- HTML
def _css_metin(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def html_olustur(st, cid, ayar: dict, rapor: dict, tablo_listesi: list[dict], dosya_url=None,
                 pdf=False, baslik_ek="") -> str:
    """Rapor HTML'i. dosya_url(satir) -> dosyanın bağlantısı (yoksa None)."""
    e = html.escape
    g = st.cihaz(cid)
    d = g["cihaz"]
    renk = rapor.get("renk") or "#1565c0"
    if not re.fullmatch(r"#[0-9a-fA-F]{3,8}", renk):
        renk = "#1565c0"
    yon = "landscape" if rapor.get("yon") == "yatay" else "portrait"
    kenar = []
    if rapor.get("ust_bilgi"):
        kenar.append("@top-left{content:%s;font:8pt 'Segoe UI',Arial;color:#666}" % _css_metin(rapor["ust_bilgi"]))
    if rapor.get("alt_bilgi"):
        kenar.append("@bottom-left{content:%s;font:8pt 'Segoe UI',Arial;color:#666}" % _css_metin(rapor["alt_bilgi"]))
    if rapor.get("sayfa_no", True):
        kenar.append('@bottom-right{content:"Sayfa " counter(page) " / " counter(pages);font:8pt "Segoe UI",Arial;color:#666}')
    logo = rapor.get("logo") or ""
    if not logo.startswith("data:image/"):
        logo = ""
    simdi = datetime.datetime.now()
    bilgi_satir = [("Dosya no", rapor.get("dosya_no")), ("İnceleyen", rapor.get("inceleyen")),
                   ("Kurum", rapor.get("kurum")), ("Rapor tarihi", simdi.strftime("%d.%m.%Y %H:%M")),
                   ("Cihaz", g["baslik"]), ("Model", d.get("model")), ("Seri numarası", d.get("seri")),
                   ("Çıkarma sayısı", len(g["cikarmalar"]))]
    olay = [(a["ad"], a["deger"]) for a in inc.olay_bilgisi(st, cid) if str(a.get("deger") or "").strip()]
    filtre = []
    if ayar.get("tarih_bas") or ayar.get("tarih_bit"):
        filtre.append(f"Tarih aralığı: {_ms_metin(ayar.get('tarih_bas'), False) or 'başlangıç'} – "
                      f"{_ms_metin(ayar.get('tarih_bit'), False) or 'bugün'}")
    if ayar.get("sadece_etiketli"):
        filtre.append("Yalnızca etiketli kayıtlar")
    if filtre:
        bilgi_satir.append(("Filtre", "; ".join(filtre)))

    def tablo_html(t):
        kol = t["kolonlar"]
        if not t["satirlar"]:
            return '<p class="bos">Kayıt yok.</p>'
        bas = "".join(f"<th>{e(b)}</th>" for _, b, _ in kol)
        govde = []
        for r in t["satirlar"]:
            hucre = []
            for alan, _, tur in kol:
                v = e(bicimle(tur, r.get(alan), alan))
                if t["ad"] == "dosyalar" and alan == "ad" and dosya_url:
                    url = dosya_url(r)
                    if url:
                        resim = ""
                        if rapor.get("_kucuk_resim") and r.get("kategori") == "Görüntü":
                            resim = f'<img class="kr" src="{e(url)}"{"" if pdf else " loading=lazy"} alt="">'
                        v = f'{resim}<a href="{e(url)}" target="_blank">{v}</a>'
                hucre.append(f'<td class="{"uzun" if tur == "uzun" else ""}">{v}</td>')
            govde.append("<tr>" + "".join(hucre) + "</tr>")
        return f'<table class="veri"><thead><tr>{bas}</tr></thead><tbody>{"".join(govde)}</tbody></table>'

    icerik = "".join(f'<li><a href="#b-{t["ad"]}">{e(t["baslik"])}</a> <span>{len(t["satirlar"])}</span></li>'
                     for t in tablo_listesi)
    bolumler = "".join(f'<section id="b-{t["ad"]}"><h2>{e(t["baslik"])} <span>{len(t["satirlar"])} kayıt</span></h2>'
                       f'{tablo_html(t)}</section>' for t in tablo_listesi)
    notu = inc.ozet(st, cid)["not"] if "etiketler" in (ayar.get("bolumler") or []) else ""
    css = """
@page{size:A4 %(yon)s;margin:18mm 12mm 16mm 12mm;%(kenar)s}
*{box-sizing:border-box}
body{font:10pt/1.45 "Segoe UI",Arial,sans-serif;color:#1f2937;margin:0;background:#f3f5f8}
.sayfa{max-width:1180px;margin:0 auto;background:#fff;padding:34px 40px 40px}
.ekran{color:#6b7280;font-size:9pt;display:flex;justify-content:space-between;border-bottom:1px solid #e5e7eb;padding-bottom:6px;margin-bottom:18px}
.kapak{border-bottom:3px solid %(renk)s;padding-bottom:16px;margin-bottom:18px}
.kapak-ust{display:flex;align-items:center;gap:20px}
.kapak-ust img{max-height:80px;max-width:220px}
.kurum{color:#6b7280;font-size:11pt;font-weight:600}
h1{margin:2px 0;color:%(renk)s;font-size:22pt}
.alt{color:#374151;font-size:12pt}
table.bilgi{border-collapse:collapse;margin-top:14px}
table.bilgi th{text-align:left;color:#6b7280;font-weight:600;padding:3px 24px 3px 0;white-space:nowrap}
table.bilgi td{padding:3px 0}
.aciklama{margin-top:12px;white-space:pre-wrap}
h3.olay-b{margin:16px 0 0;font-size:11pt;color:%(renk)s}
table.bilgi td{white-space:pre-wrap}
nav h2,section h2{color:%(renk)s;font-size:14pt;border-bottom:1px solid #e5e7eb;padding-bottom:4px;margin:24px 0 10px}
section h2 span{color:#6b7280;font-size:10pt;font-weight:400}
nav ol{columns:2;margin:0;padding-left:20px} nav li span{color:#6b7280}
nav a,td a{color:%(renk)s;text-decoration:none}
table.veri{border-collapse:collapse;width:100%%;font-size:8.5pt}
table.veri th{background:%(renk)s;color:#fff;text-align:left;padding:5px 6px;font-weight:600}
table.veri td{border-bottom:1px solid #e5e7eb;padding:4px 6px;vertical-align:top;word-break:break-word}
table.veri tr:nth-child(even) td{background:#f8fafc}
td.uzun{min-width:220px;white-space:pre-wrap}
img.kr{display:block;max-width:90px;max-height:70px;margin-bottom:2px;border-radius:2px}
.bos{color:#6b7280}
.not{white-space:pre-wrap;border-left:3px solid %(renk)s;padding:6px 12px;background:#f8fafc}
footer{margin-top:28px;color:#9ca3af;font-size:8pt;border-top:1px solid #e5e7eb;padding-top:6px}
@media print{body{background:#fff}.sayfa{padding:0;max-width:none}.ekran{display:none}
 thead{display:table-header-group} tr{break-inside:avoid}
 %(yeni)s}
""" % {"yon": yon, "kenar": "".join(kenar), "renk": renk,
       "yeni": "section{break-before:page}" if rapor.get("yeni_sayfa", True) else ""}
    ust = f'<div class="ekran"><span>{e(rapor.get("ust_bilgi") or "")}</span><span>{e(rapor.get("alt_bilgi") or "")}</span></div>' \
        if (rapor.get("ust_bilgi") or rapor.get("alt_bilgi")) else ""
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(rapor.get("baslik") or "Rapor")} - {e(g["baslik"])}{e(baslik_ek)}</title><style>{css}</style></head><body><div class="sayfa">
{ust}
<header class="kapak"><div class="kapak-ust">{f'<img src="{logo}" alt="">' if logo else ""}
<div><div class="kurum">{e(rapor.get("kurum") or "")}</div><h1>{e(rapor.get("baslik") or "İnceleme Raporu")}{e(baslik_ek)}</h1>
<div class="alt">{e(g["baslik"])}</div></div></div>
<table class="bilgi">{"".join(f"<tr><th>{e(k)}</th><td>{e(str(v))}</td></tr>" for k, v in bilgi_satir if v not in (None, ""))}</table>
{f'<div class="aciklama">{e(rapor["aciklama"])}</div>' if rapor.get("aciklama") else ""}
{f'<h3 class="olay-b">Olay ve kanıt bilgileri</h3><table class="bilgi">' + "".join(f"<tr><th>{e(k)}</th><td>{e(_olay_deger(v))}</td></tr>" for k, v in olay) + "</table>" if olay else ""}</header>
{f'<nav><h2>İçindekiler</h2><ol>{icerik}</ol></nav>' if len(tablo_listesi) > 1 else ""}
{bolumler}
{f'<section><h2>İnceleyici notu</h2><div class="not">{e(notu)}</div></section>' if notu else ""}
{f'<footer>{core.UYGULAMA_ADI} v{core.SURUM} ile oluşturuldu · {simdi:%d.%m.%Y %H:%M}</footer>' if rapor.get("program_bilgisi", True) else ""}
</div></body></html>"""


def ozet_html(st, cid, dosya_url=None) -> str:
    """'Yazdırılabilir özet': kayıtlı rapor ayarlarıyla cihaz, çıkarma, istatistik ve etiketler."""
    ayar = {"bolumler": ["cihaz", "istatistik", "etiketler"]}
    rapor = ayar_oku(st, "rapor", VARSAYILAN_RAPOR)
    rapor["yeni_sayfa"] = False
    return html_olustur(st, cid, ayar, rapor, tablolar(st, cid, ayar), dosya_url)


# ----------------------------------------------------------------- PDF (Edge / Chrome)
def tarayici_bul():
    adaylar = []
    for kok in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
        if kok:
            adaylar += [Path(kok) / "Microsoft/Edge/Application/msedge.exe", Path(kok) / "Google/Chrome/Application/chrome.exe"]
    return next((a for a in adaylar if a.exists()), None)


def pdf_yaz(html_yol: Path, pdf_yol: Path):
    t = tarayici_bul()
    if not t:
        raise RuntimeError("PDF için Microsoft Edge veya Google Chrome gerekli.")
    profil = Path(tempfile.mkdtemp(prefix="ambar_pdf_"))
    try:
        subprocess.run([str(t), "--headless=new", "--disable-gpu", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                        "--disable-extensions", f"--user-data-dir={profil}", "--virtual-time-budget=30000",
                        f"--print-to-pdf={pdf_yol}", html_yol.resolve().as_uri()],
                       timeout=900, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        shutil.rmtree(profil, ignore_errors=True)
    if not pdf_yol.exists() or pdf_yol.stat().st_size == 0:
        raise RuntimeError("PDF oluşturulamadı.")


# ----------------------------------------------------------------- XLSX (bağımlılıksız)
_GECERSIZ = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _x(s) -> str:
    s = _GECERSIZ.sub("", "" if s is None else str(s))[:32000]
    return html.escape(s, quote=False)


def _sutun(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def xlsx_yaz(yol: Path, sayfalar: list[tuple[str, list[str], list[list[str]]]]):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    adlar, kullanilan = [], set()
    for ad, _, _ in sayfalar:
        a = re.sub(r"[\[\]:*?/\\]", "", ad)[:31] or "Sayfa"
        b, i = a, 2
        while b.lower() in kullanilan:
            b = f"{a[:28]}_{i}"; i += 1
        kullanilan.add(b.lower()); adlar.append(b)
    with zipfile.ZipFile(yol, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sayfalar))) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook {ns} {rns}><sheets>'
                   + "".join(f'<sheet name="{_x(a)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, a in enumerate(adlar))
                   + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sayfalar)))
                   + f'<Relationship Id="rId{len(sayfalar) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><styleSheet {ns}>'
                   '<fonts count="2"><font><sz val="10"/><name val="Calibri"/></font><font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FF1565C0"/><bgColor indexed="64"/></patternFill></fill></fills>'
                   '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
                   '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                   '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                   '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf></cellXfs>'
                   '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        for i, (_, basliklar, satirlar) in enumerate(sayfalar):
            gen = [min(60, max(10, len(b) + 2)) for b in basliklar]
            for r in satirlar[:200]:
                for j, v in enumerate(r):
                    gen[j] = min(60, max(gen[j], len(str(v or "")) + 2))
            parca = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet {ns}><sheetViews><sheetView workbookViewId="0">'
                     '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
                     '<cols>' + "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>' for j, w in enumerate(gen))
                     + '</cols><sheetData>']
            for ri, r in enumerate([basliklar] + satirlar):
                stil = ' s="1"' if ri == 0 else ""
                parca.append(f'<row r="{ri + 1}">' + "".join(
                    f'<c r="{_sutun(j)}{ri + 1}" t="inlineStr"{stil}><is><t xml:space="preserve">{_x(v)}</t></is></c>'
                    for j, v in enumerate(r)) + "</row>")
            parca.append("</sheetData></worksheet>")
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", "".join(parca))


def _duz(t):
    """Tabloyu (başlıklar, satır listeleri) olarak biçimlendirir."""
    return ([b for _, b, _ in t["kolonlar"]],
            [[bicimle(tur, r.get(alan), alan) for alan, _, tur in t["kolonlar"]] for r in t["satirlar"]])


def _olay_deger(v) -> str:
    """'2026-11-12T10:30' gibi tarih girişlerini okunur biçime çevirir."""
    v = str(v)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?", v):
        return datetime.datetime.fromisoformat(v).strftime("%d.%m.%Y %H:%M")
    return v


def _rapor_bilgi_tablosu(st, cid, rapor):
    g = st.cihaz(cid)
    s = [("Başlık", rapor.get("baslik")), ("Kurum", rapor.get("kurum")), ("İnceleyen", rapor.get("inceleyen")),
         ("Dosya no", rapor.get("dosya_no")), ("Açıklama", rapor.get("aciklama")), ("Cihaz", g["baslik"]),
         ("Rapor tarihi", datetime.datetime.now().strftime("%d.%m.%Y %H:%M"))]
    s += [(a["ad"], _olay_deger(a["deger"])) for a in inc.olay_bilgisi(st, cid) if str(a.get("deger") or "").strip()]
    return {"ad": "rapor", "baslik": "Rapor", "kolonlar": [("a", "Bilgi", "metin"), ("b", "Değer", "metin")],
            "satirlar": [{"a": a, "b": b} for a, b in s if b]}


# ----------------------------------------------------------------- yardımcılar
def gorucu_exe():
    return inc.gorucu_exe()


def _guvenli(s: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s).strip(" .") or "Rapor"


def _cikti_klasoru(hedef: str, ad: str) -> Path:
    kok = Path(hedef).expanduser()
    k = kok / _guvenli(ad)
    if k.exists():
        k = kok / f"{_guvenli(ad)}_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    k.mkdir(parents=True, exist_ok=False)
    return k


def _dosyalari_kopyala(st, cid, satirlar, klasor: Path, bildir, bas=5, son=80):
    """Dosyaları klasor/dosyalar/<çıkarma>/<telefondaki yol> altına kopyalar. {(çıkarma, yol): göreli yol}, özetler"""
    harita, ozetler, eksik = {}, {}, []
    toplam = sum(r.get("boyut") or 0 for r in satirlar) or 1
    biten = 0
    for i, r in enumerate(satirlar):
        rel = f"dosyalar/{r['cikarma']}/{r['yol']}"
        try:
            ozetler[rel] = inc.kopyala_akis(st, cid, r["cikarma"], r["yol"], klasor / rel)
            harita[(r["cikarma"], r["yol"])] = rel
        except OSError as e:
            eksik.append(f"{r['cikarma']}/{r['yol']}: {e}")
        biten += r.get("boyut") or 0
        if i % 25 == 0:
            bildir(bas + (son - bas) * biten / toplam, f"Dosyalar kopyalanıyor ({i + 1}/{len(satirlar)})")
    if eksik:
        (klasor / "kopyalanamayanlar.txt").write_text("\n".join(eksik), encoding="utf-8")
    return harita, ozetler


def _sha_listesi(klasor: Path, ozetler: dict):
    for p in sorted(klasor.rglob("*")):
        rel = p.relative_to(klasor).as_posix()
        if p.is_file() and rel != "SHA256.txt" and rel not in ozetler:
            ozetler[rel] = inc._sha256(p)
    (klasor / "SHA256.txt").write_text("".join(f"{h}  {y}\n" for y, h in sorted(ozetler.items())), encoding="utf-8")


def _rel_url(rel: str) -> str:
    return "/".join(quote(p) for p in rel.split("/"))


# ----------------------------------------------------------------- ana giriş
def disa_aktar(st, cid: str, ayar: dict, bildir) -> Path:
    ayar = {**VARSAYILAN_DISA, **ayar}
    rapor = {**VARSAYILAN_RAPOR, **(ayar.get("rapor") or {})}
    rapor["_kucuk_resim"] = ayar.get("kucuk_resim", True)
    bicim = ayar.get("bicim", "portable")
    if bicim not in BICIMLER:
        raise ValueError("Geçersiz biçim")
    ad = ayar.get("ad") or "Rapor"
    bildir(1, "Veriler hazırlanıyor")
    if bicim == "portable":
        hedef = Path(ayar.get("hedef") or str(st.kok / "Raporlar")).expanduser()
        hedef.mkdir(parents=True, exist_ok=True)
        dosya = hedef / f"{_guvenli(ad)}.ambx"
        if dosya.exists():
            dosya = hedef / f"{_guvenli(ad)}_{datetime.datetime.now():%Y%m%d_%H%M%S}.ambx"
        portable_yaz(st, cid, ayar, rapor, dosya, bildir)
        return dosya
    klasor = _cikti_klasoru(ayar.get("hedef") or str(st.kok / "Raporlar"), ad)

    liste = tablolar(st, cid, ayar)
    harita, ozetler = {}, {}
    dosya_tablo = next((t for t in liste if t["ad"] == "dosyalar"), None)
    if ayar.get("dosya_disa") and dosya_tablo:
        harita, ozetler = _dosyalari_kopyala(st, cid, dosya_tablo["satirlar"], klasor, bildir)
    dosya_url = (lambda r: _rel_url(harita[(r["cikarma"], r["yol"])]) if (r["cikarma"], r["yol"]) in harita else None)
    bildir(85, f"{BICIMLER[bicim]} yazılıyor")
    ayri = ayar.get("ayri_dosya")
    gruplar = [(t["baslik"], [t]) for t in liste] if ayri else [(None, liste)]

    if bicim in ("html", "pdf"):
        for ek, tl in gruplar:
            dosya = _guvenli(f"{ad} - {ek}" if ek else ad)
            h = html_olustur(st, cid, ayar, rapor, tl, dosya_url, pdf=bicim == "pdf", baslik_ek=f" - {ek}" if ek else "")
            hy = klasor / f"{dosya}.html"
            hy.write_text(h, encoding="utf-8")
            if bicim == "pdf":
                bildir(90, f"PDF oluşturuluyor: {dosya}")
                pdf_yaz(hy, klasor / f"{dosya}.pdf")
                hy.unlink()
    elif bicim == "xlsx":
        bilgi = _rapor_bilgi_tablosu(st, cid, rapor)
        for ek, tl in gruplar:
            xlsx_yaz(klasor / f"{_guvenli(f'{ad} - {ek}' if ek else ad)}.xlsx",
                     [(t["baslik"], *_duz(t)) for t in ([bilgi] + tl)])
    elif bicim == "csv":
        for t in [_rapor_bilgi_tablosu(st, cid, rapor)] + liste:
            b, s = _duz(t)
            with open(klasor / f"{_guvenli(t['baslik'])}.csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(b)
                w.writerows(s)
    elif bicim == "json":
        def js(t):
            return [{b: bicimle(tur, r.get(alan), alan) if tur in ("tarih", "kurulum", "iso") else r.get(alan)
                     for alan, b, tur in t["kolonlar"]} for r in t["satirlar"]]
        temel = {"rapor": {k: v for k, v in rapor.items() if not k.startswith("_") and k != "logo"},
                 "olusturma": datetime.datetime.now().isoformat(timespec="seconds"),
                 "program": f"{core.UYGULAMA_ADI} v{core.SURUM}", "cihaz": st.cihaz(cid)["baslik"]}
        for ek, tl in gruplar:
            veri = {**temel, "bolumler": {t["ad"]: {"baslik": t["baslik"], "kayitlar": js(t)} for t in tl}}
            (klasor / f"{_guvenli(f'{ad} - {ek}' if ek else ad)}.json").write_text(
                json.dumps(veri, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    bildir(97, "SHA-256 özetleri hesaplanıyor")
    _sha_listesi(klasor, ozetler)
    bildir(100, "Tamamlandı")
    return klasor


def portable_yaz(st, cid, ayar, rapor, cikti: Path, bildir):
    """Tek dosya .ambx rapor: süzülmüş veritabanı + özet + dosyalar; yanına .sha256/.md5 ve AmbarIncele.exe."""
    import ambar_ambx
    g = st.cihaz(cid)
    db = st.db(cid)
    secili = set(ayar.get("bolumler") or [])
    bildir(2, "Veritabanı yazılıyor")
    inc.CALISMA.mkdir(parents=True, exist_ok=True)
    tanitici, gecici = tempfile.mkstemp(prefix="ambx_", suffix=".sqlite", dir=inc.CALISMA)
    os.close(tanitici)
    gecici_db = Path(gecici)
    rdb = sqlite3.connect(gecici_db)
    rdb.row_factory = sqlite3.Row
    db.backup(rdb)
    rdb.executescript("""DELETE FROM meta WHERE anahtar IN ('imza','rapor');
        CREATE TABLE IF NOT EXISTS etiketler(cihaz TEXT, tur TEXT, ref TEXT, baslik TEXT, etiket TEXT, aciklama TEXT,
                               tarih TEXT, PRIMARY KEY(cihaz, tur, ref));
        CREATE TABLE IF NOT EXISTS cihaz_notlari(cihaz TEXT PRIMARY KEY, metin TEXT, tarih TEXT);
        CREATE TABLE IF NOT EXISTS ayarlar(anahtar TEXT PRIMARY KEY, deger TEXT);
        CREATE TABLE IF NOT EXISTS olay_bilgisi(cihaz TEXT PRIMARY KEY, deger TEXT, tarih TEXT);""")
    # bölüm / tarih / etiket / dosya türü filtrelerini kopyaya uygula
    for tablo, pk in TABLO_ANAHTAR.items():
        if tablo not in secili:
            rdb.execute(f"DELETE FROM {tablo}")
            continue
        kosul, p = _kosullar(st, cid, tablo, ayar)
        if kosul:
            kalan = [r[0] for r in db.execute(f"SELECT {pk} FROM {tablo} WHERE " + " AND ".join(kosul), p)]
            rdb.execute("CREATE TEMP TABLE IF NOT EXISTS kalan(k)")
            rdb.execute("DELETE FROM kalan")
            rdb.executemany("INSERT INTO kalan VALUES(?)", [(k,) for k in kalan])
            rdb.execute(f"DELETE FROM {tablo} WHERE {pk} NOT IN (SELECT k FROM kalan)")
    for tablo in ("olay_bilgisi",) + (("etiketler", "cihaz_notlari") if "etiketler" in secili else ()):
        satirlar = st.idb.execute(f"SELECT * FROM {tablo} WHERE cihaz=?", (cid,)).fetchall()
        if satirlar:
            rdb.executemany(f"INSERT OR REPLACE INTO {tablo} VALUES({','.join('?' * len(satirlar[0]))})",
                            [tuple(r) for r in satirlar])
    rdb.execute("INSERT OR REPLACE INTO ayarlar VALUES('rapor', ?)",
                (json.dumps({k: v for k, v in rapor.items() if not k.startswith("_")}, ensure_ascii=False),))
    rdb.commit()
    dosyalar = inc._satirlar(rdb.execute("SELECT cikarma, yol, boyut, mtime FROM dosyalar")) if ayar.get("dosya_disa") else []
    toplam_dosya = rdb.execute("SELECT COUNT(*) FROM dosyalar").fetchone()[0]
    rdb.close()

    yazici = ambar_ambx.AmbxYazici(cikti)
    try:
        # 1) dosyalar (önce; hangilerinin girdiği meta'ya yazılacak)
        bildir(4, "Dosyalar rapora yazılıyor")
        toplam = sum(r.get("boyut") or 0 for r in dosyalar) or 1
        biten, giren, eksik = [0], 0, []
        for i, r in enumerate(dosyalar):
            try:
                f, _, mtime = st.dosya_ac(cid, r["cikarma"], r["yol"])
                with f:
                    yazici.ekle_akis(f"dosyalar/{r['cikarma']}/{r['yol']}", f, mtime or r.get("mtime"),
                                     lambda n: biten.__setitem__(0, biten[0] + n))
                giren += 1
            except (OSError, KeyError) as e:
                eksik.append(f"{r['cikarma']}/{r['yol']}: {e}")
            if i % 25 == 0:
                bildir(4 + 86 * biten[0] / toplam, f"Dosyalar rapora yazılıyor ({i + 1}/{len(dosyalar)})")

        # 2) veritabanı (meta ile) ve özet
        bildir(91, "Veritabanı ve özet yazılıyor")
        cihaz = json.loads(json.dumps(g, default=str))
        for c in cihaz["cikarmalar"]:
            c["kaynak_yol"], c["yol"] = c.get("kaynak_yol") or c["yol"], c["ad"]
        kayip = toplam_dosya - giren
        meta = {"tur": "ambar_rapor", "surum": 3, "bicim": "ambx", "ambar_surum": core.SURUM,
                "kapsam_adi": "Tüm dosyalar" if not kayip else f"{giren} / {toplam_dosya} dosya",
                "kapsam": "tum" if not kayip else "kismi", "dosya_adet": giren, "eksik_dosya": kayip,
                "bolumler": sorted(secili), "olusturma": datetime.datetime.now().isoformat(timespec="seconds"),
                "rapor_basligi": rapor.get("baslik"), "cihaz": cihaz}
        rdb = sqlite3.connect(gecici_db)
        rdb.execute("INSERT INTO meta VALUES('rapor', ?)", (json.dumps(meta, ensure_ascii=False),))
        rdb.commit()
        rdb.execute("VACUUM")
        rdb.close()
        yazici.ekle_bayt(inc.RAPOR_DOSYA, gecici_db.read_bytes(), sikistir=True)
        ozet_ayar = {"bolumler": [b for b in ("cihaz", "istatistik", "etiketler") if b in secili]}
        yazici.ekle_bayt("ozet_rapor.html", html_olustur(st, cid, ozet_ayar, {**rapor, "yeni_sayfa": False},
                                                          tablolar(st, cid, ozet_ayar)).encode("utf-8"), sikistir=True)
        if eksik:
            yazici.ekle_bayt("kopyalanamayanlar.txt", "\n".join(eksik).encode("utf-8"), sikistir=True)
        bildir(96, "Rapor dosyası kapatılıyor (SHA-256 / MD5)")
        ozet = yazici.kapat({"cihaz": g["baslik"], "rapor_basligi": rapor.get("baslik"), "program": f"{core.UYGULAMA_ADI} v{core.SURUM}"})
    except BaseException:
        yazici.iptal()
        raise
    finally:
        gecici_db.unlink(missing_ok=True)

    exe = gorucu_exe()
    if exe and exe.resolve() != (cikti.parent / inc.GORUCU_EXE).resolve():
        try:
            shutil.copy2(exe, cikti.parent / inc.GORUCU_EXE)
        except OSError:
            pass        # aynı klasördeki görüntüleyici şu an açıksa eskisi kalır
    bildir(100, f"Tamamlandı · {core.boyut_yaz(ozet['boyut'])} · SHA-256 {ozet['sha256'][:16]}…")
