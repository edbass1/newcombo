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
import csv, io, os, html, json, re, time, unicodedata, urllib.request
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ROME = ZoneInfo("Europe/Rome")
MADRID = ZoneInfo("Europe/Madrid")
LONDON = ZoneInfo("Europe/London")
EVENT = "CORNER 9-11"
TOP = 10
SOTTO_N = 100
SOTTO_MIN = 100
SALTATI_MIN = 6
SALTATI_GIORNI = 7
MIN_GIORNATE = 7
PROSSIME_TOP = 2
RED_TOP = 2
GIORNI = 5
REPEAT_ALERT_MIN = 4
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

# Campionato corner -> id FotMob. Le prossime partite arrivano da qui;
# fixtures.csv e adamchoi restano di riserva.
FOTMOB_IDS = {
    "E0": 47, "E1": 48, "E2": 108, "E3": 109, "EC": 117,
    "SC0": 64, "SC1": 123, "SC2": 124, "SC3": 125,
    "D1": 54, "D2": 146, "D3": 208,
    "I1": 55, "I2": 86,
    "SP1": 87, "SP2": 140,
    "F1": 53, "F2": 110,
    "N1": 57, "N2": 111,
    "B1": 40,
    "P1": 61,
    "T1": 71,
    "G1": 135,
    "AUT1": 38,
    "SU1": 69,
    "NT1": 59,
    "DEN1": 46,
    "SA1": 67,
    "CZ1": 122,
    "BR1": 268,
    "BR2": 8814,
    "ARG1": 112,
    "MLS1": 130,
    "AUS1": 113,
    "FV1": 51,
    "POL1": 196,
    "JAP1": 223,
    "MX1": 230,
    "COL1": 274,
    "CHN1": 120,
    "ECU1": 246,
    "PER1": 131,
    "RUS1": 63,
    "SAU1": 536,
    "SKO1": 9080,
    "UK1": 441,
}

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
    # L'ordine cronologico deve essere sempre crescente: i CSV e le sorgenti
    # aggregate possono arrivare in ordine diverso. L'ultimo flag deve riferirsi
    # alla partita più recente, non all'ultimo record ricevuto.
    seq = sorted(seq, key=lambda match: match["when"])
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
    start = madrid_start(now)
    horizon = now + timedelta(days=GIORNI)
    for row in reader:
        def col(key):
            real = fields.get(key)
            return row.get(real, "") if real else ""
        when, precise = london_kick(col("Date"), col("Time"))
        home, away, div = col("HomeTeam").strip(), col("AwayTeam").strip(), col("Div").strip()
        if not precise or not when or not home or not away or not div:
            continue
        if not (start <= when <= horizon):
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

def build(history, fixtures, now=None, leagues=None):
    now = now or datetime.now(timezone.utc)
    leagues = leagues or LEAGUES
    by_div = {}
    for country, league, div in leagues:
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
        return sorted(previous + current, key=lambda match: match["when"])
    return sorted(
        (m for m in source if in_venue(m, team, venue)),
        key=lambda match: match["when"],
    )

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

def _classifica(model, venue, kind):
    """Classifica globale della vista. Il posto è quello della prima squadra con lo stesso ritardo."""
    rows = []
    for item in model["leagues"]:
        block = item["block"]
        source = item["views"][venue]["rows"] if kind == "ritardo" else item["repeats"][venue]
        for row in source:
            rows.append({
                "team": row["team"], "delay": row["delay"], "full": row["full"],
                "last": row.get("last", False),
                "div": block["div"], "country": block["country"], "league": block["league"],
                "matches": row.get("matches"),
            })
    rows.sort(key=lambda row: (
        -row["delay"], row["team"].casefold(), row["country"].casefold(), row["league"].casefold()
    ))
    previous = None
    rank = 0
    total = len(rows)
    for index, row in enumerate(rows, 1):
        if row["delay"] != previous:
            rank = index
            previous = row["delay"]
        row["rank"] = rank
        row["total"] = total
    if rows and kind == "ritardo":
        cutoff = rows[min(RED_TOP, len(rows)) - 1]["delay"]
        for row in rows:
            row["red"] = row["delay"] >= cutoff
    else:
        for row in rows:
            row["red"] = False
    return rows

def _top_slice(rows, n):
    """Prime n, con il pari merito sull'ultimo posto."""
    if len(rows) <= n:
        return rows
    cutoff = rows[n - 1]["delay"]
    return [row for row in rows if row["delay"] >= cutoff]

def _by_team(rows):
    return {(row["div"], row["team"]): row for row in rows}

def _line_text(kind, row):
    return f"{kind.upper():<12} {row['team']}: ({_label(row['delay'], row['full'])}) #{row['rank']}/{row['total']}"

def _line_html(kind, row, view):
    slug = {"all": "totale", "home": "casa", "away": "trasferta"}[view]
    n = _label(row["delay"], row["full"])
    return (
        f'<div class="tagrow" data-kind="{esc(kind)}" data-team="{esc(row["team"])}" '
        f'data-n="{esc(n)}" data-view="{slug}">'
        f'{esc(kind.upper())} · <b>{esc(row["team"])}</b> '
        f'({esc(n)}) · #{row["rank"]}/{row["total"]}</div>'
    )

COPY_BUTTON_HTML = (
    '<div id="copy-corner-bar" style="display:flex;flex-wrap:wrap;align-items:center;gap:.75rem 1.1rem;margin:.35rem 0 .9rem">'
    '<a href="#corner" id="copy-corner-today" role="button" '
    'style="display:inline-block;font-size:1.35rem;font-weight:800;background:#000;color:#fff;'
    'border:2px solid #fff;border-radius:10px;padding:.7rem 1.15rem;text-decoration:none;cursor:pointer">'
    'Copia partite del giorno</a>'
    '<span id="copy-corner-days" role="group" aria-label="Giorni da copiare" '
    'style="display:flex;flex-wrap:wrap;align-items:center;gap:.35rem .95rem"></span>'
    '</div>'
)

COPY_SCRIPT = r"""<script>(function(){
/* corner-days-v2: copia i giorni spuntati, non solo la data di oggi */
function romeStamp(ms){var fmt=new Intl.DateTimeFormat('it-IT',{timeZone:'Europe/Rome',hourCycle:'h23',weekday:'short',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});var map={};fmt.formatToParts(new Date(ms)).forEach(function(p){if(p.type!=='literal')map[p.type]=p.value;});var hour=map.hour==='24'?'00':map.hour;var wd=(map.weekday||'').replace(/\./g,'');wd=wd.charAt(0).toUpperCase()+wd.slice(1);return {date:map.year+'-'+map.month+'-'+map.day,hm:hour+':'+map.minute,label:wd+' '+map.day+'/'+map.month};}
function visible(el){return el&&!el.hidden&&el.style.display!=='none';}
function viewOf(card){var v=card.querySelector('.vtag');var t=(v?v.textContent:'').replace(/\s+/g,'').toUpperCase();if(t==='CASA')return 'casa';if(t==='TRASFERTA')return 'trasferta';return 'totale';}
function rowInfo(row,view){if(row.getAttribute('data-kind')){return {kind:row.getAttribute('data-kind'),team:row.getAttribute('data-team'),n:row.getAttribute('data-n'),view:row.getAttribute('data-view')||view};}var b=row.querySelector('b');var team=b?(b.textContent||'').trim():'';var raw=(row.textContent||'');var kind=/RIPETIZIONE/i.test(raw)?'ripetizione':(/RITARDO/i.test(raw)?'ritardo':'');var nm=raw.match(/\(([^)]+)\)/);return {kind:kind,team:team,n:nm?nm[1].trim():'',view:view};}
function upcomingList(){var root=document.getElementById('corner');if(!root)return null;var sec=root.closest('section');if(sec)return sec.querySelector('.fxlist[data-upcoming]');var n=root.nextElementSibling;while(n&&!(n.tagName==='H2'&&n!==root)){if(n.matches&&n.matches('.fxlist[data-upcoming]'))return n;var inner=n.querySelector&&n.querySelector('.fxlist[data-upcoming]');if(inner)return inner;n=n.nextElementSibling;}return null;}
function readCard(card){if(!visible(card))return null;var kick=card.getAttribute('data-kick');var when=kick?Date.parse(kick):NaN;if(isNaN(when))return null;var stamp=romeStamp(when);var home=card.getAttribute('data-home'),away=card.getAttribute('data-away');if(!home||!away){var time=card.querySelector('.time');var copy=time?time.getAttribute('data-copy'):'';var parts=(copy||'').split(' vs ');home=home||parts[0]||'';away=away||parts.slice(1).join(' vs ')||'';}var bits=[],view=viewOf(card);[].slice.call(card.querySelectorAll('.tagrow')).forEach(function(row){var info=rowInfo(row,view);if(!info.kind||!info.team)return;bits.push(info.team+' ('+info.kind+' '+info.n+' '+info.view+')');});return {date:stamp.date,label:stamp.label,hm:stamp.hm,home:home,away:away,key:stamp.hm+'|'+home+'|'+away,bits:bits};}
function collectDays(){var list=upcomingList(),days=[],by={};if(!list)return days;[].slice.call(list.querySelectorAll(':scope > .fx')).forEach(function(card){var info=readCard(card);if(!info)return;if(!by[info.date]){by[info.date]={date:info.date,label:info.label,matches:{}};days.push(by[info.date]);}var bucket=by[info.date].matches;if(!bucket[info.key])bucket[info.key]={hm:info.hm,home:info.home,away:info.away,bits:[]};info.bits.forEach(function(bit){bucket[info.key].bits.push(bit);});});days.forEach(function(day){day.keys=Object.keys(day.matches).sort(function(a,b){var ga=day.matches[a],gb=day.matches[b];if(ga.hm!==gb.hm)return ga.hm<gb.hm?-1:1;return a<b?-1:1;});day.count=day.keys.length;});days.sort(function(a,b){return a.date<b.date?-1:a.date>b.date?1:0;});return days;}
function paintDays(){var box=document.getElementById('copy-corner-days');if(!box)return;var days=collectDays();var sig=days.map(function(day){return day.date+':'+day.count;}).join('|');if(box.getAttribute('data-sig')===sig)return;var prev={},had=!!box.querySelector('input'),hadChecked=false;[].forEach.call(box.querySelectorAll('input'),function(inp){prev[inp.value]=inp.checked;if(inp.checked)hadChecked=true;});box.setAttribute('data-sig',sig);box.textContent='';days.forEach(function(day,index){var label=document.createElement('label');label.style.cssText='display:inline-flex;align-items:center;gap:.4rem;color:#fff;font-weight:700;font-size:1.05rem;cursor:pointer;background:#000';var input=document.createElement('input');input.type='checkbox';input.value=day.date;input.style.cssText='width:1.15rem;height:1.15rem;accent-color:#fff';if(!had)input.checked=index===0;else if(Object.prototype.hasOwnProperty.call(prev,day.date))input.checked=!!prev[day.date];var text=document.createElement('span');text.textContent=day.label+' ('+day.count+')';label.appendChild(input);label.appendChild(text);box.appendChild(label);});var inputs=box.querySelectorAll('input'),any=false;[].forEach.call(inputs,function(inp){if(inp.checked)any=true;});if(!any&&inputs[0]&&(!had||hadChecked))inputs[0].checked=true;}
function buildCornerDay(){var days=collectDays(),picked={},box=document.getElementById('copy-corner-days');if(box)[].forEach.call(box.querySelectorAll('input:checked'),function(inp){picked[inp.value]=true;});var lines=[];days.forEach(function(day){if(!picked[day.date])return;lines.push('CORNER 9-11 | '+day.date);day.keys.forEach(function(key){var match=day.matches[key];lines.push(match.hm+' | '+match.home+' - '+match.away+' | '+match.bits.join('; '));});});return lines.join('\n');}
function showNote(text){var msg=document.getElementById('copymsg');if(!msg){msg=document.createElement('div');msg.id='copymsg';document.body.appendChild(msg);}msg.style.cssText='position:fixed;bottom:20px;left:50%;transform:translateX(-50%);z-index:300;background:#000;color:#fff;border:2px solid #fff;padding:.7rem 1.1rem;border-radius:10px;font-weight:800;font-size:1rem;max-width:min(92vw,760px);white-space:pre-wrap;text-align:left';msg.textContent=text;msg.style.display='block';clearTimeout(msg._timer);msg._timer=setTimeout(function(){msg.style.display='none';},text.length>24?4500:1800);}
function fallback(txt){var ta=document.createElement('textarea');ta.value=txt;ta.setAttribute('readonly','');ta.style.position='fixed';ta.style.left='-9999px';document.body.appendChild(ta);ta.select();var ok=false;try{ok=document.execCommand('copy');}catch(e){ok=false;}ta.remove();if(ok)showNote('Copiato!');else window.prompt('Copia il testo:',txt);}
function copyText(txt){if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(txt).then(function(){showNote('Copiato!');}).catch(function(){fallback(txt);});}else fallback(txt);}
function arm(){var list=upcomingList();if(list&&!list._cornerDays){list._cornerDays=new MutationObserver(function(){paintDays();});list._cornerDays.observe(list,{subtree:true,attributes:true,attributeFilter:['hidden','style','class'],childList:true});}paintDays();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',arm);else arm();setTimeout(arm,0);setTimeout(arm,400);
document.addEventListener('click',function(e){var btn=e.target&&e.target.closest&&e.target.closest('#copy-corner-today');if(!btn)return;e.preventDefault();var txt=buildCornerDay();if(!txt){showNote('Seleziona un giorno');return;}copyText(txt);});
if(!window.__cornerWhen){window.__cornerWhen=true;document.addEventListener('click',function(e){var btn=e.target&&e.target.closest&&e.target.closest('.corner-when');if(!btn)return;e.preventDefault();e.stopPropagation();var txt=btn.getAttribute('data-copy')||'';if(!txt)return;function shown(){showNote(txt);}if(navigator.clipboard&&window.isSecureContext){navigator.clipboard.writeText(txt).then(shown).catch(function(){fallback(txt);showNote(txt);});}else{fallback(txt);showNote(txt);}});}
})();</script>"""

_OLD_COPY_BUTTON = re.compile(
    r'<p><a href="#corner" id="copy-corner-today"[\s\S]*?</a></p>'
)

def ensure_day_copy(html):
    """Inserisce o aggiorna il pulsante, le caselle dei giorni e lo script."""
    if 'id="copy-corner-days"' not in html:
        if _OLD_COPY_BUTTON.search(html):
            html = _OLD_COPY_BUTTON.sub(COPY_BUTTON_HTML, html, count=1)
        else:
            needle = '<div class="fxlist" data-upcoming="1">'
            if needle in html and 'id="copy-corner-today"' not in html:
                html = html.replace(needle, COPY_BUTTON_HTML + needle, 1)
    if "corner-days-v2" not in html:
        start = 0
        replaced = False
        while True:
            index = html.find("<script>(function(){", start)
            if index < 0:
                break
            end = html.find("</script>", index)
            if end < 0:
                break
            chunk = html[index:end]
            if "function buildCornerDay" in chunk:
                html = html[:index] + COPY_SCRIPT + html[end + len("</script>"):]
                replaced = True
                break
            start = end + len("</script>")
        if not replaced and "<!-- /CORNER-SECTION -->" in html:
            html = html.replace("<!-- /CORNER-SECTION -->", COPY_SCRIPT + "\n<!-- /CORNER-SECTION -->", 1)
    return html

def _extra_blocks(model):
    known = {div for _country, _league, div in LEAGUES}
    return [item["block"] for item in model["leagues"] if item["block"]["div"] not in known]


def _pretty(text):
    words = []
    for word in text.split(" "):
        if word.isupper() and len(word) <= 3:
            words.append(word)
        else:
            words.append(word.title())
    return " ".join(words)


def fonte(model):
    """Riga di fonte per i testi. Non contiene la fascia, così le tre sezioni restano sostituibili."""
    if not _extra_blocks(model):
        return "Fonte: football-data.co.uk."
    return ("Fonte: football-data.co.uk per i campionati già coperti. "
            "Gli altri campionati gratuiti arrivano da adamchoi.co.uk.")


# Campionati chiesti per primi nella nota, se davvero presenti nei dati.
_FONTE_PRIMI = (
    ("OLANDA", "EERSTE DIVISIE"),
    ("SVIZZERA", "SUPER LEAGUE"),
    ("AUSTRIA", "BUNDESLIGA"),
    ("NORVEGIA", "ELITESERIEN"),
    ("DANIMARCA", "SUPERLIGAEN"),
    ("SVEZIA", "ALLSVENSKAN"),
    ("REPUBBLICA CECA", "CZECH LIGA"),
    ("BRASILE", "SERIE A"),
    ("BRASILE", "SERIE B"),
    ("ARGENTINA", "PRIMERA"),
    ("USA", "MLS"),
    ("AUSTRALIA", "A-LEAGUE"),
    ("FINLANDIA", "VEIKKAUSLIIGA"),
    ("POLONIA", "EKSTRAKLASA"),
    ("GIAPPONE", "J LEAGUE"),
    ("MESSICO", "LIGA MX"),
    ("COLOMBIA", "PRIMERA A"),
)


def fonte_page(model):
    extra = _extra_blocks(model)
    if not extra:
        return "Fonte: football-data.co.uk. Gli orari delle prossime partite sono in ora italiana."
    present = {(block["country"], block["league"]) for block in extra}
    names = [f"{_pretty(country)} {_pretty(league)}" for country, league in _FONTE_PRIMI if (country, league) in present]
    others = len(present - set(_FONTE_PRIMI))
    listed = ", ".join(names)
    if others:
        listed += f" e altri {others}"
    return ("Fonte: football-data.co.uk per i campionati già coperti. "
            f"Campionati extra gratuiti da adamchoi.co.uk: {esc(listed)}. "
            "Gli orari sono in ora italiana.")


# Forma compatta -> forma usata nella classifica. Copre i nomi FotMob più lunghi
# e ogni squadra rimasta fuori nel giro del 27/11/2026.
_NAME_ALIASES = {
    "hellasverona": "verona",
    "borussiamonchengladbach": "mgladbach",
    "monchengladbach": "mgladbach",
    "mgladbach": "mgladbach",
    "bayernmunchen": "bayernmunich",
    "eintrachtfrankfurt": "einfrankfurt",
    "1fckoln": "fckoln",
    "hamburgersv": "hamburg",
    "mainz05": "mainz",
    "vfbstuttgart": "stuttgart",
    "borussiadortmund": "dortmund",
    "bayerleverkusen": "leverkusen",
    "bayer04leverkusen": "leverkusen",
    "1fcheidenheim": "heidenheim",
    "vflwolfsburg": "wolfsburg",
    "rasporting": "sporting",
    "sportingcp": "sporting",
    "athleticbilbao": "athbilbao",
    "atleticobilbao": "athbilbao",
    "athleticclub": "athbilbao",
    "realsociedad": "sociedad",
    "rayovallecano": "vallecano",
    "atleticomadrid": "athmadrid",
    "atleticodemadrid": "athmadrid",
    "wolverhamptonwanderers": "wolves",
    "wolverhampton": "wolves",
    "queensparkrangers": "qpr",
    "chivas": "guadalajara",
    "guadalajarachivas": "guadalajara",
    "cdguadalajara": "guadalajara",
    "deportivoguadalajara": "guadalajara",
    "urawareddiamonds": "urawareds",
    "urawarediamond": "urawareds",
    "fkakhmat": "terekgrozny",
    "fcakhmat": "terekgrozny",
    "akhmatgrozny": "terekgrozny",
    "akhmat": "terekgrozny",
    "manchesterunited": "manunited",
    "manchesterutd": "manunited",
    "manchestercity": "mancity",
    "nottinghamforest": "nottmforest",
    "brightonandhovealbion": "brighton",
    "brightonhovealbion": "brighton",
}
# Parole di contorno, non il nome della squadra.
_NAME_STOP = {
    "afc", "fc", "ac", "sc", "ss", "us", "as", "cf", "cd", "fk", "sk", "sv",
    "vfb", "vfl", "tsg", "kv", "ksv", "rsc", "rsca", "club", "calcio", "de", "the",
}
# Suffissi generici: si tolgono solo nel confronto largo, così "Town" non pareggia
# tutte le squadre "Town" e "Madrid" non pareggia Real Madrid con l'Atlético.
_NAME_SOFT = {
    "town", "city", "united", "utd", "wanderers", "rovers", "hotspur", "albion",
    "county", "alexandra", "stanley", "wednesday", "orient", "deportivo",
    "athletic", "rangers",
}
# II, B, U23: una seconda squadra non è la prima squadra della classifica.
_RESERVE = {"ii", "iii", "b", "u17", "u18", "u19", "u20", "u21", "u23", "reserves", "reserve", "am"}
VIEW_WORD = {"all": "totale", "home": "casa", "away": "trasferta"}
BADGE_CSS = """<style>
.corner-when{display:inline-block;margin-left:.4rem;padding:.08rem .42rem;border:0;border-radius:6px;font-size:.68rem;font-weight:900;letter-spacing:.04em;cursor:pointer;vertical-align:middle}
.corner-when.oggi{background:#d50000;color:#fff}
.corner-when.domani{background:#6a1b9a;color:#fff}
.cr-list{margin:.2rem 0 1rem}
.cr-row{display:grid;grid-template-columns:2.6rem minmax(0,1fr) auto;gap:.05rem .5rem;align-items:baseline;padding:.16rem 0;border-bottom:1px solid var(--line);font-size:.92rem}
.cr-rank{color:var(--accent);font-variant-numeric:tabular-nums}
.cr-delay{font-weight:900;color:var(--accent2);font-variant-numeric:tabular-nums}
.cr-lg{grid-column:2/4;color:var(--muted);font-size:.76rem}
.cr-h{margin:.9rem 0 .15rem;color:var(--accent2);font-size:1rem}
</style>"""


def madrid_start(now=None):
    """Mezzanotte di oggi a Madrid, in UTC. Le partite già iniziate oggi restano nel giorno."""
    now = now or datetime.now(MADRID)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    local = now.astimezone(MADRID)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def madrid_day(when, today=None):
    """'oggi', 'domani' o None, sul calendario di Europe/Madrid."""
    if today is None:
        today = datetime.now(MADRID)
    elif today.tzinfo is None:
        today = today.replace(tzinfo=MADRID)
    day = when.astimezone(MADRID).date()
    origin = today.astimezone(MADRID).date()
    if day == origin:
        return "oggi"
    if day == origin + timedelta(days=1):
        return "domani"
    return None


def _fold_team(name):
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("'", "")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _compact_team(name):
    return re.sub(r"[^a-z0-9]+", "", _fold_team(name))


def norm_team(name):
    text = _compact_team(name)
    return _NAME_ALIASES.get(text, text)


def _tokens(name):
    return [tok for tok in _fold_team(name).split() if tok and not tok.isdigit()]


def _is_reserve(name):
    toks = _tokens(name)
    return bool(toks) and toks[-1] in _RESERVE


def _compatible(query, team):
    """Una squadra B o U23 non prende il posto della prima squadra, e il contrario."""
    return _is_reserve(query) == _is_reserve(team)


def _solid(name):
    return [tok for tok in _tokens(name) if tok not in _NAME_STOP and tok not in _RESERVE]


def _loose(name):
    return [tok for tok in _solid(name) if tok not in _NAME_SOFT]


def _alias_key(name):
    compact = _compact_team(name)
    solid = "".join(_solid(name))
    return _NAME_ALIASES.get(compact) or _NAME_ALIASES.get(solid)


def _team_keys(team):
    keys = {_compact_team(team), "".join(_solid(team))}
    alias = _alias_key(team)
    if alias:
        keys.add(alias)
    return {key for key in keys if key}


def match_name(name, teams, fuzzy=True):
    """Nome della classifica. Il token più lungo e specifico vince sul token condiviso.

    fuzzy aggiunge la somiglianza solo se un nome resta unico e staccato dal secondo.
    """
    pool = [team for team in teams if _compatible(name, team)]
    if not pool:
        return None
    alias = _alias_key(name)
    if alias:
        hits = [team for team in pool if alias in _team_keys(team)]
        if len(hits) == 1:
            return hits[0]
        exact = [team for team in hits if _compact_team(team) == alias]
        if len(exact) == 1:
            return exact[0]
        if len(hits) > 1:
            return None
    solid_q = _solid(name)
    if solid_q:
        exact = [team for team in pool if _solid(team) == solid_q]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            return None
    loose_q = _loose(name)
    if loose_q:
        contained = []
        for team in pool:
            loose_t = _loose(team)
            if loose_t and set(loose_q) <= set(loose_t):
                contained.append(team)
        if contained:
            contained.sort(key=lambda team: (len(_loose(team)) - len(loose_q), team))
            extra = len(_loose(contained[0])) - len(loose_q)
            top = [team for team in contained if len(_loose(team)) - len(loose_q) == extra]
            if len(top) == 1:
                return top[0]
    if not fuzzy:
        return None
    qkey = "".join(solid_q) or _compact_team(name)
    scored = []
    for team in pool:
        tkey = "".join(_solid(team)) or _compact_team(team)
        if len(qkey) < 4 or len(tkey) < 4:
            continue
        ratio = int(SequenceMatcher(None, qkey, tkey).ratio() * 100)
        scored.append((ratio, team))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 86:
        return None
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - 5:
        return None
    return scored[0][1]


def _rescue(name, teams):
    """L'avversario è già abbinato: resta una sola squadra abbastanza simile."""
    qkey = "".join(_solid(name)) or _compact_team(name)
    if len(qkey) < 4:
        return None
    ranked = []
    for team in teams:
        if not _compatible(name, team):
            continue
        tkey = "".join(_solid(team)) or _compact_team(team)
        if len(tkey) < 4:
            continue
        ratio = int(SequenceMatcher(None, qkey, tkey).ratio() * 100)
        ranked.append((ratio, team))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < 88:
        return None
    if len(ranked) > 1 and ranked[1][0] >= ranked[0][0] - 8:
        return None
    return ranked[0][1]


def _match_sides(home_name, away_name, teams):
    """Prima i nomi sicuri. Se ne manca uno, si cerca solo tra le squadre rimaste."""
    squad = set(teams)
    home = match_name(home_name, squad, fuzzy=False)
    away = match_name(away_name, squad, fuzzy=False)
    if home and not away:
        away = match_name(away_name, squad - {home}, fuzzy=True) or _rescue(away_name, squad - {home})
    elif away and not home:
        home = match_name(home_name, squad - {away}, fuzzy=True) or _rescue(home_name, squad - {away})
    elif not home and not away:
        home = match_name(home_name, squad, fuzzy=True)
        away = match_name(away_name, squad, fuzzy=True)
        if home and not away:
            away = _rescue(away_name, squad - {home})
        elif away and not home:
            home = _rescue(home_name, squad - {away})
    return home, away


def explain_miss(name, div, teams_by_div):
    """Perché un nome di oggi o domani non ha il badge."""
    if _is_reserve(name):
        return "squadra riserve o seconda squadra, non è in classifica"
    hits = []
    for other, teams in teams_by_div.items():
        hit = match_name(name, teams, fuzzy=False)
        if hit:
            hits.append((other, hit))
    if not hits:
        return "non è in nessuna classifica"
    if div and not any(other == div for other, _hit in hits):
        shown = ", ".join(f"{other} ({hit})" for other, hit in hits[:3])
        return f"non è in {div}; in classifica come {shown}"
    return "abbinamento ambiguo"


def _resolve_team(name, div, teams_by_div):
    if div and teams_by_div.get(div):
        team = match_name(name, teams_by_div[div])
        if team:
            return div, team
    hits = []
    for other, teams in teams_by_div.items():
        team = match_name(name, teams, fuzzy=False)
        if team:
            hits.append((other, team))
    unique = {(item[0], item[1]) for item in hits}
    if len(unique) == 1:
        return next(iter(unique))
    return None, None


def load_fotmob_fixtures(path="fotmob_fixtures.json"):
    """Prossime gare già scaricate da gen.py. Se il file manca, i badge usano le altre fonti."""
    if not os.path.exists(path):
        return []
    try:
        raw = json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            when = datetime.fromisoformat(str(row.get("when") or "").replace("Z", "+00:00"))
        except ValueError:
            continue
        home, away = str(row.get("home") or "").strip(), str(row.get("away") or "").strip()
        if home and away:
            out.append({"when": when, "home": home, "away": away, "div": str(row.get("div") or "")})
    return out


def _teams_by_div(model):
    out = {}
    for div, block in model.get("by_div", {}).items():
        names = set(block.get("teams") or [])
        if names:
            out[div] = names
    return out


def playing_map(model, extra=None, today=None):
    """Per ogni squadra e vista, la prima gara di oggi o domani. Non guarda la top 10."""
    teams_by_div = _teams_by_div(model)
    best = {}
    pool = list(model.get("upcoming") or []) + list(extra if extra is not None else [])
    for raw in pool:
        when = raw.get("when")
        if not isinstance(when, datetime):
            continue
        day = madrid_day(when, today)
        if not day:
            continue
        div = raw.get("div") or ""
        squad = teams_by_div.get(div) if div else None
        if squad:
            home, away = _match_sides(raw.get("home") or "", raw.get("away") or "", squad)
            home_div = div if home else ""
            away_div = div if away else ""
        else:
            home_div, home = _resolve_team(raw.get("home") or "", "", teams_by_div)
            away_div, away = _resolve_team(raw.get("away") or "", "", teams_by_div)
            if home and not away and home_div:
                _home, away = _match_sides(raw.get("home") or "", raw.get("away") or "", teams_by_div.get(home_div) or [])
                away_div = home_div if away else ""
            elif away and not home and away_div:
                home, _away = _match_sides(raw.get("home") or "", raw.get("away") or "", teams_by_div.get(away_div) or [])
                home_div = away_div if home else ""
        info = {
            "when": when, "day": day,
            "home": home or raw.get("home") or "",
            "away": away or raw.get("away") or "",
        }
        sides = []
        if home:
            sides.append((home_div or div, home, "home"))
        if away:
            sides.append((away_div or div, away, "away"))
        for side_div, team, side in sides:
            if not side_div or not team:
                continue
            for venue in ("all", side):
                key = (side_div, team, venue)
                prev = best.get(key)
                if prev is None or info["when"] < prev["when"]:
                    best[key] = info
    return best


def current_teams(history, leagues):
    """Squadre della stagione in corso, i nomi con cui sono scritte le classifiche."""
    out = {}
    for _country, _league, div in leagues:
        seasons = history.get(div) or {}
        usable = [code for code, rows in seasons.items() if rows and str(code).isdigit()]
        if not usable:
            continue
        code = max(usable, key=int)
        names = set()
        for match in seasons[code]:
            names.add(match["home"])
            names.add(match["away"])
        if names:
            out[div] = names
    return out


def fetch_fotmob_fixtures(divs):
    """Prossime gare FotMob, una lega alla volta. Un errore non ferma le altre."""
    now = datetime.now(timezone.utc)
    start = madrid_start(now)
    horizon = now + timedelta(days=GIORNI)
    out = []
    first = True
    for div in divs:
        lid = FOTMOB_IDS.get(div)
        if not lid:
            continue
        if not first:
            time.sleep(0.6)
        first = False
        url = f"https://www.fotmob.com/api/data/leagues?id={lid}"
        raw = fetch(url)
        if not raw:
            print(f"fotmob: {div} ({lid}) non disponibile, uso le altre fonti.", flush=True)
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
            matches = (data.get("fixtures") or {}).get("allMatches") or []
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            print(f"fotmob: {div} ({lid}) risposta illeggibile.", flush=True)
            continue
        kept = 0
        for match in matches:
            status = match.get("status") or {}
            if status.get("cancelled") or status.get("finished"):
                continue
            try:
                when = datetime.fromisoformat(str(status.get("utcTime") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if not (start <= when <= horizon):
                continue
            home = ((match.get("home") or {}).get("name") or "").strip()
            away = ((match.get("away") or {}).get("name") or "").strip()
            if not home or not away:
                continue
            out.append({"when": when, "home": home, "away": away, "div": div, "source": "fotmob"})
            kept += 1
        print(f"fotmob: {div} {kept} prossime", flush=True)
    return out


def _align_pair(fix, teams_by_div):
    div = fix.get("div") or ""
    if div and teams_by_div.get(div):
        home, away = _match_sides(fix.get("home") or "", fix.get("away") or "", teams_by_div[div])
        return div, home, away
    both = None
    one = None
    for other, teams in teams_by_div.items():
        home = match_name(fix.get("home") or "", teams, fuzzy=False)
        away = match_name(fix.get("away") or "", teams, fuzzy=False)
        if home and not away:
            away = _rescue(fix.get("away") or "", [team for team in teams if team != home])
        elif away and not home:
            home = _rescue(fix.get("home") or "", [team for team in teams if team != away])
        if home and away:
            both = (other, home, away)
            break
        if (home or away) and one is None:
            one = (other, home, away)
    return both or one or ("", None, None)


def merge_fixtures(teams_by_div, groups):
    """FotMob prima, poi le altre fonti. Stessa gara e stesso giorno: una sola riga.

    Se una fonte abbina entrambe le squadre e l'altra una sola, resta quella completa.
    """
    best = {}
    order = []
    missed = {}
    for _label, fixtures in groups:
        for fix in fixtures:
            div, home, away = _align_pair(fix, teams_by_div)
            raw_key = (
                fix["when"].astimezone(MADRID).date().isoformat(),
                norm_team(fix.get("home") or ""),
                norm_team(fix.get("away") or ""),
            )
            if not home and not away:
                if div and madrid_day(fix["when"]):
                    missed.setdefault(raw_key, (div, fix.get("home") or "", fix.get("away") or ""))
                continue
            if not div:
                continue
            missed.pop(raw_key, None)
            canon_home = home or fix.get("home") or ""
            canon_away = away or fix.get("away") or ""
            key = (
                div,
                fix["when"].astimezone(MADRID).date().isoformat(),
                norm_team(canon_home),
                norm_team(canon_away),
            )
            score = int(bool(home)) + int(bool(away))
            chosen = {
                "when": fix["when"], "div": div, "home": canon_home, "away": canon_away,
                "raw_home": fix.get("home") or "", "raw_away": fix.get("away") or "",
                "home_ok": bool(home), "away_ok": bool(away),
            }
            prev = best.get(key)
            if prev is None:
                best[key] = (score, chosen)
                order.append(key)
            elif score > prev[0]:
                best[key] = (score, chosen)
    merged = []
    stats = {"both": 0, "one": 0, "none": 0, "rows": 0, "unmatched": []}
    seen_names = set()
    for div, home_name, away_name in missed.values():
        stats["rows"] += 1
        stats["none"] += 1
        for missing in (home_name, away_name):
            if not missing or missing in seen_names:
                continue
            seen_names.add(missing)
            stats["unmatched"].append((div or "—", missing, home_name, away_name, explain_miss(missing, div, teams_by_div)))
    for key in order:
        _score, item = best[key]
        if madrid_day(item["when"]):
            stats["rows"] += 1
            if item["home_ok"] and item["away_ok"]:
                stats["both"] += 1
            elif item["home_ok"] or item["away_ok"]:
                stats["one"] += 1
                missing = item["raw_away"] if item["home_ok"] else item["raw_home"]
                if missing and missing not in seen_names:
                    seen_names.add(missing)
                    stats["unmatched"].append((
                        item["div"], missing, item["raw_home"], item["raw_away"],
                        explain_miss(missing, item["div"], teams_by_div),
                    ))
        if not item["home_ok"] and not item["away_ok"]:
            continue
        merged.append({"when": item["when"], "div": item["div"], "home": item["home"], "away": item["away"]})
    merged.sort(key=lambda item: (item["when"], item["div"], item["home"]))
    return merged, stats


def report_fixtures(stats):
    print(
        f"FIXTURE OGGI/DOMANI: {stats['rows']} partite, "
        f"{stats['both']} con entrambe le squadre, "
        f"{stats['one']} con una sola, {stats['none']} senza abbinamento.",
        flush=True,
    )
    if not stats["unmatched"]:
        print("  Nessun nome di oggi o domani rimasto fuori.", flush=True)
        return
    for div, missing, home, away, reason in stats["unmatched"]:
        print(f"  NON ABBINATA {div}: {missing}  ({home} - {away}) — {reason}", flush=True)


def _copy_line(when, home, away, team, kind, value, view):
    local = when.astimezone(MADRID)
    return (
        f"{EVENT} | {local:%Y-%m-%d} {local:%H:%M} | {home} - {away} | "
        f"{team} ({kind} {value} {view})"
    )


def _badge_html(fix, team, kind, value, view):
    if not fix:
        return ""
    word = "OGGI" if fix["day"] == "oggi" else "DOMANI"
    line = _copy_line(fix["when"], fix["home"], fix["away"], team, kind, value, view)
    return (
        f' <button type="button" class="corner-when {fix["day"]}" data-copy="{esc(line)}">{word}</button>'
    )


def _marker(playing, div, team, venue):
    fix = playing.get((div, team, venue))
    if not fix:
        return ""
    return " OGGI" if fix["day"] == "oggi" else " DOMANI"


def _stat_row(index, team, value, badge, league=""):
    extra = f'<span class="cr-lg">{esc(league)}</span>' if league else ""
    return (
        f'<div class="cr-row"><span class="cr-rank">{index}.</span>'
        f'<span class="cr-team">{esc(team)}{badge}</span>'
        f'<span class="cr-delay">{esc(value)}</span>{extra}</div>'
    )


def render_texts(model, stamp):
    leagues = model["leagues"]
    playing = playing_map(model)
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
            fonte(model), "",
            "TOP 20 GLOBALE",
        ]
        shown_rows = _top_slice(_classifica(model, venue, "ritardo"), 20)
        for row in shown_rows:
            ritardi.append(
                f"  {row['rank']:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}  | "
                f"{row['country']} - {row['league']}{_marker(playing, row['div'], row['team'], venue)}"
            )
        if not shown_rows:
            ritardi.append("  Nessun dato.")
        ritardi.append("")
        for item in leagues:
            block = item["block"]
            ritardi += ["=" * 50, f"{block['country']} - {block['league']}", "=" * 50, f"RITARDI {EVENT}"]
            rows = item["views"][venue]["rows"]
            if not rows:
                ritardi.append("  Nessuna squadra con storico.")
            for index, row in enumerate(rows, 1):
                ritardi.append(
                    f"  {index:>2}. {row['team']:<28.28} {_label(row['delay'], row['full']):>4}"
                    f"{_marker(playing, block['div'], row['team'], venue)}"
                )
            ritardi.append("")
        sotto += [
            "#" * 50, f"# {label.upper()}", "#" * 50, "",
            f"SOTTOFREQUENZE {EVENT} (ultime {SOTTO_N} partite) — {label.upper()}",
            f"Aggiornato: {stamp} (ora italiana)",
            "Uscite di corner 9-11 sulle ultime 100 partite del paese, nella vista indicata.",
            "Solo squadre con almeno 100 partite. In cima chi le fa più spesso.",
            fonte(model), "",
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
            fonte(model), "",
        ]
        ranked = _classifica(model, venue, "ripetizione")
        for row in ranked:
            repeat_lines.append(
                f"{row['rank']:>4}. {row['team']:<28.28} {_label(row['delay'], row['full']):>5}  | "
                f"{row['country']} - {row['league']}  | {row['matches']} gare"
                f"{_marker(playing, row['div'], row['team'], venue)}"
            )
        repeat_lines.append("")
    freq_lines = [
        f"FREQUENZA CAMPIONATI — {EVENT}",
        f"Aggiornato: {stamp} (ora italiana)",
        "Solo stagione in corso, tutte le partite. 1 ogni N = partite giocate / uscite.",
        f"Servono almeno {MIN_GIORNATE} giornate (partite giocate dalla squadra più attiva).",
        fonte(model), "",
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
        "Gare in arrivo in cui almeno una squadra è tra le prime 10 per ritardo, oppure tra le prime 10 per ripetizione, nella vista della scheda.",
        "Pari merito sull'ultimo posto incluso. Se lo sono entrambe, la scheda riporta tutte e due.",
        "Il numero dopo # è il posto nella classifica di quella vista.",
        "Orari in ora italiana.",
        fonte(model), "",
    ]
    return {
        "ritardi": "\n".join(ritardi) + "\n",
        "sotto": "\n".join(sotto) + "\n",
        "saltati": "\n".join(saltati) + "\n",
        "freq": "\n".join(freq_lines) + "\n",
        "repeat": "\n".join(repeat_lines) + "\n",
        "prossime": "\n".join(prossime + _prossime_lines(model)) + "\n",
    }

def _top2_by_league(rows, minimum=4):
    """Prime due posizioni della singola classifica, pari merito inclusi e ritardo minimo."""
    ordered = sorted(rows, key=lambda row: (-row["delay"], row["team"].casefold()))
    if not ordered:
        return []
    cutoff = max(minimum, ordered[min(PROSSIME_TOP, len(ordered)) - 1]["delay"])
    selected = [row for row in ordered if row["delay"] >= cutoff]
    total = len(ordered)
    rank_by_team = {}
    previous = None
    rank = 0
    for index, row in enumerate(ordered, 1):
        if row["delay"] != previous:
            rank, previous = index, row["delay"]
        rank_by_team[row["team"]] = rank
    return [{**row, "rank": rank_by_team[row["team"]], "total": total}
            for row in selected]


def _explain(model, venue, div, teams):
    """Prime due posizioni per campionato e contesto; una ripetizione richiede l'ultima gara in fascia."""
    report = next((item for item in model["leagues"] if item["block"]["div"] == div), None)
    if not report:
        return []
    ritardo = _by_team(_top2_by_league(report["views"][venue]["rows"]))
    ripetizione = _by_team(_top2_by_league(report["repeats"][venue]))
    lines = []
    for team in teams:
        if team in ritardo:
            lines.append(("ritardo", ritardo[team]))
        row = ripetizione.get(team)
        if row and row.get("last"):
            lines.append(("ripetizione", row))
    return lines

def _sides_for(venue, fixture):
    if venue == "home":
        return [fixture["home"]]
    if venue == "away":
        return [fixture["away"]]
    return [fixture["home"], fixture["away"]]

def _prossime_lines(model):
    lines = []
    day = None
    cards = _upcoming_cards(model)
    if not cards:
        return ["Nessuna partita in arrivo con una squadra nelle prime 2 posizioni e ritardo almeno 4."]
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
        for kind, row in card["lines"]:
            lines.append("           " + _line_text(kind, row))
    return lines

def _upcoming_cards(model):
    """Una scheda per vista, come nelle prossime partite combo. Entrambe le squadre se sono tutte e due in classifica."""
    cards = []
    marks = {"all": "TOTALE", "home": "CASA", "away": "TRASFERTA"}
    for fixture in model["upcoming"]:
        block = model["by_div"].get(fixture["div"])
        if not block:
            continue
        for venue, _label, _desc in VENUES:
            lines = _explain(model, venue, fixture["div"], _sides_for(venue, fixture))
            if not lines:
                continue
            cards.append({
                "when": fixture["when"], "home": fixture["home"], "away": fixture["away"],
                "country": block["country"], "league": block["league"], "venue": venue,
                "mark": marks[venue], "lines": lines,
            })
    cards.sort(key=lambda card: (card["when"], card["home"], {"all": 0, "home": 1, "away": 2}[card["venue"]]))
    return cards

def render_html(model, stamp):
    parts = [
        f'<p class="leg">Uscita = corner totali della partita, casa più trasferta, pari a 9, 10 o 11. '
        f'Ritardo = partite consecutive senza quella uscita. Le viste sono una sotto l&rsquo;altra: '
        f'<b>Totali</b>, <b>Casa</b>, <b>Trasferta</b>. Aggiornato: {esc(stamp)} (ora italiana). '
        f'{fonte_page(model)}</p>',
        BADGE_CSS,
        '<h3>Prossime partite (5 giorni)</h3>',
        '<p class="leg">Solo le gare in arrivo. Una scheda c&rsquo;&egrave; se, in quella vista, almeno una squadra &egrave; tra le prime 2 posizioni della propria classifica per ritardo oppure per ripetizione, con ritardo minimo 4 e pari merito inclusi. '
        'Il pari merito sull&rsquo;ultimo posto entra. Se lo sono tutte e due, la scheda mostra le spiegazioni di entrambe. '
        '# &egrave; il posto nella classifica di quella vista. '
        'Le partite già iniziate non restano in elenco.</p>',
        COPY_BUTTON_HTML,
        '<div class="fxlist" data-upcoming="1">',
    ]
    cards = _upcoming_cards(model)
    day = None
    if not cards:
        parts.append("<p>Nessuna partita in arrivo con una squadra nelle prime 10.</p>")
    for card in cards:
        local = card["when"].astimezone(ROME)
        stamp_day = local.strftime("%d/%m/%Y")
        if stamp_day != day:
            parts.append(f'<h3 class="day">{stamp_day}</h3>')
            day = stamp_day
        kick = card["when"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        copy = esc(card["home"] + " vs " + card["away"])
        parts.append(
            f'<div class="fx" data-kick="{kick}" data-home="{esc(card["home"])}" data-away="{esc(card["away"])}">'
            f'<span class="vtag {card["venue"]}">{esc(card["mark"])}</span>'
            f'<div class="fxh"><span class="time copy-time" data-copy="{copy}" title="Clicca per copiare la partita">{local:%H:%M}</span> '
            f'{esc(card["home"])} vs {esc(card["away"])} '
            f'<span class="lg">{esc(card["country"])} - {esc(card["league"])}</span></div>'
            + "".join(_line_html(kind, row, card["venue"]) for kind, row in card["lines"])
            + "</div>"
        )
    parts.append("</div>")
    playing = playing_map(model)
    parts.append("<h3>Ritardi e top 20</h3>")
    parts.append(
        '<p class="leg">Ogni squadra che gioca oggi ha la targhetta rossa <b>OGGI</b>, '
        'chi gioca domani quella viola <b>DOMANI</b> (giorno di Madrid), anche fuori dalla top 10. '
        'Il clic mostra la riga e la copia.</p>'
    )
    for venue, label, desc in VENUES:
        rows_html = [
            f'<p class="vnote">Vista <b>{esc(label)}</b> · {esc(desc)}</p>',
            '<div class="cr-list">',
            '<h4 class="cr-h">TOP 20 globale</h4>',
        ]
        for row in _top_slice(_classifica(model, venue, "ritardo"), 20):
            value = _label(row["delay"], row["full"]).strip()
            badge = _badge_html(playing.get((row["div"], row["team"], venue)), row["team"], "ritardo", value, VIEW_WORD[venue])
            rows_html.append(_stat_row(row["rank"], row["team"], value, badge, f"{row['country']} - {row['league']}"))
        for item in model["leagues"]:
            block = item["block"]
            rows_html.append(f'<h4 class="cr-h">{esc(block["country"])} - {esc(block["league"])}</h4>')
            league_rows = item["views"][venue]["rows"]
            if not league_rows:
                rows_html.append('<p class="leg">Nessuna squadra con storico.</p>')
            for index, row in enumerate(league_rows, 1):
                value = _label(row["delay"], row["full"]).strip()
                badge = _badge_html(
                    playing.get((block["div"], row["team"], venue)),
                    row["team"], "ritardo", value, VIEW_WORD[venue],
                )
                rows_html.append(_stat_row(index, row["team"], value, badge))
        rows_html.append("</div>")
        parts.append(f'<div class="venue" data-v="{venue}">' + "".join(rows_html) + "</div>")
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
        ranked = _classifica(model, venue, "ripetizione")
        rows_html = [
            f'<h3>{esc(label)}</h3>',
            f'<p class="vnote">{esc(desc)}</p>',
            '<div class="cr-list">',
        ]
        for row in ranked:
            value = _label(row["delay"], row["full"]).strip()
            badge = _badge_html(
                playing.get((row["div"], row["team"], venue)),
                row["team"], "ripetizione", value, VIEW_WORD[venue],
            )
            rows_html.append(_stat_row(
                row["rank"], row["team"], value, badge, f"{row['country']} - {row['league']}",
            ))
        if not ranked:
            rows_html.append('<p class="leg">Nessuna squadra con storico.</p>')
        rows_html.append("</div>")
        parts.append(f'<div class="fx">{"".join(rows_html)}</div>')
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
    parts.append(COPY_SCRIPT)
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
            selected = _by_team(_top2_by_league(table, minimum=REPEAT_ALERT_MIN))
            sides = [fixture["home"]] if venue == "home" else [fixture["away"]] if venue == "away" else [fixture["home"], fixture["away"]]
            for team in sides:
                found = selected.get(team)
                # L'ultima partita nella vista deve essere un'uscita della fascia.
                if not found or not found.get("last") or found["delay"] < REPEAT_ALERT_MIN:
                    continue
                rows.append((team, scope, found["delay"], found["full"], found["rank"], found["total"]))
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
    page = ensure_day_copy(page)
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

def _check_prossime(model):
    """Ogni riga della scheda deve coincidere con la classifica globale di quella vista."""
    tables = {}
    allowed = {}
    for venue, _label, _desc in VENUES:
        for kind in ("ritardo", "ripetizione"):
            ranked = _classifica(model, venue, kind)
            tables[(venue, kind)] = ranked
            allowed[(venue, kind)] = _by_team(_top_slice(ranked, PROSSIME_TOP))
            if kind == "ritardo" and ranked:
                cutoff = ranked[min(RED_TOP, len(ranked)) - 1]["delay"]
                for row in ranked:
                    assert row["red"] is (row["delay"] >= cutoff)
    both = None
    for card in _upcoming_cards(model):
        seen = []
        for kind, row in card["lines"]:
            key = (row["div"], row["team"])
            assert key in allowed[(card["venue"], kind)]
            full = _by_team(tables[(card["venue"], kind)])[key]
            assert (full["rank"], full["delay"], full["red"], full["total"]) == (
                row["rank"], row["delay"], row["red"], row["total"]
            )
            seen.append(row["team"])
        if card["venue"] == "all" and len(set(seen)) >= 2 and both is None:
            both = card
    print("Prossime con entrambe le squadre:", "sì" if both else "no", flush=True)
    if both:
        print(f"  {both['mark']} {both['home']} vs {both['away']} [{both['country']} - {both['league']}]", flush=True)
        for kind, row in both["lines"]:
            print("   " + _line_text(kind, row), flush=True)
    totale = _top_slice(tables[("all", "ritardo")], PROSSIME_TOP)
    print("Prime 10 per ritardo totale:", flush=True)
    for row in totale[:12]:
        print(f"  #{row['rank']} {row['team']} {row['delay']} {row['country']} - {row['league']}", flush=True)

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
    ordered_repeat = [match(True, 1), match(False, 2), match(False, 3)]
    delay, last, full = repeat_state(ordered_repeat)
    assert (delay, last, full) == (1, False, True)
    # Anche se i record arrivano al contrario, l'ultima partita reale prevale:
    # una vecchia uscita 12-14 non deve generare una ripetizione dopo un 2-2.
    delay_rev, last_rev, full_rev = repeat_state(list(reversed(ordered_repeat)))
    assert (delay_rev, last_rev, full_rev) == (1, False, True)
    delay, last, full = repeat_state([match(True, 1), match(True, 2), match(False, 3)])
    assert delay == 1 and last is False and full is False
    sample = (
        "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HC,AC\n"
        "I1,01/09/2025,15:00,Alpha,Beta,1,0,6,4\n"
        "I1,08/09/2025,15:00,Alpha,Gamma,0,0,,\n"
    ).encode()
    parsed = parse_results(sample, "I1", "2526")
    assert len(parsed) == 1 and parsed[0]["hit"] and parsed[0]["total"] == 10
    names = [f"T{i:02d}" for i in range(1, 13)]
    rit = [{"team": name, "delay": 13 - i, "full": False} for i, name in enumerate(names, 1)]
    rep = [{"team": name, "delay": i, "full": False} for i, name in enumerate(names, 1)]
    views = {venue: {"rows": list(rit), "sotto": [], "salt": []} for venue, _l, _d in VENUES}
    repeats = {venue: list(rep) for venue, _l, _d in VENUES}
    league = {
        "block": {"div": "I1", "country": "ITALIA", "league": "SERIE A"},
        "views": views, "repeats": repeats,
    }
    when = datetime.now(timezone.utc) + timedelta(hours=5)
    model = {
        "leagues": [league],
        "by_div": {"I1": league["block"]},
        "upcoming": [
            {"when": when, "home": "T01", "away": "T02", "div": "I1"},
            {"when": when, "home": "T01", "away": "T12", "div": "I1"},
            {"when": when, "home": "T12", "away": "T11", "div": "I1"},
        ],
    }
    cards = _upcoming_cards(model)
    both = next(card for card in cards if card["home"] == "T01" and card["away"] == "T02" and card["venue"] == "all")
    assert [row["team"] for _k, row in both["lines"]] == ["T01", "T02"]
    assert all(kind == "ritardo" and row["red"] for kind, row in both["lines"])
    assert both["lines"][0][1]["rank"] == 1 and both["lines"][1][1]["rank"] == 2
    mixed = next(card for card in cards if card["home"] == "T01" and card["away"] == "T12" and card["venue"] == "all")
    assert [(kind, row["team"], row["red"]) for kind, row in mixed["lines"]] == [
        ("ritardo", "T01", True), ("ripetizione", "T12", False),
    ]
    casa = next(card for card in cards if card["home"] == "T01" and card["away"] == "T02" and card["venue"] == "home")
    assert [row["team"] for _k, row in casa["lines"]] == ["T01"]
    only_rep = next(card for card in cards if card["home"] == "T12" and card["away"] == "T11" and card["venue"] == "all")
    assert [kind for kind, _r in only_rep["lines"]] == ["ripetizione", "ripetizione"]
    assert all(not row["red"] for _k, row in only_rep["lines"])
    ranked = _classifica(model, "all", "ritardo")
    assert _top_slice(ranked, 10)[-1]["team"] == "T10"
    assert ranked[2]["delay"] == 10 and ranked[3]["red"] is False and ranked[2]["red"] is True
    assert norm_team("Hellas Verona") == norm_team("Verona") == "verona"
    assert norm_team("Südtirol") == norm_team("Sudtirol") == "sudtirol"
    kick = datetime(2026, 11, 27, 22, 30, tzinfo=MADRID)
    assert _copy_line(
        kick, "Deportivo Cuenca", "Manta", "Deportivo Cuenca", "ripetizione", "11", "casa",
    ) == "CORNER 9-11 | 2026-11-27 22:30 | Deportivo Cuenca - Manta | Deportivo Cuenca (ripetizione 11 casa)"
    today = datetime(2026, 11, 26, 12, tzinfo=MADRID)
    assert madrid_day(datetime(2026, 11, 26, 23, 30, tzinfo=timezone.utc), today) == "domani"
    assert madrid_day(datetime(2026, 11, 26, 20, tzinfo=MADRID), today) == "oggi"
    assert madrid_day(datetime(2026, 11, 28, 12, tzinfo=MADRID), today) is None
    badge_model = {
        "by_div": {
            "EC1": {"div": "EC1", "teams": {"Deportivo Cuenca", "Manta", "T12"}},
            "I1": {"div": "I1", "teams": {"Verona", "Sudtirol"}},
        },
        "upcoming": [{
            "when": datetime(2026, 11, 27, 22, 30, tzinfo=MADRID),
            "home": "Deportivo Cuenca", "away": "Manta", "div": "EC1",
        }],
        "leagues": [],
    }
    found = playing_map(badge_model, [{
        "when": datetime(2026, 11, 26, 18, tzinfo=MADRID),
        "home": "Hellas Verona", "away": "Südtirol", "div": "",
    }], today)
    assert found[("EC1", "Deportivo Cuenca", "home")]["day"] == "domani"
    assert found[("EC1", "Deportivo Cuenca", "all")]["day"] == "domani"
    assert ("EC1", "Deportivo Cuenca", "away") not in found
    assert ("EC1", "T12", "all") not in found
    assert found[("I1", "Verona", "home")]["day"] == "oggi"
    assert found[("I1", "Sudtirol", "away")]["day"] == "oggi"
    assert ("I1", "Verona", "away") not in found
    badge = _badge_html(
        found[("EC1", "Deportivo Cuenca", "home")],
        "Deportivo Cuenca", "ripetizione", "11", "casa",
    )
    assert "DOMANI" in badge and "corner-when domani" in badge
    assert "CORNER 9-11 | 2026-11-27 22:30 | Deportivo Cuenca - Manta | Deportivo Cuenca (ripetizione 11 casa)" in badge
    assert _badge_html(None, "T12", "ritardo", "1", "totale") == ""
    bundesliga = {"Werder Bremen", "M'gladbach", "Bayern Munich", "FC Koln", "Hamburg", "Mainz", "Stuttgart", "Ein Frankfurt", "Dortmund", "Leverkusen"}
    assert match_name("Borussia Mönchengladbach", bundesliga) == "M'gladbach"
    assert match_name("Werder Bremen", bundesliga) == "Werder Bremen"
    assert match_name("Bayern München", bundesliga) == "Bayern Munich"
    assert match_name("1. FC Köln", bundesliga) == "FC Koln"
    assert match_name("Hamburger SV", bundesliga) == "Hamburg"
    assert match_name("VfB Stuttgart", bundesliga) == "Stuttgart"
    assert match_name("Eintracht Frankfurt", bundesliga) == "Ein Frankfurt"
    assert match_name("Bayer Leverkusen", bundesliga) == "Leverkusen"
    spain = {"Alaves", "Ath Bilbao", "Ath Madrid", "Barcelona", "Elche", "Malaga", "Real Madrid", "Vallecano"}
    assert match_name("Real Madrid", spain) == "Real Madrid"
    assert match_name("Atlético Madrid", spain) == "Ath Madrid"
    assert match_name("Athletic Club", spain) == "Ath Bilbao"
    assert match_name("Málaga", spain) == "Malaga"
    assert match_name("Deportivo Alavés", spain) == "Alaves"
    england = {"Wolves", "QPR", "Lincoln", "Sheffield United", "Sheffield Weds"}
    assert match_name("Wolverhampton Wanderers", england) == "Wolves"
    assert match_name("Queens Park Rangers", england) == "QPR"
    assert match_name("Lincoln City", england) == "Lincoln"
    assert match_name("Sheffield United", england) == "Sheffield United"
    league_two = {"Shrewsbury", "Cheltenham", "Crawley Town", "Grimsby", "Fleetwood Town", "Swindon", "Northampton", "Exeter", "Crewe", "Accrington"}
    assert match_name("Shrewsbury Town", league_two) == "Shrewsbury"
    assert match_name("Cheltenham Town", league_two) == "Cheltenham"
    assert match_name("Crawley Town", league_two) == "Crawley Town"
    assert match_name("Grimsby Town", league_two) == "Grimsby"
    assert match_name("Crewe Alexandra", league_two) == "Crewe"
    assert match_name("Accrington Stanley", league_two) == "Accrington"
    assert match_name("Exeter City", league_two) == "Exeter"
    belgium = {"Cercle Brugge", "Club Brugge", "Mechelen", "Gent"}
    assert match_name("Cercle Brugge", belgium) == "Cercle Brugge"
    assert match_name("Club Brugge", belgium) == "Club Brugge"
    assert match_name("KV Mechelen", belgium) == "Mechelen"
    assert match_name("Preussen Münster", belgium) is None
    assert match_name("Gent U23", belgium) is None
    assert match_name("Hoffenheim II", bundesliga) is None
    assert match_name("VfB Stuttgart II", bundesliga) is None
    serie_a = {"Lecce", "Inter", "Milan", "Roma"}
    assert match_name("Lecco", serie_a) is None
    assert match_name("Carpi", serie_a) is None
    austria = {"FC Salzburg", "Austria Wien", "SK Sturm Graz", "LASK"}
    assert match_name("Austria Salzburg", austria) is None
    assert match_name("Austria Wien II", austria) is None
    assert match_name("Sturm Graz II", austria) is None
    japan = {"Cerezo Osaka", "Gamba Osaka", "Kyoto Sanga", "Urawa Reds", "Shimizu S-Pulse"}
    assert match_name("Cerezo Osaka", japan) == "Cerezo Osaka"
    assert match_name("Kyoto Sanga FC", japan) == "Kyoto Sanga"
    assert match_name("Urawa Red Diamonds", japan) == "Urawa Reds"
    mexico = {"Guadalajara", "Pachuca", "América"}
    assert match_name("Chivas", mexico) == "Guadalajara"
    russia = {"CSKA Moscow", "Dinamo Moscow", "Terek Grozny", "Zenit St Petersburg", "Baltika Kaliningrad"}
    assert match_name("CSKA Moscow", russia) == "CSKA Moscow"
    assert match_name("FK Akhmat", russia) == "Terek Grozny"
    assert match_name("Zenit St. Petersburg", russia) == "Zenit St Petersburg"
    assert match_name("Baltika", russia) == "Baltika Kaliningrad"
    scotland = {"Annan Athletic", "Elgin", "Stirling"}
    assert match_name("Athletic Club", scotland) is None
    assert match_name("Málaga", scotland) is None
    home, away = _match_sides("Málaga", "Athletic Club", spain)
    assert (home, away) == ("Malaga", "Ath Bilbao")
    home, away = _match_sides("Shrewsbury Town", "Cheltenham Town", league_two)
    assert (home, away) == ("Shrewsbury", "Cheltenham")
    assert "non è in nessuna classifica" in explain_miss("Carpi", "I1", {"I1": serie_a})
    assert "riserve" in explain_miss("Hoffenheim II", "D1", {"D1": bundesliga})
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
    _check_prossime(model)
    for div, team in (("I1", "Inter"), ("E0", "Arsenal")):
        if div in model["by_div"]:
            verify_team(model, div, team)

if __name__ == "__main__":
    main()
