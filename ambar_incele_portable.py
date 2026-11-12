#!/usr/bin/env python3
"""AmbarIncele.exe giriş noktası: portable raporu (rapor.ambar) açan, yedek alma içermeyen görüntüleyici."""
import ctypes

import ambar_incele

if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    ambar_incele.portable_baslat()
