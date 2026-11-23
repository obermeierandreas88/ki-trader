"""
vergleich.py – Vergleich Bot gegen "Kaufen und Halten"

Beim ersten Lauf eines Depots werden die Startkurse gespeichert. Danach wird
bei jedem Bericht ausgerechnet, was die 1.000 € wert wären, wenn man am Start
einfach alle Aktien zu gleichen Teilen gekauft und liegen gelassen hätte.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

GEBUEHR_EUR = 1.0


def depotwert(status: dict, preise: dict[str, float]) -> float:
    """Spielgeld + aktueller Wert aller Positionen."""
    wert = status.get("paper_cash_eur") or 0.0
    for name, p in status["positions"].items():
        wert += p["quantity"] * preise.get(name, p["avg_price"])
    return wert


def halten_wert(datei: Path, preise: dict[str, float], startkapital: float,
                anteil: float | None = None) -> tuple[float, str]:
    """Gibt (Wert, Startdatum) der Kaufen-und-Halten-Strategie zurück."""
    if datei.exists():
        start = json.loads(datei.read_text(encoding="utf-8"))
    else:
        start = {"datum": date.today().isoformat(), "preise": preise}
        datei.write_text(json.dumps(start, indent=2), encoding="utf-8")
    sp = start["preise"]
    namen = [n for n in sp if n in preise]
    if not namen:
        return startkapital, start["datum"]
    rest = 0.0 if anteil is None else startkapital - anteil * len(sp)
    anteil = startkapital / len(sp) if anteil is None else anteil
    wert = rest
    for n in sp:
        if n in preise:
            wert += (anteil - GEBUEHR_EUR) / sp[n] * preise[n]
        else:
            wert += anteil  # keine aktuellen Kurse -> Startwert annehmen
    return wert, start["datum"]


def vergleich_zeilen(status: dict, preise: dict[str, float], datei: Path,
                     startkapital: float, einsatz_pro_aktie: float = 80.0) -> list[str]:
    bot = depotwert(status, preise)
    halten, seit = halten_wert(datei, preise, startkapital)
    fair, _ = halten_wert(datei, preise, startkapital, einsatz_pro_aktie)
    diff = bot - fair
    urteil = "Bot liegt VORNE" if diff > 0 else "Kaufen und Halten liegt vorne"
    pz = lambda w: f"{w:8.2f} € ({(w / startkapital - 1) * 100:+.1f} %)"  # noqa: E731
    if abs(fair - halten) < 0.01:  # Bot kann alles investieren -> nur ein Vergleich nötig
        return [
            f"VERGLEICH seit {seit}:",
            f"  Spielgeld-Depot (Bot):  {pz(bot)}",
            f"  Kaufen und Halten:      {pz(halten)}",
            f"  Unterschied: {diff:+.2f} €  -> {urteil}",
        ]
    return [
        f"Vergleich seit {seit}:",
        f"  Bot-Depot gesamt:              {pz(bot)}",
        f"  Halten, gleicher Einsatz:      {pz(fair)}",
        f"  Halten, alles investiert:      {pz(halten)}",
        f"  Fairer Vergleich: {diff:+.2f} €  -> {urteil}",
    ]
