"""
tresor.py – Verschlüsselt die Depotdaten für die öffentliche Seite mit einem Passwort.

Verfahren (nur Python-Bordmittel, im Browser mit WebCrypto entschlüsselbar):
- Schlüssel: PBKDF2-HMAC-SHA256 aus Passwort + zufälligem Salt (300.000 Runden), 64 Byte
- Verschlüsselung: HMAC-SHA256 im Zählermodus als Schlüsselstrom (XOR)
- Echtheitsprüfung: HMAC-SHA256 über Salt + Nonce + Geheimtext (Encrypt-then-MAC)
Ohne das richtige Passwort sind die Daten nicht lesbar.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

RUNDEN = 300_000


def _schluesselstrom(k_enc: bytes, nonce: bytes, laenge: int) -> bytes:
    out = bytearray()
    zaehler = 0
    while len(out) < laenge:
        out += hmac.new(k_enc, nonce + zaehler.to_bytes(4, "big"), hashlib.sha256).digest()
        zaehler += 1
    return bytes(out[:laenge])


def verschluesseln(daten: dict, passwort: str) -> dict:
    salt, nonce = os.urandom(16), os.urandom(16)
    schluessel = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salt, RUNDEN, dklen=64)
    k_enc, k_mac = schluessel[:32], schluessel[32:]
    klartext = json.dumps(daten, ensure_ascii=False).encode("utf-8")
    geheim = bytes(a ^ b for a, b in zip(klartext, _schluesselstrom(k_enc, nonce, len(klartext))))
    tag = hmac.new(k_mac, salt + nonce + geheim, hashlib.sha256).digest()
    b64 = lambda x: base64.b64encode(x).decode("ascii")  # noqa: E731
    return {"v": 1, "runden": RUNDEN, "salt": b64(salt), "nonce": b64(nonce),
            "daten": b64(geheim), "tag": b64(tag)}


def entschluesseln(paket: dict, passwort: str) -> dict:
    """Gegenstück in Python (zum Testen)."""
    d = lambda k: base64.b64decode(paket[k])  # noqa: E731
    salt, nonce, geheim, tag = d("salt"), d("nonce"), d("daten"), d("tag")
    schluessel = hashlib.pbkdf2_hmac("sha256", passwort.encode("utf-8"), salt, paket["runden"], dklen=64)
    k_enc, k_mac = schluessel[:32], schluessel[32:]
    if not hmac.compare_digest(hmac.new(k_mac, salt + nonce + geheim, hashlib.sha256).digest(), tag):
        raise ValueError("falsches Passwort")
    klar = bytes(a ^ b for a, b in zip(geheim, _schluesselstrom(k_enc, nonce, len(geheim))))
    return json.loads(klar.decode("utf-8"))
