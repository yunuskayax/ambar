#!/usr/bin/env python3
"""
WhatsApp mesaj yedeklerinin (msgstore.db.crypt12 / .crypt14 / .crypt15) şifresini çözer ve sohbetleri okur.

Anahtar türleri
  - 64 haneli uçtan uca şifreli yedek anahtarı (WhatsApp > Sohbet yedeği > Uçtan uca şifreli yedek): crypt15
  - encrypted_backup.key dosyası (crypt15) veya /data/data/com.whatsapp/files/key dosyası (crypt12/14, root gerekir)
Parola ile korunan uçtan uca yedekler (64 hane yerine parola seçilmişse) WhatsApp sunucusu olmadan çözülemez.

Çözülen veritabanı delil klasörüne değil %LOCALAPPDATA%\\Ambar\\incele\\whatsapp altına yazılır; anahtar saklanmaz.
"""
import hashlib
import hmac
import re
import sqlite3
import zlib
from pathlib import Path

import ambar as core

SQLITE_IMZA = b"SQLite format 3\x00"
MESAJ_TURU = {0: "Metin", 1: "Fotoğraf", 2: "Ses", 3: "Video", 4: "Kişi kartı", 5: "Konum", 7: "Sistem", 8: "Sesli arama",
              9: "Belge", 10: "Cevapsız arama", 11: "Grup bildirimi", 13: "GIF", 15: "Silinmiş mesaj", 16: "Canlı konum",
              20: "Çıkartma", 42: "Tek görümlük fotoğraf", 43: "Tek görümlük video", 64: "Anket", 66: "Anket", 90: "Arama"}


# ----------------------------------------------------------------- anahtar
def _hkdf(kok: bytes, mesaj: bytes = b"backup encryption", uzunluk: int = 32) -> bytes:
    """WhatsApp crypt15 anahtar türetme (HKDF-SHA256, sıfır tuz)."""
    prk = hmac.new(b"\x00" * 32, kok, hashlib.sha256).digest()
    blok, cikti, i = b"", b"", 1
    while len(cikti) < uzunluk:
        blok = hmac.new(prk, blok + mesaj + bytes([i]), hashlib.sha256).digest()
        cikti += blok
        i += 1
    return cikti[:uzunluk]


def anahtar_adaylari(hex_metin: str | None = None, dosya: bytes | None = None) -> list[bytes]:
    adaylar = []
    if hex_metin and hex_metin.strip():
        h = re.sub(r"[^0-9a-fA-F]", "", hex_metin)
        if len(h) != 64:
            raise ValueError(f"Anahtar 64 onaltılık karakter olmalı (girilen: {len(h)}). Boşluk ve tireler önemsizdir.")
        k = bytes.fromhex(h)
        adaylar += [_hkdf(k), k]
    if dosya:
        if len(dosya) < 32:
            raise ValueError("Anahtar dosyası geçersiz (32 bayttan kısa).")
        k = dosya[-32:]          # key (158 bayt) ve encrypted_backup.key: anahtar son 32 bayttadır
        adaylar += [k, _hkdf(k)]
    if not adaylar:
        raise ValueError("64 haneli anahtarı girin veya anahtar dosyasını seçin.")
    return adaylar


# ----------------------------------------------------------------- dosya düzeni
def _ivler(pb: bytes, derinlik=0):
    """Başlık protobuf'unda 16 baytlık alanları (IV adayları) bulur."""
    alanlar = core._pb_dene(pb) or []
    for _, tip, v in alanlar:
        if tip == 2 and isinstance(v, bytes):
            if len(v) == 16:
                yield v
            if derinlik < 4:
                yield from _ivler(v, derinlik + 1)


def _duzenler(ham: bytes):
    """(iv, şifreli veri, etiket) adayları. crypt14/15: [n][0x01?][protobuf][veri][etiket 16][md5 16 (tek dosyada)]"""
    if len(ham) > 64:
        n = ham[0]
        for bayrak in (1, 0):
            if bayrak and ham[1] != 1:
                continue
            bas = 1 + bayrak
            govde = ham[bas + n:]
            for iv in dict.fromkeys(_ivler(ham[bas:bas + n])):
                if hashlib.md5(ham[:-16]).digest() == ham[-16:]:
                    yield iv, govde[:-32], govde[-32:-16]
                yield iv, govde[:-16], govde[-16:]
                yield iv, govde[:-32], govde[-32:-16]
    if len(ham) > 120:   # crypt12: 67 bayt başlık, IV 51..67, sonda 16 etiket + 4 bayt
        yield ham[51:67], ham[67:-20], ham[-20:-4]


def _cozucu(anahtar: bytes, iv: bytes, etiket: bytes):
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    return Cipher(algorithms.AES(anahtar), modes.GCM(iv, etiket)).decryptor()


def coz(kaynak: Path, hedef: Path, hex_metin=None, anahtar_dosyasi: bytes | None = None) -> dict:
    """Yedeği çözer, SQLite veritabanını hedef'e yazar. Anahtar uymazsa ValueError."""
    ham = kaynak if isinstance(kaynak, (bytes, bytearray)) else Path(kaynak).read_bytes()
    for anahtar in anahtar_adaylari(hex_metin, anahtar_dosyasi):
        for iv, veri, etiket in _duzenler(ham):
            if len(veri) < 32:
                continue
            # hızlı deneme: ilk 4 KB çözülüp zlib + SQLite imzası aranır
            try:
                on = zlib.decompressobj().decompress(_cozucu(anahtar, iv, etiket).update(veri[:4096]))
            except (zlib.error, ValueError):
                continue
            if not on.startswith(SQLITE_IMZA):
                continue
            c = _cozucu(anahtar, iv, etiket)
            acik = c.update(veri)
            dogrulandi = True
            try:
                c.finalize()
            except Exception:
                dogrulandi = False      # etiket konumu farklı olabilir; içerik yine de SQLite olarak doğrulanır
            db = zlib.decompressobj().decompress(acik)
            hedef.parent.mkdir(parents=True, exist_ok=True)
            gecici = hedef.with_suffix(".tmp")
            gecici.write_bytes(db)
            gecici.replace(hedef)
            ozet = sohbet_ozeti(hedef)
            return {"dogrulandi": dogrulandi, "boyut": len(db), "sha256": hashlib.sha256(db).hexdigest(), **ozet}
    raise ValueError("Anahtar bu yedeği çözmedi. Anahtarın bu hesaba ve bu yedeğe ait olduğundan emin olun.")


# ----------------------------------------------------------------- okuma
def _baglan(db: Path):
    con = sqlite3.connect(Path(db).as_uri() + "?mode=ro", uri=True, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def _tablolar(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _kolonlar(con, tablo):
    return {r[1] for r in con.execute(f"PRAGMA table_info({tablo})")}


def _yeni_sema(con):
    return {"message", "chat", "jid"} <= _tablolar(con)


def sohbet_ozeti(db: Path) -> dict:
    con = _baglan(db)
    try:
        if _yeni_sema(con):
            return {"sohbet": con.execute("SELECT COUNT(DISTINCT chat_row_id) FROM message").fetchone()[0],
                    "mesaj": con.execute("SELECT COUNT(*) FROM message").fetchone()[0]}
        if "messages" in _tablolar(con):
            return {"sohbet": con.execute("SELECT COUNT(DISTINCT key_remote_jid) FROM messages").fetchone()[0],
                    "mesaj": con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]}
        return {"sohbet": 0, "mesaj": 0}
    finally:
        con.close()


def _numara(jid: str | None) -> str:
    if not jid:
        return ""
    k, _, s = jid.partition("@")
    if s == "s.whatsapp.net" and k.isdigit():
        return "+" + k
    return jid


def sohbetler(db: Path) -> list[dict]:
    con = _baglan(db)
    try:
        if _yeni_sema(con):
            konu = "c.subject" if "subject" in _kolonlar(con, "chat") else "NULL"
            satir = con.execute(f"""
                SELECT c._id AS id, j.raw_string AS jid, {konu} AS konu, COUNT(m._id) AS adet, MAX(m.timestamp) AS son,
                       (SELECT text_data FROM message WHERE chat_row_id = c._id AND text_data IS NOT NULL
                        ORDER BY timestamp DESC LIMIT 1) AS son_metin
                FROM chat c JOIN jid j ON j._id = c.jid_row_id
                JOIN message m ON m.chat_row_id = c._id
                GROUP BY c._id ORDER BY son DESC""").fetchall()
        else:
            konu = {r["key_remote_jid"]: r["subject"] for r in con.execute("SELECT key_remote_jid, subject FROM chat_list")} \
                if "chat_list" in _tablolar(con) else {}
            satir = [dict(r, konu=konu.get(r["jid"])) for r in con.execute("""
                SELECT key_remote_jid AS id, key_remote_jid AS jid, COUNT(*) AS adet, MAX(timestamp) AS son,
                       (SELECT data FROM messages m2 WHERE m2.key_remote_jid = m.key_remote_jid AND data IS NOT NULL
                        ORDER BY timestamp DESC LIMIT 1) AS son_metin
                FROM messages m GROUP BY key_remote_jid ORDER BY son DESC""")]
        out = []
        for r in satir:
            r = dict(r)
            jid = r.get("jid") or ""
            r["grup"] = jid.endswith("@g.us")
            r["ad"] = r.get("konu") or ("Durum güncellemeleri" if jid.startswith("status@") else _numara(jid))
            out.append(r)
        return out
    finally:
        con.close()


def mesajlar(db: Path, sohbet: str, sayfa: int | None = None, boyut: int = 300, ara: str = "") -> dict:
    con = _baglan(db)
    try:
        if _yeni_sema(con):
            mk = _kolonlar(con, "message_media") if "message_media" in _tablolar(con) else set()
            medya = (", mm.file_path AS dosya, mm.mime_type AS mime" + (", mm.media_name AS medya_adi" if "media_name" in mk else "")
                     ) if mk else ", NULL AS dosya, NULL AS mime"
            katil = " LEFT JOIN message_media mm ON mm.message_row_id = m._id" if mk else ""
            taban = f"""SELECT m._id AS id, m.from_me AS ben, m.timestamp AS tarih, m.message_type AS tur, m.text_data AS metin,
                               m.status AS durum, sj.raw_string AS gonderen {medya}
                        FROM message m LEFT JOIN jid sj ON sj._id = m.sender_jid_row_id{katil}
                        WHERE m.chat_row_id = ?"""
            p = [int(sohbet)]
        else:
            taban = """SELECT _id AS id, key_from_me AS ben, timestamp AS tarih, media_wa_type AS tur, data AS metin,
                              status AS durum, remote_resource AS gonderen, media_name AS dosya, media_mime_type AS mime
                       FROM messages WHERE key_remote_jid = ?"""
            p = [sohbet]
        if ara:
            taban += " AND m.text_data LIKE ?" if _yeni_sema(con) else " AND data LIKE ?"
            p.append(f"%{ara}%")
        toplam = con.execute(f"SELECT COUNT(*) FROM ({taban})", p).fetchone()[0]
        son_sayfa = max(1, -(-toplam // boyut))
        sayfa = son_sayfa if not sayfa else max(1, min(int(sayfa), son_sayfa))
        satir = [dict(r) for r in con.execute(f"SELECT * FROM ({taban}) ORDER BY tarih, id LIMIT ? OFFSET ?",
                                              p + [boyut, (sayfa - 1) * boyut])]
        for r in satir:
            r["tur_adi"] = MESAJ_TURU.get(r["tur"], f"Tür {r['tur']}") if r["tur"] is not None else "Metin"
            r["gonderen_ad"] = _numara(r.get("gonderen"))
        return {"mesajlar": satir, "toplam": toplam, "sayfa": sayfa, "son_sayfa": son_sayfa}
    finally:
        con.close()
