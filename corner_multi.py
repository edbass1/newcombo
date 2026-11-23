#!/usr/bin/env python3
"""Genera CORNER 6-8, 9-11 e 12-14 dagli stessi CSV.

La fascia 9-11 resta quella di corner.py. Le altre due usano lo stesso
calcolo: cambia solo quali totali di corner contano come uscita.
"""
import os
from pathlib import Path
import corner as c
import corner_adamchoi as adam

BANDS = (
    ("corner-6-8", "CORNER 6-8", 6, 8, "_6_8"),
    ("corner", "CORNER 9-11", 9, 11, ""),
    ("corner-12-14", "CORNER 12-14", 12, 14, "_12_14"),
)
TEXT_STEMS = (
    ("ritardi", "corner_ritardi"),
    ("sotto", "corner_sottofrequenze"),
    ("saltati", "corner_saltati"),
    ("freq", "corner_frequenza"),
    ("repeat", "corner_ripetizioni"),
    ("prossime", "corner_prossime"),
)
NAV = (
    '<a class="ssm" href="#corner-6-8">CORNER 6-8</a>'
    '<a class="ssm" href="#corner">CORNER 9-11</a>'
    '<a class="ssm" href="#corner-12-14">CORNER 12-14</a>'
)


def with_band(source, low, high):
    """Copia gli storici e marca l'uscita sulla fascia richiesta."""
    tagged = {}
    for div, seasons in source.items():
        tagged[div] = {}
        for season, matches in seasons.items():
            tagged[div][season] = [
                {**match, "hit": low <= match["total"] <= high}
                for match in matches
            ]
    return tagged


def adapt(text, low, high, event):
    """Sostituisce solo le frasi della fascia 9-11. I numeri delle squadre restano."""
    if (low, high) == (9, 11):
        return text
    span = f"{low}-{high}"
    phrase = f"{low}, {low + 1} o {high}"
    return (
        text.replace("9, 10 o 11", phrase)
        .replace("uscite 9-11", f"uscite {span}")
        .replace("corner 9-11", f"corner {span}")
        .replace("non è ancora 9-11", f"non è ancora {span}")
        .replace("CORNER 9-11 |", event + " |")
    )


def adapt_markup(section, section_id, low, high, event):
    """Identificatori propri per il pulsante di copia, così le tre sezioni non si mescolano."""
    section = adapt(section, low, high, event)
    if section_id == "corner":
        return section
    suffix = section_id[len("corner"):]
    return (
        section.replace('id="copy-corner-bar"', f'id="copy-corner-bar{suffix}"')
        .replace('id="copy-corner-today"', f'id="copy-corner-today{suffix}"')
        .replace('id="copy-corner-days"', f'id="copy-corner-days{suffix}"')
        .replace("getElementById('copy-corner-days')", f"getElementById('copy-corner-days{suffix}')")
        .replace("#copy-corner-today", f"#copy-corner-today{suffix}")
        .replace('href="#corner"', f'href="#{section_id}"')
        .replace("getElementById('corner')", f"getElementById('{section_id}')")
    )


def write_texts(texts, suffix):
    for key, stem in TEXT_STEMS:
        path = Path(f"{stem}{suffix}.txt")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(texts[key], encoding="utf-8")
        os.replace(tmp, path)


def install(page, block):
    start, end = "<!-- CORNER-SECTION -->", "<!-- /CORNER-SECTION -->"
    if start in page and end in page:
        page = page[:page.index(start)] + block + page[page.index(end) + len(end):]
    else:
        needle = '<button id="totop"'
        if needle not in page:
            raise SystemExit("Pagina senza punto di inserimento per CORNER.")
        page = page.replace(needle, block + "\n" + needle, 1)
    old_nav = '<a class="ssm" href="#corner">CORNER</a>'
    if old_nav in page:
        page = page.replace(old_nav, NAV, 1)
    elif '<a class="ssm" href="#corner-6-8">' not in page:
        page = page.replace(
            '<a class="ssm" href="#saltati">Saltati</a>',
            '<a class="ssm" href="#saltati">Saltati</a>' + NAV,
            1,
        )
    extra = "".join(
        f'<a href="{stem}{suffix}.txt">{stem}{suffix}.txt</a>'
        for suffix in ("_6_8", "_12_14")
        for _key, stem in TEXT_STEMS
    )
    if "corner_ritardi_6_8.txt" not in page:
        marker = '<a href="corner_saltati.txt">corner_saltati.txt</a>'
        if marker in page:
            page = page.replace(marker, marker + extra, 1)
    page = page.replace(
        "function apply(){['prossime','corner'].forEach",
        "function apply(){['prossime','corner-6-8','corner','corner-12-14'].forEach",
        1,
    )
    return page


def selfcheck():
    sample = {"I1": {"2627": [{"total": 7, "hit": False}, {"total": 10, "hit": True}, {"total": 13, "hit": False}]}}
    low = with_band(sample, 6, 8)
    high = with_band(sample, 12, 14)
    assert [row["hit"] for row in low["I1"]["2627"]] == [True, False, False]
    assert [row["hit"] for row in high["I1"]["2627"]] == [False, False, True]
    assert sample["I1"]["2627"][0]["hit"] is False
    html = adapt_markup(
        'pari a 9, 10 o 11. uscite 9-11. corner 9-11. non è ancora 9-11. '
        'id="copy-corner-today" href="#corner" getElementById(\'corner\') CORNER 9-11 |',
        "corner-6-8", 6, 8, "CORNER 6-8",
    )
    assert "9-11" not in html and "9, 10 o 11" not in html
    assert 'id="copy-corner-today-6-8"' in html and "getElementById('corner-6-8')" in html
    assert "CORNER 6-8 |" in html
    same = adapt_markup("CORNER 9-11 | id=\"copy-corner-today\"", "corner", 9, 11, "CORNER 9-11")
    assert same == "CORNER 9-11 | id=\"copy-corner-today\""


def _with_extra(source, fixtures):
    """Aggiunge i campionati gratuiti di adamchoi. Un loro errore lascia i 22 di football-data."""
    leagues = list(c.LEAGUES)
    try:
        extra = adam.load()
    except Exception as exc:
        print(f"adamchoi: errore imprevisto, continuo senza ({exc}).", flush=True)
        return source, fixtures, leagues
    known = {div for _country, _league, div in leagues}
    added = 0
    for country, league, div in extra.get("leagues") or []:
        seasons = (extra.get("history") or {}).get(div)
        if div in known or not seasons:
            continue
        leagues.append((country, league, div))
        source[div] = seasons
        added += 1
    fixtures = list(fixtures) + list(extra.get("fixtures") or [])
    print(f"Campionati corner extra: {added}.", flush=True)
    return source, fixtures, leagues


def main():
    selfcheck()
    c.selftest()
    adam.selftest()
    print("Scarico una sola volta gli storici corner...", flush=True)
    source = c.load_history()
    fixtures = c.load_fixtures()
    source, fixtures, leagues = _with_extra(source, fixtures)
    stamp = c.datetime.now(c.ROME).strftime("%d/%m/%Y %H:%M")
    saved = c.EVENT
    blocks = ["<!-- CORNER-SECTION -->"]
    try:
        for section_id, event, low, high, suffix in BANDS:
            c.EVENT = event
            model = c.build(with_band(source, low, high), fixtures, leagues=leagues)
            if not model["leagues"]:
                raise SystemExit(f"Nessun campionato con dati corner per {event}.")
            texts = {key: adapt(value, low, high, event) for key, value in c.render_texts(model, stamp).items()}
            section = adapt_markup(c.render_html(model, stamp), section_id, low, high, event)
            if (low, high) != (9, 11) and ("9-11" in section or "9, 10 o 11" in section):
                raise SystemExit(f"Nella sezione {event} è rimasta la fascia 9-11.")
            if event + " |" not in section:
                raise SystemExit(f"Manca l'intestazione di copia per {event}.")
            write_texts(texts, suffix)
            blocks.append(f'<h2 id="{section_id}">{event}</h2>')
            blocks.append(section)
            print(
                f"{event}: {len(model['leagues'])} campionati, "
                f"{len(c._upcoming_cards(model))} schede prossime.",
                flush=True,
            )
            c._check_prossime(model)
    finally:
        c.EVENT = saved
    blocks.append("<!-- /CORNER-SECTION -->")
    path = Path("index.html")
    if not path.exists():
        raise SystemExit("Manca index.html: va generata prima la pagina combo.")
    page = install(path.read_text(encoding="utf-8"), "\n".join(blocks))
    tmp = path.with_suffix(".html.tmp")
    tmp.write_text(page, encoding="utf-8")
    os.replace(tmp, path)
    print("Sezioni CORNER 6-8, 9-11 e 12-14 aggiornate.", flush=True)

if __name__ == "__main__":
    main()
