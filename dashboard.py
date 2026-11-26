"""
dashboard.py – Erstellt die handyfreundliche Übersichtsseite (berichte/dashboard.html)
und den Text für die Push-Nachricht. Wird von bot.py benutzt.
Die Seite ist eine einzelne Datei ohne Internet-Abhängigkeiten.
"""

from __future__ import annotations

from html import escape

NAMEN = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "AMZN": "Amazon", "SAP": "SAP", "BTC": "Bitcoin", "SOL": "Solana", "ETH": "Ethereum", "SPCX": "SpaceX", "INSP": "Inspect", "SKL": "SKALE", "NAVX": "NAVI Protocol", "XEM": "NEM", "SWELL": "Swell Network", "ESE": "eesee", "UTK": "xMoney (UTK)", "IREN": "IREN", "ALGS": "Aligos", "BSB": "Block Street", "IFX": "Infineon", "ARM": "Arm", "NEO": "NEO", "META": "Meta", "ANET": "Arista", "AVGO": "Broadcom", "TSM": "TSMC", "NEOPM": "Neo Perf. Materials", "ANT": "Anthropic",
         "XMN": "xMoney (XMN)"}


def _eur(w: float) -> str:
    if w != 0 and abs(w) < 1:
        # Kleine Kurse (z. B. Krypto-Token): 3 gültige Stellen zeigen
        from math import floor, log10
        stellen = min(10, max(2, -int(floor(log10(abs(w)))) + 2))
        return f"{w:.{stellen}f} €".replace(".", ",")
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


def _kaufziel_html(k) -> str:
    if not k:
        return ""
    if k["erreicht"]:
        text = (f"🎯 Kaufziel {_eur(k['ziel'])} erreicht – für {_eur(k['betrag'])} "
                f"ca. {k['stueck']:.2f} Stück".replace(".", ",", 1))
        return f'<div class="ziel erreicht">{text}</div>'
    return (f'<div class="ziel">🎯 Kaufziel {_eur(k["ziel"])} ({_eur(k["betrag"])}) – '
            f'noch {_pz(k["abstand"])} entfernt</div>')


def _breite_html(b) -> str:
    if not b:
        return ""
    return (f'<div class="breite"><div class="breite-kopf">Übliche Spanne in 30 Tagen '
            f'<span>keine Prognose</span></div>'
            f'<div class="breite-zeile"><b>{_eur(b["von68"])} – {_eur(b["bis68"])}</b>'
            f'<small>in 2 von 3 Fällen (−{b["runter68"]:.0f} % / +{b["hoch68"]:.0f} %)</small></div>'
            f'<div class="breite-zeile"><b>{_eur(b["von90"])} – {_eur(b["bis90"])}</b>'
            f'<small>in 9 von 10 Fällen</small></div></div>')


def _news_html(news) -> str:
    if not news:
        return ""
    punkte = "".join(
        f'<li><a href="{escape(n["url"])}" target="_blank" rel="noopener">{escape(n["titel"])}</a>'
        f'<small>{escape(n["quelle"])}{" · " + escape(n["datum"]) if n["datum"] else ""}</small></li>'
        for n in news)
    return f'<details class="news"><summary>Nachrichten ({len(news)})</summary><ul>{punkte}</ul></details>'


def _ki_html(k) -> str:
    if not k:
        return ""
    kl = {"Kaufen": "plus", "Verkaufen": "minus"}.get(k["signal"], "warn")
    return (f'<div class="ki"><div class="ki-kopf"><span class="ki-badge {kl}">KI: {escape(k["signal"])}</span>'
            f'<small>Sicherheit {escape(k["sicherheit"])}</small></div>'
            f'<div class="ki-text">{escape(k["begruendung"])}</div></div>')


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
  {(_sparkline(a['verlauf']) + '<div class="unter-spark">letzte 6 Monate</div>') if len(a['verlauf']) > 1 else '<div class="unter-spark">nur aktueller Preis von Bitpanda, kein Verlauf</div>'}
  <div class="chips">{chips}</div>
  <dl>
    <div><dt>Trend</dt><dd class="{trend_kl}">{trend}</dd></div>
    <div><dt>Vom Jahreshoch</dt><dd>{_pz(a['vom_hoch'])}</dd></div>
    <div><dt>RSI</dt><dd>{_rsi_text(a['rsi'])}</dd></div>
  </dl>
  {_ki_html(a.get("ki"))}
  {_breite_html(a.get("breite"))}
  {_kaufziel_html(a.get("kaufziel"))}
  {_news_html(a.get("news"))}
  {alarm}
</article>"""


def _tresor_html(tresor: dict) -> str:
    import json as _json
    daten = _json.dumps(tresor).replace("</", "<\\/")
    return f"""
<div id="tresor-zu">
  <p class="klein">🔒 Dein Depot ist verschlüsselt. Nur mit deinem Passwort sichtbar –
  das Entschlüsseln passiert nur hier auf deinem Gerät.</p>
  <form id="tresor-form" class="tresor-form" autocomplete="off">
    <input id="tresor-pw" type="password" placeholder="Passwort" autocomplete="current-password">
    <button type="submit">Depot anzeigen</button>
  </form>
  <label class="klein merken"><input id="tresor-merken" type="checkbox"> auf diesem Gerät merken</label>
  <p id="tresor-fehler" class="fehler" hidden>Passwort falsch.</p>
</div>
<div id="tresor-auf" hidden></div>
<script type="application/json" id="tresor-daten">{daten}</script>
<script>
(function () {{
  const SPEICHER = "ki-trader-depot-pw";
  const b64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
  const eur = w => new Intl.NumberFormat("de-DE", {{style: "currency", currency: "EUR",
      maximumFractionDigits: Math.abs(w) < 1 ? 4 : 2}}).format(w);
  const pz = w => (w > 0 ? "+" : "") + w.toLocaleString("de-DE", {{maximumFractionDigits: 1,
      minimumFractionDigits: 1}}) + " %";
  const kl = w => w > 0 ? "plus" : (w < 0 ? "minus" : "neutral");
  const esc = t => String(t).replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[c]);

  async function oeffnen(pw) {{
    const t = JSON.parse(document.getElementById("tresor-daten").textContent);
    const salt = b64(t.salt), nonce = b64(t.nonce), geheim = b64(t.daten), tag = b64(t.tag);
    const basis = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveBits"]);
    const bits = new Uint8Array(await crypto.subtle.deriveBits(
      {{name: "PBKDF2", salt, iterations: t.runden, hash: "SHA-256"}}, basis, 512));
    const hm = {{name: "HMAC", hash: "SHA-256"}};
    const kEnc = await crypto.subtle.importKey("raw", bits.slice(0, 32), hm, false, ["sign"]);
    const kMac = await crypto.subtle.importKey("raw", bits.slice(32), hm, false, ["verify"]);
    const msg = new Uint8Array(salt.length + nonce.length + geheim.length);
    msg.set(salt); msg.set(nonce, salt.length); msg.set(geheim, salt.length + nonce.length);
    if (!(await crypto.subtle.verify("HMAC", kMac, tag, msg))) throw new Error("falsch");
    const klar = new Uint8Array(geheim.length);
    for (let i = 0; i * 32 < geheim.length; i++) {{
      const blk = new Uint8Array(nonce.length + 4); blk.set(nonce);
      new DataView(blk.buffer).setUint32(nonce.length, i);
      const ks = new Uint8Array(await crypto.subtle.sign("HMAC", kEnc, blk));
      for (let j = 0; j < 32 && i * 32 + j < geheim.length; j++) klar[i * 32 + j] = geheim[i * 32 + j] ^ ks[j];
    }}
    return JSON.parse(new TextDecoder().decode(klar));
  }}

  function zeigen(d) {{
    const einsatz = d.posten.reduce((a, p) => a + p.einsatz, 0);
    const wert = d.posten.reduce((a, p) => a + p.wert, 0);
    const diff = einsatz ? (wert / einsatz - 1) * 100 : 0;
    const zeilen = d.posten.slice().sort((a, b) => b.wert - a.wert).map(p =>
      `<tr><td>${{esc(p.voll || p.name)}}<small>Einsatz ${{eur(p.einsatz)}}</small></td>` +
      `<td>${{eur(p.wert)}}</td><td class="${{kl(p.diff)}}">${{pz(p.diff)}}</td></tr>`).join("");
    const alarme = (d.alarme || []).map(a =>
      `<li>${{a.neu ? '<b class="neu">NEU</b> ' : ""}}${{esc(a.text.replace("MEIN DEPOT ", ""))}}</li>`).join("");
    const el = document.getElementById("tresor-auf");
    el.innerHTML = `<div class="gross ${{kl(diff)}}">${{eur(wert)}}</div>` +
      `<div class="klein">Einsatz ${{eur(einsatz)}} · <b class="${{kl(diff)}}">${{pz(diff)}}</b> · ` +
      `${{(wert - einsatz >= 0 ? "+" : "") + eur(wert - einsatz)}}</div>` +
      `<table>${{zeilen}}</table>` +
      (alarme ? `<div class="klein" style="margin-top:10px">Warnungen:</div><ul class="depot-alarme">${{alarme}}</ul>` : "") +
      `<p class="klein"><a href="#" id="tresor-sperren">🔒 wieder sperren</a></p>`;
    el.hidden = false;
    document.getElementById("tresor-zu").hidden = true;
    document.getElementById("tresor-sperren").onclick = e => {{
      e.preventDefault();
      try {{ localStorage.removeItem(SPEICHER); }} catch (x) {{}}
      el.hidden = true; el.innerHTML = "";
      document.getElementById("tresor-zu").hidden = false;
    }};
  }}

  async function versuchen(pw, merken) {{
    try {{
      zeigen(await oeffnen(pw));
      if (merken) try {{ localStorage.setItem(SPEICHER, pw); }} catch (x) {{}}
      return true;
    }} catch (x) {{
      document.getElementById("tresor-fehler").hidden = false;
      return false;
    }}
  }}

  document.getElementById("tresor-form").addEventListener("submit", e => {{
    e.preventDefault();
    document.getElementById("tresor-fehler").hidden = true;
    versuchen(document.getElementById("tresor-pw").value, document.getElementById("tresor-merken").checked);
  }});
  let gemerkt = null;
  try {{ gemerkt = localStorage.getItem(SPEICHER); }} catch (x) {{}}
  if (gemerkt) versuchen(gemerkt, false).then(ok => {{
    if (!ok) {{ try {{ localStorage.removeItem(SPEICHER); }} catch (x) {{}}
      document.getElementById("tresor-fehler").hidden = true; }}
  }});
}})();
</script>"""


def _ki_kopf_html(ki) -> str:
    if not ki:
        return ""
    if ki.get("fehler"):
        return ('<h2>KI-Einschätzung</h2><section class="block ki-block"><p class="klein">'
                '🤖 KI-Einschätzung heute nicht verfügbar – alles andere ist aktuell.</p></section>')
    k = ki.get("kontrolle") or {}
    kontrolle = ""
    if k.get("werte"):
        diff = k["ki"] - k["halten"]
        kontrolle = (f'<div class="vergleich"><div><span>Den KI-Signalen folgen</span><b>{_eur(k["ki"])}</b></div>'
                     f'<div><span>Einfach halten</span><b>{_eur(k["halten"])}</b></div></div>'
                     f'<p class="klein">Erfolgskontrolle seit {escape(str(k["seit"]))}: je {_eur(100)} pro Wert, '
                     f'{k["wechsel"]} Wechsel. Die KI liegt <b class="{_farbe(diff)}">'
                     f'{"vorne" if diff > 0 else "hinten" if diff < 0 else "gleichauf"} ({_eur(diff)})</b>.</p>')
    b = k.get("bilanz") or {}
    if b.get("bewertet"):
        teile = [f'{typ} {b[typ][0]} von {b[typ][1]} richtig ({b[typ][0] / b[typ][1] * 100:.0f} %)'
                 for typ in ("Verkaufen", "Kaufen") if b[typ][1]]
        kontrolle += (f'<p class="klein">🎯 Trefferquote (Kurs 5 Läufe später): {escape(" · ".join(teile))}. '
                      f'Die KI bekommt diese Bilanz bei jeder Anfrage mitgeteilt.</p>')
    elif k.get("werte"):
        kontrolle += ('<p class="klein">🎯 Trefferquote: erste Auswertung nach 5 Läufen '
                      '(ca. 1 Woche). Danach bekommt die KI ihre Bilanz mitgeteilt.</p>')
    return (f'<h2>KI-Einschätzung</h2><section class="block ki-block">'
            f'<p>🤖 {escape(ki.get("fazit", ""))}</p>{kontrolle}'
            f'<p class="klein">KI-Meinung ({escape(ki.get("anbieter", "KI"))}), keine Anlageberatung.'
            f'{" Für Anthropic gibt es bewusst kein Signal." if "ANT" in (ki.get("ohne_signal") or []) else ""}</p>'
            f'</section>')


def erstelle_dashboard(d: dict) -> str:
    verbergen = d.get("depot_verbergen", False)
    alarme = [a for a in d["alarme"] if not (verbergen and a.startswith("MEIN DEPOT"))]
    neu_set = set(d.get("alarme_neu", []))
    neue = [a for a in alarme if a in neu_set]
    alte = [a for a in alarme if a not in neu_set]
    if neue:
        alarm_html = ('<section class="alarmbox"><h2>⚠ ' + str(len(neue)) + " neue"
                      + (" Alarme" if len(neue) != 1 else "r Alarm") + "</h2><ul>"
                      + "".join(f"<li>{escape(a)}</li>" for a in neue) + "</ul>")
    else:
        alarm_html = '<section class="ruhig"><b>✓ Keine neuen Alarme</b>'
    if alte:
        alarm_html += (f'<details><summary>{len(alte)} weiter bestehende Hinweise anzeigen</summary><ul>'
                       + "".join(f"<li>{escape(a)}</li>" for a in alte) + "</ul></details>")
    alarm_html += "</section>"

    karten = "".join(_aktie_karte(a) for a in d["aktien"])
    fehler = "".join(f'<p class="fehler">{escape(f)}</p>' for f in d.get("fehler", []))

    tresor = d.get("depot_tresor")
    if verbergen and tresor:
        depot_html = _tresor_html(tresor)
    elif verbergen:
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
.ruhig {{ background:var(--plus-bg); color:var(--plus); border-radius:14px; padding:12px 14px; }}
details {{ margin-top:8px; color:var(--text); font-size:13px; }}
details summary {{ cursor:pointer; color:var(--leise); font-weight:600; }}
details ul {{ margin:6px 0 0; padding-left:18px; }}
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
.breite {{ margin-top:10px; background:var(--bg); border-radius:10px; padding:8px 10px; }}
.breite-kopf {{ font-size:12px; color:var(--leise); display:flex; justify-content:space-between; }}
.breite-kopf span {{ font-size:11px; border:1px solid var(--linie); border-radius:6px; padding:0 5px; }}
.breite-zeile {{ display:flex; justify-content:space-between; align-items:baseline; gap:8px;
  margin-top:4px; font-variant-numeric:tabular-nums; }}
.breite-zeile small {{ color:var(--leise); font-size:12px; text-align:right; }}
details.news {{ margin-top:8px; }}
details.news li {{ margin:6px 0; line-height:1.35; }}
details.news a {{ color:var(--text); text-decoration:none; font-weight:500; }}
details.news small {{ display:block; color:var(--leise); font-size:11px; }}
.tresor-form {{ display:flex; gap:8px; margin:10px 0 6px; }}
.tresor-form input {{ flex:1; font:inherit; padding:10px 12px; border-radius:10px;
  border:1px solid var(--linie); background:var(--bg); color:var(--text); min-width:0; }}
.tresor-form button {{ font:inherit; font-weight:600; padding:10px 14px; border-radius:10px; border:0;
  background:var(--plus); color:#fff; }}
.merken {{ display:flex; gap:6px; align-items:center; }}
td small {{ display:block; color:var(--leise); font-size:11px; }}
ul.depot-alarme {{ margin:4px 0 0; padding-left:18px; font-size:13px; }}
.ki {{ margin-top:10px; border:1px solid var(--linie); border-radius:10px; padding:8px 10px; }}
.ki-kopf {{ display:flex; justify-content:space-between; align-items:center; }}
.ki-kopf small {{ color:var(--leise); font-size:12px; }}
.ki-badge {{ font-weight:700; font-size:13px; }}
.ki-text {{ font-size:13px; margin-top:4px; line-height:1.4; }}
.ki-block p {{ margin:4px 0 8px; }}
b.neu {{ background:var(--warn); color:#fff; border-radius:4px; padding:0 5px; font-size:11px; }}
.ziel {{ margin-top:8px; font-size:13px; background:var(--bg); border-radius:8px; padding:6px 8px; }}
.ziel.erreicht {{ background:var(--plus-bg); color:var(--plus); font-weight:600; }}
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
  {_ki_kopf_html(d.get("ki"))}
  <h2>Deine Aktien</h2>
  {karten}
  {fehler}
  <h2>Mein Depot</h2>
  <section class="block">{depot_html}</section>
  {('<h2>Strategie-Test</h2><section class="block">' + strat_html + '</section>') if st else ''}
  <footer>
    Kurse in Euro, Tagesschluss (Yahoo Finance, kann verzögert sein).<br>
    RSI unter 30 = stark gefallen, über 70 = stark gestiegen. Trend = Ø50 gegen Ø200 Tage.<br>
    „Übliche Spanne“ = bisherige Kursschwankung auf 30 Tage hochgerechnet – keine Vorhersage.<br>
    Keine Anlageberatung – du entscheidest selbst.
  </footer>
</main>
</body>
</html>"""


def push_text(d: dict) -> tuple[str, str]:
    """Kurzer Text für die Push-Nachricht. Titel nur mit einfachen Zeichen."""
    neu = d.get("alarme_neu", d["alarme"])
    n = len(neu)
    ki = d.get("ki") or {}
    aend = (ki.get("kontrolle") or {}).get("geaendert") or []
    if n:
        titel = f"KI-Trader: {n} neue" + (" Alarme" if n != 1 else "r Alarm")
    elif aend:
        titel = f"KI-Trader: {len(aend)} neue KI-Signale"
    else:
        titel = "KI-Trader: keine neuen Alarme"
    zeilen = []
    if ki.get("fazit"):
        zeilen += [f"🤖 {ki['fazit']}"]
    if aend:
        zeilen += ["KI-Signal geändert: " + "; ".join(aend)]
    if zeilen:
        zeilen.append("")
    zeilen += [f"⚠ {a}" for a in neu]
    weiter = len(d["alarme"]) - n
    if weiter > 0:
        zeilen.append(f"(+ {weiter} bestehende Hinweise, siehe App)")
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
