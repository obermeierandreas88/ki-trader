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
    "BTC": "BTC-EUR",   # Bitcoin (in Euro, wird auch am Wochenende gehandelt)
    "SOL": "SOL-EUR",   # Solana (in Euro) – nur Beobachtung
    "ETH": "ETH-EUR",   # Ethereum (in Euro) – nur Beobachtung
    "SPCX": "SPCX",     # SpaceX (Nasdaq, seit 12.06.2026 an der Börse)
    "INSP": "INSP-USD", # Inspect (kleine Kryptowährung, Kurs in USD -> wird umgerechnet)
    "SKL": "SKL-EUR",   # SKALE Network (in Euro)
    "NAVX": "NAVX-USD", # NAVI Protocol (Kurs in USD -> wird umgerechnet)
    "XEM": "XEM-EUR",   # NEM (in Euro)
    "SWELL": "SWELL-USD",  # Swell Network (Kurs in USD -> wird umgerechnet)
    "ESE": "ESE-USD",   # eesee (Kurs in USD -> wird umgerechnet)
    "UTK": "bp:UTK",    # xMoney, alter Token UTK (nur aktueller Preis direkt von Bitpanda)
    "IREN": "IREN",     # IREN Limited (Nasdaq)
    "ALGS": "ALGS",     # Aligos Therapeutics (Nasdaq)
    "BSB": "cg:block-street",  # Block Street (Kurse von CoinGecko) – nur Beobachtung
    "IFX": "IFX.DE",    # Infineon (Xetra, in Euro) – nur Beobachtung
    "ARM": "ARM",       # Arm Holdings ADR (Nasdaq) – nur Beobachtung
    "NEO": "NEO-EUR",   # NEO (in Euro) – nur Beobachtung
    "META": "META",     # Meta Platforms (Nasdaq) – nur Beobachtung
    "ANET": "ANET",     # Arista Networks (NYSE) – nur Beobachtung
    "AVGO": "AVGO",     # Broadcom (Nasdaq) – nur Beobachtung
    "TSM": "TSM",       # TSMC ADR (NYSE) – nur Beobachtung
    "NEOPM": "NEO.TO",  # Neo Performance Materials (Toronto, in CAD) – nur Beobachtung
    "ANT": "ANT",       # Anthropic (Nasdaq, seit 19.11.2026 an der Börse) – nur Beobachtung
}

# Kaufziele: Alarm, sobald der Kurs auf oder unter den Zielpreis (in €) fällt.
# Name: (Zielpreis in €, geplanter Betrag in €). Der Bot kauft NICHT selbst.
KAUFZIELE: dict[str, tuple[float, float]] = {
    "ANT": (79.00, 100.00),
}
# Kürzel mit "cg:" holen die Kurse von CoinGecko statt Yahoo Finance
# (für kleine Kryptowährungen, die Yahoo nicht kennt).
# Kürzel mit "bp:" holen nur den aktuellen Preis direkt von Bitpanda (ohne Verlauf).

# Werte für den Spielgeld-Strategie-Test (leer = Test ausgeschaltet)
STRATEGIE_WERTE: list[str] = []


def ist_euro(kuerzel: str) -> bool:
    return (kuerzel.endswith(".DE") or kuerzel.endswith("-EUR")
            or kuerzel.startswith(("cg:", "bp:")))


def ist_krypto(kuerzel: str) -> bool:
    return "-" in kuerzel or kuerzel.startswith(("cg:", "bp:"))  # z. B. BTC-EUR, cg:block-street

# Deine ECHTEN Käufe und das Push-Thema stehen NICHT hier, sondern in der Datei
# einstellungen_privat.json (bzw. in der Cloud in den GitHub-"Secrets").
# So landen sie nie auf einer öffentlichen Seite.

# Alarm-Grenzen in Prozent
ALARM_TAG_PROZENT = 5.0          # Bewegung an einem Tag
ALARM_WOCHE_PROZENT = 10.0       # Bewegung in einer Woche
ALARM_VOM_HOCH_PROZENT = 20.0    # so weit unter dem 52-Wochen-Hoch
ALARM_DEPOT_PROZENT = 15.0       # deine echte Position im Minus

ALARM_FENSTER = True             # Hinweisfenster bei Alarmen anzeigen
NACHRICHTEN_ANZEIGEN = True      # neueste Schlagzeilen pro Wert anzeigen

PUSH_NUR_BEI_ALARM = False       # True = Push nur, wenn es NEUE Alarme gibt
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


def _lade_privat() -> tuple[str, dict[str, tuple[float, float]], str]:
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
    depot_roh = dict(depot_roh)
    passwort = str(depot_roh.pop("_pin", "") or daten.get("depot_passwort", ""))
    depot = {n: (float(v[0]), float(v[1])) for n, v in depot_roh.items()}
    return thema.strip(), depot, passwort.strip()


NTFY_THEMA, MEIN_DEPOT, DEPOT_PASSWORT = _lade_privat()


# ---------------------------------------------------------------------------
# Kursdaten
# ---------------------------------------------------------------------------
def lade_wechselkurs() -> float:
    import yfinance as yf
    hist = yf.Ticker("EURUSD=X").history(period="5d")
    return float(hist["Close"].dropna().iloc[-1])


_KURS_CACHE: dict[str, float] = {}


def umrechnung_in_eur(yahoo_kuerzel: str, usd_pro_eur: float) -> float:
    """Faktor, mit dem ein Kurs in Euro umgerechnet wird (USD, CAD, GBP, CHF …)."""
    if ist_euro(yahoo_kuerzel):
        return 1.0
    import yfinance as yf
    try:
        waehrung = (yf.Ticker(yahoo_kuerzel).fast_info.get("currency") or "USD")
    except Exception:  # noqa: BLE001
        waehrung = "USD"
    teiler = 1.0
    if waehrung in ("GBp", "GBX"):        # London notiert in Pence
        waehrung, teiler = "GBP", 100.0
    waehrung = waehrung.upper()
    if waehrung == "EUR":
        return 1.0 / teiler
    if waehrung == "USD":
        return 1.0 / usd_pro_eur / teiler
    if waehrung not in _KURS_CACHE:
        h = yf.Ticker(f"EUR{waehrung}=X").history(period="5d")["Close"].dropna()
        _KURS_CACHE[waehrung] = float(h.iloc[-1])
    return 1.0 / _KURS_CACHE[waehrung] / teiler


_BITPANDA_PREISE: dict = {}


def lade_bitpanda_eur(symbol: str) -> tuple[list[str], list[float]]:
    """Aktueller EUR-Preis aus der öffentlichen Bitpanda-Preisliste (kein Verlauf)."""
    import requests
    if not _BITPANDA_PREISE:
        r = requests.get("https://api.bitpanda.com/v1/ticker", timeout=20)
        r.raise_for_status()
        _BITPANDA_PREISE.update(r.json())
    eintrag = _BITPANDA_PREISE.get(symbol)
    if not isinstance(eintrag, dict) or not eintrag.get("EUR"):
        raise RuntimeError(f"{symbol} nicht in der Bitpanda-Preisliste")
    return [date.today().isoformat()], [float(eintrag["EUR"])]


_LETZTE_CG_ABFRAGE = 0.0


def lade_coingecko_eur(coin_id: str) -> tuple[list[str], list[float]]:
    """Tageskurse in EUR von CoinGecko (kostenlos, ohne Schlüssel, max. 1 Jahr)."""
    import time
    from datetime import datetime, timezone
    import requests
    global _LETZTE_CG_ABFRAGE
    pause = 8 - (time.time() - _LETZTE_CG_ABFRAGE)  # CoinGecko erlaubt nur wenige Abfragen/Minute
    if pause > 0:
        time.sleep(pause)
    _LETZTE_CG_ABFRAGE = time.time()
    url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
           "?vs_currency=eur&days=365&interval=daily")
    for versuch in range(4):
        r = requests.get(url, timeout=20)
        if r.status_code == 429:  # zu viele Anfragen -> kurz warten
            time.sleep(20 * (versuch + 1))
            continue
        r.raise_for_status()
        punkte = r.json().get("prices", [])
        break
    else:
        raise RuntimeError("CoinGecko antwortet gerade nicht (zu viele Anfragen)")
    tage: dict[str, float] = {}
    for ms, preis in punkte:
        tage[datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()] = float(preis)
    daten = sorted(tage)
    return daten, [tage[d] for d in daten]


def lade_kurse_eur(yahoo_kuerzel: str, usd_pro_eur: float) -> tuple[list[str], list[float]]:
    """Gibt (Datumsliste, Kursliste in EUR) der letzten 2 Jahre zurück."""
    if yahoo_kuerzel.startswith("cg:"):
        return lade_coingecko_eur(yahoo_kuerzel[3:])
    if yahoo_kuerzel.startswith("bp:"):
        return lade_bitpanda_eur(yahoo_kuerzel[3:])
    import time
    import yfinance as yf
    reihe = None
    for versuch in range(3):  # Yahoo bremst manchmal kurz aus -> erneut versuchen
        try:
            hist = yf.Ticker(yahoo_kuerzel).history(period="2y", interval="1d", auto_adjust=True)
            reihe = hist["Close"].dropna()
            if len(reihe):
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5 * (versuch + 1))
    if reihe is None or not len(reihe):
        raise RuntimeError("keine Kurse von Yahoo erhalten")
    faktor = umrechnung_in_eur(yahoo_kuerzel, usd_pro_eur)
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


def _kurs(w: float) -> str:
    if w != 0 and abs(w) < 1:
        from math import floor, log10
        return f"{w:.{min(10, max(2, -int(floor(log10(abs(w)))) + 2))}f}"
    return f"{w:.2f}"


def pz(w: float | None) -> str:
    return "   –   " if w is None else f"{w:+6.1f}%"


def trend_einordnen(preis: float, ma50: float | None, ma200: float | None,
                    kurz: str = "Ø50", lang: str = "Ø200") -> tuple[str, str]:
    """Gibt (Beschreibung, Stufe "plus"/"warn"/"minus") zurück.
    Ø50 gegen Ø200 zeigt den großen Trend, reagiert aber spät. Deshalb wird
    zusätzlich geprüft, wo der Kurs heute im Verhältnis zu den Durchschnitten steht."""
    if ma50 is None or ma200 is None:
        return "zu wenig Daten", "warn"
    if ma50 > ma200:
        if preis >= ma50:
            return "Aufwärtstrend", "plus"
        if preis < ma200:
            return f"Aufwärtstrend gebrochen (Kurs unter {lang})", "minus"
        return f"Aufwärtstrend wackelt (Kurs unter {kurz})", "warn"
    if preis <= ma50:
        return "Abwärtstrend", "minus"
    if preis > ma200:
        return f"Erholung, mögliche Trendwende (Kurs über {lang})", "warn"
    return "Abwärtstrend, Kurs erholt sich", "warn"


def analysiere(name: str, daten: list[str], kurse: list[float],
               krypto: bool = False) -> tuple[list[str], list[str], dict]:
    """Gibt (Überblick-Zeilen, Alarme, Infos fürs Dashboard) für eine Aktie zurück."""
    k = Kennzahlen(kurse)
    i = len(kurse) - 1
    preis = kurse[-1]
    ma50, ma200, r = k.ma(50, i), k.ma(200, i), k.rsi[i]
    # Krypto wird jeden Tag gehandelt, Aktien nur an Börsentagen
    t_woche, t_monat, t_jahr = (7, 30, 365) if krypto else (5, 21, 252)
    tag, woche, monat = veraenderung(kurse, 1), veraenderung(kurse, t_woche), veraenderung(kurse, t_monat)
    ytd = seit_jahresbeginn(daten, kurse)
    hoch52 = max(kurse[-t_jahr:])
    vom_hoch = (preis / hoch52 - 1) * 100

    trend, stufe = trend_einordnen(preis, ma50, ma200)
    if ma200 is None and k.ma(20, i) is not None and ma50 is not None:
        # Junge Aktie (z. B. nach Börsengang): kurzfristigen Trend Ø20/Ø50 zeigen
        trend, stufe = trend_einordnen(preis, k.ma(20, i), ma50, "Ø20", "Ø50")
        trend += " – kurzfristig, Aktie noch jung"

    zeilen = [
        f"  {name:5} {_kurs(preis):>12} €   Tag {pz(tag)}  Woche {pz(woche)}  Monat {pz(monat)}  "
        f"Jahr {pz(ytd)}",
        f"        {trend}; {vom_hoch:+.1f} % vom 52-Wochen-Hoch; RSI {r:.0f}"
        if r is not None else f"        {trend}",
    ]
    if name in KAUFZIELE:
        ziel, betrag = KAUFZIELE[name]
        zeilen.append(f"        Kaufziel {ziel:.2f} € ({betrag:.0f} €): Kurs liegt "
                      f"{(preis / ziel - 1) * 100:+.1f} % {'über' if preis > ziel else 'unter'} dem Ziel")

    # Eingefrorene Kursdaten erkennen (z. B. nach einer Token-Umstellung)
    letzte_aenderung = daten[-1]
    for j in range(len(kurse) - 1, 0, -1):
        if abs(kurse[j] - kurse[j - 1]) > abs(kurse[j]) * 1e-4:
            letzte_aenderung = daten[j]
            break
    else:
        letzte_aenderung = daten[0]
    eingefroren = len(kurse) >= 15 and all(
        abs(x - kurse[-1]) <= abs(kurse[-1]) * 1e-4 for x in kurse[-15:])
    if eingefroren:
        hinweis = f"Kurs seit {letzte_aenderung} unverändert – Daten prüfen (Token umgestellt?)"
        zeilen = [f"  {name:5} {_kurs(preis):>12} €   {hinweis}"]
        info = {"name": name, "preis": preis, "tag": None, "woche": None, "monat": None,
                "jahr": None, "vom_hoch": vom_hoch, "rsi": None, "trend": "Kursdaten eingefroren",
                "trend_stufe": "warn", "verlauf": kurse[-126:], "alarme": [f"{name}: {hinweis}"]}
        return zeilen, [f"{name}: {hinweis}"], info

    alarme = []
    kaufziel = None
    if name in KAUFZIELE:
        ziel, betrag = KAUFZIELE[name]
        abstand = (preis / ziel - 1) * 100
        kaufziel = {"ziel": ziel, "betrag": betrag, "abstand": abstand,
                    "stueck": betrag / preis, "erreicht": preis <= ziel}
        if preis <= ziel:
            alarme.append(f"{name}: KAUFZIEL ERREICHT – Kurs {_kurs(preis)} € ≤ {ziel:.2f} €; "
                          f"für {betrag:.0f} € bekämst du ca. {betrag / preis:.2f} Stück")
    if tag is not None and abs(tag) >= ALARM_TAG_PROZENT:
        alarme.append(f"{name}: {'Kurssturz' if tag < 0 else 'starker Anstieg'} heute {tag:+.1f} %")
    if woche is not None and abs(woche) >= ALARM_WOCHE_PROZENT:
        alarme.append(f"{name}: {woche:+.1f} % in einer Woche")
    if vom_hoch <= -ALARM_VOM_HOCH_PROZENT:
        alarme.append(f"{name}: {vom_hoch:.1f} % unter dem 52-Wochen-Hoch")
    if preis >= hoch52 and len(kurse) >= t_jahr:
        alarme.append(f"{name}: neues 52-Wochen-Hoch ({_kurs(preis)} €)")
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
            "trend_stufe": stufe, "kaufziel": kaufziel,
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
        allowed_symbols=set(STRATEGIE_WERTE), state_file=DEPOT_DATEI,
        paper_start_cash_eur=STARTKAPITAL_EUR, max_eur_per_trade=BETRAG_PRO_KAUF_EUR,
        max_eur_per_day=STARTKAPITAL_EUR, max_daily_loss_eur=100.0))


def strategie_test(alle: dict[str, tuple[list[str], list[float]]], broker: PaperBroker,
                   e: Einstellungen) -> list[str]:
    hoechst = json.loads(HOECHST_DATEI.read_text(encoding="utf-8")) if HOECHST_DATEI.exists() else {}
    preise = {}
    for name, (daten, kurse) in alle.items():
        if name not in STRATEGIE_WERTE:
            continue
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
    if len(preise) == len(STRATEGIE_WERTE):
        STRATEGIE_INFO.update(bot=depotwert(s, preise),
                              halten=halten_wert(VERGLEICH_DATEI, preise, STARTKAPITAL_EUR)[0],
                              seit=halten_wert(VERGLEICH_DATEI, preise, STARTKAPITAL_EUR)[1],
                              positionen=list(s["positions"]))
    z = [f"  Regeln: {e.text()}"]
    pos = ", ".join(s["positions"]) or "keine"
    z.append(f"  Positionen: {pos}; freies Spielgeld {s['paper_cash_eur']:.2f} €")
    if len(preise) == len(STRATEGIE_WERTE):
        z += ["  " + l for l in vergleich_zeilen(s, preise, VERGLEICH_DATEI, STARTKAPITAL_EUR,
                                                 BETRAG_PRO_KAUF_EUR)]
    return z


# ---------------------------------------------------------------------------
# Schwankungsbreite: Wo lag der Kurs nach 30 Tagen bisher üblicherweise?
# Keine Prognose! Nur die bisherige Schwankung, hochgerechnet auf 30 Tage.
# ---------------------------------------------------------------------------
def schwankungsbreite(kurse: list[float], krypto: bool) -> dict | None:
    from math import exp, log, sqrt
    werte = [k for k in kurse[-366:] if k > 0]
    if len(werte) < 60:
        return None
    renditen = [log(b / a) for a, b in zip(werte, werte[1:])]
    mittel = sum(renditen) / len(renditen)
    sigma = sqrt(sum((r - mittel) ** 2 for r in renditen) / (len(renditen) - 1))
    tage = 30 if krypto else 21          # 30 Kalendertage = ~21 Börsentage
    s30 = sigma * sqrt(tage)
    p = werte[-1]
    return {"von68": p * exp(-s30), "bis68": p * exp(s30),
            "von90": p * exp(-1.645 * s30), "bis90": p * exp(1.645 * s30),
            "hoch68": (exp(s30) - 1) * 100, "runter68": (1 - exp(-s30)) * 100}


# ---------------------------------------------------------------------------
# Nachrichten: die neuesten Schlagzeilen von Yahoo Finance
# ---------------------------------------------------------------------------
def nachrichten(kuerzel: str, anzahl: int = 3) -> list[dict]:
    if kuerzel.startswith(("cg:", "bp:")):
        return []
    try:
        import yfinance as yf
        roh = yf.Ticker(kuerzel).news or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for eintrag in roh:
        c = eintrag.get("content", eintrag)
        titel = c.get("title")
        url = ((c.get("clickThroughUrl") or {}).get("url")
               or (c.get("canonicalUrl") or {}).get("url") or c.get("link"))
        if not titel or not url:
            continue
        out.append({"titel": titel, "url": url,
                    "quelle": (c.get("provider") or {}).get("displayName") or c.get("publisher", ""),
                    "datum": (c.get("pubDate") or "")[:10]})
        if len(out) >= anzahl:
            break
    return out


# ---------------------------------------------------------------------------
# Neue Alarme erkennen: heutige Alarme gegen die vom Vortag vergleichen
# ---------------------------------------------------------------------------
def alarm_schluessel(alarm: str) -> str | None:
    """Gibt für Dauerzustände einen Schlüssel zurück, für Ereignisse None.
    Ereignisse (Kurssturz heute, Trendwende) werden immer gemeldet,
    Dauerzustände (weit unter dem Hoch, im Minus …) nur, wenn sie neu sind."""
    name, _, rest = alarm.partition(": ")
    if "heute" in rest or "Trendwende" in rest:
        return None
    for merkmal in ("in einer Woche", "unter dem 52-Wochen-Hoch", "neues 52-Wochen-Hoch",
                    "überverkauft", "überhitzt", "im Minus", "KAUFZIEL", "unverändert"):
        if merkmal in rest:
            richtung = "-" if rest.lstrip().startswith("-") else "+"
            return f"{name}|{merkmal}|{richtung if merkmal == 'in einer Woche' else ''}"
    return None


def neue_alarme(heute: list[str], gestern: list[str]) -> list[str]:
    alt = {alarm_schluessel(a) for a in gestern} - {None}
    return [a for a in heute if alarm_schluessel(a) is None or alarm_schluessel(a) not in alt]


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
    alarme_gestern: list[str] = []
    for name in AKTIEN:
        try:
            daten, kurse = kurs_lader(name)
        except Exception as ex:  # noqa: BLE001
            fehler.append(f"  {name}: Kurse nicht ladbar ({ex})")
            continue
        if len(kurse) < 1:
            fehler.append(f"  {name}: keine Kursdaten")
            continue
        alle[name] = (daten, kurse)
        krypto = ist_krypto(AKTIEN[name])
        z, a, info = analysiere(name, daten, kurse, krypto)
        info["breite"] = schwankungsbreite(kurse, krypto)
        info["news"] = nachrichten(AKTIEN[name]) if NACHRICHTEN_ANZEIGEN else []
        if info["breite"]:
            b = info["breite"]
            z.append(f"        Übliche 30-Tage-Spanne (2 von 3 Fällen): {_kurs(b['von68'])} – "
                     f"{_kurs(b['bis68'])} € (−{b['runter68']:.0f} % / +{b['hoch68']:.0f} %)")
        for n in info["news"][:2]:
            z.append(f"        News {n['datum']}: {n['titel']}")
        if len(kurse) >= 3:
            alarme_gestern += analysiere(name, daten[:-1], kurse[:-1], ist_krypto(AKTIEN[name]))[1]
        infos.append(info)
        ueberblick += z
        alarme += a

    preise = {n: k[-1] for n, (_, k) in alle.items()}
    depot, depot_alarme, posten = mein_depot_zeilen(preise)
    alarme += depot_alarme
    preise_gestern = {n: (k[-2] if len(k) >= 2 else k[-1]) for n, (_, k) in alle.items()}
    alarme_gestern += mein_depot_zeilen(preise_gestern)[1]
    neu = neue_alarme(alarme, alarme_gestern)

    # KI-Einschätzung (nur mit ANTHROPIC_API_KEY)
    from dashboard import NAMEN
    from ki_signale import ki_auswertung
    for info in infos:
        info["voll"] = NAMEN.get(info["name"], info["name"])
    ki = ki_auswertung(infos, set(MEIN_DEPOT), preise)
    if ki:
        for info in infos:
            info["ki"] = ki["signale"].get(info["name"])

    titel = f"KI-TRADER BÖRSEN-WÄCHTER – {date.today().strftime('%d.%m.%Y')}"
    z = [titel, ""]
    z += [f"NEUE ALARME ({len(neu)}):"] + ([f"  ! {a}" for a in neu] or ["  keine"])
    bestehend = [a for a in alarme if a not in neu]
    z += ["", f"WEITER BESTEHENDE HINWEISE ({len(bestehend)}):"] + (
        [f"  - {a}" for a in bestehend] or ["  keine"])
    if ki:
        z += ["", f"KI-EINSCHÄTZUNG ({ki.get('anbieter', 'KI')} – KI-Meinung, keine Anlageberatung):"]
        if ki.get("fehler"):
            z.append(f"  nicht verfügbar: {ki['fehler']}")
        else:
            z.append(f"  Fazit: {ki['fazit']}")
            for n, sg in ki["signale"].items():
                z.append(f"  {n:6} {sg['signal'].upper():9} (Sicherheit {sg['sicherheit']}): {sg['begruendung']}")
            k = ki["kontrolle"]
            if k and k["werte"]:
                z.append(f"  Erfolgskontrolle seit {k['seit']}: Signale folgen {k['ki']:.2f} € "
                         f"vs. Halten {k['halten']:.2f} € (Einsatz {k['einsatz']:.0f} €, {k['wechsel']} Wechsel)")
    z += ["", "ÜBERBLICK (Kurse in €):"] + ueberblick + fehler
    z += ["", "MEIN DEPOT:"] + depot
    if STRATEGIE_WERTE and all(n in alle for n in STRATEGIE_WERTE):
        z += ["", "STRATEGIE-TEST (Spielgeld, nur zur Beobachtung):"]
        z += strategie_test(alle, broker or neuer_broker(),
                            einstellungen or Einstellungen.laden())
    z += ["", "Erklärung: Ø50/Ø200 = Durchschnittskurs der letzten 50/200 Tage. "
              "RSI unter 30 = stark gefallen, über 70 = stark gestiegen.",
          "Das ist keine Anlageberatung. Du entscheidest selbst."]
    dash = {"datum": date.today().strftime("%d.%m.%Y"), "zeit": uhrzeit_berlin(),
            "alarme": alarme, "alarme_neu": neu, "aktien": infos,
            "ki": ({k: v for k, v in ki.items() if k != "signale"} if ki else None),
            "fehler": [f.strip() for f in fehler], "depot": posten,
            "depot_verbergen": IN_DER_CLOUD,
            "depot_tresor": depot_tresor(posten, alarme, neu),
            "strategie": dict(STRATEGIE_INFO)}
    return z, alarme, dash


def depot_tresor(posten: list[dict], alarme: list[str], neu: list[str]) -> dict | None:
    """In der Cloud: Depot mit Passwort verschlüsselt für die öffentliche Seite."""
    if not (IN_DER_CLOUD and DEPOT_PASSWORT and posten):
        return None
    from dashboard import NAMEN
    from tresor import verschluesseln
    posten = [dict(p, voll=NAMEN.get(p["name"], p["name"])) for p in posten]
    depot_alarme = [{"text": a, "neu": a in neu} for a in alarme if a.startswith("MEIN DEPOT")]
    return verschluesseln({"posten": posten, "alarme": depot_alarme}, DEPOT_PASSWORT)


def uhrzeit_berlin() -> str:
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).strftime("%H:%M")
    except Exception:  # noqa: BLE001  (Windows ohne tzdata)
        return datetime.now().strftime("%H:%M")


def sende_push(dash: dict) -> None:
    ki_aenderungen = ((dash.get("ki") or {}).get("kontrolle") or {}).get("geaendert") or []
    if not NTFY_THEMA or (PUSH_NUR_BEI_ALARM and not dash["alarme_neu"] and not ki_aenderungen):
        return
    import requests
    titel, text = push_text(dash)
    try:
        requests.post(f"https://ntfy.sh/{NTFY_THEMA}", data=text.encode("utf-8"), timeout=15,
                      headers={"Title": titel,
                               "Priority": "high" if (dash["alarme_neu"] or ki_aenderungen) else "default",
                               "Tags": "warning" if dash["alarme_neu"] else "chart_with_upwards_trend"})
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
    zeige_alarmfenster(dash["alarme_neu"])


if __name__ == "__main__":
    main()
