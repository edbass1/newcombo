#!/usr/bin/env python3
"""Campionati corner gratuiti da adamchoi.co.uk, oltre a quelli di football-data.

Si usano solo gli endpoint pubblici del sito: l'elenco
countryLeagueNavigationCorners.json, corners_league_json.php e getFixturesJson.php.
Niente percorsi _secure o Premium. Le richieste sono una alla volta, con una
pausa, e la cache fa sì che un giro ravvicinato non riscarichi nulla e che un
giro successivo riscarichi solo la stagione in corso.
"""
import json, os, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import corner as c

NAV_URL = "https://www.adamchoi.co.uk/scripts/data/json/objects/countryLeagueNavigationCorners.json"
SEASON_URL = "https://www.adamchoi.co.uk/scripts/data/json/scripts/pages/corners/detailed/corners_league_json.php"
FIXTURES_URL = "https://www.adamchoi.co.uk/scripts/data/json/scripts/getFixturesJson.php"
CACHE = Path(".cache/adamchoi")
PAUSE = 2.5
REFRESH = 4 * 60 * 60
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json, text/plain, */*",
    "Authorization-Client": "ADAMCHOI.CO.UK",
    "Referer": "https://www.adamchoi.co.uk/",
}
# Nomi già usati da football-data, così Eerste Divisie condivide il paese con l'Eredivisie.
COUNTRY = {
    "Netherlands": "OLANDA",
    "Switzerland": "SVIZZERA",
    "Austria": "AUSTRIA",
    "Norway": "NORVEGIA",
    "Denmark": "DANIMARCA",
    "Sweden": "SVEZIA",
    "Czechia": "REPUBBLICA CECA",
    "Brazil": "BRASILE",
    "Argentina": "ARGENTINA",
    "Australia": "AUSTRALIA",
    "Finland": "FINLANDIA",
    "Poland": "POLONIA",
    "Japan": "GIAPPONE",
    "Mexico": "MESSICO",
    "Colombia": "COLOMBIA",
    "China": "CINA",
    "Ecuador": "ECUADOR",
    "Peru": "PERU",
    "Russia": "RUSSIA",
    "Saudi Arabia": "ARABIA SAUDITA",
    "South Korea": "COREA DEL SUD",
    "Ukraine": "UCRAINA",
    "USA": "USA",
}
LEAGUE = {
    "N2": "EERSTE DIVISIE",
    "SU1": "SUPER LEAGUE",
    "AUT1": "BUNDESLIGA",
    "NT1": "ELITESERIEN",
    "DEN1": "SUPERLIGAEN",
    "SA1": "ALLSVENSKAN",
    "CZ1": "CZECH LIGA",
    "BR1": "SERIE A",
    "BR2": "SERIE B",
    "ARG1": "PRIMERA",
    "MLS1": "MLS",
    "AUS1": "A-LEAGUE",
    "FV1": "VEIKKAUSLIIGA",
    "POL1": "EKSTRAKLASA",
    "JAP1": "J LEAGUE",
    "MX1": "LIGA MX",
    "COL1": "PRIMERA A",
    "CHN1": "SUPER LEAGUE",
    "ECU1": "LIGA PRO",
    "PER1": "PRIMERA",
    "RUS1": "PREMIER LEAGUE",
    "SAU1": "PRO LEAGUE",
    "SKO1": "K LEAGUE 1",
    "UK1": "PREMIER LEAGUE",
}


def _fd_codes():
    return {div for _country, _league, div in c.LEAGUES}


def _country_name(name):
    return COUNTRY.get(name, (name or "").strip().upper() or "ALTRO")


def _league_name(code, name):
    if code in LEAGUE:
        return LEAGUE[code]
    return " ".join((name or code).split()).upper()


def _check_url(url):
    lowered = url.lower()
    if "_secure" in lowered or "secure" in lowered:
        raise RuntimeError(f"Endpoint non consentito: {url}")


def season_url(code, label):
    query = urllib.parse.urlencode({"clflc": "abc", "league": code, "season": label})
    url = f"{SEASON_URL}?{query}"
    _check_url(url)
    return url


def fixtures_url(now):
    local = now.astimezone(c.ROME)
    offset = local.utcoffset() or timedelta(0)
    minutes = -int(offset.total_seconds() // 60)
    url = f"{FIXTURES_URL}?clflc=abc&timezoneOffset={minutes}"
    _check_url(url)
    return url


def _as_int(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text or text in {"-", "–"}:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _when_ms(value):
    number = _as_int(value)
    if number is None:
        return None
    seconds = number / 1000 if number > 10**12 else number
    try:
        return datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _played(raw, div, season):
    if not isinstance(raw, dict):
        return None
    hc, ac = _as_int(raw.get("homeCorners")), _as_int(raw.get("awayCorners"))
    home = str(raw.get("homeTeam") or "").strip()
    away = str(raw.get("awayTeam") or "").strip()
    when = _when_ms(raw.get("timestamp"))
    if hc is None or ac is None or not home or not away or not when:
        return None
    total = hc + ac
    return {
        "when": when, "home": home, "away": away, "hc": hc, "ac": ac,
        "total": total, "hit": c.is_hit(total), "div": div, "season": season,
    }


def _fixture(raw, div):
    if not isinstance(raw, dict):
        return None
    home = str(raw.get("homeTeam") or raw.get("hometeam") or "").strip()
    away = str(raw.get("awayTeam") or raw.get("awayteam") or "").strip()
    when = _when_ms(raw.get("timestamp") or raw.get("datetimestamp"))
    if not home or not away or not when:
        return None
    # nextMatch di una gara già giocata ha i corner: quella resta nello storico.
    if _as_int(raw.get("homeCorners")) is not None and _as_int(raw.get("awayCorners")) is not None:
        return None
    return {"when": when, "home": home, "away": away, "div": div}


def parse_payload(data, div, season):
    teams = data.get("teams") if isinstance(data, dict) else None
    if not isinstance(teams, list):
        return [], []
    matches, upcoming = [], []
    for team in teams:
        if not isinstance(team, dict):
            continue
        for raw in team.get("allMatches") or []:
            parsed = _played(raw, div, season)
            if parsed:
                matches.append(parsed)
        upcoming_match = _fixture(team.get("nextMatch"), div)
        if upcoming_match:
            upcoming.append(upcoming_match)
    return matches, upcoming


def parse_fixture_feed(data, codes):
    out = []
    dates = data.get("dates") if isinstance(data, dict) else None
    if not isinstance(dates, list):
        return out
    for day in dates:
        if not isinstance(day, dict):
            continue
        for league in day.get("leagues") or []:
            if not isinstance(league, dict):
                continue
            code = str(league.get("league") or "").strip()
            if code not in codes or league.get("subscriptionType"):
                continue
            for raw in league.get("fixtures") or []:
                parsed = _fixture(raw, code)
                if parsed:
                    out.append(parsed)
    return out


def dedupe_matches(matches):
    return c._dedupe(matches)


def dedupe_fixtures(fixtures):
    seen, out = set(), []
    for item in sorted(fixtures, key=lambda row: (row["when"], row["div"], row["home"])):
        key = (item["div"], item["when"].date().isoformat(), item["home"].casefold(), item["away"].casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def in_window(fixtures, now):
    now = now.astimezone(timezone.utc)
    start = c.madrid_start(now)
    horizon = now + timedelta(days=c.GIORNI)
    return [item for item in fixtures if start <= item["when"] <= horizon]


def select_leagues(nav):
    countries = nav.get("countries") if isinstance(nav, dict) else None
    if not isinstance(countries, list):
        return []
    taken = _fd_codes()
    chosen, seen = [], set()
    for country in countries:
        if not isinstance(country, dict):
            continue
        country_name = _country_name(country.get("countryName") or "")
        for league in country.get("leagues") or []:
            if not isinstance(league, dict) or league.get("subscriptionType"):
                continue
            code = str(league.get("leagueCode") or "").strip()
            if not code or code in taken or code in seen:
                continue
            seasons = []
            for season in league.get("seasons") or []:
                if not isinstance(season, dict):
                    continue
                sid = str(season.get("seasonID") or "").strip()
                label = str(season.get("season") or "").strip()
                if sid.isdigit() and label:
                    seasons.append({"id": sid, "label": label})
            if not seasons:
                continue
            seen.add(code)
            chosen.append({
                "code": code,
                "country": country_name,
                "league": _league_name(code, league.get("leagueName") or code),
                "seasons": seasons,
            })
    return chosen


def _dump_match(match):
    copy = dict(match)
    copy["when"] = match["when"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return copy


def _load_match(raw):
    match = dict(raw)
    match["when"] = datetime.fromisoformat(raw["when"].replace("Z", "+00:00"))
    return match


def _dump_fixture(item):
    return {
        "when": item["when"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home": item["home"], "away": item["away"], "div": item["div"],
    }


def _load_fixture(raw):
    return {
        "when": datetime.fromisoformat(raw["when"].replace("Z", "+00:00")),
        "home": raw["home"], "away": raw["away"], "div": raw["div"],
    }


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


class Cache:
    def __init__(self, root=CACHE):
        self.root = Path(root)

    def age(self, now):
        meta = _read_json(self.root / "meta.json")
        if not isinstance(meta, dict) or "fetched_at" not in meta:
            return None
        try:
            return now.timestamp() - float(meta["fetched_at"])
        except (TypeError, ValueError):
            return None

    def complete(self):
        nav = _read_json(self.root / "nav.json")
        return isinstance(nav, list)

    def save_nav(self, leagues):
        _write_json(self.root / "nav.json", leagues)

    def load_nav(self):
        nav = _read_json(self.root / "nav.json")
        return nav if isinstance(nav, list) else []

    def season(self, code, sid):
        payload = _read_json(self.root / "seasons" / code / f"{sid}.json")
        if not isinstance(payload, list):
            return None
        out = []
        for raw in payload:
            try:
                out.append(_load_match(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def save_season(self, code, sid, matches):
        _write_json(self.root / "seasons" / code / f"{sid}.json", [_dump_match(m) for m in matches])

    def save_fixtures(self, fixtures):
        _write_json(self.root / "fixtures.json", [_dump_fixture(item) for item in fixtures])

    def load_fixtures(self):
        payload = _read_json(self.root / "fixtures.json")
        if not isinstance(payload, list):
            return []
        out = []
        for raw in payload:
            try:
                out.append(_load_fixture(raw))
            except (KeyError, TypeError, ValueError):
                continue
        return out

    def save_meta(self, now):
        _write_json(self.root / "meta.json", {"fetched_at": now.timestamp()})

    def snapshot(self, now):
        history = {}
        leagues = []
        for league in self.load_nav():
            code = league.get("code")
            if not code:
                continue
            bucket = {}
            for season in league.get("seasons") or []:
                rows = self.season(code, season.get("id"))
                if rows:
                    bucket[str(season["id"])] = rows
            if not bucket:
                continue
            history[code] = bucket
            leagues.append((league["country"], league["league"], code))
        return {
            "leagues": leagues,
            "history": history,
            "fixtures": in_window(self.load_fixtures(), now),
        }


def _empty():
    return {"leagues": [], "history": {}, "fixtures": []}


class _Pace:
    def __init__(self):
        self._ready = False

    def wait(self):
        if self._ready:
            time.sleep(PAUSE)
        self._ready = True


def fetch_json(url, pace):
    _check_url(url)
    pace.wait()
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        print(f"adamchoi: HTTP {exc.code} {url.split('?')[0].rsplit('/', 1)[-1]}", flush=True)
        return None, exc.code
    except Exception as exc:
        print(f"adamchoi: errore di rete ({exc.__class__.__name__})", flush=True)
        return None, exc.__class__.__name__
    try:
        return json.loads(raw.decode("utf-8")), 200
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("adamchoi: risposta non JSON", flush=True)
        return None, "bad-json"


def _refresh(cache, now):
    pace = _Pace()
    nav, status = fetch_json(NAV_URL, pace)
    leagues = select_leagues(nav) if isinstance(nav, dict) else []
    if not leagues:
        print(f"adamchoi: elenco campionati non disponibile ({status}).", flush=True)
        if cache.complete():
            print("adamchoi: uso la cache precedente.", flush=True)
            return cache.snapshot(now)
        return _empty()
    cache.save_nav(leagues)
    codes = {league["code"] for league in leagues}
    history = {}
    fixtures = []
    for league in leagues:
        code = league["code"]
        current = max(league["seasons"], key=lambda season: int(season["id"]))["id"]
        bucket = {}
        for season in league["seasons"]:
            sid, label = season["id"], season["label"]
            cached = cache.season(code, sid)
            if cached is not None and sid != current:
                if cached:
                    bucket[sid] = cached
                continue
            data, season_status = fetch_json(season_url(code, label), pace)
            if not isinstance(data, dict):
                if season_status in (401, 403):
                    print(f"adamchoi: {code} {label} non autorizzato, continuo.", flush=True)
                else:
                    print(f"adamchoi: {code} {label} saltato ({season_status}).", flush=True)
                if cached:
                    bucket[sid] = cached
                continue
            matches, upcoming = parse_payload(data, code, sid)
            matches = dedupe_matches(matches)
            cache.save_season(code, sid, matches)
            if matches:
                bucket[sid] = matches
            if sid == current:
                fixtures.extend(upcoming)
            print(f"adamchoi: {league['country']} {league['league']} {label}: {len(matches)} partite", flush=True)
        if bucket:
            history[code] = bucket
    feed, feed_status = fetch_json(fixtures_url(now), pace)
    if isinstance(feed, dict):
        fixtures.extend(parse_fixture_feed(feed, codes))
    else:
        print(f"adamchoi: calendario non disponibile ({feed_status}), uso le prossime dei campionati.", flush=True)
    fixtures = dedupe_fixtures(fixtures)
    cache.save_fixtures(fixtures)
    cache.save_meta(now)
    chosen = [(country, name, code) for country, name, code in
              ((lg["country"], lg["league"], lg["code"]) for lg in leagues) if code in history]
    print(f"adamchoi: {len(chosen)} campionati, {len(in_window(fixtures, now))} prossime nei {c.GIORNI} giorni.", flush=True)
    return {"leagues": chosen, "history": history, "fixtures": in_window(fixtures, now)}


def load(now=None, cache=None):
    """Storico e prossime gare. Un errore di rete non blocca il resto dei corner."""
    now = now or datetime.now(timezone.utc)
    cache = cache or Cache()
    age = cache.age(now)
    if cache.complete() and age is not None and age < REFRESH:
        print(f"adamchoi: cache di {int(age // 60)} minuti, nessun download.", flush=True)
        return cache.snapshot(now)
    try:
        return _refresh(cache, now)
    except Exception as exc:
        print(f"adamchoi: aggiornamento non riuscito ({exc}).", flush=True)
        if cache.complete():
            print("adamchoi: uso la cache precedente.", flush=True)
            return cache.snapshot(now)
        print("adamchoi: nessuna cache, i campionati extra saltano.", flush=True)
        return _empty()


def selftest():
    sample = {
        "teams": [
            {"teamName": "Alpha", "allMatches": [
                {"date": "01-11-2026", "timestamp": 1793491200000, "homeTeam": "Alpha", "awayTeam": "Beta",
                 "homeCorners": 6, "awayCorners": 4, "result": "6 - 4"},
                {"date": "08-11-2026", "timestamp": 1794096000000, "homeTeam": "Gamma", "awayTeam": "Alpha",
                 "homeCorners": None, "awayCorners": 3, "result": ""},
            ], "nextMatch": {"date": "28-11-2026", "timestamp": 1795885200000, "homeTeam": "Alpha",
                             "awayTeam": "Beta", "homeCorners": None, "awayCorners": None, "result": " - "}},
            {"teamName": "Beta", "allMatches": [
                {"date": "01-11-2026", "timestamp": 1793491200000, "homeTeam": "Alpha", "awayTeam": "Beta",
                 "homeCorners": 6, "awayCorners": 4, "result": "6 - 4"},
            ], "nextMatch": {"date": "28-11-2026", "timestamp": 1795885200000, "homeTeam": "Alpha",
                             "awayTeam": "Beta", "homeCorners": None, "awayCorners": None}},
        ]
    }
    matches, upcoming = parse_payload(sample, "SU1", "26199")
    matches = dedupe_matches(matches)
    assert len(matches) == 1 and matches[0]["total"] == 10 and matches[0]["home"] == "Alpha"
    assert len(dedupe_fixtures(upcoming)) == 1
    url = season_url("SU1", "2025/2026")
    assert "corners_league_json.php" in url and "_secure" not in url and "2025" in url
    try:
        season_url("SU1", "../_secure/no")
    except RuntimeError:
        pass
    else:
        raise AssertionError("un percorso secure deve essere rifiutato")
    feed = {"dates": [{"leagues": [
        {"league": "SU1", "subscriptionType": None, "fixtures": [
            {"hometeam": "Vaduz", "awayteam": "Zürich", "datetimestamp": "1795885200000"}
        ]},
        {"league": "E0", "subscriptionType": None, "fixtures": [
            {"hometeam": "A", "awayteam": "B", "datetimestamp": "1795885200000"}
        ]},
        {"league": "ARG2", "subscriptionType": "Premium", "fixtures": [
            {"hometeam": "C", "awayteam": "D", "datetimestamp": "1795885200000"}
        ]},
    ]}]}
    parsed = parse_fixture_feed(feed, {"SU1"})
    assert len(parsed) == 1 and parsed[0]["home"] == "Vaduz" and parsed[0]["div"] == "SU1"
    nav = {"countries": [
        {"countryName": "Switzerland", "leagues": [
            {"leagueCode": "SU1", "leagueName": "Swiss Super League", "subscriptionType": None,
             "seasons": [{"seasonID": "26199", "season": "2026/2027"}]},
            {"leagueCode": "E0", "leagueName": "Premier League", "subscriptionType": None,
             "seasons": [{"seasonID": "1", "season": "2026/2027"}]},
        ]},
        {"countryName": "Belarus", "leagues": [
            {"leagueCode": "BEL1", "leagueName": "Vysshaya Liga", "subscriptionType": "Premium",
             "seasons": [{"seasonID": "2", "season": "2026"}]},
        ]},
    ]}
    chosen = select_leagues(nav)
    assert [row["code"] for row in chosen] == ["SU1"]
    assert chosen[0]["country"] == "SVIZZERA" and chosen[0]["league"] == "SUPER LEAGUE"
    history = {"SU1": {"26199": matches}}
    model = c.build(history, dedupe_fixtures(upcoming), now=matches[0]["when"],
                    leagues=[("SVIZZERA", "SUPER LEAGUE", "SU1")])
    assert model["by_div"]["SU1"]["teams"] == {"Alpha", "Beta"}
    assert model["leagues"][0]["block"]["league"] == "SUPER LEAGUE"
    note = c.fonte_page(model)
    assert "adamchoi.co.uk" in note and "9-11" not in note and "9, 10 o 11" not in note
    plain = c.fonte(model)
    assert "adamchoi.co.uk" in plain and "9-11" not in plain


if __name__ == "__main__":
    selftest()
    print("selftest ok")
    loaded = load()
    for country, league, code in loaded["leagues"]:
        seasons = loaded["history"][code]
        print(f"  {code} {country} - {league}: {sum(len(v) for v in seasons.values())} partite, {len(seasons)} stagioni")
