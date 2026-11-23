"""
bot.py – KI-Trader als Börsen-Wächter

Jeden Abend (oder wann du willst):
1. ÜBERBLICK: Trend, Kursentwicklung und Kennzahlen deiner Aktien – ohne Kaufbefehle
2. ALARME: meldet sich bei auffälligen Bewegungen (Kurssturz, starker Anstieg,
   Trendwende, neues Jahreshoch, weit unter dem Hoch …) – mit Fenster am Bildschirm
3. MEIN DEPOT: wenn du unten deine echten Käufe einträgst, siehst du deren Wert
4. STRATEGIE-TEST: die Trend-Regeln laufen im Hintergrund mit Spielgeld weiter,
   damit du siehst, ob sie auf Dauer mit Kaufen und Halten mithalten

Echte Orders schickt dieser Bot NIE ab.

Starten:  py bot.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date
from pathlib import Path

from dashboard import erstelle_dashboard, push_text
from fusion_broker import BrokerConfig, PaperBroker, Signal
from strategie import Einstellungen, Kennzahlen, entscheide
from vergleich import vergleich_zeilen

# ===========================================================================
# EINSTELLUNGEN – hier kannst du anpassen
# ===========================================================================
AKTIEN = {              # Name -> Kürzel bei Yahoo Finance
    "AAPL": "AAPL",     # Apple
    "MSFT": "MSFT",     # Microsoft
    "NVDA": "NVDA",     # Nvidia
    "AMZN": "AMZN",     # Amazon
    "SAP": "SAP.DE",    # SAP (Xetra, in Euro)
}

# Deine ECHTEN Käufe und das Push-Thema stehen NICHT hier, sondern in der Datei
# einstellungen_privat.json (bzw. in der Cloud in den GitHub-"Secrets").
# So landen sie nie auf einer öffentlichen Seite.

# Alarm-Grenzen in Prozent
ALARM_TAG_PROZENT = 5.0          # Bewegung an einem Tag
ALARM_WOCHE_PROZENT = 10.0       # Bewegung in einer Woche
ALARM_VOM_HOCH_PROZENT = 20.0    # so weit unter dem 52-Wochen-Hoch
ALARM_DEPOT_PROZENT = 15.0       # deine echte Position im Minus

ALARM_FENSTER = True             # Hinweisfenster bei Alarmen anzeigen

PUSH_NUR_BEI_ALARM = False       # True = nur bei Alarmen eine Push-Nachricht
# ===========================================================================

BETRAG_PRO_KAUF_EUR = 200.0
STARTKAPITAL_EUR = 1000.0
BERICHT_ORDNER = Path("berichte")
DEPOT_DATEI = Path("depot_tag.json")
HOECHST_DATEI = Path("hoechstkurse_tag.json")
VERGLEICH_DATEI = Path("vergleich_tag.json")

log = logging.getLogger("ki_trader.bot")
STRATEGIE_INFO: dict = {}

# ---------------------------------------------------------------------------
# Private Einstellungen: Umgebungsvariablen (Cloud) oder einstellungen_privat.json
#   {"ntfy_thema": "ki-trader-…", "mein_depot": {"AAPL": [0.5, 210.00]}}
# ---------------------------------------------------------------------------
PRIVAT_DATEI = Path("einstellungen_privat.json")
IN_DER_CLOUD = os.getenv("KI_CLOUD") == "1"


def _lade_privat() -> tuple[str, dict[str, tuple[float, float]]]:
    daten = {}
    if PRIVAT_DATEI.exists():
        try:
            daten = json.loads(PRIVAT_DATEI.read_text(encoding="utf-8"))
        except ValueError as ex:
            print(f"Achtung: {PRIVAT_DATEI} ist fehlerhaft ({ex}) – wird ignoriert.")
    thema = os.getenv("NTFY_THEMA") or daten.get("ntfy_thema", "")
    depot_roh = daten.get("mein_depot", {})
    if os.getenv("MEIN_DEPOT"):
        try:
            depot_roh = json.loads(os.environ["MEIN_DEPOT"])
        except ValueError:
            print("Achtung: MEIN_DEPOT ist kein gültiges JSON – wird ignoriert.")
    depot = {n: (float(v[0]), float(v[1])) for n, v in depot_roh.items()}
    return thema.strip(), depot


NTFY_THEMA, MEIN_DEPOT = _lade_privat()


# ---------------------------------------------------------------------------
# Kursdaten
# ---------------------------------------------------------------------------
def lade_wechselkurs() -> float:
    import yfinance as yf
    hist = yf.Ticker("EURUSD=X").history(period="5d")
    return float(hist["Close"].dropna().iloc[-1])


def lade_kurse_eur(yahoo_kuerzel: str, usd_pro_eur: float) -> tuple[list[str], list[float]]:
    """Gibt (Datumsliste, Kursliste in EUR) der letzten 2 Jahre zurück."""
    import yfinance as yf
    hist = yf.Ticker(yahoo_kuerzel).history(period="2y", interval="1d", auto_adjust=True)
    reihe = hist["Close"].dropna()
    faktor = 1.0 if yahoo_kuerzel.endswith(".DE") else 1 / usd_pro_eur
    return ([i.date().isoformat() for i in reihe.index],
            [float(x) * faktor for x in reihe.tolist()])


# ---------------------------------------------------------------------------
# Überblick und Alarme
# ---------------------------------------------------------------------------
def veraenderung(kurse: list[float], tage_zurueck: int) -> float | None:
    if len(kurse) <= tage_zurueck:
        return None
    return (kurse[-1] / kurse[-1 - tage_zurueck] - 1) * 100


def seit_jahresbeginn(daten: list[str], kurse: list[float]) -> float | None:
    jahr = daten[-1][:4]
    vorjahr = [k for d, k in zip(daten, kurse) if d[:4] < jahr]
    if not vorjahr:
        return None
    return (kurse[-1] / vorjahr[-1] - 1) * 100


def pz(w: float | None) -> str:
    return "   –   " if w is None else f"{w:+6.1f}%"


def trend_einordnen(preis: float, ma50: float | None, ma200: float | None) -> tuple[str, str]:
    """Gibt (Beschreibung, Stufe "plus"/"warn"/"minus") zurück.
    Ø50 gegen Ø200 zeigt den großen Trend, reagiert aber spät. Deshalb wird
    zusätzlich geprüft, wo der Kurs heute im Verhältnis zu den Durchschnitten steht."""
    if ma50 is None or ma200 is None:
        return "zu wenig Daten", "warn"
    if ma50 > ma200:
        if preis >= ma50:
            return "Aufwärtstrend", "plus"
        if preis < ma200:
            return "Aufwärtstrend gebrochen (Kurs unter Ø200)", "minus"
        return "Aufwärtstrend wackelt (Kurs unter Ø50)", "warn"
    if preis <= ma50:
        return "Abwärtstrend", "minus"
    if preis > ma200:
        return "Erholung, mögliche Trendwende (Kurs über Ø200)", "warn"
    return "Abwärtstrend, Kurs erholt sich", "warn"


def analysiere(name: str, daten: list[str], kurse: list[float]) -> tuple[list[str], list[str], dict]:
    """Gibt (Überblick-Zeilen, Alarme, Infos fürs Dashboard) für eine Aktie zurück."""
    k = Kennzahlen(kurse)
    i = len(kurse) - 1
    preis = kurse[-1]
    ma50, ma200, r = k.ma(50, i), k.ma(200, i), k.rsi[i]
    tag, woche, monat = veraenderung(kurse, 1), veraenderung(kurse, 5), veraenderung(kurse, 21)
    ytd = seit_jahresbeginn(daten, kurse)
    hoch52 = max(kurse[-252:])
    vom_hoch = (preis / hoch52 - 1) * 100

    trend, stufe = trend_einordnen(preis, ma50, ma200)

    zeilen = [
        f"  {name:5} {preis:9.2f} €   Tag {pz(tag)}  Woche {pz(woche)}  Monat {pz(monat)}  "
        f"Jahr {pz(ytd)}",
        f"        {trend}; {vom_hoch:+.1f} % vom 52-Wochen-Hoch; RSI {r:.0f}"
        if r is not None else f"        {trend}",
    ]

    alarme = []
    if tag is not None and abs(tag) >= ALARM_TAG_PROZENT:
        alarme.append(f"{name}: {'Kurssturz' if tag < 0 else 'starker Anstieg'} heute {tag:+.1f} %")
    if woche is not None and abs(woche) >= ALARM_WOCHE_PROZENT:
        alarme.append(f"{name}: {woche:+.1f} % in einer Woche")
    if vom_hoch <= -ALARM_VOM_HOCH_PROZENT:
        alarme.append(f"{name}: {vom_hoch:.1f} % unter dem 52-Wochen-Hoch")
    if preis >= hoch52 and len(kurse) >= 252:
        alarme.append(f"{name}: neues 52-Wochen-Hoch ({preis:.2f} €)")
    if i >= 1 and None not in (ma50, ma200, k.ma(50, i - 1), k.ma(200, i - 1)):
        vorher_hoch = k.ma(50, i - 1) > k.ma(200, i - 1)
        jetzt_hoch = ma50 > ma200
        if jetzt_hoch and not vorher_hoch:
            alarme.append(f"{name}: Trendwende nach OBEN (Ø50 kreuzt Ø200 von unten)")
        if vorher_hoch and not jetzt_hoch:
            alarme.append(f"{name}: Trendwende nach UNTEN (Ø50 fällt unter Ø200)")
    if r is not None and r <= 25:
        alarme.append(f"{name}: stark überverkauft (RSI {r:.0f})")
    if r is not None and r >= 80:
        alarme.append(f"{name}: stark überhitzt (RSI {r:.0f})")
    info = {"name": name, "preis": preis, "tag": tag, "woche": woche, "monat": monat,
            "jahr": ytd, "vom_hoch": vom_hoch, "rsi": r, "trend": trend,
            "trend_stufe": stufe,
            "verlauf": kurse[-126:], "alarme": [a for a in alarme]}
    return zeilen, alarme, info


def mein_depot_zeilen(preise: dict[str, float]) -> tuple[list[str], list[str], list[dict]]:
    if not MEIN_DEPOT:
        return ["  (noch leer – trag deine Käufe in einstellungen_privat.json ein)"], [], []
    zeilen, alarme, posten = [], [], []
    wert_ges = einsatz_ges = 0.0
    for name, (stueck, kaufpreis) in MEIN_DEPOT.items():
        if name not in preise:
            zeilen.append(f"  {name:5} kein aktueller Kurs (steht {name} auch bei AKTIEN?)")
            continue
        wert, einsatz = stueck * preise[name], stueck * kaufpreis
        diff = (wert / einsatz - 1) * 100
        wert_ges += wert
        einsatz_ges += einsatz
        zeilen.append(f"  {name:5} {stueck:.4f} Stk.  Einsatz {einsatz:8.2f} €  "
                      f"Wert {wert:8.2f} €  ({diff:+.1f} %)")
        posten.append({"name": name, "stueck": stueck, "einsatz": einsatz, "wert": wert,
                       "diff": diff})
        if diff <= -ALARM_DEPOT_PROZENT:
            alarme.append(f"MEIN DEPOT {name}: {diff:.1f} % im Minus")
    if einsatz_ges:
        zeilen.append(f"  GESAMT       Einsatz {einsatz_ges:8.2f} €  Wert {wert_ges:8.2f} €  "
                      f"({(wert_ges / einsatz_ges - 1) * 100:+.1f} %)")
    return zeilen, alarme, posten


# ---------------------------------------------------------------------------
# Strategie-Test mit Spielgeld (läuft im Hintergrund weiter)
# ---------------------------------------------------------------------------
def neuer_broker() -> PaperBroker:
    return PaperBroker(BrokerConfig(
        allowed_symbols=set(AKTIEN), state_file=DEPOT_DATEI,
        paper_start_cash_eur=STARTKAPITAL_EUR, max_eur_per_trade=BETRAG_PRO_KAUF_EUR,
        max_eur_per_day=STARTKAPITAL_EUR, max_daily_loss_eur=100.0))


def strategie_test(alle: dict[str, tuple[list[str], list[float]]], broker: PaperBroker,
                   e: Einstellungen) -> list[str]:
    hoechst = json.loads(HOECHST_DATEI.read_text(encoding="utf-8")) if HOECHST_DATEI.exists() else {}
    preise = {}
    for name, (daten, kurse) in alle.items():
        preis = kurse[-1]
        preise[name] = preis
        pos = broker.state.positions.get(name)
        if pos:
            h = hoechst.setdefault(name, {"kaufdatum": daten[-1], "hoechst": pos.avg_price})
            h["hoechst"] = max([h["hoechst"], *[k for d, k in zip(daten, kurse)
                                                 if d >= h["kaufdatum"]]])
        aktion, grund = entscheide(Kennzahlen(kurse), len(kurse) - 1, e,
                                   pos.avg_price if pos else None,
                                   hoechst[name]["hoechst"] if pos else None)
        if aktion == "Halten":
            continue
        erg = broker.execute(Signal(name, aktion, BETRAG_PRO_KAUF_EUR if aktion == "Buy" else 0,
                                    preis, reason=grund))
        if erg.accepted:
            if aktion == "Buy":
                hoechst[name] = {"kaufdatum": daten[-1], "hoechst": preis}
            else:
                hoechst.pop(name, None)
    HOECHST_DATEI.write_text(json.dumps(hoechst, indent=2), encoding="utf-8")
    s = broker.status()
    from vergleich import depotwert, halten_wert
    if len(preise) == len(AKTIEN):
        STRATEGIE_INFO.update(bot=depotwert(s, preise),
                              halten=halten_wert(VERGLEICH_DATEI, preise, STARTKAPITAL_EUR)[0],
                              seit=halten_wert(VERGLEICH_DATEI, preise, STARTKAPITAL_EUR)[1],
                              positionen=list(s["positions"]))
    z = [f"  Regeln: {e.text()}"]
    pos = ", ".join(s["positions"]) or "keine"
    z.append(f"  Positionen: {pos}; freies Spielgeld {s['paper_cash_eur']:.2f} €")
    if len(preise) == len(AKTIEN):
        z += ["  " + l for l in vergleich_zeilen(s, preise, VERGLEICH_DATEI, STARTKAPITAL_EUR,
                                                 BETRAG_PRO_KAUF_EUR)]
    return z


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------
def lauf(kurs_lader=None, broker=None,
         einstellungen: Einstellungen | None = None) -> tuple[list[str], list[str], dict]:
    """Gibt (Berichtszeilen, Alarme, Daten fürs Dashboard) zurück."""
    if kurs_lader is None:
        fx = lade_wechselkurs()
        kurs_lader = lambda name: lade_kurse_eur(AKTIEN[name], fx)  # noqa: E731

    alle, ueberblick, alarme, fehler, infos = {}, [], [], [], []
    for name in AKTIEN:
        try:
            daten, kurse = kurs_lader(name)
        except Exception as ex:  # noqa: BLE001
            fehler.append(f"  {name}: Kurse nicht ladbar ({ex})")
            continue
        if len(kurse) < 30:
            fehler.append(f"  {name}: zu wenig Kursdaten")
            continue
        alle[name] = (daten, kurse)
        z, a, info = analysiere(name, daten, kurse)
        infos.append(info)
        ueberblick += z
        alarme += a

    preise = {n: k[-1] for n, (_, k) in alle.items()}
    depot, depot_alarme, posten = mein_depot_zeilen(preise)
    alarme += depot_alarme

    titel = f"KI-TRADER BÖRSEN-WÄCHTER – {date.today().strftime('%d.%m.%Y')}"
    z = [titel, ""]
    z += [f"ALARME ({len(alarme)}):"] + ([f"  ! {a}" for a in alarme] or ["  keine – alles ruhig"])
    z += ["", "ÜBERBLICK (Kurse in €):"] + ueberblick + fehler
    z += ["", "MEIN DEPOT:"] + depot
    if len(alle) == len(AKTIEN):
        z += ["", "STRATEGIE-TEST (Spielgeld, nur zur Beobachtung):"]
        z += strategie_test(alle, broker or neuer_broker(),
                            einstellungen or Einstellungen.laden())
    z += ["", "Erklärung: Ø50/Ø200 = Durchschnittskurs der letzten 50/200 Tage. "
              "RSI unter 30 = stark gefallen, über 70 = stark gestiegen.",
          "Das ist keine Anlageberatung. Du entscheidest selbst."]
    dash = {"datum": date.today().strftime("%d.%m.%Y"), "zeit": uhrzeit_berlin(),
            "alarme": alarme, "aktien": infos,
            "fehler": [f.strip() for f in fehler], "depot": posten,
            "depot_verbergen": IN_DER_CLOUD,
            "strategie": dict(STRATEGIE_INFO)}
    return z, alarme, dash


def uhrzeit_berlin() -> str:
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).strftime("%H:%M")
    except Exception:  # noqa: BLE001  (Windows ohne tzdata)
        return datetime.now().strftime("%H:%M")


def sende_push(dash: dict) -> None:
    if not NTFY_THEMA or (PUSH_NUR_BEI_ALARM and not dash["alarme"]):
        return
    import requests
    titel, text = push_text(dash)
    try:
        requests.post(f"https://ntfy.sh/{NTFY_THEMA}", data=text.encode("utf-8"), timeout=15,
                      headers={"Title": titel,
                               "Priority": "high" if dash["alarme"] else "default",
                               "Tags": "warning" if dash["alarme"] else "chart_with_upwards_trend"})
    except Exception as ex:  # noqa: BLE001
        log.warning("Push-Nachricht fehlgeschlagen: %s", ex)


def zeige_alarmfenster(alarme: list[str]) -> None:
    if not (ALARM_FENSTER and alarme and sys.platform == "win32"):
        return
    try:
        import ctypes
        text = "\n".join(alarme[:10]) + "\n\nDetails stehen in berichte\\aktuell.txt"
        ctypes.windll.user32.MessageBoxW(0, text, f"KI-Trader: {len(alarme)} Alarm(e)", 0x40030)
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler("bot.log", encoding="utf-8")])
    zeilen, alarme, dash = lauf()
    text = "\n".join(zeilen)
    print("\n" + text)
    BERICHT_ORDNER.mkdir(exist_ok=True)
    (BERICHT_ORDNER / f"bericht_{date.today().isoformat()}.txt").write_text(text, encoding="utf-8")
    (BERICHT_ORDNER / "aktuell.txt").write_text(text, encoding="utf-8")
    seite = BERICHT_ORDNER / "dashboard.html"
    seite.write_text(erstelle_dashboard(dash), encoding="utf-8")
    print(f"\nDashboard: {seite.resolve()}")
    sende_push(dash)
    zeige_alarmfenster(alarme)


if __name__ == "__main__":
    main()
