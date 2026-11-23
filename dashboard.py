"""
dashboard.py – Erstellt die handyfreundliche Übersichtsseite (berichte/dashboard.html)
und den Text für die Push-Nachricht. Wird von bot.py benutzt.
Die Seite ist eine einzelne Datei ohne Internet-Abhängigkeiten.
"""

from __future__ import annotations

from html import escape

NAMEN = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon", "SAP": "SAP"}


def _eur(w: float) -> str:
    return f"{w:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _pz(w) -> str:
    return "–" if w is None else f"{w:+.1f} %".replace(".", ",")


def _farbe(w) -> str:
    if w is None:
        return "neutral"
    return "plus" if w > 0 else "minus" if w < 0 else "neutral"


def _sparkline(werte: list[float], breite: int = 300, hoehe: int = 56) -> str:
    if len(werte) < 2:
        return ""
    lo, hi = min(werte), max(werte)
    spanne = (hi - lo) or 1
    schritt = breite / (len(werte) - 1)
    punkte = [(i * schritt, hoehe - 4 - (w - lo) / spanne * (hoehe - 8)) for i, w in enumerate(werte)]
    linie = " ".join(f"{x:.1f},{y:.1f}" for x, y in punkte)
    flaeche = f"0,{hoehe} {linie} {breite},{hoehe}"
    klasse = "plus" if werte[-1] >= werte[0] else "minus"
    return (f'<svg class="spark {klasse}" viewBox="0 0 {breite} {hoehe}" preserveAspectRatio="none" '
            f'aria-hidden="true"><polygon points="{flaeche}"/><polyline points="{linie}"/></svg>')


def _rsi_text(r) -> str:
    if r is None:
        return "–"
    if r <= 30:
        return f"{r:.0f} · stark gefallen"
    if r >= 70:
        return f"{r:.0f} · stark gestiegen"
    return f"{r:.0f} · normal"


def _aktie_karte(a: dict) -> str:
    name = escape(a["name"])
    voll = escape(NAMEN.get(a["name"], a["name"]))
    trend = escape(a["trend"])
    trend_kl = {"plus": "plus", "minus": "minus"}.get(a["trend_stufe"], "warn")
    alarm = "".join(f'<div class="kalarm">⚠ {escape(x.split(": ", 1)[-1])}</div>' for x in a["alarme"])
    chips = "".join(
        f'<div class="chip"><span>{lbl}</span><b class="{_farbe(a[k])}">{_pz(a[k])}</b></div>'
        for lbl, k in (("Woche", "woche"), ("Monat", "monat"), ("Jahr", "jahr")))
    return f"""
<article class="karte{' hat-alarm' if a['alarme'] else ''}">
  <header>
    <div><h3>{voll}</h3><small>{name}</small></div>
    <div class="rechts"><div class="preis">{_eur(a['preis'])}</div>
      <div class="tag {_farbe(a['tag'])}">{_pz(a['tag'])} heute</div></div>
  </header>
  {_sparkline(a['verlauf'])}
  <div class="unter-spark">letzte 6 Monate</div>
  <div class="chips">{chips}</div>
  <dl>
    <div><dt>Trend</dt><dd class="{trend_kl}">{trend}</dd></div>
    <div><dt>Vom Jahreshoch</dt><dd>{_pz(a['vom_hoch'])}</dd></div>
    <div><dt>RSI</dt><dd>{_rsi_text(a['rsi'])}</dd></div>
  </dl>
  {alarm}
</article>"""


def erstelle_dashboard(d: dict) -> str:
    verbergen = d.get("depot_verbergen", False)
    alarme = [a for a in d["alarme"] if not (verbergen and a.startswith("MEIN DEPOT"))]
    if alarme:
        alarm_html = ('<section class="alarmbox"><h2>⚠ ' + str(len(alarme)) + " Alarm"
                      + ("e" if len(alarme) != 1 else "") + "</h2><ul>"
                      + "".join(f"<li>{escape(a)}</li>" for a in alarme) + "</ul></section>")
    else:
        alarm_html = '<section class="ruhig">✓ Keine Alarme – alles ruhig</section>'

    karten = "".join(_aktie_karte(a) for a in d["aktien"])
    fehler = "".join(f'<p class="fehler">{escape(f)}</p>' for f in d.get("fehler", []))

    if verbergen:
        depot_html = ('<p class="klein">Diese Seite ist öffentlich erreichbar. Deinen echten '
                      'Depotwert bekommst du deshalb nur per Push-Nachricht aufs Handy.</p>')
    elif d["depot"]:
        einsatz = sum(p["einsatz"] for p in d["depot"])
        wert = sum(p["wert"] for p in d["depot"])
        diff = (wert / einsatz - 1) * 100 if einsatz else 0
        zeilen = "".join(
            f'<tr><td>{escape(NAMEN.get(p["name"], p["name"]))}</td><td>{_eur(p["wert"])}</td>'
            f'<td class="{_farbe(p["diff"])}">{_pz(p["diff"])}</td></tr>' for p in d["depot"])
        depot_html = (f'<div class="gross {_farbe(diff)}">{_eur(wert)}</div>'
                      f'<div class="klein">Einsatz {_eur(einsatz)} · <b class="{_farbe(diff)}">'
                      f'{_pz(diff)}</b></div><table>{zeilen}</table>')
    else:
        depot_html = ('<p class="klein">Noch leer. Trag deine echten Käufe in <code>bot.py</code> '
                      'bei <code>MEIN_DEPOT</code> ein, dann siehst du hier ihren Wert.</p>')

    st = d.get("strategie") or {}
    if "bot" in st:
        vorne = st["bot"] > st["halten"]
        strat_html = (f'<p class="klein">Spielgeld-Test seit {escape(st["seit"])} '
                      f'(nur Beobachtung, keine Empfehlung)</p>'
                      f'<div class="vergleich"><div><span>Trend-Regeln</span><b>{_eur(st["bot"])}</b></div>'
                      f'<div><span>Kaufen &amp; Halten</span><b>{_eur(st["halten"])}</b></div></div>'
                      f'<p class="klein">{"Die Regeln liegen gerade vorne." if vorne else "Kaufen und Halten liegt vorne."}</p>')
    else:
        strat_html = '<p class="klein">Startet, sobald alle Kurse geladen werden konnten.</p>'

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#0f8a4b">
<meta name="robots" content="noindex, nofollow">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="KI-Trader">
<link rel="manifest" href="manifest.webmanifest">
<link rel="icon" href="icon-192.png">
<link rel="apple-touch-icon" href="icon-192.png">
<title>KI-Trader · {escape(d['datum'])}</title>
<style>
:root {{
  --bg:#f4f5f7; --karte:#fff; --text:#16181d; --leise:#6b7280; --linie:#e5e7eb;
  --plus:#0f8a4b; --minus:#c62828; --plus-bg:#e7f6ee; --minus-bg:#fdecec; --warn:#b45309; --warn-bg:#fff4e0;
}}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#0e1014; --karte:#181b21; --text:#eef0f3; --leise:#9aa3af; --linie:#2a2f37;
    --plus:#3ccf7e; --minus:#ff6b6b; --plus-bg:#12301f; --minus-bg:#3a1717; --warn:#f6b04b; --warn-bg:#3a2a10; }}
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text);
  font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width:560px; margin:0 auto; padding:max(16px, env(safe-area-inset-top)) 14px 40px; }}
.kopf {{ display:flex; justify-content:space-between; align-items:baseline; margin:4px 2px 14px; }}
.kopf h1 {{ font-size:22px; margin:0; letter-spacing:-.01em; }}
.kopf span {{ color:var(--leise); font-size:13px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.06em; color:var(--leise); margin:22px 2px 8px; }}
.alarmbox {{ background:var(--warn-bg); color:var(--warn); border-radius:14px; padding:12px 14px; }}
.alarmbox h2 {{ color:inherit; margin:0 0 6px; font-size:15px; text-transform:none; letter-spacing:0; }}
.alarmbox ul {{ margin:0; padding-left:18px; }}
.alarmbox li {{ margin:3px 0; color:var(--text); }}
.ruhig {{ background:var(--plus-bg); color:var(--plus); border-radius:14px; padding:12px 14px; font-weight:600; }}
.karte {{ background:var(--karte); border-radius:16px; padding:14px; margin-bottom:12px;
  box-shadow:0 1px 2px rgba(0,0,0,.06); border:1px solid var(--linie); }}
.karte.hat-alarm {{ border-color:var(--warn); }}
.karte header {{ display:flex; justify-content:space-between; gap:10px; }}
.karte h3 {{ margin:0; font-size:17px; }}
.karte small {{ color:var(--leise); }}
.rechts {{ text-align:right; }}
.preis {{ font-size:19px; font-weight:700; font-variant-numeric:tabular-nums; }}
.tag {{ font-size:13px; font-weight:600; }}
.spark {{ width:100%; height:56px; margin-top:10px; display:block; }}
.spark polyline {{ fill:none; stroke-width:2; vector-effect:non-scaling-stroke; }}
.spark.plus polyline {{ stroke:var(--plus); }} .spark.plus polygon {{ fill:var(--plus-bg); }}
.spark.minus polyline {{ stroke:var(--minus); }} .spark.minus polygon {{ fill:var(--minus-bg); }}
.unter-spark {{ font-size:11px; color:var(--leise); text-align:right; margin-top:2px; }}
.chips {{ display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-top:10px; }}
.chip {{ background:var(--bg); border-radius:10px; padding:6px 8px; text-align:center; }}
.chip span {{ display:block; font-size:11px; color:var(--leise); }}
.chip b {{ font-variant-numeric:tabular-nums; }}
dl {{ margin:10px 0 0; display:grid; gap:4px; }}
dl div {{ display:flex; justify-content:space-between; font-size:14px; }}
dt {{ color:var(--leise); }} dd {{ margin:0; font-weight:600; }}
.kalarm {{ margin-top:8px; font-size:13px; color:var(--warn); background:var(--warn-bg);
  border-radius:8px; padding:5px 8px; }}
.plus {{ color:var(--plus); }} .minus {{ color:var(--minus); }} .neutral {{ color:var(--leise); }}
.warn {{ color:var(--warn); }}
dd {{ text-align:right; max-width:62%; }}
.block {{ background:var(--karte); border:1px solid var(--linie); border-radius:16px; padding:14px; }}
.gross {{ font-size:26px; font-weight:700; }}
.klein {{ font-size:13px; color:var(--leise); margin:4px 0; }}
table {{ width:100%; border-collapse:collapse; margin-top:10px; font-variant-numeric:tabular-nums; }}
td {{ padding:7px 0; border-top:1px solid var(--linie); }} td:nth-child(n+2) {{ text-align:right; }}
.vergleich {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin:8px 0; }}
.vergleich div {{ background:var(--bg); border-radius:10px; padding:8px 10px; }}
.vergleich span {{ display:block; font-size:12px; color:var(--leise); }}
.fehler {{ color:var(--minus); font-size:13px; }}
footer {{ color:var(--leise); font-size:12px; margin-top:24px; line-height:1.5; }}
code {{ font-size:12px; }}
</style>
</head>
<body>
<main>
  <div class="kopf"><h1>KI-Trader</h1><span>Stand {escape(d['datum'])}{(' · ' + escape(d['zeit']) + ' Uhr') if d.get('zeit') else ''}</span></div>
  {alarm_html}
  <h2>Deine Aktien</h2>
  {karten}
  {fehler}
  <h2>Mein Depot</h2>
  <section class="block">{depot_html}</section>
  <h2>Strategie-Test</h2>
  <section class="block">{strat_html}</section>
  <footer>
    Kurse in Euro, Tagesschluss (Yahoo Finance, kann verzögert sein).<br>
    RSI unter 30 = stark gefallen, über 70 = stark gestiegen. Trend = Ø50 gegen Ø200 Tage.<br>
    Keine Anlageberatung – du entscheidest selbst.
  </footer>
</main>
</body>
</html>"""


def push_text(d: dict) -> tuple[str, str]:
    """Kurzer Text für die Push-Nachricht. Titel nur mit einfachen Zeichen."""
    n = len(d["alarme"])
    titel = f"KI-Trader: {n} Alarm" + ("e" if n != 1 else "") if n else "KI-Trader: alles ruhig"
    zeilen = [f"⚠ {a}" for a in d["alarme"]]
    if zeilen:
        zeilen.append("")
    for a in d["aktien"]:
        pfeil = "▲" if (a["tag"] or 0) >= 0 else "▼"
        zeilen.append(f"{pfeil} {a['name']}: {_eur(a['preis'])} ({_pz(a['tag'])})")
    if d["depot"]:
        einsatz = sum(p["einsatz"] for p in d["depot"])
        wert = sum(p["wert"] for p in d["depot"])
        zeilen.append(f"\nMein Depot: {_eur(wert)} ({_pz((wert / einsatz - 1) * 100)})")
    return titel, "\n".join(zeilen)
