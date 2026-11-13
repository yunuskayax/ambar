#!/usr/bin/env python3
"""
Ambar Agent APK'sını derler (Gradle / Android Studio gerekmez).

  javac (JDK 17) -> d8 (dex) -> aapt2 link (manifest) -> classes.dex eklenir -> zipalign -> apksigner

Araçlar makinede kuruluysa (JAVA_HOME / ANDROID_HOME - GitHub Actions dahil) onlar kullanılır; yoksa
<proje>/.arac/ altına indirilir (Microsoft OpenJDK 17, Android build-tools 34, android-34 platformu).
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
SURUM_KODU, SURUM_ADI = 2, "2.0"
MIN_SDK, HEDEF_SDK = 21, 34
INDIR = [("jdk", "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip"),
         ("build-tools", "https://dl.google.com/android/repository/build-tools_r34-windows.zip"),
         ("platform", "https://dl.google.com/android/repository/platform-34-ext7_r03.zip")]
SIFRE = "ambaragent"        # yalnızca yerel imza deposu için (anahtar depoya girmez)


def _kurulu_jdk():
    """Makinede kurulu JDK (javac + keytool). GitHub Actions: JAVA_HOME_17_X64."""
    adaylar = [os.environ.get(e) for e in ("JAVA_HOME_17_X64", "JAVA_HOME_21_X64", "JAVA_HOME")]
    for y in adaylar:
        if y and (Path(y) / "bin" / "javac.exe").exists() and (Path(y) / "bin" / "keytool.exe").exists():
            return Path(y) / "bin"
    return None


def _kurulu_sdk():
    """Makinede kurulu Android SDK'dan (build-tools, platform). GitHub Actions: ANDROID_HOME."""
    for e in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        kok = os.environ.get(e)
        if not kok:
            continue
        sdk = Path(kok)
        btler = [p for p in sorted(sdk.glob("build-tools/*"), key=lambda p: p.name)
                 if (p / "aapt2.exe").exists() and (p / "zipalign.exe").exists()
                 and (p / "lib" / "d8.jar").exists() and (p / "lib" / "apksigner.jar").exists()]
        jarlar = sorted(sdk.glob("platforms/android-*/android.jar"),
                        key=lambda p: int("".join(c for c in p.parent.name[8:] if c.isdigit()) or 0))
        if btler and jarlar:
            return btler[-1], jarlar[-1]
    return None, None


def _indir(url, hedef: Path):
    """Araç arşivini indirir (bazı CDN'ler User-Agent'sız isteği reddediyor; birkaç kez dener)."""
    istek = urllib.request.Request(url, headers={"User-Agent": "Ambar-agent-build"})
    son = None
    for deneme in range(3):
        try:
            with urllib.request.urlopen(istek, timeout=120) as y, open(hedef, "wb") as f:
                shutil.copyfileobj(y, f)
            return
        except Exception as e:                      # ağ hatası / geçici 5xx
            son = e
            print(f"  indirme başarısız ({deneme + 1}/3): {type(e).__name__}: {e}", flush=True)
    raise RuntimeError(f"İndirilemedi: {url}\n{son}")


def araclar():
    """(jdk_bin, build_tools, android_jar). Kurulu araçlar varsa onlar kullanılır, yoksa .arac/ altına indirilir."""
    jbin = _kurulu_jdk()
    bt, jar = _kurulu_sdk()
    if jbin:
        print(f"JDK (kurulu): {jbin}", flush=True)
    if bt:
        print(f"build-tools (kurulu): {bt}\nandroid.jar (kurulu): {jar}", flush=True)

    gerekli = []
    if not jbin:
        gerekli.append("jdk")
    if not bt:
        gerekli += ["build-tools", "platform"]
    for ad, url in INDIR:
        if ad in gerekli and not (ARAC / ad).is_dir():
            print(f"İndiriliyor: {url}", flush=True)
            ARAC.mkdir(exist_ok=True)
            z = ARAC / f"{ad}.zip"
            _indir(url, z)
            with zipfile.ZipFile(z) as f:
                f.extractall(ARAC / ad)
            z.unlink()
    if not jbin:
        jbin = next((ARAC / "jdk").iterdir()) / "bin"
    if not bt:
        bt = next((ARAC / "build-tools").iterdir())
        jar = next((ARAC / "platform").glob("*/android.jar"))
    return jbin, bt, jar


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
            ARAC.mkdir(parents=True, exist_ok=True)     # araçlar indirilmediyse klasör henüz yoktur
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
