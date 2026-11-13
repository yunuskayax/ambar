package com.ambar.agent;

import android.accounts.Account;
import android.accounts.AccountManager;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.drawable.Drawable;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.StatFs;
import android.provider.Settings;
import android.util.Base64;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.Charset;
import java.util.ArrayDeque;
import java.util.HashSet;
import java.util.LinkedHashSet;

/**
 * Ambar Agent.
 *
 * Bilgisayar "adb forward tcp:N localabstract:ambar_agent" ile bu sokete bağlanır.
 * İlk satır oturum anahtarıdır (am start --es anahtar ... ile verilir); eşleşmezse bağlantı kapatılır.
 * Sonra her satır bir komuttur; yanıt çerçeveler halinde gelir: [4 bayt uzunluk (big endian)][veri], uzunluk 0 = bitti.
 *
 *   bilgi                 cihaz ve izin bilgisi (tek JSON)
 *   sorgu <uri>           içerik sağlayıcısının tüm satırları (satır başına bir JSON çerçevesi; BLOB -> base64)
 *   akis <uri>            içerik akışı (MMS eki, kişi fotoğrafı) ham baytlar
 *   uygulamalar           yüklü paketler (ad, sürüm, kurulum zamanları, izinler, simge PNG base64)
 *   hesaplar              AccountManager hesapları
 *   bluetooth             eşleşmiş Bluetooth cihazları
 *   dosya_izni durum      "tüm dosyalara erişim" izni var mı
 *   dosya_izni iste       telefonda izin ekranını açar (kullanıcı onayı); izin verilince liste/dosya çalışır
 *   kokler                depolama kökleri (dahili bellek, takılı SD kart) ve boyutları
 *   liste <kök>           kökün altındaki tüm dosya ve klasörler (satır başına bir JSON çerçevesi)
 *   dosya <yol>           dosyanın içeriği: ilk çerçeve JSON başlık {"b":boyut,"t":zaman}, sonrası ham baytlar
 *   bitir                 bağlantıyı kapatır ve uygulamayı kapatır
 *
 * Uygulama yalnızca OKUR; telefondaki hiçbir veriyi değiştirmez. İnternet izni yoktur.
 */
public class Ana extends Activity {
    static final String SOKET = "ambar_agent";
    static final Charset UTF8 = Charset.forName("UTF-8");

    volatile String anahtar;
    volatile LocalServerSocket sunucu;
    TextView yazi;
    volatile String durum = "Bilgisayar bağlantısı bekleniyor...";
    volatile long gonderilen = 0;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        yazi = new TextView(this);
        yazi.setGravity(Gravity.CENTER);
        yazi.setTextSize(18);
        yazi.setPadding(40, 40, 40, 40);
        setContentView(yazi);
        anahtar = getIntent().getStringExtra("anahtar");
        guncelle();
        new Thread(new Runnable() { public void run() { sun(); } }, "ambar-sunucu").start();
    }

    @Override
    protected void onNewIntent(android.content.Intent i) {
        super.onNewIntent(i);
        String a = i.getStringExtra("anahtar");
        if (a != null) anahtar = a;
    }

    void guncelle() {
        runOnUiThread(new Runnable() {
            public void run() {
                yazi.setText("Ambar Agent\n\nVeriler USB kablosu üzerinden bilgisayara aktarılıyor.\n"
                        + "Telefondaki hiçbir veri değiştirilmez.\nİşlem bitince uygulama otomatik kaldırılır.\n\n"
                        + durum + "\n" + (gonderilen / 1024) + " KB gönderildi");
            }
        });
    }

    @Override
    protected void onDestroy() {
        try { if (sunucu != null) sunucu.close(); } catch (IOException ignored) { }
        super.onDestroy();
    }

    // ------------------------------------------------------------------ sunucu
    void sun() {
        try {
            sunucu = new LocalServerSocket(SOKET);
        } catch (IOException e) {
            durum = "Soket açılamadı: " + e;
            guncelle();
            return;
        }
        while (true) {
            LocalSocket s;
            try {
                s = sunucu.accept();
            } catch (IOException e) {
                return;     // sunucu kapatıldı
            }
            try {
                if (oturum(s)) break;
            } catch (Exception e) {
                durum = "Bağlantı kesildi: " + e.getMessage();
                guncelle();
            } finally {
                try { s.close(); } catch (IOException ignored) { }
            }
        }
        try { sunucu.close(); } catch (IOException ignored) { }
        runOnUiThread(new Runnable() { public void run() { finish(); } });
    }

    /** true: "bitir" komutu geldi. */
    boolean oturum(LocalSocket s) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), UTF8));
        DataOutputStream out = new DataOutputStream(s.getOutputStream());
        String gelen = in.readLine();
        if (anahtar == null || gelen == null || !anahtar.equals(gelen.trim())) {
            cerceve(out, new JSONObject().put("hata", "Oturum anahtarı geçersiz").toString().getBytes(UTF8));
            out.writeInt(0);
            return false;
        }
        durum = "Bilgisayara bağlandı";
        guncelle();
        String komut;
        while ((komut = in.readLine()) != null) {
            komut = komut.trim();
            int bosluk = komut.indexOf(' ');
            String ad = bosluk < 0 ? komut : komut.substring(0, bosluk);
            String arg = bosluk < 0 ? "" : komut.substring(bosluk + 1);
            try {
                if (ad.equals("bitir")) {
                    out.writeInt(0);
                    out.flush();
                    return true;
                } else if (ad.equals("bilgi")) {
                    cerceve(out, bilgi().toString().getBytes(UTF8));
                } else if (ad.equals("sorgu")) {
                    durum = "Okunuyor: " + arg;
                    guncelle();
                    sorgu(out, Uri.parse(arg));
                } else if (ad.equals("akis")) {
                    akis(out, Uri.parse(arg));
                } else if (ad.equals("uygulamalar")) {
                    durum = "Uygulamalar okunuyor";
                    guncelle();
                    uygulamalar(out);
                } else if (ad.equals("hesaplar")) {
                    hesaplar(out);
                } else if (ad.equals("bluetooth")) {
                    bluetooth(out);
                } else if (ad.equals("dosya_izni")) {
                    dosyaIzni(out, arg);
                } else if (ad.equals("kokler")) {
                    kokler(out);
                } else if (ad.equals("liste")) {
                    durum = "Dosyalar listeleniyor";
                    guncelle();
                    liste(out, arg);
                } else if (ad.equals("dosya")) {
                    dosya(out, arg);
                } else {
                    hata(out, "Bilinmeyen komut: " + ad);
                }
            } catch (SecurityException e) {
                hata(out, "İzin yok: " + e.getMessage());
            } catch (Exception e) {
                hata(out, e.getClass().getSimpleName() + ": " + e.getMessage());
            }
            out.writeInt(0);
            out.flush();
            guncelle();
        }
        return false;
    }

    void cerceve(DataOutputStream out, byte[] b) throws IOException {
        out.writeInt(b.length);
        out.write(b);
        gonderilen += b.length;
    }

    void hata(DataOutputStream out, String m) throws Exception {
        cerceve(out, new JSONObject().put("_hata", m).toString().getBytes(UTF8));
    }

    // ------------------------------------------------------------------ komutlar
    JSONObject bilgi() throws Exception {
        JSONObject j = new JSONObject();
        j.put("surum", 1);
        j.put("sdk", Build.VERSION.SDK_INT);
        j.put("android", Build.VERSION.RELEASE);
        j.put("marka", Build.MANUFACTURER);
        j.put("model", Build.MODEL);
        j.put("cihaz", Build.DEVICE);
        j.put("yapi", Build.DISPLAY);
        j.put("parmak_izi", Build.FINGERPRINT);
        JSONObject izin = new JSONObject();
        String[] liste = getPackageManager().getPackageInfo(getPackageName(), PackageManager.GET_PERMISSIONS).requestedPermissions;
        if (liste != null) {
            for (String p : liste) {
                izin.put(p, Build.VERSION.SDK_INT < 23 || checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED);
            }
        }
        j.put("izinler", izin);
        return j;
    }

    void sorgu(DataOutputStream out, Uri uri) throws Exception {
        Cursor c = getContentResolver().query(uri, null, null, null, null);
        if (c == null) {
            hata(out, "Sağlayıcı yanıt vermedi: " + uri);
            return;
        }
        try {
            String[] kol = c.getColumnNames();
            while (c.moveToNext()) {
                JSONObject r = new JSONObject();
                for (int i = 0; i < kol.length; i++) {
                    int tur;
                    try { tur = c.getType(i); } catch (Exception e) { tur = Cursor.FIELD_TYPE_STRING; }
                    switch (tur) {
                        case Cursor.FIELD_TYPE_NULL: break;
                        case Cursor.FIELD_TYPE_BLOB:
                            byte[] b = c.getBlob(i);
                            if (b != null && b.length <= 4 * 1024 * 1024) r.put(kol[i], "base64:" + Base64.encodeToString(b, Base64.NO_WRAP));
                            break;
                        default:
                            r.put(kol[i], c.getString(i));
                    }
                }
                cerceve(out, r.toString().getBytes(UTF8));
            }
        } finally {
            c.close();
        }
    }

    void akis(DataOutputStream out, Uri uri) throws Exception {
        InputStream in = getContentResolver().openInputStream(uri);
        if (in == null) {
            hata(out, "Akış açılamadı");
            return;
        }
        try {
            byte[] tampon = new byte[64 * 1024];
            int n;
            while ((n = in.read(tampon)) > 0) {
                out.writeInt(n);
                out.write(tampon, 0, n);
                gonderilen += n;
            }
        } finally {
            in.close();
        }
    }

    void uygulamalar(DataOutputStream out) throws Exception {
        PackageManager pm = getPackageManager();
        for (PackageInfo p : pm.getInstalledPackages(PackageManager.GET_PERMISSIONS)) {
            JSONObject r = new JSONObject();
            ApplicationInfo a = p.applicationInfo;
            r.put("paket", p.packageName);
            r.put("surum_adi", p.versionName);
            r.put("surum", Build.VERSION.SDK_INT >= 28 ? p.getLongVersionCode() : p.versionCode);
            r.put("ilk_yukleme", p.firstInstallTime);
            r.put("guncelleme", p.lastUpdateTime);
            try { r.put("yukleyen", pm.getInstallerPackageName(p.packageName)); } catch (Exception ignored) { }
            if (a != null) {
                r.put("ad", String.valueOf(a.loadLabel(pm)));
                r.put("sistem", (a.flags & ApplicationInfo.FLAG_SYSTEM) != 0);
                r.put("etkin", a.enabled);
                r.put("uid", a.uid);
                r.put("apk", a.sourceDir);
                r.put("veri_klasoru", a.dataDir);
                try { r.put("simge", "base64:" + png(a.loadIcon(pm), 96)); } catch (Exception ignored) { }
            }
            if (p.requestedPermissions != null) {
                JSONArray izin = new JSONArray();
                for (int i = 0; i < p.requestedPermissions.length; i++) {
                    boolean verildi = p.requestedPermissionsFlags != null
                            && (p.requestedPermissionsFlags[i] & PackageInfo.REQUESTED_PERMISSION_GRANTED) != 0;
                    izin.put(p.requestedPermissions[i] + (verildi ? " (verildi)" : ""));
                }
                r.put("izinler", izin);
            }
            cerceve(out, r.toString().getBytes(UTF8));
        }
    }

    static String png(Drawable d, int boyut) {
        Bitmap bmp = Bitmap.createBitmap(boyut, boyut, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        d.setBounds(0, 0, boyut, boyut);
        d.draw(c);
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        bmp.compress(Bitmap.CompressFormat.PNG, 100, o);
        return Base64.encodeToString(o.toByteArray(), Base64.NO_WRAP);
    }

    // ------------------------------------------------------------------ dosyalar
    /** Android 11+ : "tüm dosyalara erişim" (MANAGE_EXTERNAL_STORAGE). Öncesi: READ_EXTERNAL_STORAGE. */
    boolean tumDosyaIzni() {
        if (Build.VERSION.SDK_INT >= 30) return Environment.isExternalStorageManager();
        if (Build.VERSION.SDK_INT < 23) return true;
        return checkSelfPermission(android.Manifest.permission.READ_EXTERNAL_STORAGE) == PackageManager.PERMISSION_GRANTED;
    }

    /** arg "iste" ise telefonda izin ekranını açar (kullanıcı onayı burada verilir), "durum" ise yalnızca bildirir. */
    void dosyaIzni(DataOutputStream out, String arg) throws Exception {
        boolean izin = tumDosyaIzni();
        boolean ekran = false;
        if (!izin && arg.startsWith("iste") && Build.VERSION.SDK_INT >= 30) {
            durum = "Telefonda 'Tüm dosyalara erişim' onayı bekleniyor...";
            guncelle();
            ekran = izinEkrani();
        }
        cerceve(out, new JSONObject().put("izin", izin).put("ekran", ekran)
                .put("sdk", Build.VERSION.SDK_INT).toString().getBytes(UTF8));
    }

    /** Sistemdeki "Tüm dosyalara erişim" ekranını açar; kullanıcı izni oradan verir. */
    boolean izinEkrani() {
        final boolean[] acildi = {false};
        final boolean[] bitti = {false};
        runOnUiThread(new Runnable() {
            public void run() {
                try {
                    startActivity(new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION,
                            Uri.parse("package:" + getPackageName())));
                    acildi[0] = true;
                } catch (Exception e) {
                    try {
                        startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
                        acildi[0] = true;
                    } catch (Exception ignored) { }
                }
                bitti[0] = true;
            }
        });
        for (int i = 0; i < 60 && !bitti[0]; i++) {
            try { Thread.sleep(50); } catch (InterruptedException ignored) { }
        }
        return acildi[0];
    }

    /** Depolama kökleri: dahili bellek ve (varsa) takılı SD kart. */
    void kokler(DataOutputStream out) throws Exception {
        LinkedHashSet<String> yollar = new LinkedHashSet<String>();
        File birincil = Environment.getExternalStorageDirectory();
        if (birincil != null) yollar.add(birincil.getAbsolutePath());
        try {
            for (File f : getExternalFilesDirs(null)) {
                if (f == null) continue;
                String y = f.getAbsolutePath();
                int k = y.indexOf("/Android/data");
                if (k > 0) yollar.add(y.substring(0, k));
            }
        } catch (Exception ignored) { }
        boolean ilk = true;
        for (String y : yollar) {
            File d = new File(y);
            JSONObject j = new JSONObject();
            j.put("yol", y);
            j.put("ad", d.getName());
            j.put("birincil", ilk);
            j.put("okunabilir", d.canRead());
            try {
                StatFs st = new StatFs(y);
                j.put("toplam", st.getTotalBytes());
                j.put("bos", st.getAvailableBytes());
            } catch (Exception ignored) { }
            cerceve(out, j.toString().getBytes(UTF8));
            ilk = false;
        }
    }

    static String goreli(File kok, File f) {
        String a = f.getAbsolutePath(), k = kok.getAbsolutePath();
        return a.startsWith(k) ? a.substring(k.length()).replaceFirst("^/", "") : a;
    }

    /** Kökün altındaki her klasör ve dosya için bir JSON çerçevesi: y=yol, b=boyut, t=değişim, d=1 klasör, h=hata. */
    void liste(DataOutputStream out, String kokYol) throws Exception {
        File kok = new File(kokYol);
        if (!kok.exists()) {
            hata(out, "Yol bulunamadı: " + kokYol);
            return;
        }
        ArrayDeque<File> yigin = new ArrayDeque<File>();
        HashSet<String> gezilen = new HashSet<String>();
        yigin.push(kok);
        int sayi = 0;
        while (!yigin.isEmpty()) {
            File d = yigin.pop();
            String kanonik;
            try { kanonik = d.getCanonicalPath(); } catch (IOException e) { kanonik = d.getAbsolutePath(); }
            if (!gezilen.add(kanonik)) continue;                 // sembolik bağ döngüsü
            File[] icerik = d.listFiles();
            if (icerik == null) {
                JSONObject j = new JSONObject().put("y", goreli(kok, d)).put("d", 1);
                cerceve(out, j.put("h", d.canRead() ? "listelenemedi" : "izin yok").toString().getBytes(UTF8));
                continue;
            }
            for (File f : icerik) {
                JSONObject j = new JSONObject();
                j.put("y", goreli(kok, f));
                j.put("t", f.lastModified());
                if (f.isDirectory()) {
                    j.put("d", 1);
                    yigin.push(f);
                } else {
                    j.put("b", f.length());
                    if (!f.canRead()) j.put("h", "izin yok");
                }
                cerceve(out, j.toString().getBytes(UTF8));
                if ((++sayi & 511) == 0) {
                    durum = "Dosyalar listeleniyor: " + sayi;
                    guncelle();
                }
            }
        }
    }

    /** Dosya içeriği: ilk çerçeve JSON başlık, sonraki çerçeveler ham bayt. */
    void dosya(DataOutputStream out, String yol) throws Exception {
        File f = new File(yol);
        if (!f.isFile()) {
            hata(out, "Dosya değil veya yok: " + yol);
            return;
        }
        if (!f.canRead()) {
            hata(out, "İzin yok: " + yol);
            return;
        }
        cerceve(out, new JSONObject().put("b", f.length()).put("t", f.lastModified()).toString().getBytes(UTF8));
        FileInputStream in = new FileInputStream(f);
        try {
            byte[] tampon = new byte[64 * 1024];
            int n;
            while ((n = in.read(tampon)) > 0) {
                out.writeInt(n);
                out.write(tampon, 0, n);
                gonderilen += n;
            }
        } finally {
            in.close();
        }
    }

    void hesaplar(DataOutputStream out) throws Exception {
        for (Account a : AccountManager.get(this).getAccounts()) {
            cerceve(out, new JSONObject().put("ad", a.name).put("tur", a.type).toString().getBytes(UTF8));
        }
    }

    void bluetooth(DataOutputStream out) throws Exception {
        BluetoothAdapter ba = BluetoothAdapter.getDefaultAdapter();
        if (ba == null) return;
        for (BluetoothDevice d : ba.getBondedDevices()) {
            JSONObject r = new JSONObject();
            r.put("ad", d.getName());
            r.put("adres", d.getAddress());
            try { r.put("tur", d.getBluetoothClass().getMajorDeviceClass()); } catch (Exception ignored) { }
            cerceve(out, r.toString().getBytes(UTF8));
        }
    }
}
