#!/usr/bin/env python3
"""Ambar - grafik arayüz (tkinter). ambar.py'deki fonksiyonları kullanır.

Görünüm: solda menü, üstte yol çubuğu, ortada büyük simgeli kartlar
(Masaüstü / Bağlı cihaz / Yöntemler / Araçlar / Favoriler / Günlük / Ayarlar).
"""

import datetime
import json
import os
import queue
import shutil
import sys
import threading
import webbrowser
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import ambar as core

# ---------------------------------------------------------------- renkler (açık tema)
BG = "#ffffff"          # pencere / kart üstü
KART = "#f5f6f8"        # kart alt bölümü
KART_HOVER = "#e8eff9"
KENAR = "#c9d1dc"
KENAR_HOVER = "#7aa5d8"
SECILI = "#c5daf5"      # seçili menü / yol çubuğu hapı
UST = "#eaf2fc"         # yol çubuğu arka planı
MENU_HOVER = "#eef4fc"
LACIVERT = "#0d2340"    # simge rengi
YAZI = "#111827"
SOLUK = "#5b6472"
VURGU = "#16a34a"       # yeşil (bağlı / başarı)
MARKA = "#f59e0b"       # kehribar (Ambar marka rengi)
VURGU2 = "#2563eb"      # mavi (bağlantı)
UYARI = "#d97706"
HATA = "#dc2626"
YILDIZ = "#f5a623"
LOG_BG = "#fbfcfe"

FONT = "Segoe UI"
IKON = "Segoe Fluent Icons"          # Windows 11; yoksa Segoe MDL2 Assets (Windows 10)
YEDEK_GLIF = {"F0E3": "E9D5", "F012": "E8B7"}   # MDL2'de olmayan glifler

AYAR_DOSYASI = Path(os.environ.get("LOCALAPPDATA", str(core.SCRIPT_DIR))) / "Ambar" / "ayarlar.json"
VARSAYILAN_FAVORILER = ["incele", "komple", "whatsapp"]


def glif(kod):
    if IKON != "Segoe Fluent Icons":
        kod = YEDEK_GLIF.get(kod, kod)
    return chr(int(kod, 16))


class KuyrukYazici:
    """print() çıktısını thread-güvenli kuyruğa aktarır."""

    def __init__(self, q):
        self.q = q

    def write(self, s):
        if s:
            self.q.put(("log", s))

    def flush(self):
        pass


class Uygulama(tk.Tk):
    def __init__(self):
        super().__init__()
        global IKON
        if IKON not in tkfont.families(self):
            IKON = "Segoe MDL2 Assets"
        self.title(f"{core.UYGULAMA_ADI} v{core.SURUM}")
        # Ekran ölçeklendirmesine (DPI) göre pencere boyutu
        o = max(1.0, self.winfo_fpixels("1i") / 96)
        w = min(int(1360 * o), int(self.winfo_screenwidth() * 0.92))
        h = min(int(880 * o), int(self.winfo_screenheight() * 0.88))
        self.geometry(f"{w}x{h}+{(self.winfo_screenwidth() - w) // 2}+{max(0, (self.winfo_screenheight() - h) // 3)}")
        self.minsize(int(1000 * o), int(600 * o))
        self.configure(bg=BG)
        try:
            self.iconbitmap(default=str(core.PAKET_DIR / "icon.ico"))
        except tk.TclError:
            pass

        self.q = queue.Queue()
        sys.stdout = sys.stderr = KuyrukYazici(self.q)
        core.ILERLEME = lambda y, m: self.q.put(("ilerleme", (y, m)))

        self.ayarlar = self._ayar_oku()
        self.mesgul = False
        self.hedef = tk.StringVar(value=self.ayarlar.get("hedef") or str(core.VARSAYILAN_HEDEF))
        self.hedef.trace_add("write", lambda *_: self._ayar_yaz())
        self.favoriler = [f for f in self.ayarlar.get("favoriler", VARSAYILAN_FAVORILER)]
        self.durum = tk.StringVar(value="Hazır")
        self.cihaz = ("bekle", "Cihaz aranıyor...", "", None)   # durum, başlık, detay, seri
        self.aktif = None
        self.dar = False

        # Kart tanımları: anahtar -> (başlık, açıklama, glif, renk, işlev)
        self.isler = {
            "incele": ("Yedekleri İncele", "Çağrı, mesaj, kişi, dosya, fotoğraf ve zaman çizelgesi",
                       "E721", "#16a34a", self.is_incele),
            "komple": ("Komple Yedek", "Depolama, çağrı/mesaj, uygulama listesi, APK ve SHA-256",
                       "E7B8", "#f59e0b", None),
            "whatsapp": ("WhatsApp Yedeği", "Klasörü çeker, hesap başına Oxygen uyumlu ZIP üretir",
                         "E8BD", "#22a559", None),
            "kayitlar": ("Çağrı ve Mesajlar", "Çağrı kaydı, SMS ve MMS'i hızlıca alır",
                         "E717", "#0ea5e9", None),
            "agent": ("Android Agent", "Rehber, takvim, MMS ekleri ve tüm uygulamalar (geçici ajan uygulamasıyla)",
                      "E9F5", "#0d2340", None),
            "uicc": ("SIM Kart İmajı (UICC)", "Kart okuyucudaki SIM'in dosya sistemini çıkarır",
                     "E875", "#ea580c", None),
            "sim": ("SIM Bilgisi (telefondan)", "Takılı ve daha önce takılmış SIM kartların kaydı",
                    "E870", "#0891b2", None),
            "notlar_yeni": ("Güncel Notlar", "Telefonda not yedeği aldırıp metne çevirir",
                            "E70B", "#eab308", None),
            "notlar": ("Mevcut Not Yedeği", "Telefondaki son MIUI not yedeğini çeker ve çözer",
                       "E81C", "#a855f7", None),
            "ab": ("adb backup (tam)", "Sadece .ab yedeği (telefonda onay gerekir)",
                   "E896", "#3b82f6", None),
            "paket": ("Paket Yedeği", "Tek bir uygulamanın .ab yedeğini alır",
                      "EA86", "#6366f1", None),
            "oxygen": ("Oxygen ZIP Üret", "Çekilmiş WhatsApp klasöründen ZIP oluşturur",
                       "F012", "#ef4444", self.is_oxygen),
            "ab2tar": (".ab → .tar", "Şifresiz .ab dosyasını açılabilir .tar'a çevirir",
                       "E8AB", "#64748b", self.is_ab2tar),
            "klasor": ("Kayıt Klasörü", "Alınan yedeklerin bulunduğu klasörü açar",
                       "E838", "#0d9488", lambda: self._klasor_ac(self.hedef.get())),
        }
        self.YONTEMLER = ["komple", "agent", "whatsapp", "kayitlar", "uicc", "sim", "notlar_yeni", "notlar", "ab", "paket"]
        self.ARACLAR = ["incele", "oxygen", "ab2tar", "klasor"]
        # Yöntem kartı tıklanınca açıklama sayfası açılır (Oxygen düzeni); iş oradaki eylem kartıyla başlar
        for a in self.YONTEMLER:
            b, ac, kod, renk, _ = self.isler[a]
            self.isler[a] = (b, ac, kod, renk, lambda a=a: self._yontem_ac(a))
        self.yontem = None
        self.yontem_yol = tk.StringVar(value=self.hedef.get())
        self.yontem_yol.trace_add("write", lambda *_: self._alan_guncelle())
        self.sim_pin = tk.StringVar()
        self.paket_adi = tk.StringVar(value="com.whatsapp")
        self.okuyucu = tk.StringVar()
        self.favoriler = [f for f in self.favoriler if f in self.isler]

        self._stil()
        self._arayuz()
        self._goster("masaustu")
        self.after(100, self._kuyruk_isle)
        self.after(300, self._cihaz_yokla)
        self.protocol("WM_DELETE_WINDOW", self._kapat)

    # ------------------------------------------------------------ ayarlar
    def _ayar_oku(self):
        try:
            return json.loads(AYAR_DOSYASI.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _ayar_yaz(self):
        try:
            AYAR_DOSYASI.parent.mkdir(parents=True, exist_ok=True)
            self.ayarlar.update(hedef=self.hedef.get(), favoriler=self.favoriler)
            AYAR_DOSYASI.write_text(json.dumps(self.ayarlar, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    # ------------------------------------------------------------ stil
    def _stil(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=YAZI, font=(FONT, 10))
        s.configure("TEntry", fieldbackground=BG, foreground=YAZI, insertcolor=YAZI,
                    bordercolor=KENAR, lightcolor=KENAR, darkcolor=KENAR, padding=6)
        s.configure("Ince.TButton", background=KART, foreground=YAZI, bordercolor=KENAR,
                    lightcolor=KART, darkcolor=KART, focusthickness=0, padding=(14, 6), font=(FONT, 10))
        s.map("Ince.TButton", background=[("active", KART_HOVER), ("disabled", BG)],
              foreground=[("disabled", KENAR)])
        s.configure("Ana.TButton", background=VURGU2, foreground="#ffffff", bordercolor=VURGU2,
                    lightcolor=VURGU2, darkcolor=VURGU2, focusthickness=0, padding=(22, 6),
                    font=(FONT, 10, "bold"))
        s.map("Ana.TButton", background=[("active", "#1d4ed8")])
        s.configure("Yesil.Horizontal.TProgressbar", troughcolor="#e5e9f0", background=VURGU2,
                    bordercolor="#e5e9f0", lightcolor=VURGU2, darkcolor=VURGU2, thickness=8)
        s.configure("Vertical.TScrollbar", background=KART, troughcolor=BG, bordercolor=BG,
                    arrowcolor=SOLUK, lightcolor=KART, darkcolor=KART)

    # ------------------------------------------------------------ iskelet
    def _arayuz(self):
        # --- sol menü
        self.yan = tk.Frame(self, bg=BG)
        self.yan.pack(side="left", fill="y")
        tk.Frame(self, bg=KENAR, width=1).pack(side="left", fill="y")

        bas = tk.Frame(self.yan, bg=BG)
        bas.pack(fill="x")
        try:
            self._logo = tk.PhotoImage(file=str(core.PAKET_DIR / "icon.png")).subsample(4, 4)
            self.logo_l = tk.Label(bas, image=self._logo, bg=BG, cursor="hand2")
        except tk.TclError:
            self.logo_l = tk.Label(bas, text=glif("E7B8"), font=(IKON, 30), bg=BG, fg=LACIVERT)
        self.logo_l.pack(side="left", padx=(16, 10), pady=(30, 22))
        self.logo_l.bind("<Button-1>", lambda _: self._goster("masaustu"))
        self.bas_yazi = tk.Frame(bas, bg=BG)
        self.bas_yazi.pack(side="left", pady=(24, 18), padx=(0, 40))
        tk.Label(self.bas_yazi, text=core.UYGULAMA_ADI, bg=BG, fg=YAZI,
                 font=(FONT, 24)).pack(anchor="w")
        tk.Label(self.bas_yazi, text=core.ALT_BASLIK, bg=BG, fg=YAZI,
                 font=(FONT, 13)).pack(anchor="w")
        self.daralt = tk.Label(bas, text=glif("E76B") + glif("E76B"), font=(IKON, 10), bg=BG,
                               fg=LACIVERT, cursor="hand2")
        self.daralt.place(relx=1, x=-8, y=8, anchor="ne")
        self.daralt.bind("<Button-1>", lambda _: self._daralt())
        tk.Frame(self.yan, bg=KENAR, height=1).pack(fill="x")

        self.menu = {}
        for anahtar, baslik, kod in [
                ("masaustu", "Masaüstü", "E80F"),
                ("cihaz", "Bağlı cihaz", "E8EA"),
                ("cihazlar", "Cihazlar", "E8CC"),
                ("yontemler", "Yöntemler", "F0E3"),
                ("araclar", "Araçlar", "E90F"),
                ("favoriler", "Favoriler", "E734"),
                ("gunluk", "İşlem günlüğü", "E9D9"),
                ("ayarlar", "Ayarlar", "E713"),
                ("hakkinda", "Hakkında", "E946")]:
            self._menu_ogesi(anahtar, baslik, kod)

        self.imza = tk.Label(self.yan, text=f"v{core.SURUM}  ·  © {core.YAPIMCI}\n{core.WEB.split('//')[1]}",
                             bg=BG, fg=SOLUK, font=(FONT, 9), cursor="hand2", justify="left")
        self.imza.pack(side="bottom", anchor="w", padx=20, pady=16)
        self.imza.bind("<Button-1>", lambda _: webbrowser.open(core.WEB))

        # --- sağ taraf
        sag = tk.Frame(self, bg=BG)
        sag.pack(side="left", fill="both", expand=True)

        ust = tk.Frame(sag, bg=UST, height=78)
        ust.pack(fill="x")
        ust.pack_propagate(False)
        self.yol = tk.Frame(ust, bg=UST)
        self.yol.pack(side="left", padx=16)
        # cihaz durumu (sağ üst)
        self.cihaz_kutu = tk.Frame(ust, bg=UST, cursor="hand2")
        self.cihaz_kutu.pack(side="right", padx=20)
        self.nokta = tk.Canvas(self.cihaz_kutu, width=12, height=12, bg=UST, highlightthickness=0)
        self.nokta.pack(side="left", padx=(0, 8))
        self.nokta_id = self.nokta.create_oval(1, 1, 11, 11, fill=UYARI, outline="")
        self.cihaz_yazi = tk.Label(self.cihaz_kutu, text="Cihaz aranıyor...", bg=UST, fg=YAZI,
                                   font=(FONT, 11), cursor="hand2")
        self.cihaz_yazi.pack(side="left")
        for w in (self.cihaz_kutu, self.cihaz_yazi, self.nokta):
            w.bind("<Button-1>", lambda _: self._goster("cihaz"))
        tk.Frame(sag, bg=KENAR, height=1).pack(fill="x")

        # alt durum çubuğu
        alt = tk.Frame(sag, bg=KART)
        alt.pack(side="bottom", fill="x")
        tk.Frame(alt, bg=KENAR, height=1).pack(fill="x", side="top")
        tk.Label(alt, textvariable=self.durum, bg=KART, fg=SOLUK, font=(FONT, 10),
                 anchor="w").pack(side="left", padx=18, pady=9, fill="x", expand=True)
        gl = tk.Label(alt, text="Günlüğü göster", bg=KART, fg=VURGU2, font=(FONT, 10, "underline"),
                      cursor="hand2")
        gl.pack(side="right", padx=(10, 18))
        gl.bind("<Button-1>", lambda _: self._goster("gunluk"))
        self.pb = ttk.Progressbar(alt, style="Yesil.Horizontal.TProgressbar", maximum=100, length=320)
        self.pb.pack(side="right")

        # içerik: kaydırılabilir sayfa + günlük
        self.icerik = tk.Frame(sag, bg=BG)
        self.icerik.pack(fill="both", expand=True)

        self.kaydir = tk.Frame(self.icerik, bg=BG)
        self.tuval = tk.Canvas(self.kaydir, bg=BG, highlightthickness=0)
        cubuk = ttk.Scrollbar(self.kaydir, orient="vertical", command=self.tuval.yview)
        self.tuval.configure(yscrollcommand=cubuk.set)
        cubuk.pack(side="right", fill="y")
        self.tuval.pack(side="left", fill="both", expand=True)
        self.sayfa = tk.Frame(self.tuval, bg=BG)
        self._sayfa_id = self.tuval.create_window(0, 0, window=self.sayfa, anchor="nw")
        self.sayfa.bind("<Configure>", lambda _: self.tuval.configure(scrollregion=self.tuval.bbox("all")))
        self.tuval.bind("<Configure>", lambda e: self.tuval.itemconfigure(self._sayfa_id, width=e.width))
        self.bind_all("<MouseWheel>", self._tekerlek)

        self._gunluk_kur()

    def _tekerlek(self, e):
        if self.aktif == "gunluk" or not self.kaydir.winfo_ismapped():
            return
        if self.sayfa.winfo_height() > self.tuval.winfo_height():
            self.tuval.yview_scroll(int(-e.delta / 120) * 3, "units")

    def _menu_ogesi(self, anahtar, baslik, kod):
        f = tk.Frame(self.yan, bg=BG, cursor="hand2")
        f.pack(fill="x")
        cizgi = tk.Frame(f, bg=BG, width=5)
        cizgi.pack(side="left", fill="y")
        i = tk.Label(f, text=glif(kod), font=(IKON, 18), bg=BG, fg=LACIVERT, cursor="hand2")
        i.pack(side="left", padx=(14, 14), pady=16)
        t = tk.Label(f, text=baslik, font=(FONT, 14), bg=BG, fg=YAZI, anchor="w", cursor="hand2")
        t.pack(side="left", fill="x", expand=True, padx=(0, 30))
        parcalar = (f, i, t)
        self.menu[anahtar] = (f, cizgi, i, t)

        def renk(r):
            for w in parcalar:
                w.configure(bg=r)

        def tikla(_):
            if anahtar == "hakkinda":
                HakkindaPenceresi(self)
            else:
                self._goster(anahtar)

        for w in parcalar:
            w.bind("<Button-1>", tikla)
            w.bind("<Enter>", lambda _: self.aktif != anahtar and renk(MENU_HOVER))
            w.bind("<Leave>", lambda _: self.aktif != anahtar and renk(BG))

    def _menu_guncelle(self):
        aktif = "yontemler" if self.aktif == "yontem" else self.aktif
        for anahtar, (f, cizgi, i, t) in self.menu.items():
            r = SECILI if anahtar == aktif else BG
            for w in (f, i, t):
                w.configure(bg=r)
            cizgi.configure(bg=LACIVERT if anahtar == aktif else r)

    def _daralt(self):
        self.dar = not self.dar
        for _, (f, cizgi, i, t) in self.menu.items():
            if self.dar:
                t.pack_forget()
            else:
                t.pack(side="left", fill="x", expand=True, padx=(0, 30))
        if self.dar:
            self.bas_yazi.pack_forget()
            self.imza.pack_forget()
            self.daralt.configure(text=glif("E76C") + glif("E76C"))
            self.logo_l.pack_configure(padx=(16, 16), pady=(40, 22))
        else:
            self.bas_yazi.pack(side="left", pady=(24, 18), padx=(0, 40))
            self.imza.pack(side="bottom", anchor="w", padx=20, pady=16)
            self.daralt.configure(text=glif("E76B") + glif("E76B"))
            self.logo_l.pack_configure(padx=(16, 10), pady=(30, 22))

    # ------------------------------------------------------------ yol çubuğu
    def _yol_hapi(self, metin, kod, hedef, secili):
        r = SECILI if secili else UST
        f = tk.Frame(self.yol, bg=r, cursor="hand2")
        f.pack(side="left", padx=(0, 4))
        parcalar = [f]
        if kod:
            i = tk.Label(f, text=glif(kod), font=(IKON, 15), bg=r, fg=LACIVERT, cursor="hand2")
            i.pack(side="left", padx=(14, 0), pady=10)
            parcalar.append(i)
        t = tk.Label(f, text=metin, font=(FONT, 15), bg=r, fg=YAZI, cursor="hand2")
        t.pack(side="left", padx=(10, 18), pady=8)
        parcalar.append(t)
        for w in parcalar:
            w.bind("<Button-1>", lambda _: self._goster(hedef))

    def _yol_ciz(self):
        for w in self.yol.winfo_children():
            w.destroy()
        adlar = {"cihaz": ("Bağlı cihaz", "E8EA"), "cihazlar": ("Cihazlar", "E8CC"), "yontemler": ("Yöntemler", "F0E3"),
                 "araclar": ("Araçlar", "E90F"), "favoriler": ("Favoriler", "E734"),
                 "gunluk": ("İşlem günlüğü", "E9D9"), "ayarlar": ("Ayarlar", "E713")}
        self._yol_hapi("Masaüstü", "E80F", "masaustu", self.aktif == "masaustu")
        if self.aktif == "yontem":
            for ad, kod, hedef, secili in [("Yöntemler", "F0E3", "yontemler", False),
                                           (self.isler[self.yontem][0], self.isler[self.yontem][2], "yontem", True)]:
                tk.Label(self.yol, text=glif("E76C"), font=(IKON, 10), bg=UST, fg=SOLUK).pack(side="left", padx=6)
                self._yol_hapi(ad, kod, hedef, secili)
        elif self.aktif in adlar:
            tk.Label(self.yol, text=glif("E76C"), font=(IKON, 10), bg=UST, fg=SOLUK).pack(side="left", padx=6)
            ad, kod = adlar[self.aktif]
            self._yol_hapi(ad, kod, self.aktif, True)

    # ------------------------------------------------------------ sayfalar
    def _goster(self, anahtar):
        self.aktif = anahtar
        self._menu_guncelle()
        self._yol_ciz()
        if anahtar == "gunluk":
            self.kaydir.pack_forget()
            self.gunluk.pack(fill="both", expand=True)
            return
        self.gunluk.pack_forget()
        self.kaydir.pack(fill="both", expand=True)
        for w in self.sayfa.winfo_children():
            w.destroy()
        self.tuval.yview_moveto(0)
        getattr(self, f"_sayfa_{anahtar}")()

    def _yenile(self):
        if self.aktif and self.aktif != "gunluk":
            konum = self.tuval.yview()[0]
            self._goster(self.aktif)
            self.after(10, lambda: self.tuval.yview_moveto(konum))

    def _baslik(self, metin, alt=None, ust_bosluk=30):
        tk.Label(self.sayfa, text=metin, bg=BG, fg=YAZI, font=(FONT, 17, "bold"),
                 anchor="w").pack(fill="x", padx=38, pady=(ust_bosluk, 0))
        if alt:
            tk.Label(self.sayfa, text=alt, bg=BG, fg=SOLUK, font=(FONT, 11),
                     anchor="w", justify="left").pack(fill="x", padx=38, pady=(2, 0))

    def _izgara(self, sutun, ust_bosluk=18):
        g = tk.Frame(self.sayfa, bg=BG)
        g.pack(fill="x", padx=30, pady=(ust_bosluk, 0))
        for c in range(sutun):
            g.columnconfigure(c, weight=1, uniform="k")
        return g

    def _sayfa_masaustu(self):
        g = self._izgara(3, ust_bosluk=34)
        kartlar = [
            ("Bağlı cihaz", "Bağlı cihazı otomatik algılayıp ondan veri almak için seçin",
             self._cihaz_katman(LACIVERT), lambda: self._goster("cihaz")),
            ("Yöntemler", "Gerekli veri alma yöntemini biliyorsanız seçin",
             [("F0E3", LACIVERT, 84)], lambda: self._goster("yontemler")),
            ("Araçlar", "Yedekleri inceleyin, Oxygen ZIP üretin, .ab dosyalarını çevirin",
             [("E90F", LACIVERT, 80)], lambda: self._goster("araclar")),
        ]
        for i, (b, a, k, fn) in enumerate(kartlar):
            self._kart(g, b, a, k, fn, ikon_h=190, bf=(FONT, 21), af=(FONT, 12)).grid(
                row=0, column=i, sticky="nsew", padx=8, pady=8)

        self._baslik("Favoriler", ust_bosluk=34)
        self._favori_izgara(kucuk=True)
        tk.Frame(self.sayfa, bg=BG, height=30).pack()

    def _favori_izgara(self, kucuk):
        if not self.favoriler:
            tk.Label(self.sayfa, text="Henüz favori yok. Yöntemler veya Araçlar sayfasında kartın "
                                      "köşesindeki ☆ simgesine tıklayarak ekleyebilirsiniz.",
                     bg=BG, fg=SOLUK, font=(FONT, 11), anchor="w").pack(fill="x", padx=38, pady=(10, 0))
            return
        sutun = 4
        g = self._izgara(sutun, ust_bosluk=10)
        for i, a in enumerate(self.favoriler):
            k = self._is_karti(g, a, kucuk=kucuk)
            k.grid(row=i // sutun, column=i % sutun, sticky="nsew", padx=8, pady=8)

    KATEGORI = {"uicc": "Diğer"}            # varsayılan: Android
    SEKMELER = ["Tümü", "Android", "Diğer"]

    def _sekme_cubugu(self, sekmeler, secili, sec, gorunum=None, gorunum_sec=None):
        """Sayfa üstü sekmeler (altı çizili) + sağda görünüm düğmeleri."""
        ust = tk.Frame(self.sayfa, bg=BG)
        ust.pack(fill="x", padx=30, pady=(14, 0))
        for ad in sekmeler:
            f = tk.Frame(ust, bg=BG, cursor="hand2")
            f.pack(side="left", padx=(8, 6))
            l = tk.Label(f, text=ad, bg=BG, fg=YAZI if ad == secili else SOLUK, font=(FONT, 12), cursor="hand2")
            l.pack(padx=2)
            tk.Frame(f, bg=LACIVERT if ad == secili else BG, height=2).pack(fill="x")
            for w in (f, l):
                w.bind("<Button-1>", lambda _, ad=ad: sec(ad))
        if gorunum:
            for kod, deger in (("E8FD", "liste"), ("E8A9", "izgara")):
                l = tk.Label(ust, text=glif(kod), font=(IKON, 14), bg=SECILI if gorunum == deger else BG,
                             fg=LACIVERT, cursor="hand2", padx=6, pady=3)
                l.pack(side="right", padx=2)
                l.bind("<Button-1>", lambda _, d=deger: gorunum_sec(d))
        tk.Frame(self.sayfa, bg=KENAR, height=1).pack(fill="x", padx=30, pady=(6, 0))

    def _ayar_degistir(self, **kw):
        self.ayarlar.update(kw)
        self._ayar_yaz()
        self._yenile()

    def _sayfa_yontemler(self):
        sekme = self.ayarlar.get("yontem_sekme", "Tümü")
        gorunum = self.ayarlar.get("yontem_gorunum", "izgara")
        self._sekme_cubugu(self.SEKMELER, sekme, lambda a: self._ayar_degistir(yontem_sekme=a),
                           gorunum, lambda d: self._ayar_degistir(yontem_gorunum=d))
        toplam = 0
        for kat in ("Android", "Diğer"):
            if sekme not in ("Tümü", kat):
                continue
            liste = [a for a in self.YONTEMLER if self.KATEGORI.get(a, "Android") == kat]
            tk.Label(self.sayfa, text=kat, bg=BG, fg=YAZI, font=(FONT, 12), anchor="w").pack(fill="x", padx=40, pady=(16, 0))
            toplam += len(liste)
            if gorunum == "liste":
                self._yontem_listesi(liste)
            else:
                g = self._izgara(7, ust_bosluk=8)
                for i, a in enumerate(liste):
                    self._is_karti(g, a, kucuk=True).grid(row=i // 7, column=i % 7, sticky="nsew", padx=7, pady=7)
        tk.Label(self.sayfa, text=f"Toplam: {toplam}", bg=BG, fg=YAZI, font=(FONT, 11), anchor="w").pack(
            fill="x", padx=40, pady=(22, 24))

    def _yontem_listesi(self, anahtarlar):
        kutu = tk.Frame(self.sayfa, bg=BG, highlightbackground=KENAR, highlightthickness=1)
        kutu.pack(fill="x", padx=38, pady=(8, 0))
        for i, a in enumerate(anahtarlar):
            baslik, aciklama, kod, renk, fn = self.isler[a]
            r = tk.Frame(kutu, bg=BG if i % 2 == 0 else KART, cursor="hand2")
            r.pack(fill="x")
            c = tk.Canvas(r, width=58, height=44, bg=r["bg"], highlightthickness=0, cursor="hand2")
            c.pack(side="left", padx=(12, 10), pady=8)
            c.create_rectangle(2, 2, 56, 42, fill=renk, outline="")
            c.create_text(29, 22, text=glif(kod), fill="#ffffff", font=(IKON, 17))
            t = tk.Label(r, text=baslik, bg=r["bg"], fg=YAZI, font=(FONT, 12), anchor="w", cursor="hand2", width=26)
            t.pack(side="left")
            d = tk.Label(r, text=aciklama, bg=r["bg"], fg=SOLUK, font=(FONT, 11), anchor="w", cursor="hand2")
            d.pack(side="left", fill="x", expand=True)
            fav = a in self.favoriler
            y = tk.Label(r, text=glif("E735" if fav else "E734"), font=(IKON, 14), bg=r["bg"],
                         fg=YILDIZ if fav else "#9aa3af", cursor="hand2")
            y.pack(side="right", padx=14)
            y.bind("<Button-1>", lambda _, a=a: self._favori_degistir(a))
            for w in (r, c, t, d):
                w.bind("<Button-1>", lambda _, fn=fn: fn())

    # ------------------------------------------------------------ cihazlar (desteklenen yöntemler)
    def _sayfa_cihazlar(self):
        import ambar_destek
        if not hasattr(self, "cihaz_ara"):
            self.cihaz_ara = tk.StringVar()
            self.cihaz_ara.trace_add("write", lambda *_: self._cihaz_listesi_ciz())
        ara = tk.Frame(self.sayfa, bg=BG, highlightbackground=KENAR, highlightcolor=VURGU2, highlightthickness=1)
        ara.pack(fill="x", padx=38, pady=(20, 0))
        tk.Label(ara, text=glif("E71C"), font=(IKON, 13), bg=BG, fg=SOLUK).pack(side="left", padx=(12, 6))
        tk.Frame(ara, bg=KENAR, width=1).pack(side="left", fill="y", pady=4)
        e = tk.Entry(ara, textvariable=self.cihaz_ara, font=(FONT, 12), relief="flat", bg=BG, fg=YAZI,
                     insertbackground=YAZI)
        e.pack(side="left", fill="x", expand=True, padx=10, ipady=7)
        tk.Label(ara, text=glif("E721"), font=(IKON, 13), bg=BG, fg=SOLUK).pack(side="right", padx=12)
        tk.Label(self.sayfa, text="Cihaz markasına, modeline, yonga setine veya işletim sistemine göre arayın. "
                                  "Düğmeye basınca ilgili yöntem açılır.",
                 bg=BG, fg=SOLUK, font=(FONT, 10), anchor="w").pack(fill="x", padx=40, pady=(6, 0))
        self.cihaz_liste_kutu = tk.Frame(self.sayfa, bg=BG, highlightbackground=KENAR, highlightthickness=1)
        self.cihaz_liste_kutu.pack(fill="x", padx=38, pady=(10, 0))
        self.cihaz_toplam = tk.Label(self.sayfa, bg=BG, fg=YAZI, font=(FONT, 11), anchor="w")
        self.cihaz_toplam.pack(fill="x", padx=40, pady=(10, 24))
        # bağlı cihaz ve yedeklerdeki cihazlar arka planda okunur
        self._katalog = getattr(self, "_katalog", None) or ambar_destek.katalog(None, [])
        self._cihaz_listesi_ciz()

        def oku():
            bagli = None
            try:
                if self.cihaz[0] == "ok":
                    bagli = core.cihaz_ozellikleri(ekran=False)
                    bagli["seri"] = self.cihaz[3]
            except Exception:
                pass
            try:
                import ambar_incele
                yedek = ambar_incele.cihazlari_grupla(ambar_incele.cikarmalari_bul(Path(self.hedef.get())))
            except Exception:
                yedek = []
            k = ambar_destek.katalog(bagli, yedek)

            def goster():
                self._katalog = k
                if self.aktif == "cihazlar":
                    self._cihaz_listesi_ciz()
            self.q.put(("fonk", goster))
        threading.Thread(target=oku, daemon=True).start()

    def _cihaz_listesi_ciz(self):
        import ambar_destek
        kutu = getattr(self, "cihaz_liste_kutu", None)
        if not kutu or not kutu.winfo_exists():
            return
        for w in kutu.winfo_children():
            w.destroy()
        ara = self.cihaz_ara.get().strip().lower()
        liste = [c for c in self._katalog if not ara or ara in " ".join(
            (c["ad"], c["marka"], c["model"], c["yonga"], c["isletim"], c["aciklama"])).lower()]
        for i, c in enumerate(liste):
            r = tk.Frame(kutu, bg=BG if i % 2 == 0 else KART)
            r.pack(fill="x")
            sol = tk.Frame(r, bg=r["bg"])
            sol.pack(side="left", fill="x", expand=True, padx=(16, 8), pady=8)
            b = tk.Frame(sol, bg=r["bg"])
            b.pack(fill="x")
            tk.Label(b, text=c["ad"], bg=r["bg"], fg=YAZI, font=(FONT, 12), anchor="w").pack(side="left")
            if c["kaynak"] != "Profil":
                tk.Label(b, text=c["kaynak"], bg=VURGU if c["kaynak"] == "Bağlı" else "#64748b", fg="#ffffff",
                         font=(FONT, 8, "bold"), padx=6).pack(side="left", padx=8)
            for k, v in (("Yonga seti", c["yonga"] or "-"), ("İşletim sistemi", c["isletim"]),
                         *((("", c["aciklama"]),) if c["aciklama"] else ())):
                s2 = tk.Frame(sol, bg=r["bg"])
                s2.pack(fill="x")
                if k:
                    tk.Label(s2, text=k, bg=r["bg"], fg=SOLUK, font=(FONT, 9), width=14, anchor="e").pack(side="left")
                tk.Label(s2, text=v, bg=r["bg"], fg=YAZI if k else SOLUK, font=(FONT, 9), anchor="w").pack(
                    side="left", padx=(6 if k else 0, 0))
            sag = tk.Frame(r, bg=r["bg"])
            sag.pack(side="right", padx=14)
            for y, notu in c["yontemler"]:
                yazi = ambar_destek.YONTEM_KISA[y][0]
                l = tk.Label(sag, text=yazi, bg=LACIVERT if not notu else "#64748b", fg="#ffffff", font=(FONT, 10),
                             padx=12, pady=5, cursor="hand2")
                l.pack(side="left", padx=4)
                l.bind("<Button-1>", lambda _, y=y: self._yontem_ac(y))
                if notu:
                    self._ipucu(l, notu)
                else:
                    self._ipucu(l, ambar_destek.YONTEM_KISA[y][1])
        self.cihaz_toplam.configure(text=f"Toplam: {len(liste)}")

    def _ipucu(self, w, metin):
        def gir(e):
            self._ip = tk.Toplevel(self)
            self._ip.wm_overrideredirect(True)
            self._ip.geometry(f"+{e.x_root + 12}+{e.y_root + 14}")
            tk.Label(self._ip, text=metin, bg="#111827", fg="#ffffff", font=(FONT, 9), padx=8, pady=4).pack()

        def cik(_):
            ip = getattr(self, "_ip", None)
            if ip:
                ip.destroy()
                self._ip = None
        w.bind("<Enter>", gir, add="+")
        w.bind("<Leave>", cik, add="+")
        w.bind("<Button-1>", cik, add="+")

    # ------------------------------------------------------------ yöntem sayfası (açıklama + çıkarma yolu + eylem)
    def _yontem_bilgisi(self, a):
        """giris, maddeler, telefon (bağlı cihaz gerekir mi), alanlar, eylemler[(başlık, alt, glif, iş(h))]."""
        if a == "komple":
            return dict(
                giris="Bu yöntem, bağlı Android cihazdan root gerektirmeden alınabilen tüm verileri tek seferde çıkarmak için tasarlanmıştır.",
                maddeler=["Telefonda USB hata ayıklama açık olmalı ve bu bilgisayara izin verilmiş olmalıdır.",
                          "Dahili depolamanın tamamı kopyalanır; hedef sürücüde yeterli boş alan olduğundan emin olun.",
                          "adb backup adımında telefonda 'Verilerimi yedekle' düğmesine basın, şifre girmeyin.",
                          "Çağrı kaydı, SMS/MMS, hesaplar, SIM abonelik geçmişi, uygulama listesi ve APK'lar da alınır.",
                          "İşlem sonunda tüm dosyaların SHA-256 özet listesi oluşturulur.",
                          "İşlem süresince telefonun ekranını açık tutun ve kabloyu çıkarmayın."],
                telefon=True, eylemler=[
                    ("Komple yedek", "Depolama, kayıtlar, adb backup ve APK'ların çıkarılması", "E7B8",
                     lambda h: core.komple_yedek(h)),
                    ("Komple yedek (adb backup olmadan)", "Telefonda onay gerektirmeyen tüm verilerin çıkarılması", "E7B8",
                     lambda h: core.komple_yedek(h, ab_al=False))])
        if a == "agent":
            return dict(
                giris="Bu yöntem, telefona geçici olarak kurulan Ambar Agent uygulamasıyla adb'nin tek başına okuyamadığı "
                      "verileri çıkarmak için tasarlanmıştır.",
                maddeler=["Android 5.0 - 16 çalıştıran cihazlar desteklenir.",
                          "Telefonda 'USB hata ayıklama' açık olmalı ve bu bilgisayara izin verilmiş olmalıdır. Seçenek "
                          "görünmüyorsa Ayarlar > Telefon hakkında > Yapım numarasına 7 kez dokunarak geliştirici modunu açın.",
                          "Ambar Agent kurulum sırasında gereken izinleri alır; telefonda tek tek onay gerekmez. Bazı "
                          "markalarda kurulum onayı veya Play Protect uyarısı çıkar: 'Yükle' / 'Yine de yükle'ye basın.",
                          "Xiaomi / Redmi / POCO: Geliştirici seçenekleri > 'USB ile yükle' açık olmalıdır.",
                          "Çıkarma süresince telefonun kilidi açık kalmalıdır; otomatik kilidi geçici olarak kapatın.",
                          "Cihazı uçak moduna almanız önerilir (veri çıkarma USB üzerinden yapılır, internet gerekmez).",
                          "Okunanlar: rehber (kişi fotoğraflarıyla), takvim, çağrı kaydı, SMS, MMS ve ekleri, tüm "
                          "uygulamaların gerçek adları ve simgeleri, hesaplar, eşleşmiş Bluetooth cihazları.",
                          "Ambar Agent'ın internet izni yoktur; veri yalnızca USB kablosu üzerinden, her oturumda "
                          "değişen bir anahtarla aktarılır. Telefondaki hiçbir veri değiştirilmez."],
                uyarilar=["İşlem bitince Ambar Agent telefondan otomatik olarak kaldırılır.",
                          "Ambar Agent kaldırılamadıysa (kablo çıkarıldıysa vb.) aşağıdaki 'Ambar Agent'ı kaldır' "
                          "kartını kullanın veya telefonda Ayarlar > Uygulamalar > Ambar Agent > Kaldır'a basın."],
                telefon=True, eylemler=[
                    ("Kullanıcı verilerini USB üzerinden çıkarın", "Kullanıcı verilerinin cihazdan çıkarılması", "E9F5",
                     lambda h: __import__("ambar_agent").agent_cek(h)),
                    ("Ambar Agent'ı telefondan kaldırın", "Önceki bir işlemden kalan ajan uygulamasını siler", "E74D",
                     lambda h: print("Ambar Agent " + ("kaldırıldı." if "Success" in (core.adb("uninstall", "com.ambar.agent").stdout or "")
                                                       else "telefonda kurulu değil."))) ])
        if a == "whatsapp":
            def secici(liste):
                return self.ana_threadde(lambda: SecimPenceresi(self, liste).sonuc)
            return dict(
                giris="Bu yöntem, WhatsApp ve WhatsApp Business verilerini dahili depolamadan çıkarmak için tasarlanmıştır.",
                maddeler=["Mesaj veritabanı yedekleri (msgstore.db.crypt14/15) ve tüm medya dosyaları alınır.",
                          "Birden fazla WhatsApp klasörü (çoklu hesap) bulunursa hangilerinin alınacağı sorulur.",
                          "Her hesap için Oxygen Forensic Detective ile açılabilen ZIP dosyası üretilir.",
                          "Mesajları çözmek için 64 haneli yedek anahtarı gerekir; anahtar inceleme ekranında girilir."],
                telefon=True, eylemler=[("WhatsApp verilerini çıkart", "Klasörün çekilmesi ve Oxygen uyumlu ZIP üretimi",
                                         "E8BD", lambda h: core.whatsapp_cek(h, None, sor=False, secici=secici))])
        if a == "kayitlar":
            return dict(
                giris="Bu yöntem, çağrı ve mesaj kayıtlarını telefonun sistem veritabanlarından hızlıca çıkarmak için tasarlanmıştır.",
                maddeler=["Çağrı kaydı, SMS ve MMS mesajları alınır.",
                          "Telefondaki hesaplar, uygulama listesi ve SIM abonelik geçmişi de kaydedilir.",
                          "Telefonda ayrıca bir onay gerekmez; işlem genellikle bir dakikadan kısa sürer."],
                telefon=True, eylemler=[("Kayıtları çıkart", "Çağrı, SMS, MMS, hesap ve SIM kayıtlarının çıkarılması", "E717",
                                         lambda h: core.kayitlar_cek(h))])
        if a == "uicc":
            return dict(
                giris="Bu yöntem, dosya sistemini SIM kartlardan çıkarmak için tasarlanmıştır.",
                maddeler=["Yalnızca fiziksel SIM kartlar desteklenir (eSIM okunamaz).",
                          "Bilgisayar tarafından desteklenen (PC/SC - CCID uyumlu) bir USB SIM / akıllı kart okuyucu kullanın.",
                          "Çıkarmaya başlamadan önce kartı SIM kart okuyucusuna takın ve kart okuyucuyu PC'ye bağlayın.",
                          "Okuyucunun program tarafından algılandığından emin olun (aşağıda listelenir).",
                          "Rehber ve SMS gibi korumalı dosyalar için PIN girilmesi gerekebilir. "
                          "Kalan deneme hakkı 1 ise PIN karta gönderilmez (kart kilitlenmesin).",
                          "Kart yalnızca okunur; içeriği değiştirilmez. Karta gönderilen tüm komutlar kayda geçirilir."],
                telefon=False, alanlar=["okuyucu", "pin"], eylemler=[
                    ("Dosya sistemini çıkart", "Dosya sisteminin SIM karttan çıkarılması", "E875", self._uicc_is)])
        if a == "sim":
            return dict(
                giris="Bu yöntem, SIM kart bilgilerini telefonun kayıtlarından çıkarmak için tasarlanmıştır.",
                maddeler=["Takılı ve daha önce bu telefona takılmış tüm SIM kartlar listelenir (operatör, numara, ICCID, IMSI).",
                          "Android 14 ve sonrası ICCID, IMSI ve numaranın son hanelerini maskeler. "
                          "Tam değerler için kartı 'SIM Kart İmajı (UICC)' yöntemiyle okuyun.",
                          "Telefon izin veriyorsa SIM rehberi ve SIM'de saklanan SMS'ler de alınır.",
                          "Çağrı ve mesajların hangi SIM'den yapıldığı inceleme ekranında bu kayıtla eşleştirilir."],
                telefon=True, eylemler=[("SIM bilgilerini çıkart", "Abonelik geçmişi, SIM rehberi ve SIM SMS'leri", "E870",
                                         lambda h: core.sim_cek(h))])
        if a == "notlar_yeni":
            return dict(
                giris="Bu yöntem, telefonda yeni bir not yedeği oluşturup notları metne çevirmek için tasarlanmıştır (MIUI / Xiaomi).",
                maddeler=["Başlat'a bastıktan sonra telefonda yedekleme ekranı açılır.",
                          "Telefonda 'Yedekle'ye basın, yalnızca 'Notlar'ı seçin ve şifre koymadan başlatın.",
                          "Program yeni yedeği otomatik algılayıp çeker ve notları çözümler."],
                telefon=True, eylemler=[("Not yedeği al ve çöz", "Telefonda yedek oluşturulması ve notların çıkarılması",
                                         "E70B", lambda h: core.notlari_cek(h, yeni=True))])
        if a == "notlar":
            return dict(
                giris="Bu yöntem, telefonda daha önce alınmış en son not yedeğini çekip çözümlemek için tasarlanmıştır (MIUI / Xiaomi).",
                maddeler=["Telefonda yeni bir işlem yapılmaz; mevcut yedek kopyalanır.",
                          "Notlar metne çevrilir ve inceleme ekranında listelenir."],
                telefon=True, eylemler=[("Mevcut not yedeğini çıkart", "Son MIUI not yedeğinin çekilmesi ve çözülmesi",
                                         "E81C", lambda h: core.notlari_cek(h))])
        if a == "ab":
            return dict(
                giris="Bu yöntem, Android'in yerleşik adb backup özelliğiyle tam yedek almak için tasarlanmıştır.",
                maddeler=["Başlat'a bastıktan sonra telefonda 'Verilerimi yedekle' düğmesine basın. Şifre girmeyin.",
                          "Uygulamaların yedeklenmeye izin verdiği veriler ve APK'lar alınır.",
                          "Çıkan .ab dosyası Araçlar > '.ab → .tar' ile açılabilir hale getirilir.",
                          "Android 12 ve sonrasında birçok uygulama bu yöntemle veri vermez."],
                telefon=True, eylemler=[("adb backup al", "Tam .ab yedeğinin oluşturulması", "E896",
                                         lambda h: core.adb_backup(h, None, apk=True, paylasilan=True, sistem=False))])
        if a == "paket":
            return dict(
                giris="Bu yöntem, tek bir uygulamanın adb backup yedeğini almak için tasarlanmıştır.",
                maddeler=["Aşağıya uygulamanın paket adını yazın (örn. com.whatsapp).",
                          "Başlat'a bastıktan sonra telefonda 'Verilerimi yedekle' düğmesine basın. Şifre girmeyin.",
                          "Uygulama yedeklenmeye izin vermiyorsa .ab dosyası boş çıkar."],
                telefon=True, alanlar=["paket"], eylemler=[
                    ("Paket yedeği al", "Seçilen uygulamanın .ab yedeğinin oluşturulması", "EA86",
                     lambda h: core.adb_backup(h, self.paket_adi.get().strip(), apk=True, paylasilan=False, sistem=False))])
        return dict(giris="", maddeler=[], eylemler=[])

    def _yontem_ac(self, a):
        self.yontem = a
        self.yontem_yol.set(self.hedef.get())
        self._goster("yontem")

    def _sayfa_yontem(self):
        a = self.yontem
        b = self._yontem_bilgisi(a)
        g = tk.Frame(self.sayfa, bg=BG)
        g.pack(fill="both", expand=True, padx=38, pady=(28, 24))
        sar = []
        tk.Label(g, text=b["giris"], bg=BG, fg=YAZI, font=(FONT, 12), justify="left", anchor="w").pack(fill="x")
        sar.append(g.winfo_children()[-1])
        mf = tk.Frame(g, bg=BG)
        mf.pack(fill="x", pady=(18, 0))
        for m in b["maddeler"]:
            r = tk.Frame(mf, bg=BG)
            r.pack(fill="x", pady=2)
            tk.Label(r, text="●", bg=BG, fg=YAZI, font=(FONT, 8)).pack(side="left", anchor="n", padx=(4, 8), pady=(5, 0))
            l = tk.Label(r, text=m, bg=BG, fg=YAZI, font=(FONT, 12), justify="left", anchor="w")
            l.pack(side="left", fill="x", expand=True)
            sar.append(l)
        g.bind("<Configure>", lambda e: [w.configure(wraplength=max(200, e.width - 40)) for w in sar], add="+")

        for u in b.get("uyarilar", []):
            k = tk.Frame(g, bg="#f3dfa2")
            k.pack(fill="x", pady=(10 if u == b["uyarilar"][0] else 4, 0))
            tk.Label(k, text="!", bg="#f3dfa2", fg="#d97706", font=(FONT, 12, "bold")).pack(side="left", padx=(10, 4), anchor="n", pady=6)
            l = tk.Label(k, text=u, bg="#f3dfa2", fg=YAZI, font=(FONT, 12), justify="left", anchor="w")
            l.pack(side="left", fill="x", expand=True, pady=8, padx=(0, 10))
            sar.append(l)
        if b.get("telefon") and self.cihaz[0] != "ok":
            u = tk.Frame(g, bg="#fef3c7", highlightbackground="#f59e0b", highlightthickness=1)
            u.pack(fill="x", pady=(18, 0))
            tk.Label(u, text=glif("E7BA") + "  Bağlı cihaz yok: " + self.cihaz[1], bg="#fef3c7", fg="#92400e",
                     font=(FONT, 11), anchor="w").pack(fill="x", padx=12, pady=8)

        tk.Frame(g, bg=BG, height=40).pack()
        # ek alanlar
        for alan in b.get("alanlar", []):
            if alan == "okuyucu":
                tk.Label(g, text="Kart okuyucu:", bg=BG, fg=YAZI, font=(FONT, 12), anchor="w").pack(fill="x")
                r = tk.Frame(g, bg=BG)
                r.pack(fill="x", pady=(4, 12))
                import ambar_sim
                liste = ambar_sim.okuyucu_listesi()
                if self.okuyucu.get() not in liste:
                    self.okuyucu.set(liste[0] if liste else "")
                if liste:
                    ttk.Combobox(r, textvariable=self.okuyucu, values=liste, state="readonly",
                                 font=(FONT, 11)).pack(side="left", fill="x", expand=True)
                else:
                    tk.Label(r, text=glif("E7BA") + "  Bağlı kart okuyucu bulunamadı. Okuyucuyu takıp 'Yenile'ye basın.",
                             bg=BG, fg=HATA, font=(FONT, 11), anchor="w").pack(side="left", fill="x", expand=True)
                ttk.Button(r, text="Yenile", style="Ince.TButton", command=self._yenile).pack(side="left", padx=(8, 0))
            elif alan == "pin":
                tk.Label(g, text="PIN (gerekmiyorsa boş bırakın):", bg=BG, fg=YAZI, font=(FONT, 12), anchor="w").pack(fill="x")
                e = ttk.Entry(g, textvariable=self.sim_pin, show="●", font=(FONT, 12), width=12)
                e.pack(anchor="w", pady=(4, 12))
            elif alan == "paket":
                tk.Label(g, text="Paket adı:", bg=BG, fg=YAZI, font=(FONT, 12), anchor="w").pack(fill="x")
                ttk.Entry(g, textvariable=self.paket_adi, font=(FONT, 12)).pack(fill="x", pady=(4, 12))

        # çıkarma yolu
        tk.Label(g, text="Çıkarma yolu:", bg=BG, fg=YAZI, font=(FONT, 12), anchor="w").pack(fill="x")
        kutu = tk.Frame(g, bg=BG, highlightbackground=KENAR, highlightcolor=VURGU2, highlightthickness=1)
        kutu.pack(fill="x", pady=(4, 0))
        giris = tk.Entry(kutu, textvariable=self.yontem_yol, font=(FONT, 12), relief="flat", bg=BG, fg=YAZI,
                         insertbackground=YAZI)
        giris.pack(side="left", fill="x", expand=True, padx=(10, 4), ipady=7)
        k = tk.Label(kutu, text=glif("ED25"), font=(IKON, 15), bg=BG, fg=LACIVERT, cursor="hand2")
        k.pack(side="right", padx=(4, 10))
        k.bind("<Button-1>", lambda _: self._yontem_gozat())
        self.alan_l = tk.Label(g, bg=BG, fg=YAZI, font=(FONT, 12), anchor="w", justify="left")
        self.alan_l.pack(fill="x", pady=(8, 0))
        self._alan_guncelle()

        # eylem kartları
        for baslik, alt, kod, is_ in b["eylemler"]:
            self._eylem_karti(g, baslik, alt, kod, lambda baslik=baslik, is_=is_, tel=b.get("telefon"):
                              self._yontem_baslat(baslik, is_, tel))

    def _eylem_karti(self, ust, baslik, alt, kod, fn):
        f = tk.Frame(ust, bg=BG, highlightbackground=KENAR, highlightthickness=1, cursor="hand2")
        f.pack(fill="x", pady=(16, 0))
        c = tk.Canvas(f, width=96, height=96, bg=BG, highlightthickness=0, cursor="hand2")
        c.pack(side="left", padx=(14, 6), pady=12)
        c.create_oval(6, 6, 90, 90, fill=LACIVERT, outline="")
        c.create_text(48, 48, text=glif(kod), fill="#ffffff", font=(IKON, 28))
        m = tk.Frame(f, bg=BG, cursor="hand2")
        m.pack(side="left", fill="both", expand=True, pady=12)
        t1 = tk.Label(m, text=baslik, bg=BG, fg=YAZI, font=(FONT, 13), anchor="w", cursor="hand2")
        t1.pack(fill="x", pady=(8, 0))
        t2 = tk.Label(m, text=alt, bg=BG, fg=SOLUK, font=(FONT, 12), anchor="w", cursor="hand2")
        t2.pack(fill="x", pady=(14, 0))
        parcalar = [f, c, m, t1, t2]
        for w in parcalar:
            w.bind("<Enter>", lambda _: [x.configure(bg=KART_HOVER) for x in parcalar] + [f.configure(highlightbackground=KENAR_HOVER)])
            w.bind("<Leave>", lambda _: [x.configure(bg=BG) for x in parcalar] + [f.configure(highlightbackground=KENAR)])
            w.bind("<Button-1>", lambda _: fn())

    def _alan_guncelle(self):
        l = getattr(self, "alan_l", None)
        if not l or not l.winfo_exists():
            return
        yol = self.yontem_yol.get().strip()
        try:
            p = Path(yol)
            while not p.exists() and p != p.parent:
                p = p.parent
            metin = f"Kullanılabilir alan: {core.boyut_yaz(shutil.disk_usage(p).free)}"
            if yol and not Path(yol).is_dir():
                metin += "   (klasör yoksa oluşturulur)"
        except Exception:
            metin = "Klasör yolu geçersiz görünüyor."
        l.configure(text=metin)

    def _yontem_gozat(self):
        """Gezgin görünümlü seçici ayrı süreçte açılır ve sahibi bu penceredir. Seçici kapanana kadar ana iş
        parçacığı beklerse pencere mesaj işleyemez, seçici de çizilemez (karşılıklı kilitlenme). Bu yüzden
        bekleme arka planda yapılır; sonuç kuyrukla ana iş parçacığına döner."""
        import ambar_incele
        if getattr(self, "_secici_acik", False):
            return
        self._secici_acik = True
        baslangic = self.yontem_yol.get()
        sahip = self.winfo_toplevel().wm_frame()

        def calis():
            try:
                d, hata = ambar_incele.klasor_sec(baslangic, "Çıkarma yolunu seçin", int(sahip, 16)), None
            except Exception as e:
                d, hata = "", e

            def bitti():
                self._secici_acik = False
                if hata:            # PowerShell yoksa vb.: tkinter'in kendi seçicisi
                    s = filedialog.askdirectory(initialdir=baslangic or str(core.SCRIPT_DIR))
                else:
                    s = d
                if s:
                    self.yontem_yol.set(str(Path(s)))
            self.q.put(("fonk", bitti))
        threading.Thread(target=calis, daemon=True).start()

    def _yontem_baslat(self, ad, is_, telefon):
        yol = self.yontem_yol.get().strip()
        if not yol:
            messagebox.showerror(ad, "Çıkarma yolunu seçin.")
            return
        try:
            Path(yol).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror(ad, f"Klasör oluşturulamadı:\n{e}")
            return
        if telefon and self.cihaz[0] != "ok":
            messagebox.showerror(ad, f"Bağlı cihaz yok: {self.cihaz[1]}\n\n{self.cihaz[2]}")
            return
        if self.yontem == "paket" and not self.paket_adi.get().strip():
            messagebox.showerror(ad, "Paket adını yazın.")
            return
        self.hedef.set(str(Path(yol)))
        self.calistir(ad, is_)

    def _uicc_is(self, h):
        import ambar_sim
        pin = self.sim_pin.get().strip() or None
        self.ana_threadde(lambda: self.sim_pin.set(""))      # PIN bellekte / ekranda kalmasın
        return ambar_sim.uicc_cek(h, pin=pin, okuyucu=self.okuyucu.get() or None)

    def _sayfa_araclar(self):
        self._baslik("Araçlar", "Alınmış yedekler üzerinde çalışan araçlar (cihaz bağlı olması gerekmez).")
        self._is_izgarasi(self.ARACLAR)

    def _sayfa_favoriler(self):
        self._baslik("Favoriler", "Sık kullandığınız yöntem ve araçlar. Kaldırmak için ★ simgesine tıklayın.")
        self._favori_izgara(kucuk=False)

    def _is_izgarasi(self, anahtarlar, sutun=4):
        g = self._izgara(sutun)
        for i, a in enumerate(anahtarlar):
            self._is_karti(g, a).grid(row=i // sutun, column=i % sutun, sticky="nsew", padx=8, pady=8)
        tk.Frame(self.sayfa, bg=BG, height=30).pack()

    def _is_karti(self, ust, anahtar, kucuk=False):
        baslik, aciklama, kod, renk, fn = self.isler[anahtar]
        if kucuk:   # Favoriler: renkli kutu + başlık (Oxygen'deki gibi)
            return self._kart(ust, baslik, None, [("#kutu", renk, 104, 78), (kod, "#ffffff", 34)], fn,
                              ikon_h=128, bf=(FONT, 12), ortali=False, yildiz=anahtar)
        return self._kart(ust, baslik, aciklama, [(kod, LACIVERT, 50)], fn,
                          ikon_h=150, bf=(FONT, 15), af=(FONT, 10), yildiz=anahtar)

    # ------------------------------------------------------------ kart
    def _cihaz_katman(self, rozet_renk, boyut=80):
        """Telefon + sağ altta onay rozeti."""
        o = boyut / 80
        return [("E8EA", LACIVERT, boyut),
                ("E91F", BG, int(36 * o), 25 * o, 31 * o),
                ("EC61", rozet_renk, int(30 * o), 25 * o, 31 * o)]

    def _ikon_ciz(self, c, katmanlar):
        def ciz(_=None):
            c.delete("all")
            w, h = c.winfo_width(), c.winfo_height()
            for k in katmanlar:
                if k[0] == "#kutu":
                    _, renk, kw, kh = k
                    c.create_rectangle(w / 2 - kw / 2, h / 2 - kh / 2, w / 2 + kw / 2, h / 2 + kh / 2,
                                       fill=renk, outline="")
                    continue
                kod, renk, boyut, dx, dy = (tuple(k) + (0, 0))[:5]
                px = self.winfo_fpixels("1p")
                c.create_text(w / 2 + dx * px, h / 2 + dy * px, text=glif(kod), fill=renk,
                              font=(IKON, boyut), anchor="center")
        c.bind("<Configure>", ciz)

    def _kart(self, ust, baslik, aciklama, katmanlar, fn, ikon_h=160, bf=(FONT, 18), af=(FONT, 11),
              ortali=True, yildiz=None):
        f = tk.Frame(ust, bg=BG, highlightbackground=KENAR, highlightcolor=KENAR,
                     highlightthickness=1, cursor="hand2")
        c = tk.Canvas(f, bg=BG, height=ikon_h, highlightthickness=0, cursor="hand2")
        c.pack(fill="x")
        self._ikon_ciz(c, katmanlar)
        alt = tk.Frame(f, bg=KART, cursor="hand2")
        alt.pack(fill="both", expand=True)
        b = tk.Label(alt, text=baslik, bg=KART, fg=YAZI, font=bf, cursor="hand2",
                     anchor="center" if ortali else "w")
        b.pack(fill="x", padx=14, pady=(14 if ortali else 12, 2))
        if not ortali:      # uzun başlık iki satıra sarılır (yıldıza yer bırakılır)
            b.configure(justify="left")
            f.bind("<Configure>", lambda e: b.configure(wraplength=max(80, e.width - 50)), add="+")
        parcalar = [c, alt, b]
        if aciklama:
            a = tk.Label(alt, text=aciklama, bg=KART, fg=YAZI if bf[1] > 18 else SOLUK, font=af,
                         justify="center" if ortali else "left", cursor="hand2")
            a.pack(fill="x", padx=14, pady=(2, 34 if yildiz else 22))
            f.bind("<Configure>", lambda e: a.configure(wraplength=max(100, e.width - 34)), add="+")
            parcalar.append(a)
        else:
            tk.Frame(alt, bg=KART, height=34).pack()
        y = None
        if yildiz:
            fav = yildiz in self.favoriler
            y = tk.Label(alt, text=glif("E735" if fav else "E734"), font=(IKON, 15), bg=KART,
                         fg=YILDIZ if fav else "#9aa3af", cursor="hand2")
            y.place(relx=1, rely=1, x=-10, y=-8, anchor="se")
            y.bind("<Button-1>", lambda _: self._favori_degistir(yildiz))

        def gir(_):
            f.configure(highlightbackground=KENAR_HOVER)
            for w in parcalar[1:] + ([y] if y else []):
                w.configure(bg=KART_HOVER)

        def cik(_):
            f.configure(highlightbackground=KENAR)
            for w in parcalar[1:] + ([y] if y else []):
                w.configure(bg=KART)

        for w in parcalar + [f]:
            w.bind("<Enter>", gir)
            w.bind("<Leave>", cik)
            w.bind("<Button-1>", lambda _: fn())
        if y:
            y.bind("<Enter>", gir)
        return f

    def _favori_degistir(self, anahtar):
        if anahtar in self.favoriler:
            self.favoriler.remove(anahtar)
        else:
            self.favoriler.append(anahtar)
        self._ayar_yaz()
        self._yenile()

    # ------------------------------------------------------------ bağlı cihaz sayfası
    def _sayfa_cihaz(self):
        d, baslik, detay, seri = self.cihaz
        panel = tk.Frame(self.sayfa, bg=BG, highlightbackground=KENAR, highlightthickness=1)
        panel.pack(fill="x", padx=38, pady=(34, 0))
        renk = {"ok": VURGU, "uyari": UYARI, "bekle": UYARI}.get(d, HATA)
        c = tk.Canvas(panel, bg=BG, width=230, height=230, highlightthickness=0)
        c.pack(side="left", padx=(10, 0), pady=10)
        self._ikon_ciz(c, self._cihaz_katman(renk, 92))

        sag = tk.Frame(panel, bg=BG)
        sag.pack(side="left", fill="both", expand=True, padx=(10, 24), pady=26)
        tk.Label(sag, text=baslik, bg=BG, fg=YAZI, font=(FONT, 20), anchor="w").pack(fill="x")
        tk.Label(sag, text=detay if d != "ok" else "Bağlı ve yetkili  ·  USB hata ayıklama",
                 bg=BG, fg=renk if d != "ok" else VURGU, font=(FONT, 11), anchor="w").pack(fill="x", pady=(0, 12))
        self.ozellik_kutu = tk.Frame(sag, bg=BG)
        self.ozellik_kutu.pack(fill="x")
        dugmeler = tk.Frame(sag, bg=BG)
        dugmeler.pack(fill="x", pady=(16, 0))
        ttk.Button(dugmeler, text="Yeniden algıla", style="Ince.TButton",
                   command=self._cihaz_yeniden).pack(side="left")
        ttk.Button(dugmeler, text="Kayıt klasörünü aç", style="Ince.TButton",
                   command=lambda: self._klasor_ac(self.hedef.get())).pack(side="left", padx=8)

        if d == "ok":
            tk.Label(self.ozellik_kutu, text="Cihaz bilgileri okunuyor...", bg=BG, fg=SOLUK,
                     font=(FONT, 10)).pack(anchor="w")
            kutu = self.ozellik_kutu

            def oku():
                try:
                    o = core.cihaz_ozellikleri(ekran=False)     # sayfaya bakmak telefonda ekran açmasın
                    o["seri"] = seri
                except Exception as e:
                    o = {"hata": str(e)[:120]}
                self.q.put(("fonk", lambda: self._ozellik_goster(kutu, o)))
            threading.Thread(target=oku, daemon=True).start()
        else:
            yardim = ("1) Telefonda Ayarlar > Telefon hakkında > Yapım numarasına 7 kez dokunun.\n"
                      "2) Geliştirici seçenekleri > USB hata ayıklama'yı açın.\n"
                      "3) USB kablosunu takın ve telefonda çıkan izni onaylayın.")
            tk.Label(self.ozellik_kutu, text=yardim, bg=BG, fg=YAZI, font=(FONT, 11),
                     justify="left").pack(anchor="w")

        self._baslik("Bu cihazdan veri al", "Yöntemi seçin; işlem başlayınca ilerleme İşlem günlüğünde görünür.",
                     ust_bosluk=30)
        self._is_izgarasi(self.YONTEMLER)

    def _ozellik_goster(self, kutu, o):
        if not kutu.winfo_exists():
            return
        for w in kutu.winfo_children():
            w.destroy()
        etiket = [("marka", "Üretici"), ("model", "Model"), ("ticari_ad", "Ticari ad"),
                  ("cihaz_adi", "Cihaz adı"), ("cihaz_kodu", "Cihaz kodu"), ("android", "Android"),
                  ("sdk", "SDK"), ("guvenlik_yamasi", "Güvenlik yaması"), ("yapi", "Yapı"),
                  ("seri", "Seri no"), ("imei", "IMEI"), ("yonga", "Yonga seti"), ("android_id", "Android ID"),
                  ("saat_dilimi", "Saat dilimi"), ("hata", "Hata")]
        satirlar = [(ad, o[k]) for k, ad in etiket if o.get(k)]
        yarim = (len(satirlar) + 1) // 2
        for i, (ad, v) in enumerate(satirlar):
            sutun = 0 if i < yarim else 2
            tk.Label(kutu, text=ad, bg=BG, fg=SOLUK, font=(FONT, 10), anchor="w").grid(
                row=i % yarim, column=sutun, sticky="w", padx=(0, 14), pady=2)
            tk.Label(kutu, text=v, bg=BG, fg=YAZI, font=(FONT, 10), anchor="w").grid(
                row=i % yarim, column=sutun + 1, sticky="w", padx=(0, 40), pady=2)

    def _cihaz_yeniden(self):
        self._son_seri = None
        threading.Thread(target=self._cihaz_bilgi, args=(True,), daemon=True).start()

    # ------------------------------------------------------------ ayarlar sayfası
    def _sayfa_ayarlar(self):
        self._baslik("Kayıt klasörü", "Tüm yedekler bu klasörün altına, tarih damgalı alt klasörlere kaydedilir.")
        r = tk.Frame(self.sayfa, bg=BG)
        r.pack(fill="x", padx=38, pady=(12, 0))
        ttk.Entry(r, textvariable=self.hedef, font=(FONT, 11)).pack(side="left", fill="x", expand=True)
        ttk.Button(r, text="Gözat", style="Ince.TButton", command=self._gozat).pack(side="left", padx=(8, 0))
        ttk.Button(r, text="Klasörü aç", style="Ince.TButton",
                   command=lambda: self._klasor_ac(self.hedef.get())).pack(side="left", padx=(6, 0))

        self._baslik("Favoriler", "Masaüstündeki favori kartlarını varsayılana döndürür.", ust_bosluk=34)
        r2 = tk.Frame(self.sayfa, bg=BG)
        r2.pack(fill="x", padx=38, pady=(12, 0))
        ttk.Button(r2, text="Favorileri sıfırla", style="Ince.TButton",
                   command=self._favori_sifirla).pack(side="left")

        self._baslik("Program", ust_bosluk=34)
        bilgi = tk.Frame(self.sayfa, bg=BG)
        bilgi.pack(fill="x", padx=38, pady=(10, 0))
        for i, (k, v) in enumerate([("Sürüm", core.SURUM), ("adb", self._adb_yolu()),
                                    ("Ayar dosyası", str(AYAR_DOSYASI))]):
            tk.Label(bilgi, text=k, bg=BG, fg=SOLUK, font=(FONT, 10)).grid(row=i, column=0, sticky="w",
                                                                         padx=(0, 18), pady=2)
            tk.Label(bilgi, text=v, bg=BG, fg=YAZI, font=(FONT, 10)).grid(row=i, column=1, sticky="w", pady=2)
        r3 = tk.Frame(self.sayfa, bg=BG)
        r3.pack(fill="x", padx=38, pady=(14, 30))
        ttk.Button(r3, text="Hakkında / Güncellemeleri denetle", style="Ince.TButton",
                   command=lambda: HakkindaPenceresi(self)).pack(side="left")

    def _favori_sifirla(self):
        self.favoriler = list(VARSAYILAN_FAVORILER)
        self._ayar_yaz()
        messagebox.showinfo("Favoriler", "Favoriler varsayılana döndürüldü.")

    # ------------------------------------------------------------ günlük
    def _gunluk_kur(self):
        self.gunluk = tk.Frame(self.icerik, bg=BG)
        bas = tk.Frame(self.gunluk, bg=BG)
        bas.pack(fill="x", padx=38, pady=(26, 10))
        tk.Label(bas, text="İşlem günlüğü", bg=BG, fg=YAZI, font=(FONT, 17, "bold")).pack(side="left")
        ttk.Button(bas, text="Temizle", style="Ince.TButton", command=self._log_temizle).pack(side="right")
        ttk.Button(bas, text="Kayıt klasörünü aç", style="Ince.TButton",
                   command=lambda: self._klasor_ac(self.hedef.get())).pack(side="right", padx=8)
        cerceve = tk.Frame(self.gunluk, bg=KENAR)
        cerceve.pack(fill="both", expand=True, padx=38, pady=(0, 24))
        self.log = tk.Text(cerceve, bg=LOG_BG, fg=YAZI, insertbackground=YAZI, relief="flat",
                           font=("Cascadia Mono", 10), wrap="word", padx=12, pady=10, state="disabled")
        kay = ttk.Scrollbar(cerceve, command=self.log.yview)
        self.log.configure(yscrollcommand=kay.set)
        kay.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.log.tag_configure("hata", foreground=HATA)
        self.log.tag_configure("uyari", foreground=UYARI)
        self.log.tag_configure("tamam", foreground=VURGU)
        self.log.tag_configure("baslik", foreground="#1d4ed8", font=("Cascadia Mono", 10, "bold"))

    # ------------------------------------------------------------ yardımcılar
    def _adb_yolu(self):
        try:
            if core.ADB is None:
                core.ADB = core.adb_bul()
            return core.ADB
        except SystemExit:
            return "BULUNAMADI"

    def _gozat(self):
        d = filedialog.askdirectory(initialdir=self.hedef.get() or str(core.SCRIPT_DIR))
        if d:
            self.hedef.set(d)

    def _klasor_ac(self, yol):
        p = Path(yol)
        p.mkdir(parents=True, exist_ok=True)
        os.startfile(p)

    def _log_temizle(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log_ekle(self, s):
        self.log.configure(state="normal")
        for satir in s.splitlines(keepends=True):
            u = satir.upper()
            tag = ("hata" if "HATA" in u else
                   "uyari" if "UYARI" in u else
                   "tamam" if ("TAMAM" in u or "BİTTİ" in u or "KAYDEDİLDİ" in u) else
                   "baslik" if satir.startswith((">>>", "[", "=====")) else None)
            self.log.insert("end", satir, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _kuyruk_isle(self):
        try:
            while True:
                tur, veri = self.q.get_nowait()
                if tur == "log":
                    self._log_ekle(veri)
                elif tur == "ilerleme":
                    y, m = veri
                    if y is None:
                        if str(self.pb["mode"]) != "indeterminate":
                            self.pb.configure(mode="indeterminate")
                            self.pb.start(12)
                    else:
                        if str(self.pb["mode"]) != "determinate":
                            self.pb.stop()
                            self.pb.configure(mode="determinate")
                        self.pb["value"] = y
                    if m:
                        self.durum.set(m + (f"  (%{y:.0f})" if y is not None else ""))
                elif tur == "bitti":
                    self._bitti(*veri)
                elif tur == "cihaz":
                    self._cihaz_goster(*veri)
                elif tur == "fonk":
                    veri()
                elif tur == "cagir":
                    fn, olay, kutu = veri
                    kutu.append(fn())
                    olay.set()
        except queue.Empty:
            pass
        self.after(100, self._kuyruk_isle)

    def ana_threadde(self, fn):
        """İş parçacığından GUI diyaloğu açmak için."""
        olay, kutu = threading.Event(), []
        self.q.put(("cagir", (fn, olay, kutu)))
        olay.wait()
        return kutu[0]

    # ------------------------------------------------------------ cihaz yoklama
    def _cihaz_yokla(self, tek=False):
        if not self.mesgul:
            threading.Thread(target=self._cihaz_bilgi, daemon=True).start()
        if not tek:
            self.after(4000, self._cihaz_yokla)

    def _cihaz_bilgi(self, zorla=False):
        try:
            satirlar = core.adb("devices").stdout.strip().splitlines()[1:]
            cihazlar = [s.split() for s in satirlar if s.strip()]
            if not cihazlar:
                self.q.put(("cihaz", ("yok", "Cihaz bağlı değil", "USB hata ayıklamayı açıp kabloyu takın",
                                      None, zorla)))
                return
            seri, durum = cihazlar[0][0], cihazlar[0][1]
            if durum == "unauthorized":
                self.q.put(("cihaz", ("uyari", "İzin bekleniyor",
                                      "Telefonda USB hata ayıklama iznini onaylayın", seri, zorla)))
                return
            if durum != "device":
                self.q.put(("cihaz", ("uyari", f"Durum: {durum}", seri, seri, zorla)))
                return
            if getattr(self, "_son_seri", None) != seri:
                model = core.adb("shell", "getprop", "ro.product.model").stdout.strip()
                surum = core.adb("shell", "getprop", "ro.build.version.release").stdout.strip()
                self._son_seri, self._son_bilgi = seri, f"{model}  ·  Android {surum}"
            self.q.put(("cihaz", ("ok", self._son_bilgi, f"Seri: {seri}", seri, zorla)))
        except SystemExit:
            self.q.put(("cihaz", ("yok", "adb bulunamadı", "platform-tools eksik", None, zorla)))
        except Exception as e:
            self.q.put(("cihaz", ("yok", "Hata", str(e)[:60], None, zorla)))

    def _cihaz_goster(self, d, baslik, detay, seri, zorla=False):
        if d != "ok":
            self._son_seri = None
        degisti = self.cihaz[:2] != (d, baslik)
        self.cihaz = (d, baslik, detay, seri)
        self.nokta.itemconfigure(self.nokta_id, fill={"ok": VURGU, "uyari": UYARI}.get(d, HATA))
        self.cihaz_yazi.configure(text=baslik)
        if (self.aktif == "cihaz" and (degisti or zorla)) or (self.aktif == "yontem" and degisti):
            self._yenile()

    # ------------------------------------------------------------ iş çalıştırma
    def calistir(self, ad, fn):
        """fn(kayıt klasörü) iş parçacığında çalışır; çıktı İşlem günlüğüne akar."""
        if self.mesgul:
            messagebox.showinfo(ad, "Başka bir işlem devam ediyor. Bitmesini bekleyin.")
            return
        self.mesgul = True
        self.configure(cursor="watch")
        self.durum.set(f"{ad} çalışıyor...")
        self.pb.configure(mode="indeterminate")
        self.pb.start(12)
        self._goster("gunluk")
        self._log_ekle(f"\n===== {ad} =====\n")

        def is_parcacigi():
            sonuc, hata = None, None
            try:
                sonuc = fn(Path(self.hedef.get()))
            except SystemExit as e:
                hata = e.code if isinstance(e.code, str) else "İşlem durduruldu."
            except Exception as e:
                hata = f"{type(e).__name__}: {e}"
            self.q.put(("bitti", (ad, sonuc, hata)))

        threading.Thread(target=is_parcacigi, daemon=True).start()

    def _bitti(self, ad, sonuc, hata):
        self.mesgul = False
        self.configure(cursor="")
        self.pb.stop()
        self.pb.configure(mode="determinate")
        if hata:
            self.pb["value"] = 0
            self.durum.set(f"{ad}: HATA")
            self._log_ekle(f"HATA: {hata}\n")
            messagebox.showerror(ad, hata)
            return
        self.pb["value"] = 100
        self.durum.set(f"{ad}: tamamlandı")
        if isinstance(sonuc, Path) and sonuc.exists():
            secim = BittiPenceresi(self, ad, sonuc).secim
            if secim == "incele":
                try:
                    self.is_incele(sonuc.parent, sonuc)
                except Exception as e:
                    messagebox.showerror("Yedekleri İncele", f"İnceleme ekranı açılamadı:\n{e}")
            elif secim == "klasor":
                self._klasor_ac(sonuc if sonuc.is_dir() else sonuc.parent)
        else:
            messagebox.showinfo(ad, "İşlem tamamlandı.")

    # ------------------------------------------------------------ işler
    def is_oxygen(self):
        d = filedialog.askdirectory(title="WhatsApp yedek klasörünü seçin", initialdir=self.hedef.get())
        if d:
            self.calistir("Oxygen ZIP", lambda h: (core.oxygen_ziple(Path(d)), Path(d))[1])

    def is_incele(self, kok: Path | None = None, cikarma: Path | None = None):
        """İnceleme programını (yerel sunucu + uygulama penceresi) açar.
        cikarma verilirse (yeni biten yedek) inceleme ekranı doğrudan o cihazla açılır."""
        import ambar_incele
        kok = Path(kok or self.hedef.get())
        if not kok.is_dir():
            messagebox.showerror("Yedekleri İncele", f"Kayıt klasörü bulunamadı:\n{kok}")
            return
        if getattr(self, "_incele", None) and self._incele[0] != kok:
            self._incele[1].shutdown()
            self._incele = None
        if not getattr(self, "_incele", None):
            srv, url = ambar_incele.sunucu_baslat(kok)
            self._incele = (kok, srv, url)
            self._log_ekle(f"\n===== Yedekleri İncele =====\nİnceleme programı açıldı: {url}\nKlasör: {kok}\n")
        st = ambar_incele.Isleyici.st
        if Path(st.kok).resolve() != kok.resolve():     # inceleme ekranından başka klasöre geçilmişse geri al
            st.kok_degistir(kok)
        else:
            st.yenile()                                   # yeni alınan yedek listeye girsin
        adres = self._incele[2]
        if cikarma:
            hedef = str(Path(cikarma).resolve())
            g = next((g for g in st.cihazlar if any(c["yol"] == hedef for c in g["cikarmalar"])), None)
            if g:
                adres += f"#c={g['id']}"
        ambar_incele.pencere_ac(adres)

    def is_ab2tar(self):
        f = filedialog.askopenfilename(title=".ab dosyası seçin", initialdir=self.hedef.get(),
                                       filetypes=[("Android yedeği", "*.ab"), ("Tümü", "*.*")])
        if f:
            self.calistir(".ab → .tar", lambda h: core.ab_to_tar(Path(f)))

    def _kapat(self):
        if self.mesgul and not messagebox.askyesno(
                "Çıkış", "Bir işlem devam ediyor. Yine de kapatılsın mı?\n(Yarım kalan yedek eksik olur.)"):
            return
        self.destroy()


class HakkindaPenceresi(tk.Toplevel):
    def __init__(self, ust):
        super().__init__(ust)
        self.title(f"Hakkında - {core.UYGULAMA_ADI}")
        self.configure(bg=BG)
        self.resizable(False, False)

        govde = tk.Frame(self, bg=BG)
        govde.pack(padx=32, pady=(26, 10))

        # Simge (varsa)
        try:
            self._ikon = tk.PhotoImage(file=str(core.PAKET_DIR / "icon.png")).subsample(3, 3)
            tk.Label(govde, image=self._ikon, bg=BG).pack()
        except tk.TclError:
            pass

        tk.Label(govde, text=core.UYGULAMA_ADI, bg=BG, fg=YAZI,
                 font=(FONT, 18, "bold")).pack(pady=(10, 0))
        tk.Label(govde, text=f"{core.ALT_BASLIK}  ·  Sürüm {core.SURUM}", bg=BG, fg=SOLUK,
                 font=(FONT, 10)).pack()

        tk.Label(govde, text=("Telefonun içindeki her şey; düzenli, doğrulanabilir\n"
                              "ve dokunulmamış halde ambarınızda.\n\n"
                              "Dahili depolama, WhatsApp (çoklu hesap / Oxygen uyumlu ZIP),\n"
                              "Xiaomi Notlar, APK'lar ve SHA-256 özet listesi."),
                 bg=BG, fg=YAZI, font=(FONT, 10), justify="center").pack(pady=(14, 14))

        kart = tk.Frame(govde, bg=KART, highlightbackground=KENAR, highlightthickness=1)
        kart.pack(fill="x")
        satirlar = [
            ("Yapımcı", core.YAPIMCI, None),
            ("Web", core.WEB.split("//")[1], core.WEB),
            ("GitHub", core.GITHUB.split("//")[1], core.GITHUB),
            ("Python", sys.version.split()[0], None),
            ("adb", ust._adb_yolu(), None),
        ]
        for i, (k, v, link) in enumerate(satirlar):
            tk.Label(kart, text=k, bg=KART, fg=SOLUK, font=(FONT, 9)).grid(
                row=i, column=0, sticky="w", padx=(14, 18), pady=4)
            l = tk.Label(kart, text=v, bg=KART, fg=VURGU2 if link else YAZI,
                         font=(FONT, 10, "underline" if link else "normal"),
                         cursor="hand2" if link else "", wraplength=330, justify="left")
            l.grid(row=i, column=1, sticky="w", padx=(0, 14), pady=4)
            if link:
                l.bind("<Button-1>", lambda _, u=link: webbrowser.open(u))

        tk.Label(govde, text=("Yalnızca sahibi olduğunuz veya incelemeye yetkili olduğunuz\n"
                              "cihazlarda kullanın."),
                 bg=BG, fg=SOLUK, font=(FONT, 8), justify="center").pack(pady=(14, 0))
        tk.Label(govde, text=f"© {datetime.date.today().year} {core.YAPIMCI}. Tüm hakları saklıdır.",
                 bg=BG, fg=SOLUK, font=(FONT, 8)).pack(pady=(4, 0))

        alt = tk.Frame(self, bg=BG)
        alt.pack(fill="x", padx=32, pady=(10, 22))
        ttk.Button(alt, text="Kapat", style="Ince.TButton", command=self.destroy).pack(side="right")
        ttk.Button(alt, text="Web sitesi", style="Ince.TButton",
                   command=lambda: webbrowser.open(core.WEB)).pack(side="right", padx=6)
        self.gnc = ttk.Button(alt, text="Güncellemeleri denetle", style="Ince.TButton",
                              command=self._guncelleme)
        self.gnc.pack(side="left")

        self.transient(ust)
        self.update_idletasks()
        x = ust.winfo_rootx() + (ust.winfo_width() - self.winfo_width()) // 2
        y = ust.winfo_rooty() + (ust.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.bind("<Escape>", lambda _: self.destroy())

    def _guncelleme(self):
        self.gnc.configure(text="Denetleniyor...", state="disabled")

        def calis():
            try:
                sonuc = ("ok", core.guncelleme_denetle())
            except Exception as e:
                sonuc = ("hata", str(e))
            self.after(0, lambda: self._guncelleme_sonuc(*sonuc))

        threading.Thread(target=calis, daemon=True).start()

    def _guncelleme_sonuc(self, tur, veri):
        if not self.winfo_exists():
            return
        self.gnc.configure(text="Güncellemeleri denetle", state="normal")
        if tur == "hata":
            if "404" in veri:   # henüz yayınlanmış sürüm yok
                messagebox.showinfo("Güncelleme", "GitHub'da henüz yayınlanmış bir sürüm yok.", parent=self)
            else:
                messagebox.showerror("Güncelleme", f"Denetlenemedi:\n{veri}", parent=self)
            return
        yeni, son, url = veri
        if yeni:
            if messagebox.askyesno("Güncelleme",
                                   f"Yeni sürüm var: v{son}\nKullandığınız sürüm: v{core.SURUM}\n\n"
                                   "İndirme sayfası açılsın mı?", parent=self):
                webbrowser.open(url)
        else:
            messagebox.showinfo("Güncelleme", f"En güncel sürümü kullanıyorsunuz (v{core.SURUM}).", parent=self)


class BittiPenceresi(tk.Toplevel):
    """İşlem bitince: incelemede aç / klasörü aç / kapat. secim: "incele" | "klasor" | None"""

    def __init__(self, ust, ad, sonuc: Path):
        super().__init__(ust)
        self.secim = None
        self.title(ad)
        self.configure(bg=BG)
        self.resizable(False, False)
        incelenebilir = sonuc.is_dir() and (sonuc / "ambar_bilgi.json").exists()
        g = tk.Frame(self, bg=BG)
        g.pack(fill="both", expand=True, padx=28, pady=(22, 8))
        bas = tk.Frame(g, bg=BG)
        bas.pack(fill="x")
        tk.Label(bas, text=glif("E930"), font=(IKON, 28), bg=BG, fg=VURGU).pack(side="left", padx=(0, 14), anchor="n")
        bb = tk.Frame(bas, bg=BG)
        bb.pack(side="left", fill="x")
        tk.Label(bb, text="İşlem tamamlandı", bg=BG, fg=YAZI, font=(FONT, 15, "bold"), anchor="w").pack(fill="x")
        tk.Label(bb, text=("Alınan veriler inceleme ekranında açılsın mı?" if incelenebilir
                           else "Çıktı aşağıdaki konuma kaydedildi."),
                 bg=BG, fg=SOLUK, font=(FONT, 10), anchor="w").pack(fill="x")
        k = tk.Frame(g, bg=KART, highlightbackground=KENAR, highlightthickness=1)
        k.pack(fill="x", pady=(16, 0))
        satirlar = [("Kayıt yeri", str(sonuc))]
        if incelenebilir:
            try:
                m = json.loads((sonuc / "ambar_bilgi.json").read_text(encoding="utf-8"))
                if m.get("sure_sn") is not None:
                    satirlar.append(("Süre", str(datetime.timedelta(seconds=int(m["sure_sn"])))))
                adet, boyut = core.klasor_istatistik(sonuc)
                satirlar.append(("İçerik", f"{adet} dosya · {core.boyut_yaz(boyut)}"))
            except Exception:
                pass
        for i, (a, v) in enumerate(satirlar):
            tk.Label(k, text=a, bg=KART, fg=SOLUK, font=(FONT, 10)).grid(row=i, column=0, sticky="nw", padx=(12, 10), pady=3)
            tk.Label(k, text=v, bg=KART, fg=YAZI, font=(FONT, 10), wraplength=560, justify="left").grid(
                row=i, column=1, sticky="w", padx=(0, 12), pady=3)
        alt = tk.Frame(self, bg=BG)
        alt.pack(fill="x", padx=28, pady=(16, 22))
        if incelenebilir:
            ana = ttk.Button(alt, text="İncelemede aç", style="Ana.TButton", command=lambda: self._sec("incele"))
            ana.pack(side="right")
        ttk.Button(alt, text="Klasörü aç", style="Ince.TButton", command=lambda: self._sec("klasor")).pack(side="right", padx=8)
        ttk.Button(alt, text="Kapat", style="Ince.TButton", command=self.destroy).pack(side="right")
        self.bind("<Return>", lambda _: self._sec("incele" if incelenebilir else "klasor"))
        self.bind("<Escape>", lambda _: self.destroy())
        self.transient(ust)
        self.update_idletasks()
        x = ust.winfo_rootx() + (ust.winfo_width() - self.winfo_width()) // 2
        y = ust.winfo_rooty() + (ust.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.grab_set()
        self.focus_force()
        self.wait_window()

    def _sec(self, s):
        self.secim = s
        self.destroy()


class SecimPenceresi(tk.Toplevel):
    """Birden fazla com.whatsapp klasörü bulunduğunda seçim penceresi."""

    def __init__(self, ust, liste):
        super().__init__(ust)
        self.title("Klasör seçin")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.sonuc = []
        tk.Label(self, text="Hangi klasörler çekilsin?", bg=BG, fg=YAZI,
                 font=(FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(14, 6))
        self.degiskenler = []
        for i, y in enumerate(liste):
            v = tk.BooleanVar(value=(i == 0))
            tk.Checkbutton(self, text=y, variable=v, bg=BG, fg=YAZI, selectcolor=KART,
                           activebackground=BG, activeforeground=YAZI,
                           font=(FONT, 10)).pack(anchor="w", padx=16)
            self.degiskenler.append((v, y))
        f = tk.Frame(self, bg=BG)
        f.pack(fill="x", padx=16, pady=14)
        ttk.Button(f, text="Tamam", style="Ince.TButton", command=self._tamam).pack(side="right")
        ttk.Button(f, text="İptal", style="Ince.TButton", command=self.destroy).pack(side="right", padx=6)
        self.transient(ust)
        self.grab_set()
        self.wait_window()

    def _tamam(self):
        self.sonuc = [y for v, y in self.degiskenler if v.get()]
        self.destroy()


if __name__ == "__main__":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)   # keskin yazı (yüksek DPI)
    except Exception:
        pass
    Uygulama().mainloop()
