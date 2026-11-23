"""
backtest.py – Rückblick-Test für den Tages-Bot

    py backtest.py              -> aktuelle Einstellungen über die letzten 2 Jahre testen
    py backtest.py optimieren   -> beste Einstellungen suchen (sauber getrennt):
                                   1. TRAINING: Regeln an den älteren Jahren einstellen
                                   2. TEST:     beste Regeln an den letzten 2 Jahren prüfen,
                                                die beim Einstellen NICHT benutzt wurden

Gerechnet wird wie im echten Bot: 200 € pro Kauf, 1 € Gebühr pro Trade,
0,3 % schlechterer Preis durch die Limit-Order.
Ein gutes Ergebnis in der Vergangenheit ist keine Garantie für die Zukunft.
"""

from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from strategie import Einstellungen, Kennzahlen, entscheide

GEBUEHR = 1.0
PUFFER = 0.003
STARTKAPITAL = 1000.0
BETRAG = 200.0
TESTTAGE = 504          # ca. 2 Börsenjahre


@dataclass
class Ergebnis:
    bot: float
    halten: float
    trades: int
    gewinner: int
    verlierer: int
    gebuehren: float
    max_rueckgang: float
    protokoll: list[str] = field(default_factory=list)
    offen: list[str] = field(default_factory=list)

    @property
    def vorsprung(self) -> float:
        return self.bot - self.halten


class Aktie:
    def __init__(self, reihe: dict[date, float]):
        self.daten = sorted(reihe)
        self.kurse = [reihe[d] for d in self.daten]
        self.index = {d: i for i, d in enumerate(self.daten)}
        self.k = Kennzahlen(self.kurse)


def simulieren(aktien: dict[str, Aktie], tage: list[date], e: Einstellungen,
               mit_protokoll: bool = False) -> Ergebnis:
    geld = STARTKAPITAL
    pos: dict[str, list[float]] = {}  # name -> [stück, kaufpreis, höchstkurs]
    letzte: dict[str, float] = {}
    start: dict[str, float] = {}
    trades = gew = verl = 0
    protokoll: list[str] = []
    hoch, schlimmst = STARTKAPITAL, 0.0

    for tag in tage:
        for name, a in aktien.items():
            i = a.index.get(tag)
            if i is None:
                continue
            preis = a.kurse[i]
            letzte[name] = preis
            start.setdefault(name, preis)
            p = pos.get(name)
            aktion, grund = entscheide(a.k, i, e, p[1] if p else None, p[2] if p else None)
            if p:
                p[2] = max(p[2], preis)
            if aktion == "Buy" and geld >= BETRAG + GEBUEHR:
                kp = preis * (1 + PUFFER)
                pos[name] = [BETRAG / kp, kp, preis]
                geld -= BETRAG + GEBUEHR
                trades += 1
                if mit_protokoll:
                    protokoll.append(f"{tag} KAUF    {name:5} @ {kp:8.2f} €")
            elif aktion == "Sell" and p:
                vp = preis * (1 - PUFFER)
                geld += p[0] * vp - GEBUEHR
                erg = p[0] * (vp - p[1]) - 2 * GEBUEHR
                gew += erg > 0
                verl += erg <= 0
                trades += 1
                del pos[name]
                if mit_protokoll:
                    protokoll.append(f"{tag} VERKAUF {name:5} @ {vp:8.2f} €  {erg:+8.2f} €  "
                                     f"{grund.split('|')[0].strip()}")
        wert = geld + sum(p[0] * letzte[n] for n, p in pos.items())
        hoch = max(hoch, wert)
        schlimmst = min(schlimmst, wert / hoch - 1)

    anteil = STARTKAPITAL / len(aktien)
    halten = sum((anteil - GEBUEHR) / start[n] * letzte[n] for n in start)
    halten += anteil * (len(aktien) - len(start))
    return Ergebnis(wert, halten, trades, gew, verl, trades * GEBUEHR, schlimmst * 100,
                    protokoll, list(pos))


def kandidaten() -> list[Einstellungen]:
    out = []
    for kurz, lang, stop, ziel, rsi in itertools.product(
            [20, 50], [100, 150, 200], [8, 12, 20], [0, 40], [70, 100]):
        if kurz < lang:
            out.append(Einstellungen(kurz, lang, stop, ziel, rsi))
    return out


def prozent(w: float) -> str:
    return f"{w:8.2f} € ({(w / STARTKAPITAL - 1) * 100:+6.1f} %)"


def bericht(titel: str, von: date, bis: date, e: Einstellungen, r: Ergebnis) -> list[str]:
    return [titel, f"Zeitraum: {von} bis {bis}", f"Regeln: {e.text()}", "",
            f"Bot:               {prozent(r.bot)}",
            f"Kaufen und Halten: {prozent(r.halten)}",
            f"Unterschied: {r.vorsprung:+.2f} €  -> "
            + ("BOT IST BESSER" if r.vorsprung > 0 else "Kaufen und Halten ist besser"),
            f"Trades: {r.trades} (Gewinner {r.gewinner}, Verlierer {r.verlierer}), "
            f"Gebühren {r.gebuehren:.0f} €, größter Rückgang {r.max_rueckgang:.1f} %"]


def zeitraeume(aktien: dict[str, Aktie]) -> tuple[list[date], list[date]]:
    alle = sorted(set().union(*[set(a.daten) for a in aktien.values()]))
    # 210 Tage Vorlauf, damit der 200-Tage-Durchschnitt existiert
    return alle[210:-TESTTAGE], alle[-TESTTAGE:]


def optimieren(aktien: dict[str, Aktie]) -> list[str]:
    training, test = zeitraeume(aktien)
    if len(training) < 250:
        return ["Zu wenig ältere Kursdaten für ein sauberes Training."]
    alle = []
    for e in kandidaten():
        alle.append((simulieren(aktien, training, e), e))
    alle.sort(key=lambda x: x[0].vorsprung, reverse=True)

    z = [f"OPTIMIERUNG: {len(alle)} Regel-Varianten getestet",
         f"Training: {training[0]} bis {training[-1]}   |   Test: {test[0]} bis {test[-1]}", "",
         "Die 5 besten im TRAINING und wie sie sich danach im TEST geschlagen haben:",
         "  (Vorsprung = Bot minus Kaufen und Halten)", ""]
    for r_train, e in alle[:5]:
        r_test = simulieren(aktien, test, e)
        z.append(f"  {e.text()}")
        z.append(f"     Training {r_train.vorsprung:+8.2f} €   ->   Test {r_test.vorsprung:+8.2f} € "
                 f"({r_test.trades} Trades)")
    bester_train, beste = alle[0]
    r_test = simulieren(aktien, test, beste, mit_protokoll=True)
    beste.speichern()
    if r_test.vorsprung > 0 and bester_train.vorsprung > 0:
        fazit = ("FAZIT: Die Regeln waren im Training UND im Test besser als Kaufen und Halten. "
                 "Trotzdem erst einige Wochen im Ratgeber-Modus beobachten.")
    elif r_test.vorsprung > 0:
        fazit = ("FAZIT: Im Test besser, im Training aber nicht – das kann Zufall sein. "
                 "Nicht mit echtem Geld handeln.")
    else:
        fazit = ("FAZIT: Keine Variante schlägt Kaufen und Halten zuverlässig. "
                 "Bot nur als Ratgeber nutzen, nicht mit echtem Geld handeln lassen.")
    z += ["", *bericht("ERGEBNIS DER BESTEN REGELN IM TEST (unbekannte Daten):",
                       test[0], test[-1], beste, r_test), "", fazit, "",
          f"Gespeichert in {Path('beste_einstellungen.json')} – der Bot nutzt sie ab jetzt.",
          "", "Trades im Test:"] + (r_test.protokoll or ["(keine)"])
    return z


# ---------------------------------------------------------------------------
# Kursdaten
# ---------------------------------------------------------------------------
def lade_daten(jahre: int = 10) -> dict[str, Aktie]:
    import yfinance as yf
    from bot import AKTIEN, lade_wechselkurs
    fx = lade_wechselkurs()
    aktien = {}
    for name, k in AKTIEN.items():
        h = yf.Ticker(k).history(period=f"{jahre}y", interval="1d", auto_adjust=True)
        h = h["Close"].dropna()
        f = 1.0 if k.endswith(".DE") else 1 / fx
        aktien[name] = Aktie({i.date(): float(v) * f for i, v in h.items()})
    return aktien


def main():
    modus_opt = len(sys.argv) > 1 and sys.argv[1].lower().startswith("opt")
    print("Lade 10 Jahre Kursdaten … (dauert etwa eine halbe Minute)")
    aktien = lade_daten()
    if modus_opt:
        print("Teste alle Regel-Varianten … (dauert ein bis zwei Minuten)")
        zeilen = optimieren(aktien)
        datei = Path("berichte") / "optimierung.txt"
        kurz = zeilen[:28]
    else:
        e = Einstellungen.laden()
        _, test = zeitraeume(aktien)
        r = simulieren(aktien, test, e, mit_protokoll=True)
        zeilen = bericht("RÜCKBLICK-TEST TAGES-BOT", test[0], test[-1], e, r)
        zeilen += ["", "Alle Trades:"] + (r.protokoll or ["(keine)"])
        datei = Path("berichte") / "backtest_tag.txt"
        kurz = zeilen[:8]
    datei.parent.mkdir(exist_ok=True)
    datei.write_text("\n".join(zeilen), encoding="utf-8")
    print("\n" + "\n".join(kurz))
    print(f"\nAusführlich in: {datei}")


if __name__ == "__main__":
    main()
