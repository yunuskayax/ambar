#!/usr/bin/env python3
"""
Ambar Agent APK'sını derler (Gradle / Android Studio gerekmez).

  javac (JDK 17) -> d8 (dex) -> aapt2 link (manifest) -> classes.dex eklenir -> zipalign -> apksigner

Araçlar yoksa <proje>/.arac/ altına indirilir (Microsoft OpenJDK 17, Android build-tools 34, android-34 platformu).
İmza anahtarı ilk derlemede .arac/agent_imza.jks olarak üretilir ve depoya eklenmez.
Çıktı: agent/ambar_agent.apk
"""
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
ARAC = KOK / ".arac"
AGENT = KOK / "agent"
CIKTI = AGENT / "ambar_agent.apk"
SURUM_KODU, SURUM_ADI = 1, "1.0"
MIN_SDK, HEDEF_SDK = 21, 34
INDIR = [("jdk", "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"),
         ("build-tools", "https://dl.google.com/android/repository/build-tools_r34-windows.zip"),
         ("platform", "https://dl.google.com/android/repository/platform-34-ext7_r03.zip")]
SIFRE = "ambaragent"        # yalnızca yerel imza deposu için (anahtar depoya girmez)


def araclar():
    for ad, url in INDIR:
        if not (ARAC / ad).is_dir():
            print(f"İndiriliyor: {url}")
            ARAC.mkdir(exist_ok=True)
            z = ARAC / f"{ad}.zip"
            urllib.request.urlretrieve(url, z)
            with zipfile.ZipFile(z) as f:
                f.extractall(ARAC / ad)
            z.unlink()
    jdk = next((ARAC / "jdk").iterdir())
    bt = next((ARAC / "build-tools").iterdir())
    jar = next((ARAC / "platform").glob("*/android.jar"))
    return jdk / "bin", bt, jar


def calis(*komut, **kw):
    r = subprocess.run([str(x) for x in komut], capture_output=True, text=True, **kw)
    if r.returncode:
        sys.exit(f"HATA ({Path(str(komut[0])).name}):\n{r.stdout}\n{r.stderr}")
    return r


def main():
    jbin, bt, android_jar = araclar()
    java, javac, keytool = jbin / "java.exe", jbin / "javac.exe", jbin / "keytool.exe"
    with tempfile.TemporaryDirectory() as g:
        g = Path(g)
        kaynaklar = sorted((AGENT / "src").rglob("*.java"))
        (g / "obj").mkdir()
        calis(javac, "-encoding", "UTF-8", "-source", "8", "-target", "8", "-nowarn", "-Xlint:-options",
              "-bootclasspath", android_jar, "-d", g / "obj", *kaynaklar)
        siniflar = sorted((g / "obj").rglob("*.class"))
        (g / "dex").mkdir()
        calis(java, "-cp", bt / "lib" / "d8.jar", "com.android.tools.r8.D8", "--release", "--lib", android_jar,
              "--min-api", MIN_SDK, "--output", g / "dex", *siniflar)
        calis(bt / "aapt2.exe", "link", "-o", g / "ham.apk", "-I", android_jar, "--manifest", AGENT / "AndroidManifest.xml",
              "--min-sdk-version", MIN_SDK, "--target-sdk-version", HEDEF_SDK,
              "--version-code", SURUM_KODU, "--version-name", SURUM_ADI)
        with zipfile.ZipFile(g / "ham.apk", "a", zipfile.ZIP_DEFLATED) as z:
            z.write(g / "dex" / "classes.dex", "classes.dex")
        calis(bt / "zipalign.exe", "-f", "-p", "4", g / "ham.apk", g / "hizali.apk")
        anahtar = ARAC / "agent_imza.jks"
        if not anahtar.exists():
            calis(keytool, "-genkeypair", "-keystore", anahtar, "-alias", "ambar", "-keyalg", "RSA", "-keysize", "2048",
                  "-validity", "10000", "-storepass", SIFRE, "-keypass", SIFRE, "-dname", "CN=Ambar Agent, O=Ambar")
        calis(java, "-jar", bt / "lib" / "apksigner.jar", "sign", "--ks", anahtar, "--ks-pass", f"pass:{SIFRE}",
              "--out", CIKTI, g / "hizali.apk")
        calis(java, "-jar", bt / "lib" / "apksigner.jar", "verify", CIKTI)
    for f in AGENT.glob("*.idsig"):
        f.unlink()
    print(f"TAMAM: {CIKTI} ({CIKTI.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
