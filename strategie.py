"""
strategie.py – Die Handelsregeln des KI-Traders (Tages-Strategie)

Kaufen, wenn:
  - der kurze Durchschnitt über dem langen liegt (Aufwärtstrend)
  - der Kurs über dem kurzen Durchschnitt liegt (Trend ist intakt)
  - der RSI nicht über der Grenze liegt (nicht überhitzt)

Verkaufen, wenn:
  - der Kurs mehr als STOP % unter den höchsten Kurs seit dem Kauf fällt
    (mitlaufender Stop-Loss: sichert Gewinne, begrenzt Verluste)
  - oder der Trend dreht (kurzer Durchschnitt fällt unter den langen)
  - oder ein Gewinnziel erreicht ist (falls eingestellt)

Die Zahlen stehen in "Einstellungen" und werden von backtest.py optimiert.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

EINSTELLUNGEN_DATEI = Path("beste_einstellungen.json")


@dataclass(frozen=True)
class Einstellungen:
    kurz: int = 50              # kurzer Durchschnitt in Tagen
    lang: int = 200             # langer Durchschnitt in Tagen
    stop_prozent: float = 12.0  # mitlaufender Stop-Loss
    ziel_prozent: float = 0.0   # 0 = kein festes Gewinnziel
    rsi_max: float = 70.0       # 100 = RSI wird ignoriert

    def text(self) -> str:
        ziel = f"{self.ziel_prozent:.0f} %" if self.ziel_prozent else "keins"
        rsi = f"{self.rsi_max:.0f}" if self.rsi_max < 100 else "aus"
        return (f"Ø{self.kurz}/Ø{self.lang} Tage, Stop {self.stop_prozent:.0f} % mitlaufend, "
                f"Ziel {ziel}, RSI-Grenze {rsi}")

    def speichern(self, datei: Path = EINSTELLUNGEN_DATEI) -> None:
        datei.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def laden(cls, datei: Path = EINSTELLUNGEN_DATEI) -> "Einstellungen":
        if datei.exists():
            return cls(**json.loads(datei.read_text(encoding="utf-8")))
        return cls()


# ---------------------------------------------------------------------------
# Kennzahlen (schnell berechnet, für jeden Tag auf einmal)
# ---------------------------------------------------------------------------
def gleitender_durchschnitt(werte: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(werte)
    summe = 0.0
    for i, w in enumerate(werte):
        summe += w
        if i >= n:
            summe -= werte[i - n]
        if i >= n - 1:
            out[i] = summe / n
    return out


def rsi_reihe(werte: list[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(werte)
    gew = verl = 0.0
    diffs = [0.0] + [b - a for a, b in zip(werte, werte[1:])]
    for i in range(1, len(werte)):
        d = diffs[i]
        gew += max(d, 0)
        verl += max(-d, 0)
        if i > n:
            alt = diffs[i - n]
            gew -= max(alt, 0)
            verl -= max(-alt, 0)
        if i >= n:
            out[i] = 100.0 if verl <= 1e-12 else 100 - 100 / (1 + gew / verl)
    return out


class Kennzahlen:
    """Alle Kennzahlen einer Aktie, einmal berechnet."""

    def __init__(self, kurse: list[float], laengen=(20, 50, 100, 150, 200)):
        self.kurse = kurse
        self.sma = {n: gleitender_durchschnitt(kurse, n) for n in laengen}
        self.rsi = rsi_reihe(kurse)

    def ma(self, n: int, i: int):
        if n not in self.sma:
            self.sma[n] = gleitender_durchschnitt(self.kurse, n)
        return self.sma[n][i]


# ---------------------------------------------------------------------------
# Entscheidung
# ---------------------------------------------------------------------------
def entscheide(k: Kennzahlen, i: int, e: Einstellungen,
               kaufpreis: float | None = None, hoechst: float | None = None) -> tuple[str, str]:
    """Gibt ("Buy" / "Sell" / "Halten", Begründung) für Tag i zurück."""
    preis = k.kurse[i]
    kurz, lang, r = k.ma(e.kurz, i), k.ma(e.lang, i), k.rsi[i]
    if kurz is None or lang is None or r is None:
        return "Halten", "zu wenig Kursdaten"
    info = f"Kurs {preis:.2f} €, Ø{e.kurz} {kurz:.2f}, Ø{e.lang} {lang:.2f}, RSI {r:.0f}"
    trend_hoch = kurz > lang

    if kaufpreis:
        hoechst = max(hoechst or kaufpreis, preis)
        seit_kauf = (preis / kaufpreis - 1) * 100
        vom_hoch = (preis / hoechst - 1) * 100
        if vom_hoch <= -e.stop_prozent:
            return "Sell", f"Stop: {vom_hoch:.1f} % unter Höchststand ({seit_kauf:+.1f} % seit Kauf) | {info}"
        if e.ziel_prozent and seit_kauf >= e.ziel_prozent:
            return "Sell", f"Gewinnziel erreicht ({seit_kauf:+.1f} %) | {info}"
        if not trend_hoch:
            return "Sell", f"Trend gedreht ({seit_kauf:+.1f} % seit Kauf) | {info}"
        return "Halten", f"Position halten ({seit_kauf:+.1f} % seit Kauf) | {info}"

    if not trend_hoch:
        return "Halten", f"Abwärtstrend, nicht kaufen | {info}"
    if preis < kurz:
        return "Halten", f"Kurs unter Ø{e.kurz}, abwarten | {info}"
    if r > e.rsi_max:
        return "Halten", f"überhitzt (RSI {r:.0f}), abwarten | {info}"
    return "Buy", f"Aufwärtstrend intakt | {info}"
