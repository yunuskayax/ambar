#!/usr/bin/env python3
"""Ambar - grafik arayüz (tkinter). ambar.py'deki fonksiyonları kullanır."""

import datetime
import os
import queue
import sys
import threading
import webbrowser
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import ambar as core

# ---------------------------------------------------------------- renkler
BG = "#0f172a"        # arka plan
KART = "#1e293b"      # kart
KART_HOVER = "#273449"
KENAR = "#334155"
YAZI = "#e2e8f0"
SOLUK = "#94a3b8"
VURGU = "#22c55e"     # yeşil (başarı / bağlı)
MARKA = "#f59e0b"     # kehribar (Ambar marka rengi)
VURGU2 = "#3b82f6"    # mavi
UYARI = "#f59e0b"
HATA = "#ef4444"
LOG_BG = "#020617"

FONT = "Segoe UI"


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
        self.title(f"{core.UYGULAMA_ADI} v{core.SURUM}")
        self.geometry("1100x760")
        self.minsize(860, 600)
        self.configure(bg=BG)
        try:
            self.iconbitmap(default=str(core.PAKET_DIR / "icon.ico"))
        except tk.TclError:
            pass

        self.q = queue.Queue()
        sys.stdout = sys.stderr = KuyrukYazici(self.q)
        core.ILERLEME = lambda y, m: self.q.put(("ilerleme", (y, m)))

        self.mesgul = False
        self.hedef = tk.StringVar(value=str(core.VARSAYILAN_HEDEF))
        self.cihaz_durum = tk.StringVar(value="Cihaz aranıyor...")
        self.cihaz_detay = tk.StringVar(value="")
        self.durum = tk.StringVar(value="Hazır")
        self.butonlar = []

        self._stil()
        self._arayuz()
        self.after(100, self._kuyruk_isle)
        self.after(300, self._cihaz_yokla)
        self.protocol("WM_DELETE_WINDOW", self._kapat)

    # ------------------------------------------------------------ stil
    def _stil(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("Kart.TFrame", background=KART)
        s.configure("TLabel", background=BG, foreground=YAZI, font=(FONT, 10))
        s.configure("Baslik.TLabel", font=(FONT, 20, "bold"))
        s.configure("Alt.TLabel", foreground=SOLUK, font=(FONT, 10))
        s.configure("KartBaslik.TLabel", background=KART, foreground=YAZI, font=(FONT, 11, "bold"))
        s.configure("KartYazi.TLabel", background=KART, foreground=SOLUK, font=(FONT, 9))
        s.configure("TEntry", fieldbackground=KART, foreground=YAZI, insertcolor=YAZI,
                    bordercolor=KENAR, lightcolor=KENAR, darkcolor=KENAR)
        s.configure("Ince.TButton", background=KART, foreground=YAZI, bordercolor=KENAR,
                    focusthickness=0, padding=(10, 5), font=(FONT, 9))
        s.map("Ince.TButton", background=[("active", KART_HOVER), ("disabled", BG)],
              foreground=[("disabled", KENAR)])
        s.configure("Yesil.Horizontal.TProgressbar", troughcolor=KART, background=MARKA,
                    bordercolor=KART, lightcolor=MARKA, darkcolor=MARKA, thickness=10)

    # ------------------------------------------------------------ arayüz
    def _arayuz(self):
        kok = ttk.Frame(self, padding=(22, 18))
        kok.pack(fill="both", expand=True)

        # Başlık + cihaz durumu
        ust = ttk.Frame(kok)
        ust.pack(fill="x")
        sol = ttk.Frame(ust)
        sol.pack(side="left")
        ttk.Label(sol, text=core.UYGULAMA_ADI, style="Baslik.TLabel").pack(anchor="w")
        ttk.Label(sol, text=f"{core.ALT_BASLIK}  ·  depolama, WhatsApp, notlar, APK",
                  style="Alt.TLabel").pack(anchor="w")

        cihaz = tk.Frame(ust, bg=KART, highlightbackground=KENAR, highlightthickness=1)
        cihaz.pack(side="right")
        self.nokta = tk.Canvas(cihaz, width=14, height=14, bg=KART, highlightthickness=0)
        self.nokta.pack(side="left", padx=(14, 6), pady=12)
        self.nokta_id = self.nokta.create_oval(2, 2, 12, 12, fill=UYARI, outline="")
        bilgi = tk.Frame(cihaz, bg=KART)
        bilgi.pack(side="left", padx=(0, 10), pady=6)
        tk.Label(bilgi, textvariable=self.cihaz_durum, bg=KART, fg=YAZI,
                 font=(FONT, 10, "bold")).pack(anchor="w")
        tk.Label(bilgi, textvariable=self.cihaz_detay, bg=KART, fg=SOLUK, font=(FONT, 9)).pack(anchor="w")
        ttk.Button(cihaz, text="Yenile", style="Ince.TButton",
                   command=lambda: self._cihaz_yokla(tek=True)).pack(side="left", padx=(0, 12))

        # Hedef klasör
        hedef = ttk.Frame(kok)
        hedef.pack(fill="x", pady=(18, 10))
        ttk.Label(hedef, text="Kayıt klasörü:").pack(side="left")
        ttk.Entry(hedef, textvariable=self.hedef, font=(FONT, 10)).pack(
            side="left", fill="x", expand=True, padx=8, ipady=3)
        ttk.Button(hedef, text="Gözat", style="Ince.TButton", command=self._gozat).pack(side="left")
        ttk.Button(hedef, text="Klasörü aç", style="Ince.TButton",
                   command=lambda: self._klasor_ac(self.hedef.get())).pack(side="left", padx=(6, 0))

        # İşlem kartları
        izgara = ttk.Frame(kok)
        izgara.pack(fill="x")
        kartlar = [
            ("Komple Yedek", "Tüm depolama, APK'lar, adb backup ve SHA-256", MARKA, self.is_komple),
            ("WhatsApp Yedeği", "Klasörü çeker, hesap başına Oxygen ZIP üretir", MARKA, self.is_whatsapp),
            ("Güncel Notlar", "Telefonda not yedeği aldırır,\nnotlar.txt olarak kaydeder", MARKA, self.is_notlar_yeni),
            ("Mevcut Not Yedeği", "Telefondaki son MIUI not\nyedeğini çeker ve çözer", VURGU2, self.is_notlar),
            ("adb backup (tam)", "Sadece .ab yedeği\n(telefonda onay gerekir)", VURGU2, self.is_ab),
            ("Paket Yedeği", "Tek uygulamanın .ab yedeği", VURGU2, self.is_paket),
            ("Oxygen ZIP Üret", "Çekilmiş WhatsApp klasöründen ZIP oluşturur", VURGU2, self.is_oxygen),
            (".ab → .tar", "Şifresiz .ab dosyasını\naçılabilir .tar'a çevirir", VURGU2, self.is_ab2tar),
        ]
        for i, (baslik, aciklama, renk, fn) in enumerate(kartlar):
            self._kart(izgara, baslik, aciklama, renk, fn).grid(
                row=i // 4, column=i % 4, sticky="nsew", padx=5, pady=5)
        for c in range(4):
            izgara.columnconfigure(c, weight=1, uniform="k")

        # İlerleme
        alt = ttk.Frame(kok)
        alt.pack(fill="x", pady=(12, 6))
        ttk.Label(alt, textvariable=self.durum, style="Alt.TLabel").pack(anchor="w")
        self.pb = ttk.Progressbar(alt, style="Yesil.Horizontal.TProgressbar", maximum=100)
        self.pb.pack(fill="x", pady=(4, 0))

        # Log
        alt2 = ttk.Frame(kok)
        alt2.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Button(alt2, text="Hakkında", style="Ince.TButton",
                   command=lambda: HakkindaPenceresi(self)).pack(side="right", padx=(6, 0))
        ttk.Button(alt2, text="Logu temizle", style="Ince.TButton",
                   command=self._log_temizle).pack(side="right")
        imza = tk.Label(alt2, text=f"v{core.SURUM}  ·  © {core.YAPIMCI}  ·  {core.WEB.split('//')[1]}",
                        bg=BG, fg=SOLUK, font=(FONT, 9), cursor="hand2")
        imza.pack(side="left")
        imza.bind("<Button-1>", lambda _: webbrowser.open(core.WEB))
        imza.bind("<Enter>", lambda _: imza.configure(fg=MARKA))
        imza.bind("<Leave>", lambda _: imza.configure(fg=SOLUK))

        log_cerceve = tk.Frame(kok, bg=KENAR)
        log_cerceve.pack(fill="both", expand=True, pady=(6, 0))
        self.log = tk.Text(log_cerceve, bg=LOG_BG, fg=YAZI, insertbackground=YAZI, relief="flat",
                           font=("Cascadia Mono", 9), wrap="word", padx=10, pady=8, state="disabled",
                           height=8)
        kay = ttk.Scrollbar(log_cerceve, command=self.log.yview)
        self.log.configure(yscrollcommand=kay.set)
        kay.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.log.tag_configure("hata", foreground=HATA)
        self.log.tag_configure("uyari", foreground=UYARI)
        self.log.tag_configure("tamam", foreground=VURGU)
        self.log.tag_configure("baslik", foreground="#7dd3fc")

    def _kart(self, ust, baslik, aciklama, renk, fn):
        f = tk.Frame(ust, bg=KART, highlightbackground=KENAR, highlightthickness=1, cursor="hand2")
        serit = tk.Frame(f, bg=renk, height=3)
        serit.pack(fill="x")
        b = tk.Label(f, text=baslik, bg=KART, fg=YAZI, font=(FONT, 11, "bold"), anchor="w")
        b.pack(fill="x", padx=12, pady=(10, 2))
        a = tk.Label(f, text=aciklama.replace("\n", " "), bg=KART, fg=SOLUK, font=(FONT, 9),
                     justify="left", anchor="nw", height=2)
        a.pack(fill="x", padx=12, pady=(0, 12))
        f.bind("<Configure>", lambda e, a=a: a.configure(wraplength=max(80, e.width - 28)), add="+")
        parcalar = (f, b, a)

        def gir(_):
            if not self.mesgul:
                for w in parcalar:
                    w.configure(bg=KART_HOVER)

        def cik(_):
            for w in parcalar:
                w.configure(bg=KART)

        def tikla(_):
            if not self.mesgul:
                fn()

        for w in parcalar:
            w.bind("<Enter>", gir)
            w.bind("<Leave>", cik)
            w.bind("<Button-1>", tikla)
        self.butonlar.append(parcalar)
        return f

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

    def _cihaz_bilgi(self):
        try:
            satirlar = core.adb("devices").stdout.strip().splitlines()[1:]
            cihazlar = [s.split() for s in satirlar if s.strip()]
            if not cihazlar:
                self.q.put(("cihaz", ("yok", "Cihaz bağlı değil", "USB hata ayıklamayı açıp kabloyu takın")))
                return
            seri, durum = cihazlar[0][0], cihazlar[0][1]
            if durum == "unauthorized":
                self.q.put(("cihaz", ("uyari", "İzin bekleniyor", "Telefonda USB hata ayıklama iznini onaylayın")))
                return
            if durum != "device":
                self.q.put(("cihaz", ("uyari", f"Durum: {durum}", seri)))
                return
            if getattr(self, "_son_seri", None) != seri:
                model = core.adb("shell", "getprop", "ro.product.model").stdout.strip()
                surum = core.adb("shell", "getprop", "ro.build.version.release").stdout.strip()
                self._son_seri, self._son_bilgi = seri, f"{model}  ·  Android {surum}"
            self.q.put(("cihaz", ("ok", self._son_bilgi, f"Seri: {seri}")))
        except SystemExit:
            self.q.put(("cihaz", ("yok", "adb bulunamadı", "platform-tools eksik")))
        except Exception as e:
            self.q.put(("cihaz", ("yok", "Hata", str(e)[:60])))

    def _cihaz_goster(self, d, baslik, detay):
        if d != "ok":
            self._son_seri = None
        self.nokta.itemconfigure(self.nokta_id, fill={"ok": VURGU, "uyari": UYARI}.get(d, HATA))
        self.cihaz_durum.set(baslik)
        self.cihaz_detay.set(detay)

    # ------------------------------------------------------------ iş çalıştırma
    def calistir(self, ad, fn):
        if self.mesgul:
            return
        self.mesgul = True
        for parcalar in self.butonlar:
            for w in parcalar:
                w.configure(cursor="watch", bg=KART)
                if isinstance(w, tk.Label):
                    w.configure(fg=KENAR)
        self.durum.set(f"{ad} çalışıyor...")
        self.pb.configure(mode="indeterminate")
        self.pb.start(12)
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
        self.pb.stop()
        self.pb.configure(mode="determinate")
        for parcalar in self.butonlar:
            parcalar[0].configure(cursor="hand2")
            parcalar[1].configure(fg=YAZI, cursor="hand2")
            parcalar[2].configure(fg=SOLUK, cursor="hand2")
        if hata:
            self.pb["value"] = 0
            self.durum.set(f"{ad}: HATA")
            self._log_ekle(f"HATA: {hata}\n")
            messagebox.showerror(ad, hata)
            return
        self.pb["value"] = 100
        self.durum.set(f"{ad}: tamamlandı")
        if isinstance(sonuc, Path) and sonuc.exists():
            if messagebox.askyesno(ad, f"İşlem tamamlandı.\n\n{sonuc}\n\nKlasör açılsın mı?"):
                self._klasor_ac(sonuc if sonuc.is_dir() else sonuc.parent)
        else:
            messagebox.showinfo(ad, "İşlem tamamlandı.")

    # ------------------------------------------------------------ işler
    def is_komple(self):
        if messagebox.askokcancel(
                "Komple Yedek",
                "Komple yedek başlatılacak:\n\n"
                "• Cihaz bilgileri ve uygulama listesi\n"
                "• adb backup (.ab) – telefonda 'Verilerimi yedekle'ye basmanız gerekir\n"
                "• Tüm dahili depolama (GB'larca sürebilir)\n"
                "• Uygulama APK'ları\n• SHA-256 özet listesi\n\nDevam edilsin mi?"):
            self.calistir("Komple Yedek", lambda h: core.komple_yedek(h))

    def is_whatsapp(self):
        def secici(liste):
            return self.ana_threadde(lambda: SecimPenceresi(self, liste).sonuc)

        self.calistir("WhatsApp Yedeği", lambda h: core.whatsapp_cek(h, None, sor=False, secici=secici))

    def is_notlar_yeni(self):
        messagebox.showinfo(
            "Güncel Notlar",
            "Telefonda yedekleme ekranı açılacak.\n\n"
            "1) 'Yedekle'ye basın\n2) Sadece 'Notlar'ı seçin\n3) Şifre koymadan yedeği başlatın\n\n"
            "Program yeni yedeği otomatik algılayıp notları çekecek.")
        self.calistir("Güncel Notlar", lambda h: core.notlari_cek(h, yeni=True))

    def is_notlar(self):
        self.calistir("Mevcut Not Yedeği", lambda h: core.notlari_cek(h))

    def is_ab(self):
        messagebox.showinfo("adb backup", "Telefonda 'Verilerimi yedekle' düğmesine basın.\n"
                                          "Şifre girmeyin.")
        self.calistir("adb backup", lambda h: core.adb_backup(h, None, apk=True, paylasilan=True, sistem=False))

    def is_paket(self):
        p = simpledialog.askstring("Paket Yedeği", "Paket adı:", initialvalue="com.whatsapp", parent=self)
        if p:
            self.calistir(f"Paket Yedeği ({p})",
                          lambda h: core.adb_backup(h, p.strip(), apk=True, paylasilan=False, sistem=False))

    def is_oxygen(self):
        d = filedialog.askdirectory(title="WhatsApp yedek klasörünü seçin", initialdir=self.hedef.get())
        if d:
            self.calistir("Oxygen ZIP", lambda h: (core.oxygen_ziple(Path(d)), Path(d))[1])

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
