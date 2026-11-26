"""
ki_signale.py – KI-Einschätzung (Kaufen / Halten / Verkaufen) mit Erfolgskontrolle

- Anbieter (der erste mit Schlüssel wird benutzt):
  Google Gemini (GEMINI_API_KEY, kostenlose Stufe) > Mistral (MISTRAL_API_KEY) > Claude (ANTHROPIC_API_KEY)
- Eine Anfrage pro Lauf mit allen Werten auf einmal
- Die KI bekommt: Kursentwicklung, Trend, RSI, übliche Spanne, Schlagzeilen,
  und ob du den Wert besitzt (ohne Beträge)
- Bei Claude bekommt Anthropic (ANT) bewusst KEIN Signal (Interessenkonflikt). Bei Mistral schon.
- Erfolgskontrolle: Jedes Signal wird am NÄCHSTEN Lauf zum dann gültigen Kurs virtuell
  ausgeführt (1 € Gebühr je Wechsel) und mit einfachem Halten verglichen.

Ohne API-Schlüssel wird alles übersprungen.
Das ist eine KI-Meinung, keine Anlageberatung.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

GEMINI_MODELLE = ("gemini-3.5-flash-lite", "gemini-3.6-flash", "gemini-3.1-flash-lite")  # der Reihe nach
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{modell}:generateContent"
MISTRAL_MODELL = "mistral-small-latest"
MISTRAL_ERSATZ = "ministral-8b-latest"   # kleineres Modell, falls das große ausgelastet ist
WARTEZEITEN = (20, 60)                   # Sekunden Pause vor erneutem Versuch bei "zu viele Anfragen"
MISTRAL_URL = "https://api.mistral.ai/v1/chat/completions"
CLAUDE_MODELL = "claude-haiku-5-5"
CLAUDE_VERSION = "2026-10-21"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
LOG_DATEI = Path("ki_signale_log.json")
OHNE_SIGNAL: set[str] = set()      # wird je nach Anbieter gesetzt (Claude -> {"ANT"})
GEBUEHR = 1.0
START_BETRAG = 100.0              # virtueller Betrag je Wert für die Erfolgskontrolle
PRUEF_ABSTAND = 5                 # ein Signal wird nach 5 weiteren Läufen (~1 Woche) bewertet
HISTORIE_TAGE = 180
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


def frage_ki(infos: list[dict], besitz: set[str], api_key: str, post=None,
             anbieter: str = "mistral", rueckmeldung: str = "") -> dict:
    """Gibt {"fazit": str, "signale": {kuerzel: {...}}} zurück. post = requests.post (für Tests)."""
    if post is None:
        import requests
        post = requests.post
    frage = ("Datum: " + date.today().isoformat() + rueckmeldung
             + "\nWerte:\n" + _eingabe(infos, besitz))
    if anbieter == "gemini":
        import time
        antwort = None
        for modell in GEMINI_MODELLE:
            for pause in (0, 20, 60):
                if pause:
                    time.sleep(pause)
                antwort = post(
                    GEMINI_URL.format(modell=modell), timeout=120,
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={"systemInstruction": {"parts": [{"text": ANWEISUNG}]},
                          "contents": [{"role": "user", "parts": [{"text": frage}]}],
                          "generationConfig": {"responseMimeType": "application/json",
                                               "temperature": 0.3, "maxOutputTokens": 8000}})
                if antwort.status_code not in (429, 503):
                    break
            # Modell nicht verfügbar/zu ausgelastet -> nächstes Modell probieren
            if antwort.status_code not in (404, 429, 503):
                break
    elif anbieter == "mistral":
        import time

        def senden(modell):
            return post(
                MISTRAL_URL, timeout=90,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                         "Accept": "application/json"},
                json={"model": modell, "max_tokens": 3000, "temperature": 0.3,
                      "response_format": {"type": "json_object"},
                      "messages": [{"role": "system", "content": ANWEISUNG},
                                   {"role": "user", "content": frage}]})
        # Bei "zu viele Anfragen" (429) bzw. Überlastung (503): warten und erneut versuchen,
        # zuletzt mit dem kleineren Ersatzmodell.
        versuche = [(MISTRAL_MODELL, 0)] + [(MISTRAL_MODELL, w) for w in WARTEZEITEN] + [(MISTRAL_ERSATZ, 30)]
        for modell, pause in versuche:
            if pause:
                time.sleep(pause)
            antwort = senden(modell)
            if antwort.status_code not in (429, 503):
                break
    else:
        antwort = post(
            CLAUDE_URL, timeout=90,
            headers={"Authorization": f"Bearer {api_key}", "anthropic-version": CLAUDE_VERSION,
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODELL, "max_tokens": 4000, "system": ANWEISUNG,
                  "messages": [{"role": "user", "content": frage}]})
    if antwort.status_code >= 400:
        raise RuntimeError(f"KI-Anfrage fehlgeschlagen (HTTP {antwort.status_code}): {antwort.text[:200]}")
    daten = antwort.json()
    if anbieter == "gemini":
        kandidaten = daten.get("candidates") or [{}]
        teile = ((kandidaten[0].get("content") or {}).get("parts")) or []
        text = "".join(t.get("text", "") for t in teile if not t.get("thought"))
        if not text:
            raise RuntimeError(f"Gemini lieferte keinen Text (Grund: {kandidaten[0].get('finishReason', '?')})")
    elif anbieter == "mistral":
        inhalt = ((daten.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        if isinstance(inhalt, list):  # manche Modelle liefern Teile statt Text
            inhalt = "".join(t.get("text", "") for t in inhalt if isinstance(t, dict))
        text = inhalt or ""
    if anbieter == "claude":
        text = "".join(t.get("text", "") for t in daten.get("content", []) if t.get("type") == "text")
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

    # Verlauf für die Bilanz: heutiges Signal + Kurs je Wert (pro Tag nur ein Eintrag)
    hist = [h for h in log.get("historie", []) if h["d"] != heute]
    for name, preis in preise.items():
        if name in signale and preis > 0:
            hist.append({"d": heute, "n": name, "s": signale[name]["signal"], "p": preis})
    grenze = (date.fromisoformat(heute) - timedelta(days=HISTORIE_TAGE)).isoformat() \
        if len(heute) == 10 else ""
    log["historie"] = [h for h in hist if h["d"] >= grenze]
    LOG_DATEI.write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")

    ki = halten = 0.0
    wechsel = 0
    for name, w in werte.items():
        p = preise.get(name, w.get("letzter_preis", w["start_preis"]))
        ki += w["anteile"] * p + w["geld"]
        halten += START_BETRAG / w["start_preis"] * p
        wechsel += w["wechsel"]
    return {"seit": log["start"], "werte": len(werte), "ki": ki, "halten": halten,
            "einsatz": START_BETRAG * len(werte), "wechsel": wechsel, "geaendert": geaendert,
            "bilanz": bilanz(log)}


# ---------------------------------------------------------------------------
# Bilanz: Wie oft lag die KI richtig? (Rückmeldung an die KI und Anzeige in der App)
# ---------------------------------------------------------------------------
def bilanz(log: dict | None = None) -> dict:
    """Bewertet jedes Kaufen/Verkaufen-Signal, das mindestens PRUEF_ABSTAND Läufe alt ist:
    richtig, wenn der Kurs danach in die vorhergesagte Richtung ging."""
    log = log if log is not None else _lade_log()
    nach_wert: dict[str, list[dict]] = {}
    for h in sorted(log.get("historie", []), key=lambda x: x["d"]):
        nach_wert.setdefault(h["n"], []).append(h)
    gesamt = {"Kaufen": [0, 0], "Verkaufen": [0, 0]}
    pro_wert: dict[str, list[int]] = {}
    for name, eintraege in nach_wert.items():
        for i, h in enumerate(eintraege):
            if h["s"] not in gesamt or i + PRUEF_ABSTAND >= len(eintraege):
                continue
            spaeter = eintraege[i + PRUEF_ABSTAND]["p"]
            richtig = (spaeter > h["p"]) if h["s"] == "Kaufen" else (spaeter < h["p"])
            gesamt[h["s"]][0] += richtig
            gesamt[h["s"]][1] += 1
            pw = pro_wert.setdefault(name, [0, 0])
            pw[0] += richtig
            pw[1] += 1
    return {"Kaufen": gesamt["Kaufen"], "Verkaufen": gesamt["Verkaufen"], "pro_wert": pro_wert,
            "bewertet": gesamt["Kaufen"][1] + gesamt["Verkaufen"][1]}


def bilanz_text(b: dict) -> str:
    """Kurzer Text für die KI-Anfrage."""
    if not b or not b.get("bewertet"):
        return ""
    teile = [f"{typ}: {b[typ][0]} von {b[typ][1]} richtig" for typ in ("Kaufen", "Verkaufen") if b[typ][1]]
    werte = [f"{n} {r}/{a}" for n, (r, a) in sorted(b["pro_wert"].items()) if a >= 2]
    return ("\n\nDeine eigene bisherige Bilanz (jedes Kaufen/Verkaufen-Signal wurde "
            f"{PRUEF_ABSTAND} Läufe später geprüft: lief der Kurs in die vorhergesagte Richtung?): "
            + "; ".join(teile) + "."
            + (" Pro Wert (richtig/gesamt): " + ", ".join(werte) + "." if werte else "")
            + " Berücksichtige das: Wo du oft falsch lagst, sei vorsichtiger (eher Halten, niedrigere "
              "Sicherheit). Die Stichprobe ist klein – zieh daraus keine starken Schlüsse.")


# ---------------------------------------------------------------------------
# Einstieg für bot.py
# ---------------------------------------------------------------------------
def ki_auswertung(infos: list[dict], besitz: set[str], preise: dict[str, float],
                  post=None, api_key: str | None = None, anbieter: str | None = None) -> dict | None:
    if api_key is None:
        anbieter, api_key = "claude", ""
        for name, variable in (("gemini", "GEMINI_API_KEY"), ("mistral", "MISTRAL_API_KEY"),
                               ("claude", "ANTHROPIC_API_KEY")):
            wert = os.getenv(variable, "").strip()
            if wert:
                anbieter, api_key = name, wert
                break
    anbieter = anbieter or "gemini"
    if not api_key:
        return None
    OHNE_SIGNAL.clear()
    if anbieter == "claude":
        OHNE_SIGNAL.add("ANT")   # Anthropic-KI gibt kein Signal zur Anthropic-Aktie
    name = {"gemini": "Gemini", "mistral": "Mistral"}.get(anbieter, "Claude")
    try:
        ergebnis = frage_ki(infos, besitz, api_key, post=post, anbieter=anbieter,
                            rueckmeldung=bilanz_text(bilanz()))
    except Exception as ex:  # noqa: BLE001
        return {"fehler": str(ex)[:300], "signale": {}, "fazit": "", "kontrolle": None,
                "anbieter": name}
    ergebnis["kontrolle"] = erfolgskontrolle(ergebnis["signale"], preise)
    ergebnis["fehler"] = None
    ergebnis["anbieter"] = name
    ergebnis["ohne_signal"] = sorted(OHNE_SIGNAL)
    return ergebnis
