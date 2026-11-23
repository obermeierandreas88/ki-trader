"""
ki_signale.py – KI-Einschätzung (Kaufen / Halten / Verkaufen) mit Erfolgskontrolle

- Eine Anfrage pro Lauf an die Claude API (Modell in KI_MODELL), mit allen Werten auf einmal
- Die KI bekommt: Kursentwicklung, Trend, RSI, übliche Spanne, Schlagzeilen,
  und ob du den Wert besitzt (ohne Beträge)
- Anthropic (ANT) bekommt bewusst KEIN Signal (Interessenkonflikt: die KI stammt von Anthropic)
- Erfolgskontrolle: Jedes Signal wird am NÄCHSTEN Lauf zum dann gültigen Kurs virtuell
  ausgeführt (1 € Gebühr je Wechsel) und mit einfachem Halten verglichen.

Ohne API-Schlüssel (Umgebungsvariable ANTHROPIC_API_KEY) wird alles übersprungen.
Das ist eine KI-Meinung, keine Anlageberatung.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

KI_MODELL = "claude-haiku-5-5"
API_VERSION = "2026-10-21"
API_URL = "https://api.anthropic.com/v1/messages"
LOG_DATEI = Path("ki_signale_log.json")
OHNE_SIGNAL = {"ANT"}              # Anthropic: kein Signal von einer Anthropic-KI
GEBUEHR = 1.0
START_BETRAG = 100.0              # virtueller Betrag je Wert für die Erfolgskontrolle
SIGNALE = ("Kaufen", "Halten", "Verkaufen")

ANWEISUNG = """Du bist ein nüchterner, vorsichtiger Börsen-Analyst. Du bekommst Kennzahlen und
Schlagzeilen zu Aktien und Kryptowährungen einer privaten Beobachtungsliste.
Gib für JEDEN Wert genau ein Signal: "Kaufen", "Halten" oder "Verkaufen".
- "Halten" bei Werten, die die Person nicht besitzt, bedeutet: abwarten, nicht kaufen.
- Sei ehrlich unsicher: Kurse sind kaum vorhersagbar. Wenn die Lage unklar ist oder Informationen
  fehlen (z. B. keine Nachrichten, kurzer Kursverlauf), wähle "Halten" mit Sicherheit "niedrig".
- "hoch" nur bei sehr eindeutiger Lage. Kein Hype, keine Kursziele, keine Versprechen.
- Begründung: höchstens 2 kurze Sätze auf Deutsch, konkret (Nachrichten, Trend, Schwankung).
- Fazit: 2–3 Sätze auf Deutsch, was heute insgesamt los war (z. B. ganze Branche unter Druck).
Antworte NUR mit JSON in genau dieser Form, ohne weiteren Text:
{"fazit": "...", "signale": {"KÜRZEL": {"signal": "Kaufen|Halten|Verkaufen",
 "sicherheit": "niedrig|mittel|hoch", "begruendung": "..."}}}"""


# ---------------------------------------------------------------------------
# Anfrage an die KI
# ---------------------------------------------------------------------------
def _eingabe(infos: list[dict], besitz: set[str]) -> str:
    zeilen = []
    for a in infos:
        if a["name"] in OHNE_SIGNAL:
            continue
        b = a.get("breite") or {}
        eintrag = {
            "kuerzel": a["name"], "name": a.get("voll", a["name"]),
            "im_depot": a["name"] in besitz,
            "kurs_eur": round(a["preis"], 8),
            "veraenderung_prozent": {k: (round(a[k], 1) if a.get(k) is not None else None)
                                     for k in ("tag", "woche", "monat", "jahr")},
            "vom_52_wochen_hoch_prozent": round(a["vom_hoch"], 1) if a.get("vom_hoch") is not None else None,
            "trend": a.get("trend"), "rsi": round(a["rsi"]) if a.get("rsi") is not None else None,
            "uebliche_30_tage_spanne_prozent": (
                {"runter": round(b["runter68"]), "hoch": round(b["hoch68"])} if b else None),
            "schlagzeilen": [n["titel"] for n in a.get("news", [])][:3],
        }
        zeilen.append(eintrag)
    return json.dumps(zeilen, ensure_ascii=False)


def _json_aus_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, ende = text.find("{"), text.rfind("}")
    if start < 0 or ende < 0:
        raise ValueError("keine JSON-Antwort")
    return json.loads(text[start:ende + 1])


def frage_ki(infos: list[dict], besitz: set[str], api_key: str, post=None) -> dict:
    """Gibt {"fazit": str, "signale": {kuerzel: {...}}} zurück. post = requests.post (für Tests)."""
    if post is None:
        import requests
        post = requests.post
    antwort = post(
        API_URL, timeout=90,
        headers={"Authorization": f"Bearer {api_key}", "anthropic-version": API_VERSION,
                 "content-type": "application/json"},
        json={"model": KI_MODELL, "max_tokens": 4000, "system": ANWEISUNG,
              "messages": [{"role": "user", "content":
                            "Datum: " + date.today().isoformat() + "\nWerte:\n" + _eingabe(infos, besitz)}]})
    if antwort.status_code >= 400:
        raise RuntimeError(f"KI-Anfrage fehlgeschlagen (HTTP {antwort.status_code}): {antwort.text[:200]}")
    teile = antwort.json().get("content", [])
    text = "".join(t.get("text", "") for t in teile if t.get("type") == "text")
    roh = _json_aus_text(text)
    gueltige = {a["name"] for a in infos} - OHNE_SIGNAL
    signale = {}
    for k, v in (roh.get("signale") or {}).items():
        if k not in gueltige or not isinstance(v, dict):
            continue
        signal = str(v.get("signal", "")).strip().capitalize()
        if signal not in SIGNALE:
            continue
        sicherheit = str(v.get("sicherheit", "niedrig")).lower()
        signale[k] = {"signal": signal,
                      "sicherheit": sicherheit if sicherheit in ("niedrig", "mittel", "hoch") else "niedrig",
                      "begruendung": str(v.get("begruendung", ""))[:300]}
    return {"fazit": str(roh.get("fazit", ""))[:600], "signale": signale}


# ---------------------------------------------------------------------------
# Erfolgskontrolle
# ---------------------------------------------------------------------------
def _lade_log() -> dict:
    if LOG_DATEI.exists():
        try:
            return json.loads(LOG_DATEI.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"start": None, "werte": {}}


def erfolgskontrolle(signale: dict, preise: dict[str, float], heute: str | None = None) -> dict:
    """Führt das Signal vom letzten Lauf zum heutigen Kurs aus, merkt sich das neue Signal
    und vergleicht 'den Signalen folgen' mit 'einfach halten'. Gibt eine Auswertung zurück."""
    heute = heute or date.today().isoformat()
    log = _lade_log()
    werte = log["werte"]
    geaendert = []
    for name, preis in preise.items():
        if name in OHNE_SIGNAL or preis <= 0:
            continue
        w = werte.get(name)
        if w is None:
            if name not in signale:
                continue
            w = werte[name] = {"start_preis": preis, "start": heute, "anteile": START_BETRAG / preis,
                               "geld": 0.0, "investiert": True, "wechsel": 0,
                               "offen": None, "ausgefuehrt_am": heute, "letztes_signal": None}
            log["start"] = log["start"] or heute
        # 1) Signal vom letzten Lauf heute ausführen (einmal pro Tag)
        if w.get("offen") and w.get("ausgefuehrt_am") != heute:
            if w["offen"] == "Verkaufen" and w["investiert"]:
                w["geld"] = w["anteile"] * preis - GEBUEHR
                w["anteile"], w["investiert"] = 0.0, False
                w["wechsel"] += 1
            elif w["offen"] == "Kaufen" and not w["investiert"]:
                w["anteile"] = max(w["geld"] - GEBUEHR, 0) / preis
                w["geld"], w["investiert"] = 0.0, True
                w["wechsel"] += 1
            w["ausgefuehrt_am"] = heute
        # 2) neues Signal merken (wird beim nächsten Lauf ausgeführt)
        if name in signale:
            neu = signale[name]["signal"]
            if w.get("letztes_signal") and neu != w["letztes_signal"]:
                geaendert.append(f"{name}: {w['letztes_signal']} → {neu}")
            w["offen"], w["letztes_signal"] = neu, neu
        w["letzter_preis"] = preis

    LOG_DATEI.write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")

    ki = halten = 0.0
    wechsel = 0
    for name, w in werte.items():
        p = preise.get(name, w.get("letzter_preis", w["start_preis"]))
        ki += w["anteile"] * p + w["geld"]
        halten += START_BETRAG / w["start_preis"] * p
        wechsel += w["wechsel"]
    return {"seit": log["start"], "werte": len(werte), "ki": ki, "halten": halten,
            "einsatz": START_BETRAG * len(werte), "wechsel": wechsel, "geaendert": geaendert}


# ---------------------------------------------------------------------------
# Einstieg für bot.py
# ---------------------------------------------------------------------------
def ki_auswertung(infos: list[dict], besitz: set[str], preise: dict[str, float],
                  post=None, api_key: str | None = None) -> dict | None:
    api_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        ergebnis = frage_ki(infos, besitz, api_key, post=post)
    except Exception as ex:  # noqa: BLE001
        return {"fehler": str(ex)[:300], "signale": {}, "fazit": "", "kontrolle": None}
    ergebnis["kontrolle"] = erfolgskontrolle(ergebnis["signale"], preise)
    ergebnis["fehler"] = None
    return ergebnis
