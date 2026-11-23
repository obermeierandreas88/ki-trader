"""
fusion_broker.py – Broker-Modul für den KI-Trader (Bitpanda Fusion API)

Funktionen
----------
- PaperBroker:  simuliert Orders (Standard, kein echtes Geld)
- FusionBroker: sendet echte Orders an die Bitpanda Fusion API
- RiskManager:  Budget pro Trade, Budget pro Tag, max. Tagesverlust,
                max. Positionen, Whitelist, Mindest-Ordergröße (Gebühren)
- KillSwitch:   Not-Aus per Datei "STOP", Umgebungsvariable oder automatisch
                bei Überschreiten des Tagesverlusts

Nutzung
-------
    from fusion_broker import BrokerConfig, create_broker, Signal

    cfg = BrokerConfig(live=False)          # live=True erst nach Papiertest!
    broker = create_broker(cfg)
    result = broker.execute(Signal(symbol="AAPL", side="Buy", eur_amount=50, price=180.0))
    print(result)

Live schalten
-------------
    export FUSION_API_KEY="dein-key"        # niemals in den Code schreiben
    export KI_TRADER_LIVE=1                 # zweite Sicherung neben live=True

Not-Aus
-------
    touch STOP                              # Datei im Arbeitsordner anlegen
    # oder: export KI_TRADER_KILL=1
    # Wieder freigeben: Datei löschen und state-Datei prüfen

WICHTIG: Pfade der Endpunkte außer "Create order" und das Format der
Aktien-Paare (z. B. "AAPL-EUR") sind aus der Doku nicht eindeutig belegt.
Vor dem Live-Betrieb in https://docs.fusion.bitpanda.com/ prüfen und unten
in ENDPOINTS bzw. BrokerConfig.pair_format anpassen.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger("ki_trader.broker")

# ---------------------------------------------------------------------------
# API-Endpunkte
# ---------------------------------------------------------------------------
BASE_URL = "https://api.fusion.bitpanda.com"
ENDPOINTS = {
    "create_order": "/v1/account/orders",            # laut Doku bestätigt
    "get_order": "/v1/account/orders/{order_id}",    # PRÜFEN
    "cancel_order": "/v1/account/orders/{order_id}", # PRÜFEN (DELETE)
    "balances": "/v1/account/balances",              # PRÜFEN
}


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
@dataclass
class BrokerConfig:
    live: bool = False                      # False = Papiertrading
    max_eur_per_trade: float = 100.0
    max_eur_per_day: float = 500.0          # Summe aller Käufe pro Tag
    max_daily_loss_eur: float = 50.0        # realisierter Verlust -> Not-Aus
    max_open_positions: int = 5
    max_trades_per_day: int = 10
    min_order_eur: float = 50.0             # bei 1 € Gebühr = max. 2 % Kosten
    fee_per_trade_eur: float = 1.0
    max_price_deviation_pct: float = 2.0    # Limit-Preis max. x % vom Signalpreis
    allowed_symbols: Optional[set[str]] = None   # None = alle erlaubt
    pair_format: str = "{symbol}-EUR"       # PRÜFEN für Aktien
    order_type: str = "Limit"               # Limit schützt vor Slippage
    limit_buffer_pct: float = 0.3           # Kauf leicht über / Verkauf unter Kurs
    time_in_force: str = "GTC"
    state_file: Path = Path("broker_state.json")
    kill_file: Path = Path("STOP")
    request_timeout_s: float = 10.0
    paper_start_cash_eur: float = 1000.0


@dataclass
class Signal:
    symbol: str          # z. B. "AAPL"
    side: str            # "Buy" oder "Sell"
    eur_amount: float    # gewünschter Ordernwert in EUR (bei Sell: None = alles)
    price: float         # aktueller Kurs laut Bot
    reason: str = ""     # Begründung der KI, wird geloggt


@dataclass
class OrderResult:
    accepted: bool
    message: str
    order_id: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    quantity: float = 0.0
    price: float = 0.0
    paper: bool = True
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Zustand (wird zwischen Neustarts gespeichert)
# ---------------------------------------------------------------------------
@dataclass
class Position:
    quantity: float = 0.0
    avg_price: float = 0.0


@dataclass
class BrokerState:
    day: str = field(default_factory=lambda: date.today().isoformat())
    spent_today_eur: float = 0.0
    trades_today: int = 0
    realized_pnl_today_eur: float = 0.0
    killed: bool = False
    kill_reason: str = ""
    positions: dict[str, Position] = field(default_factory=dict)
    paper_cash_eur: Optional[float] = None

    @classmethod
    def load(cls, path: Path) -> "BrokerState":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        data["positions"] = {k: Position(**v) for k, v in data.get("positions", {}).items()}
        return cls(**data)

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)  # atomar, damit die Datei nie halb geschrieben ist

    def roll_day(self) -> None:
        today = date.today().isoformat()
        if self.day != today:
            self.day = today
            self.spent_today_eur = 0.0
            self.trades_today = 0
            self.realized_pnl_today_eur = 0.0
            # Ein automatischer Not-Aus bleibt bewusst aktiv, bis du ihn löst.


# ---------------------------------------------------------------------------
# Not-Aus
# ---------------------------------------------------------------------------
class KillSwitch:
    def __init__(self, cfg: BrokerConfig, state: BrokerState):
        self.cfg, self.state = cfg, state

    def active(self) -> tuple[bool, str]:
        if os.getenv("KI_TRADER_KILL") == "1":
            return True, "Umgebungsvariable KI_TRADER_KILL=1"
        if self.cfg.kill_file.exists():
            return True, f"Datei '{self.cfg.kill_file}' vorhanden"
        if self.state.killed:
            return True, self.state.kill_reason or "automatischer Not-Aus"
        return False, ""

    def trigger(self, reason: str) -> None:
        self.state.killed = True
        self.state.kill_reason = reason
        log.critical("NOT-AUS ausgelöst: %s", reason)

    def reset(self) -> None:
        """Manuell aufrufen, nachdem du die Ursache geprüft hast."""
        self.state.killed = False
        self.state.kill_reason = ""


# ---------------------------------------------------------------------------
# Risikoprüfung
# ---------------------------------------------------------------------------
class RiskManager:
    def __init__(self, cfg: BrokerConfig, state: BrokerState, kill: KillSwitch):
        self.cfg, self.state, self.kill = cfg, state, kill

    def check(self, sig: Signal) -> tuple[bool, str, float]:
        """Gibt (erlaubt, Grund, Menge) zurück."""
        c, s = self.cfg, self.state

        on, why = self.kill.active()
        if on:
            return False, f"Not-Aus aktiv: {why}", 0.0
        if sig.side not in ("Buy", "Sell"):
            return False, f"Unbekannte Seite '{sig.side}'", 0.0
        if sig.price is None or sig.price <= 0:
            return False, "Ungültiger Kurs", 0.0
        if c.allowed_symbols is not None and sig.symbol not in c.allowed_symbols:
            return False, f"{sig.symbol} steht nicht auf der Whitelist", 0.0
        if s.trades_today >= c.max_trades_per_day:
            return False, f"Tageslimit von {c.max_trades_per_day} Trades erreicht", 0.0

        pos = s.positions.get(sig.symbol, Position())

        if sig.side == "Buy":
            amount = min(sig.eur_amount, c.max_eur_per_trade)
            if amount < c.min_order_eur:
                return False, (f"Order {amount:.2f} € unter Mindestgröße "
                               f"{c.min_order_eur:.2f} € (Gebühr wäre zu hoch)"), 0.0
            if s.spent_today_eur + amount > c.max_eur_per_day:
                rest = c.max_eur_per_day - s.spent_today_eur
                return False, f"Tagesbudget erschöpft (noch {rest:.2f} €)", 0.0
            open_positions = sum(1 for p in s.positions.values() if p.quantity > 0)
            if pos.quantity <= 0 and open_positions >= c.max_open_positions:
                return False, f"Max. {c.max_open_positions} offene Positionen erreicht", 0.0
            # Menge mit Limit-Aufschlag rechnen, damit das Budget nie überschritten wird
            eff_price = sig.price * (1 + c.limit_buffer_pct / 100) if c.order_type == "Limit" else sig.price
            qty = int(amount / eff_price * 1e6) / 1e6   # abrunden
            return True, "ok", qty

        # Sell
        if pos.quantity <= 0:
            return False, f"Keine Position in {sig.symbol} zum Verkaufen", 0.0
        if sig.eur_amount:
            qty = min(pos.quantity, round(sig.eur_amount / sig.price, 6))
        else:
            qty = pos.quantity
        if qty * sig.price < c.min_order_eur and qty < pos.quantity:
            return False, "Teilverkauf unter Mindestgröße", 0.0
        return True, "ok", qty

    def record_fill(self, symbol: str, side: str, qty: float, price: float) -> None:
        c, s = self.cfg, self.state
        pos = s.positions.setdefault(symbol, Position())
        s.trades_today += 1
        if side == "Buy":
            cost = qty * price
            new_qty = pos.quantity + qty
            pos.avg_price = (pos.avg_price * pos.quantity + cost) / new_qty
            pos.quantity = new_qty
            s.spent_today_eur += cost
            s.realized_pnl_today_eur -= c.fee_per_trade_eur
        else:
            pnl = (price - pos.avg_price) * qty - c.fee_per_trade_eur
            s.realized_pnl_today_eur += pnl
            pos.quantity = round(pos.quantity - qty, 6)
            if pos.quantity <= 0:
                s.positions.pop(symbol, None)
        if s.realized_pnl_today_eur <= -c.max_daily_loss_eur:
            self.kill.trigger(
                f"Tagesverlust {s.realized_pnl_today_eur:.2f} € überschreitet "
                f"Limit von {c.max_daily_loss_eur:.2f} €")


# ---------------------------------------------------------------------------
# API-Client
# ---------------------------------------------------------------------------
class FusionAPIError(Exception):
    pass


class FusionClient:
    def __init__(self, api_key: str, timeout: float = 10.0,
                 session: Optional[requests.Session] = None):
        if not api_key:
            raise ValueError("FUSION_API_KEY fehlt")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": api_key,
                                     "Content-Type": "application/json"})

    def _request(self, method: str, path: str, retries: int = 0, **kw) -> dict:
        url = BASE_URL + path
        for attempt in range(retries + 1):
            try:
                r = self.session.request(method, url, timeout=self.timeout, **kw)
            except requests.RequestException as e:
                if attempt < retries:
                    time.sleep(2 ** attempt)
                    continue
                raise FusionAPIError(f"Netzwerkfehler: {e}") from e
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(2 ** attempt)
                continue
            if r.status_code >= 400:
                raise FusionAPIError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r.json() if r.content else {}
        raise FusionAPIError("Unerreichbar")

    def create_order(self, payload: dict) -> dict:
        # KEINE automatische Wiederholung: sonst droht eine doppelte Order.
        return self._request("POST", ENDPOINTS["create_order"], json=payload)

    def get_order(self, order_id: str) -> dict:
        return self._request("GET", ENDPOINTS["get_order"].format(order_id=order_id), retries=3)

    def cancel_order(self, order_id: str) -> dict:
        return self._request("DELETE", ENDPOINTS["cancel_order"].format(order_id=order_id), retries=2)

    def balances(self) -> dict:
        return self._request("GET", ENDPOINTS["balances"], retries=3)


# ---------------------------------------------------------------------------
# Broker
# ---------------------------------------------------------------------------
class BaseBroker:
    paper = True

    def __init__(self, cfg: BrokerConfig):
        self.cfg = cfg
        self.state = BrokerState.load(cfg.state_file)
        self.kill = KillSwitch(cfg, self.state)
        self.risk = RiskManager(cfg, self.state, self.kill)
        self._lock = threading.Lock()

    def execute(self, sig: Signal) -> OrderResult:
        with self._lock:
            self.state.roll_day()
            ok, why, qty = self.risk.check(sig)
            if not ok:
                log.warning("ABGELEHNT %s %s: %s", sig.side, sig.symbol, why)
                return OrderResult(False, why, symbol=sig.symbol, side=sig.side, paper=self.paper)
            try:
                result = self._send(sig, qty)
            except Exception as e:  # noqa: BLE001
                log.error("Order fehlgeschlagen %s %s: %s", sig.side, sig.symbol, e)
                result = OrderResult(False, f"Fehler: {e}", symbol=sig.symbol,
                                     side=sig.side, paper=self.paper)
            if result.accepted:
                self.risk.record_fill(sig.symbol, sig.side, result.quantity, result.price)
                log.info("%s %s %s x%.6f @ %.2f € | Grund: %s",
                         "PAPIER" if self.paper else "LIVE", sig.side, sig.symbol,
                         result.quantity, result.price, sig.reason)
            self.state.save(self.cfg.state_file)
            return result

    def emergency_stop(self, reason: str = "manuell") -> None:
        with self._lock:
            self.kill.trigger(reason)
            self.state.save(self.cfg.state_file)

    def status(self) -> dict:
        on, why = self.kill.active()
        return {"modus": "papier" if self.paper else "LIVE", "not_aus": on,
                "not_aus_grund": why, **asdict(self.state)}

    def _limit_price(self, sig: Signal) -> float:
        buf = self.cfg.limit_buffer_pct / 100
        p = sig.price * (1 + buf) if sig.side == "Buy" else sig.price * (1 - buf)
        return round(p, 2)

    def _send(self, sig: Signal, qty: float) -> OrderResult:
        raise NotImplementedError


class PaperBroker(BaseBroker):
    paper = True

    def __init__(self, cfg: BrokerConfig):
        super().__init__(cfg)
        if self.state.paper_cash_eur is None:
            self.state.paper_cash_eur = cfg.paper_start_cash_eur

    def _send(self, sig: Signal, qty: float) -> OrderResult:
        price = self._limit_price(sig) if self.cfg.order_type == "Limit" else sig.price
        value = qty * price
        if sig.side == "Buy":
            if value + self.cfg.fee_per_trade_eur > self.state.paper_cash_eur:
                return OrderResult(False, "Nicht genug Papier-Guthaben", symbol=sig.symbol,
                                   side=sig.side)
            self.state.paper_cash_eur -= value + self.cfg.fee_per_trade_eur
        else:
            self.state.paper_cash_eur += value - self.cfg.fee_per_trade_eur
        return OrderResult(True, "Papier-Order ausgeführt", order_id=f"paper-{uuid.uuid4().hex[:8]}",
                           symbol=sig.symbol, side=sig.side, quantity=qty, price=price)


class FusionBroker(BaseBroker):
    paper = False

    def __init__(self, cfg: BrokerConfig, client: Optional[FusionClient] = None):
        super().__init__(cfg)
        self.client = client or FusionClient(os.getenv("FUSION_API_KEY", ""),
                                             cfg.request_timeout_s)

    def _send(self, sig: Signal, qty: float) -> OrderResult:
        payload = {
            "pair": self.cfg.pair_format.format(symbol=sig.symbol),
            "side": sig.side,
            "type": self.cfg.order_type,
            "quantity": f"{qty:.6f}",
        }
        price = sig.price
        if self.cfg.order_type == "Limit":
            price = self._limit_price(sig)
            dev = abs(price - sig.price) / sig.price * 100
            if dev > self.cfg.max_price_deviation_pct:
                return OrderResult(False, f"Limit weicht {dev:.1f}% ab", symbol=sig.symbol,
                                   side=sig.side, paper=False)
            payload["limitPrice"] = f"{price:.2f}"
            payload["timeInForce"] = self.cfg.time_in_force

        resp = self.client.create_order(payload)
        order_id = resp.get("id")
        status = str(resp.get("status", "")).lower()
        if status in ("rejected", "cancelled", "canceled", "failed"):
            return OrderResult(False, f"Order abgelehnt: {status}", order_id=order_id,
                               symbol=sig.symbol, side=sig.side, paper=False, raw=resp)

        # Hinweis: Die Order wird asynchron ausgeführt. Hier wird sie als
        # angenommen gezählt (konservativ fürs Tagesbudget). Mit
        # sync_order() kannst du später den tatsächlichen Fill abfragen.
        filled_price = float(resp.get("filledAveragePrice") or price)
        return OrderResult(True, f"Order angenommen ({status or 'new'})", order_id=order_id,
                           symbol=sig.symbol, side=sig.side, quantity=qty,
                           price=filled_price, paper=False, raw=resp)

    def sync_order(self, order_id: str) -> dict:
        return self.client.get_order(order_id)

    def emergency_stop(self, reason: str = "manuell", cancel_open: list[str] | None = None) -> None:
        super().emergency_stop(reason)
        for oid in cancel_open or []:
            try:
                self.client.cancel_order(oid)
                log.warning("Offene Order %s storniert", oid)
            except FusionAPIError as e:
                log.error("Storno von %s fehlgeschlagen: %s", oid, e)


def create_broker(cfg: BrokerConfig) -> BaseBroker:
    """Live nur, wenn live=True UND KI_TRADER_LIVE=1 gesetzt ist."""
    if cfg.live and os.getenv("KI_TRADER_LIVE") == "1":
        log.warning("LIVE-MODUS aktiv – echte Orders mit echtem Geld")
        return FusionBroker(cfg)
    if cfg.live:
        log.warning("live=True, aber KI_TRADER_LIVE fehlt – Papiermodus bleibt aktiv")
    return PaperBroker(cfg)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    b = create_broker(BrokerConfig(allowed_symbols={"AAPL", "MSFT"}))
    print(b.execute(Signal("AAPL", "Buy", 80, 180.0, reason="Demo")))
    print(b.execute(Signal("TSLA", "Buy", 80, 250.0, reason="Demo")))
    print(json.dumps(b.status(), indent=2, default=str))
