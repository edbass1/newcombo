#!/usr/bin/env python3
"""Sezione CORNER 9-11 da football-data.co.uk.

Un'uscita è una partita i cui corner totali (casa + trasferta) sono 9, 10 o 11.
Il ritardo conta le partite consecutive senza uscita, in tre viste: tutte,
solo in casa, solo in trasferta. La doppia ripetizione usa la stessa regola
della sezione combo: il ritardo sale se un'uscita non si ripete nella partita
subito dopo, e torna a zero con due uscite di fila.

I file «new» (campionati extra) non hanno le colonne dei corner: si usano solo
i CSV stagionali mmz4281 che le pubblicano. Un errore qui non deve impedire
il salvataggio dei dati combo: lo script scrive i file solo alla fine.
"""
import csv, io, os, html, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
LONDON = ZoneInfo("Europe/London")
EVENT = "CORNER 9-11"
TOP = 10
SOTTO_N = 100
SOTTO_MIN = 100
SALTATI_MIN = 6
SALTATI_GIORNI = 7
MIN_GIORNATE = 7
PROSSIME_MIN = 6
GIORNI = 5
REPEAT_ALERT_MIN = 10
SEASON_COUNT = 12
VENUES = (
    ("all", "Totali", "tutte le partite, in casa o in trasferta"),
    ("home", "Casa", "solo le partite giocate in casa"),
    ("away", "Trasferta", "solo le partite giocate in trasferta"),
)
# (paese, campionato, codice file). EC e D3 entrano solo se un CSV ha i corner.
LEAGUES = [
    ("INGHILTERRA", "PREMIER LEAGUE", "E0"),
    ("INGHILTERRA", "CHAMPIONSHIP", "E1"),
    ("INGHILTERRA", "LEAGUE ONE", "E2"),
    ("INGHILTERRA", "LEAGUE TWO", "E3"),
    ("INGHILTERRA", "NATIONAL LEAGUE", "EC"),
    ("SCOZIA", "PREMIERSHIP", "SC0"),
    ("SCOZIA", "CHAMPIONSHIP", "SC1"),
    ("SCOZIA", "LEAGUE ONE", "SC2"),
    ("SCOZIA", "LEAGUE TWO", "SC3"),
    ("GERMANIA", "BUNDESLIGA", "D1"),
    ("GERMANIA", "2. BUNDESLIGA", "D2"),
    ("GERMANIA", "3. LIGA", "D3"),
    ("ITALIA", "SERIE A", "I1"),
    ("ITALIA", "SERIE B", "I2"),
    ("SPAGNA", "LALIGA", "SP1"),
    ("SPAGNA", "LALIGA 2", "SP2"),
    ("FRANCIA", "LIGUE 1", "F1"),
    ("FRANCIA", "LIGUE 2", "F2"),
    ("OLANDA", "EREDIVISIE", "N1"),
    ("BELGIO", "FIRST DIVISION A", "B1"),
    ("PORTOGALLO", "LIGA PORTUGAL", "P1"),
    ("TURCHIA", "SUPER LIG", "T1"),
    ("GRECIA", "SUPER LEAGUE", "G1"),
]

def is_hit(total):
    return 9 <= total <= 11

def season_codes(today=None, n=SEASON_COUNT):
    today = today or datetime.now(ROME)
    start = today.year if today.month >= 7 else today.year - 1
    out = []
    for i in range(n):
        y = start - i
        out.append(f"{y % 100:02d}{(y + 1) % 100:02d}")
    return out

def parse_date(text):
    text = (text or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None

def london_kick(date_text, time_text):
    """L'orario dei CSV è quello del Regno Unito. Senza ora, mezzogiorno solo per l'ordine."""
    day = parse_date(date_text)
    if not day:
        return None, False
    hm = (time_text or "").strip()
    precise = False
    hour, minute = 12, 0
    if hm and ":" in hm:
        parts = hm.split(":")
        try:
            hour, minute = int(parts[0]), int(parts[1])
            precise = True
        except ValueError:
            hour, minute = 12, 0
    local = day.replace(hour=hour, minute=minute, tzinfo=LONDON)
    return local.astimezone(timezone.utc), precise

def drought(seq):
    """Ritardo in coda. full è vero se nello storico caricato non c'è nessuna uscita."""
    if not seq:
        return None
    n = 0
    for match in reversed(seq):
        if match["hit"]:
            break
        n += 1
    return n, n == len(seq)

def repeat_state(seq):
    delay = 0
    previous = False
    last = False
    had_pair = False
    for match in seq:
        hit = match["hit"]
        if previous:
            if hit:
                delay = 0
                had_pair = True
            else:
                delay += 1
        previous = hit
        last = hit
    return delay, last, (not had_pair and bool(seq))

def in_venue(match, team, venue):
    if venue == "home":
        return match["home"] == team
    if venue == "away":
        return match["away"] == team
    return team in (match["home"], match["away"])

def esc(value):
    return html.escape(str(value), quote=True)

def num_it(value):
    return f"{float(value):.1f}".replace(".", ",")

def _int(value):
    text = (value or "").strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None

def parse_results(raw, div, season):
    if not raw:
        return []
    text = raw.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fields = {(name or "").replace("\ufeff", "").strip(): name for name in reader.fieldnames}
    if "HC" not in fields or "AC" not in fields:
        return []
    out = []
    for row in reader:
        def col(key):
            real = fields.get(key)
            return row.get(real, "") if real else ""
        hc, ac = _int(col("HC")), _int(col("AC"))
        hg = col("FTHG") or col("HG")
        if hc is None or ac is None or not str(hg).strip():
            continue
        when, _precise = london_kick(col("Date"), col("Time"))
        home, away = col("HomeTeam").strip(), col("AwayTeam").strip()
        if not when or not home or not away:
            continue
        total = hc + ac
        out.append({
            "when": when, "home": home, "away": away, "hc": hc, "ac": ac,
            "total": total, "hit": is_hit(total), "div": div, "season": season,
        })
    return out

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            return response.read()
    except Exception:
        return None

def load_history(codes=None):
    codes = codes or season_codes()
    jobs = []
    for _country, _league, div in LEAGUES:
        for code in codes:
            url = f"https://www.football-data.co.uk/mmz4281/{code}/{div}.csv"
            jobs.append((div, code, url))
    found = {div: {} for _c, _l, div in LEAGUES}
    with ThreadPoolExecutor(max_workers=12) as pool:
        future_map = {pool.submit(fetch, url): (div, code) for div, code, url in jobs}
        for future in as_completed(future_map):
            div, code = future_map[future]
            matches = parse_results(future.result(), div, code)
            if matches:
                found[div][code] = matches
    return found

def load_fixtures():
    raw = fetch("https://www.football-data.co.uk/fixtures.csv")
    if not raw:
        return []
    text = raw.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []
    fields = {(name or "").replace("\ufeff", "").strip(): name for name in reader.fieldnames}
    out = []
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=GIORNI)
    for row in reader:
        def col(key):
            real = fields.get(key)
            return row.get(real, "") if real else ""
        when, precise = london_kick(col("Date"), col("Time"))
        home, away, div = col("HomeTeam").strip(), col("AwayTeam").strip(), col("Div").strip()
        if not precise or not when or not home or not away or not div:
            continue
        if not (now <= when <= horizon):
            continue
        out.append({"when": when, "home": home, "away": away, "div": div})
    out.sort(key=lambda item: (item["when"], item["div"], item["home"]))
    return out

def _dedupe(matches):
    seen, out = set(), []
    for match in sorted(matches, key=lambda item: item["when"]):
        key = (match["div"], match["when"].date().isoformat(), match["home"], match["away"])
        if key in seen:
            continue
        seen.add(key)
        out.append(match)
    return out

def build(history, fixtures, now=None):
    now = now or datetime.now(timezone.utc)
    by_div = {}
    for country, league, div in LEAGUES:
        seasons = history.get(div) or {}
        if not seasons:
            continue
        ordered = sorted(seasons, key=int, reverse=True)
        current_code = ordered[0]
        previous_code = ordered[1] if len(ordered) > 1 else None
        current = _dedupe(seasons[current_code])
        previous = _dedupe(seasons[previous_code]) if previous_code else []
        all_matches = _dedupe([m for code in ordered for m in seasons[code]])
        if not current and not all_matches:
            continue
        teams = set()
        for match in current:
            teams.add(match["home"])
            teams.add(match["away"])
        by_div[div] = {
            "country": country, "league": league, "div": div,
            "current_code": current_code, "previous_code": previous_code,
            "current": current, "previous": previous, "all": all_matches,
            "teams": teams, "seasons": ordered,
        }
    pools = {}
    for block in by_div.values():
        pools.setdefault(block["country"], []).extend(block["all"])
    for country, matches in pools.items():
        pools[country] = _dedupe(matches)
    reports = []
    for block in by_div.values():
        pool = pools[block["country"]]
        reports.append(_league_report(block, pool, now))
    upcoming = [item for item in fixtures if item["div"] in by_div]
    return {"leagues": reports, "by_div": by_div, "upcoming": upcoming, "now": now}

def _seq(block, team, venue, pool=None):
    source = pool if pool is not None else None
    if source is None:
        previous = [m for m in block["previous"] if in_venue(m, team, venue)]
        played_prev = any(team in (m["home"], m["away"]) for m in block["previous"])
        if not played_prev:
            previous = []
        current = [m for m in block["current"] if in_venue(m, team, venue)]
        return previous + current
    return [m for m in source if in_venue(m, team, venue)]

def _league_report(block, pool, now):
    teams = sorted(block["teams"], key=str.casefold)
    views = {}
    for venue, _label, _desc in VENUES:
        rows = []
        sotto = []
        salt = []
        cutoff = now - timedelta(days=SALTATI_GIORNI)
        for team in teams:
            seq = _seq(block, team, venue)
            found = drought(seq)
            if not found:
                continue
            delay, full = found
            rows.append({"team": team, "delay": delay, "full": full, "n": len(seq)})
            run = 0
            for match in seq:
                if match["hit"]:
                    if run >= SALTATI_MIN and match["when"] >= cutoff:
                        salt.append({"when": match["when"], "team": team, "run": run, "match": match})
                    run = 0
                else:
                    run += 1
            national = _seq(block, team, venue, pool)
            last = national[-SOTTO_N:]
            if len(last) >= SOTTO_MIN:
                hits = sum(1 for match in last if match["hit"])
                sotto.append({"team": team, "hits": hits, "n": len(last)})
        rows.sort(key=lambda item: (-item["delay"], item["team"].casefold()))
        sotto.sort(key=lambda item: (-item["hits"], item["team"].casefold()))
        salt.sort(key=lambda item: item["when"], reverse=True)
        views[venue] = {"rows": rows, "sotto": sotto, "salt": salt}
    played = block["current"]
    hits = sum(1 for match in played if match["hit"])
    per_team = {}
    for match in played:
        per_team[match["home"]] = per_team.get(match["home"], 0) + 1
        per_team[match["away"]] = per_team.get(match["away"], 0) + 1
    rounds = max(per_team.values()) if per_team else 0
    freq = None
    if rounds >= MIN_GIORNATE and played:
        freq = {
            "played": len(played), "hits": hits, "rounds": rounds,
            "ogni": (len(played) / hits) if hits else None,
        }
    repeats = {}
    for venue, _label, _desc in VENUES:
        table = []
        for team in teams:
            national = _seq(block, team, venue, pool)
            if not national:
                continue
            delay, last, full = repeat_state(national)
            table.append({
                "team": team, "delay": delay, "last": last, "full": full,
                "matches": len(national),
            })
        table.sort(key=lambda item: (-item["delay"], item["team"].casefold()))
        repeats[venue] = table
    return {"block": block, "views": views, "freq": freq, "repeats": repeats}

def _label(delay, full):
    return f"{delay}{'+' if full else ''}"

def render_texts(model, stamp):
    leagues = model["leagues"]
    ritardi, sotto, saltati, freq_lines, repeat_lines, prossime = [], [], [], [], [], []
    for venue, label, desc in VENUES:
        ritardi += [
            "#" * 50, f"# {label.upper()}", "#" * 50, "",
            f"RITARDI {EVENT} — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)",
            f"Vista {label}: {desc}.",
            "Uscita = corner totali 9, 10 o 11. Ritardo = partite consecutive senza uscita.",
            "Lo storico del ritardo è la stagione in corso più la precedente, se la squadra era nella stessa divisione.",
            "+ = nessuna uscita nel periodo considerato.",
            "Fonte: football-data.co.uk", "",
            "TOP 20 GLOBALE",
        ]
        flat = []
        for item in leagues:
            for row in item["views"][venue]["rows"]:
                flat.append((row, item))
        flat.sort(key=lambda pair: (-pair[0]["delay"], pair[0]["team"].casefold(), pair[1]["block"]["league"].casefold()))
        cutoff = flat[19][0]["delay"] if len(flat) > 20 else (flat[-1][0]["delay"] if flat else None)
        rank_at, previous = 0, None
        shown = 0
        for index, (row, item) in enumerate(flat, 1):
            if cutoff is None or row["delay"] < cutoff:
                continue
            if row["delay"] != previous:
                rank_at, previous = index, row["delay"]
            shown += 1
            block = item["block"]
            ritardi.append(
                f"  {rank_at:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}  | "
                f"{block['country']} - {block['league']}"
            )
        if not shown:
            ritardi.append("  Nessun dato.")
        ritardi.append("")
        for item in leagues:
            block = item["block"]
            ritardi += ["=" * 50, f"{block['country']} - {block['league']}", "=" * 50, f"RITARDI {EVENT}"]
            rows = item["views"][venue]["rows"][:TOP]
            if not rows:
                ritardi.append("  Nessuna squadra con storico.")
            for index, row in enumerate(rows, 1):
                ritardi.append(f"  {index:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}")
            ritardi.append("")
        sotto += [
            "#" * 50, f"# {label.upper()}", "#" * 50, "",
            f"SOTTOFREQUENZE {EVENT} (ultime {SOTTO_N} partite) — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)",
            "Uscite di corner 9-11 sulle ultime 100 partite del paese, nella vista indicata.",
            "Solo squadre con almeno 100 partite. In cima chi le fa più spesso.",
            "Fonte: football-data.co.uk", "",
        ]
        for item in leagues:
            block = item["block"]
            rows = item["views"][venue]["sotto"]
            sotto += ["=" * 50, f"{block['country']} - {block['league']}", "=" * 50]
            if not rows:
                sotto.append("  Storico insufficiente.")
                sotto.append("")
                continue
            top = rows[:5]
            if len(rows) > 5:
                cut = top[-1]["hits"]
                top = [row for row in rows if row["hits"] >= cut]
            for index, row in enumerate(top, 1):
                sotto.append(f"  {index:>2}/{len(rows):<3} {row['team']:<28.28} {row['hits']}/{row['n']}")
            last = rows[-1]
            if len(rows) > len(top):
                sotto += ["     ...", f"  {len(rows):>2}/{len(rows):<3} {last['team']:<28.28} {last['hits']}/{last['n']}  (la fa meno spesso)"]
            sotto.append("")
        saltati += [
            "#" * 50, f"# {label.upper()}", "#" * 50, "",
            f"SALTATI {EVENT} — ritardi >= {SALTATI_MIN} spezzati negli ultimi {SALTATI_GIORNI} giorni — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)", "",
        ]
        events = []
        for item in leagues:
            for row in item["views"][venue]["salt"]:
                events.append((row, item))
        events.sort(key=lambda pair: pair[0]["when"], reverse=True)
        if not events:
            saltati.append("Nessun ritardo spezzato negli ultimi giorni.")
            saltati.append("")
        for row, item in events:
            match = row["match"]
            local = match["when"].astimezone(ROME)
            block = item["block"]
            saltati.append(
                f"{local:%d/%m %H:%M}  {row['team']}: {EVENT} uscita, era a {row['run']}  -  "
                f"{match['home']} {match['hc']}-{match['ac']} {match['away']}   [{block['country']} - {block['league']}]"
            )
        saltati.append("")
        repeat_lines += [
            "#" * 50, f"# {label.upper()}", "#" * 50, "",
            f"RITARDI DOPPIA RIPETIZIONE — {EVENT} — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)",
            "Il ritardo sale quando un'uscita non si ripete nella partita immediatamente successiva.",
            "Due uscite consecutive azzerano il ritardo. + = nello storico non c'è mai stata una coppia.",
            "Fonte: football-data.co.uk", "",
        ]
        ranking = []
        for item in leagues:
            for row in item["repeats"][venue]:
                ranking.append((row, item))
        ranking.sort(key=lambda pair: (-pair[0]["delay"], pair[0]["team"].casefold()))
        for index, (row, item) in enumerate(ranking, 1):
            block = item["block"]
            repeat_lines.append(
                f"{index:>4}. {row['team']:<28.28} {_label(row['delay'], row['full']):>5}  | "
                f"{block['country']} - {block['league']}  | {row['matches']} gare"
            )
        repeat_lines.append("")
    freq_lines = [
        f"FREQUENZA CAMPIONATI — {EVENT}",
        f"Aggiornato: {stamp} (ora italiana)",
        "Solo stagione in corso, tutte le partite. 1 ogni N = partite giocate / uscite.",
        f"Servono almeno {MIN_GIORNATE} giornate (partite giocate dalla squadra più attiva).",
        "Fonte: football-data.co.uk", "",
    ]
    ranked = [item for item in leagues if item["freq"]]
    ranked.sort(key=lambda item: ((item["freq"]["ogni"] is None), item["freq"]["ogni"] or 10**9, item["block"]["league"]))
    if not ranked:
        freq_lines.append("Nessun campionato con abbastanza giornate.")
    for index, item in enumerate(ranked, 1):
        freq = item["freq"]
        block = item["block"]
        ogni = num_it(freq["ogni"]) if freq["ogni"] else "mai"
        freq_lines.append(
            f"  {index:>2}. {block['country']} - {block['league']:<22} 1 ogni {ogni:<6} "
            f"({freq['hits']}/{freq['played']}, {freq['rounds']} giornate)"
        )
    prossime = [
        f"PROSSIME PARTITE ({GIORNI} GIORNI) — {EVENT}",
        f"Aggiornato: {stamp} (ora italiana)",
        f"Solo squadre con ritardo da {PROSSIME_MIN} in su, nella vista della scheda.",
        "Orari convertiti dall'ora del Regno Unito all'ora italiana.",
        "Fonte partite: football-data.co.uk fixtures.csv. Fonte ritardi: CSV stagionali.", "",
    ]
    return {
        "ritardi": "\n".join(ritardi) + "\n",
        "sotto": "\n".join(sotto) + "\n",
        "saltati": "\n".join(saltati) + "\n",
        "freq": "\n".join(freq_lines) + "\n",
        "repeat": "\n".join(repeat_lines) + "\n",
        "prossime": "\n".join(prossime + _prossime_lines(model)) + "\n",
    }

def _prossime_lines(model):
    lines = []
    day = None
    cards = _upcoming_cards(model)
    if not cards:
        return ["Nessuna partita nei prossimi giorni con ritardo da 6 in su."]
    for card in cards:
        local = card["when"].astimezone(ROME)
        stamp = local.strftime("%d/%m/%Y")
        if stamp != day:
            lines += ["", "=" * 50, stamp, "=" * 50]
            day = stamp
        lines.append(
            f"{card['mark']:<9} {local:%H:%M}  {card['home']} vs {card['away']}   "
            f"[{card['country']} - {card['league']}]"
        )
        for team, delay, full in card["delays"]:
            lines.append(f"           {team}: {EVENT} ({_label(delay, full)})")
    return lines

def _upcoming_cards(model):
    by_div = model["by_div"]
    cards = []
    for fixture in model["upcoming"]:
        block = by_div.get(fixture["div"])
        if not block:
            continue
        report = next(item for item in model["leagues"] if item["block"]["div"] == fixture["div"])
        for venue, label, _desc in VENUES:
            delays = []
            sides = [fixture["home"]] if venue == "home" else [fixture["away"]] if venue == "away" else [fixture["home"], fixture["away"]]
            known = {row["team"]: row for row in report["views"][venue]["rows"]}
            for team in sides:
                row = known.get(team)
                if row and row["delay"] >= PROSSIME_MIN:
                    delays.append((team, row["delay"], row["full"]))
            if not delays:
                continue
            cards.append({
                "when": fixture["when"], "home": fixture["home"], "away": fixture["away"],
                "country": block["country"], "league": block["league"], "venue": venue,
                "mark": {"all": "TOTALE", "home": "CASA", "away": "TRASFERTA"}[venue],
                "delays": delays,
            })
    cards.sort(key=lambda card: (card["when"], card["home"], {"all": 0, "home": 1, "away": 2}[card["venue"]]))
    return cards

def render_html(model, stamp):
    parts = [
        f'<p class="leg">Uscita = corner totali della partita, casa più trasferta, pari a 9, 10 o 11. '
        f'Ritardo = partite consecutive senza quella uscita. Le viste sono una sotto l&rsquo;altra: '
        f'<b>Totali</b>, <b>Casa</b>, <b>Trasferta</b>. Aggiornato: {esc(stamp)} (ora italiana). '
        f'Fonte: football-data.co.uk. Gli orari delle prossime partite sono convertiti in ora italiana.</p>',
        '<h3>Prossime partite (5 giorni)</h3>',
        f'<p class="leg">In elenco solo se, nella vista della scheda, almeno una squadra ha ritardo da {PROSSIME_MIN} in su. '
        f'Le partite già iniziate non restano in elenco.</p>',
        '<div class="fxlist" data-upcoming="1">',
    ]
    cards = _upcoming_cards(model)
    day = None
    if not cards:
        parts.append("<p>Nessuna partita nei prossimi giorni con ritardo da 6 in su.</p>")
    for card in cards:
        local = card["when"].astimezone(ROME)
        stamp_day = local.strftime("%d/%m/%Y")
        if stamp_day != day:
            parts.append(f'<h3 class="day">{stamp_day}</h3>')
            day = stamp_day
        kick = card["when"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        detail = " · ".join(f"{esc(team)} ({esc(_label(delay, full))})" for team, delay, full in card["delays"])
        copy = esc(card["home"] + " vs " + card["away"])
        parts.append(
            f'<div class="fx" data-kick="{kick}"><div class="fxh">'
            f'<span class="time copy-time" data-copy="{copy}" title="Clicca per copiare la partita">{local:%H:%M}</span> '
            f'{esc(card["mark"])} · {esc(card["home"])} vs {esc(card["away"])} '
            f'<span class="lg">{esc(card["country"])} - {esc(card["league"])}</span></div>'
            f'<div class="tagrow">{esc(EVENT)} · {detail}</div></div>'
        )
    parts.append("</div>")
    parts.append("<h3>Ritardi e top 20</h3>")
    for venue, label, desc in VENUES:
        lines = [
            f"RITARDI {EVENT} — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)",
            desc,
            "TOP 20 globale, pari merito sul confine inclusi.",
            "",
        ]
        flat = []
        for item in model["leagues"]:
            for row in item["views"][venue]["rows"]:
                flat.append((row, item))
        flat.sort(key=lambda pair: (-pair[0]["delay"], pair[0]["team"].casefold()))
        cutoff = flat[19][0]["delay"] if len(flat) > 20 else (flat[-1][0]["delay"] if flat else None)
        rank_at, previous = 0, None
        for index, (row, item) in enumerate(flat, 1):
            if cutoff is None or row["delay"] < cutoff:
                continue
            if row["delay"] != previous:
                rank_at, previous = index, row["delay"]
            block = item["block"]
            lines.append(f"{rank_at:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}  {block['country']} - {block['league']}")
        lines.append("")
        for item in model["leagues"]:
            block = item["block"]
            lines += ["=" * 50, f"{block['country']} - {block['league']}", f"RITARDI {EVENT}"]
            for index, row in enumerate(item["views"][venue]["rows"][:TOP], 1):
                lines.append(f"  {index:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}")
            lines.append("")
        parts.append(
            f'<div class="venue" data-v="{venue}"><p class="vnote">Vista <b>{esc(label)}</b> · {esc(desc)}</p>'
            f'<pre>{esc(chr(10).join(lines))}</pre></div>'
        )
    parts.append("<h3>Doppia ripetizione</h3>")
    parts.append(
        '<p class="leg">Stesso conto della sezione combo: dopo un&rsquo;uscita, se la partita successiva '
        'non è ancora 9-11 il ritardo di ripetizione sale di uno. Due uscite di fila lo azzerano. '
        'In arrivo solo se l&rsquo;ultima partita è stata un&rsquo;uscita e il ritardo di ripetizione è almeno 10: '
        'la prossima gara può chiudere la coppia.</p>'
    )
    parts.append('<div class="repeat-alerts"><h3>Partite in arrivo — possibile rottura della ripetizione</h3>')
    alerts = _repeat_alerts(model)
    if not alerts:
        parts.append('<p class="leg">Nessuna partita in arrivo con i requisiti.</p>')
    for alert in alerts:
        local = alert["when"].astimezone(ROME)
        kick = alert["when"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        bits = " | ".join(
            f"{esc(team)} · {esc(scope)} · ritardo {_label(delay, full)} · #{rank}/{total}"
            for team, scope, delay, full, rank, total in alert["rows"]
        )
        parts.append(
            f'<div class="fx" data-kick="{kick}"><b>{local:%d/%m %H:%M}</b> · '
            f'{esc(alert["home"])} vs {esc(alert["away"])} '
            f'<span class="lg">{esc(alert["country"])} - {esc(alert["league"])}</span>'
            f'<div class="tagrow">{bits}</div></div>'
        )
    parts.append("</div>")
    for venue, label, desc in VENUES:
        lines = [f"DOPPIA RIPETIZIONE {EVENT} — {label.upper()}", f"Aggiornato: {stamp}", ""]
        ranking = []
        for item in model["leagues"]:
            for row in item["repeats"][venue]:
                ranking.append((row, item))
        ranking.sort(key=lambda pair: (-pair[0]["delay"], pair[0]["team"].casefold()))
        visible = ranking[:30]
        for index, (row, item) in enumerate(visible, 1):
            block = item["block"]
            lines.append(f"{index:>3}. {row['team']:<28.28} {_label(row['delay'], row['full']):>5}  {block['country']} - {block['league']}")
        more = ""
        if len(ranking) > 30:
            full_lines = []
            for index, (row, item) in enumerate(ranking, 1):
                block = item["block"]
                full_lines.append(f"{index:>3}. {row['team']:<28.28} {_label(row['delay'], row['full']):>5}  {block['country']} - {block['league']}")
            more = f'<details><summary>Classifica {esc(label.lower())} completa ({len(ranking)} squadre)</summary><pre>{esc(chr(10).join(full_lines))}</pre></details>'
        parts.append(f'<div class="fx"><h3>{esc(label)}</h3><pre>{esc(chr(10).join(lines))}</pre>{more}</div>')
    parts.append("<h3>Frequenza campionati</h3>")
    freq_lines = [
        f"FREQUENZA CAMPIONATI — {EVENT}",
        f"Aggiornato: {stamp} (ora italiana)",
        "1 ogni N = partite della stagione / uscite 9-11. Almeno 7 giornate.",
        "",
    ]
    ranked = [item for item in model["leagues"] if item["freq"]]
    ranked.sort(key=lambda item: ((item["freq"]["ogni"] is None), item["freq"]["ogni"] or 10**9))
    for index, item in enumerate(ranked, 1):
        freq = item["freq"]
        block = item["block"]
        ogni = num_it(freq["ogni"]) if freq["ogni"] else "mai"
        freq_lines.append(f"{index:>2}. {block['country']} - {block['league']:<22} 1 ogni {ogni} ({freq['hits']}/{freq['played']})")
    parts.append(f"<pre>{esc(chr(10).join(freq_lines))}</pre>")
    parts.append("<h3>Sottofrequenze (ultime 100)</h3>")
    for venue, label, desc in VENUES:
        lines = [f"SOTTOFREQUENZE {EVENT} — {label.upper()}", f"Aggiornato: {stamp}", "Ultime 100, almeno 100 partite.", ""]
        for item in model["leagues"]:
            block = item["block"]
            rows = item["views"][venue]["sotto"]
            lines += ["=" * 50, f"{block['country']} - {block['league']}"]
            if not rows:
                lines.append("  Storico insufficiente.")
                continue
            top = rows[:5]
            if len(rows) > 5:
                cut = top[-1]["hits"]
                top = [row for row in rows if row["hits"] >= cut]
            for index, row in enumerate(top, 1):
                lines.append(f"  {index:>2}/{len(rows):<3} {row['team']:<28.28} {row['hits']}/{row['n']}")
            last = rows[-1]
            if len(rows) > len(top):
                lines += ["     ...", f"  {len(rows):>2}/{len(rows):<3} {last['team']:<28.28} {last['hits']}/{last['n']}  (la fa meno spesso)"]
        parts.append(
            f'<div class="venue" data-v="{venue}"><p class="vnote">Vista <b>{esc(label)}</b> · {esc(desc)}</p>'
            f'<pre>{esc(chr(10).join(lines))}</pre></div>'
        )
    parts.append(f"<h3>Saltati (ritardi &ge; {SALTATI_MIN}, ultimi {SALTATI_GIORNI} giorni)</h3>")
    any_salt = False
    parts.append('<div class="fxlist">')
    for venue, label, _desc in VENUES:
        events = []
        for item in model["leagues"]:
            for row in item["views"][venue]["salt"]:
                events.append((row, item, label))
        events.sort(key=lambda triple: triple[0]["when"], reverse=True)
        for row, item, scope in events:
            any_salt = True
            match = row["match"]
            local = match["when"].astimezone(ROME)
            block = item["block"]
            parts.append(
                f'<div class="fx"><div class="fxh"><span class="time">{local:%d/%m %H:%M}</span> '
                f'{esc(match["home"])} <b>{match["hc"]}-{match["ac"]}</b> {esc(match["away"])} '
                f'<span class="lg">{esc(block["country"])} - {esc(block["league"])}</span></div>'
                f'<div class="tagrow">{esc(scope)} · <b>{esc(row["team"])}</b>: {esc(EVENT)} uscita, era a {row["run"]}</div></div>'
            )
    if not any_salt:
        parts.append("<p>Nessun ritardo spezzato negli ultimi giorni.</p>")
    parts.append("</div>")
    return "\n".join(parts)

def _repeat_alerts(model):
    alerts = []
    for fixture in model["upcoming"]:
        report = next((item for item in model["leagues"] if item["block"]["div"] == fixture["div"]), None)
        if not report:
            continue
        rows = []
        for venue, scope in (("all", "TOTALE"), ("home", "CASA"), ("away", "TRASFERTA")):
            table = report["repeats"][venue]
            total = len(table)
            sides = [fixture["home"]] if venue == "home" else [fixture["away"]] if venue == "away" else [fixture["home"], fixture["away"]]
            for team in sides:
                found = next((row for row in table if row["team"] == team), None)
                if not found or not found["last"] or found["delay"] < REPEAT_ALERT_MIN:
                    continue
                rank = next(index for index, row in enumerate(table, 1) if row["team"] == team)
                rows.append((team, scope, found["delay"], found["full"], rank, total))
        if rows:
            block = report["block"]
            alerts.append({
                "when": fixture["when"], "home": fixture["home"], "away": fixture["away"],
                "country": block["country"], "league": block["league"], "rows": rows,
            })
    alerts.sort(key=lambda item: item["when"])
    return alerts

def _patch_page(section_html):
    path = "index.html"
    if not os.path.exists(path):
        raise SystemExit("Manca index.html: va generata prima la pagina combo.")
    page = open(path, encoding="utf-8").read()
    nav = '<a class="ssm" href="#corner">CORNER</a>'
    if nav not in page:
        page = page.replace(
            '<a class="ssm" href="#saltati">Saltati</a>',
            '<a class="ssm" href="#saltati">Saltati</a>' + nav,
            1,
        )
    links = (
        '<a href="corner_ritardi.txt">corner_ritardi.txt</a>'
        '<a href="corner_prossime.txt">corner_prossime.txt</a>'
        '<a href="corner_ripetizioni.txt">corner_ripetizioni.txt</a>'
        '<a href="corner_frequenza.txt">corner_frequenza.txt</a>'
        '<a href="corner_sottofrequenze.txt">corner_sottofrequenze.txt</a>'
        '<a href="corner_saltati.txt">corner_saltati.txt</a>'
    )
    if "corner_ritardi.txt" not in page:
        page = page.replace("</details>", links + "</details>", 1)
    block = "<!-- CORNER-SECTION -->\n<h2 id=\"corner\">CORNER 9-11</h2>\n" + section_html + "\n<!-- /CORNER-SECTION -->"
    start, end = "<!-- CORNER-SECTION -->", "<!-- /CORNER-SECTION -->"
    if start in page and end in page:
        page = page[:page.index(start)] + block + page[page.index(end) + len(end):]
    else:
        needle = '<button id="totop"'
        if needle not in page:
            raise SystemExit("Pagina senza punto di inserimento per CORNER.")
        page = page.replace(needle, block + "\n" + needle, 1)
    old = "function apply(){var now=Date.now();var root=document.getElementById('prossime');"
    if "function hideUpcoming" not in page and old in page:
        new = (
            "function hideUpcoming(root){if(!root)return;var sec=root.parentElement;if(!sec)return;"
            "var list=root.id==='prossime'?sec.querySelector('.fxlist'):sec.querySelector('.fxlist[data-upcoming]');"
            "if(!list)return;var now=Date.now();var nodes=[].slice.call(list.children);var shown=0;"
            "nodes.forEach(function(el){if(!(el.classList&&el.classList.contains('fx')))return;var ms=kickMs(el);"
            "var past=ms!==null&&ms<=now;setGone(el,past);if(!past)shown++;});"
            "nodes.forEach(function(el){if(!(el.tagName==='H3'&&el.classList.contains('day')))return;"
            "var n=el.nextElementSibling,any=false;while(n&&!(n.tagName==='H3'&&n.classList.contains('day'))){"
            "if(n.classList&&n.classList.contains('fx')&&!n.hidden)any=true;n=n.nextElementSibling;}setGone(el,!any);});"
            "var note=list.querySelector('.kick-empty');if(!shown){if(!note){note=document.createElement('p');"
            "note.className='leg kick-empty';note.textContent='Nessuna partita ancora da giocare. Quelle già iniziate restano nascoste.';"
            "list.appendChild(note);}setGone(note,false);}else if(note){setGone(note,true);}}"
            "function apply(){['prossime','corner'].forEach(function(id){hideUpcoming(document.getElementById(id));});"
        )
        page = page.replace(old, new, 1)
    tmp = path + ".corner-tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(page)
    os.replace(tmp, path)

def write_outputs(texts, section_html):
    names = {
        "corner_ritardi.txt": texts["ritardi"],
        "corner_sottofrequenze.txt": texts["sotto"],
        "corner_saltati.txt": texts["saltati"],
        "corner_frequenza.txt": texts["freq"],
        "corner_ripetizioni.txt": texts["repeat"],
        "corner_prossime.txt": texts["prossime"],
    }
    for name, body in names.items():
        tmp = name + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp, name)
    _patch_page(section_html)

def verify_team(model, div, team):
    """Stampa le ultime uscite e il ritardo, da confrontare con il CSV."""
    block = model["by_div"][div]
    seq = _seq(block, team, "all")
    print(f"VERIFICA {team} {div} partite nel ritardo {len(seq)}")
    for match in seq[-12:]:
        local = match["when"].astimezone(ROME)
        mark = "USCITA" if match["hit"] else "no"
        side = "casa" if match["home"] == team else "trasferta"
        other = match["away"] if match["home"] == team else match["home"]
        print(f"  {local:%d/%m/%Y} {side:<9} vs {other:<22} corner {match['hc']}+{match['ac']}={match['total']} {mark}")
    found = drought(seq)
    print(f"  ritardo totale = {found}")
    for venue in ("home", "away"):
        found_v = drought(_seq(block, team, venue))
        print(f"  ritardo {venue} = {found_v}")

def selftest():
    assert is_hit(9) and is_hit(10) and is_hit(11)
    assert not is_hit(8) and not is_hit(12)
    assert season_codes(datetime(2026, 11, 20, tzinfo=ROME))[0] == "2627"
    assert season_codes(datetime(2026, 6, 1, tzinfo=ROME))[0] == "2526"
    when, precise = london_kick("23/08/2025", "17:30")
    assert precise and when.astimezone(ROME).strftime("%H:%M") == "18:30"
    when, precise = london_kick("20/11/2026", "19:45")
    assert precise and when.astimezone(ROME).strftime("%H:%M") == "20:45"
    def match(hit, day):
        return {"when": datetime(2026, 1, day, tzinfo=timezone.utc), "home": "A", "away": "B",
                "hit": hit, "hc": 5, "ac": 5, "total": 10, "div": "I1", "season": "2526"}
    seq = [match(False, 1), match(True, 2), match(False, 3), match(False, 4)]
    assert drought(seq) == (2, False)
    assert drought([match(False, 1), match(False, 2)]) == (2, True)
    delay, last, full = repeat_state([match(True, 1), match(False, 2), match(False, 3)])
    assert (delay, last, full) == (1, False, True)
    delay, last, full = repeat_state([match(True, 1), match(True, 2), match(False, 3)])
    assert delay == 1 and last is False and full is False
    sample = (
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n"
        "I1,01/09/2025,15:00,Alpha,Beta,1,0,6,4\n"
        "I1,08/09/2025,15:00,Alpha,Gamma,0,0,,\n"
    ).encode()
    parsed = parse_results(sample, "I1", "2526")
    assert len(parsed) == 1 and parsed[0]["hit"] and parsed[0]["total"] == 10
    print("selftest ok")

def main():
    selftest()
    print("Scarico gli storici corner...", flush=True)
    history = load_history()
    fixtures = load_fixtures()
    model = build(history, fixtures)
    if not model["leagues"]:
        raise SystemExit("Nessun campionato con corner: non aggiorno la sezione.")
    stamp = datetime.now(ROME).strftime("%d/%m/%Y %H:%M")
    texts = render_texts(model, stamp)
    section = render_html(model, stamp)
    write_outputs(texts, section)
    print("Campionati con corner:", flush=True)
    for item in model["leagues"]:
        block = item["block"]
        print(
            f"  {block['div']} {block['country']} - {block['league']}: "
            f"{len(block['all'])} partite, stagioni {block['seasons'][-1]}–{block['seasons'][0]}",
            flush=True,
        )
    print(f"Prossime in elenco: {len(_upcoming_cards(model))}", flush=True)
    for div, team in (("I1", "Inter"), ("E0", "Arsenal")):
        if div in model["by_div"]:
            verify_team(model, div, team)

if __name__ == "__main__":
    main()
