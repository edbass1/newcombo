#!/usr/bin/env python3
"""Genera le tre fasce corner in modo indipendente usando gli stessi CSV."""
import copy
from pathlib import Path
import corner as c

BANDS = (("6-8", 6, 8), ("9-11", 9, 11), ("12-14", 12, 14))

def write_texts(texts, suffix):
    names = {
        "ritardi": "corner_ritardi",
        "sotto": "corner_sottofrequenze",
        "saltati": "corner_saltati",
        "freq": "corner_frequenza",
        "repeat": "corner_ripetizioni",
        "prossime": "corner_prossime",
    }
    for key, stem in names.items():
        Path(f"{stem}{suffix}.txt").write_text(texts[key], encoding="utf-8")

def namespace(section, key, event):
    for name in ("copy-corner-bar", "copy-corner-today", "copy-corner-days", "copymsg"):
        section = section.replace(name, name + "-" + key)
    section = section.replace("getElementById('corner')", f"getElementById('corner-panel-{key}')")
    section = section.replace("CORNER 9-11 |", event + " |")
    return section

def main():
    c.selftest()
    print("Scarico una sola volta gli storici corner...", flush=True)
    source = c.load_history()
    fixtures = c.load_fixtures()
    stamp = c.datetime.now(c.ROME).strftime("%d/%m/%Y %H:%M")
    panels = []
    for label, low, high in BANDS:
        c.EVENT = "CORNER " + label
        history = {
            div: {
                season: [
                    {**match, "hit": low <= match["total"] <= high}
                    for match in matches
                ]
                for season, matches in seasons.items()
            }
            for div, seasons in source.items()
        }
        model = c.build(history, fixtures)
        if not model["leagues"]:
            raise SystemExit(f"Nessun campionato con dati corner per la fascia {label}.")
        texts = c.render_texts(model, stamp)
        section = c.render_html(model, stamp)
        # Le descrizioni testuali del generatore originario erano specifiche per 9-11.
        nums = f"{low}, {low + 1} o {high}"
        section = section.replace("9, 10 o 11", nums).replace("9-11", label)
        for k, value in texts.items():
            texts[k] = value.replace("9, 10 o 11", nums).replace("9-11", label)
        suffix = "" if label == "9-11" else "_" + label.replace("-", "_")
        write_texts(texts, suffix)
        key = label.replace("-", "_")
        panels.append((label, namespace(section, key, c.EVENT)))
        print(f"Fascia {label}: {len(model['leagues'])} campionati, {len(c._upcoming_cards(model))} schede prossime.", flush=True)
        c._check_prossime(model)
    buttons = []
    contents = []
    for label, section in panels:
        active = label == "9-11"
        buttons.append(
            f'<button type="button" class="corner-tab{" active" if active else ""}" '
            f'data-corner-target="{label}" aria-selected="{str(active).lower()}">{label}</button>'
        )
        contents.append(
            f'<div id="corner-panel-{label.replace("-", "_")}" class="corner-panel"'
            f'{" " if not active else ""}{"hidden" if not active else ""}>{section}</div>'
        )
    tabs = (
        '<style>.corner-tabs{display:flex;gap:.55rem;flex-wrap:wrap;margin:.6rem 0 1rem}'
        '.corner-tab{border:1px solid var(--border,#777);border-radius:10px;padding:.65rem 1.15rem;'
        'font-weight:800;font-size:1rem;background:var(--card,#222);color:inherit;cursor:pointer}'
        '.corner-tab.active{background:#16834a;color:#fff;border-color:#16834a}'
        '.corner-panel[hidden]{display:none!important}</style>'
        '<div class="corner-tabs" role="tablist" aria-label="Fasce corner">'
        + "".join(buttons) + "</div>" + "".join(contents)
        + '<script>(function(){var tabs=[].slice.call(document.querySelectorAll(".corner-tab[data-corner-target]"));'
        'function show(key){tabs.forEach(function(b){var on=b.getAttribute("data-corner-target")===key;'
        'b.classList.toggle("active",on);b.setAttribute("aria-selected",on?"true":"false");'
        'var p=document.getElementById("corner-panel-"+key.replace(/-/g,"_"));if(p)p.hidden=!on;});}'
        'tabs.forEach(function(b){b.addEventListener("click",function(){show(b.getAttribute("data-corner-target"));});});'
        'show("9-11");})();</script>'
    )
    original_patch = c._patch_page
    def patch_page(html):
        original_patch(tabs + html)
        page = Path("index.html").read_text(encoding="utf-8")
        extra = "".join(
            f'<a href="corner_{kind}_{suffix}.txt">corner_{kind}_{suffix}.txt</a>'
            for suffix in ("6_8", "12_14")
            for kind in ("ritardi", "prossime", "ripetizioni", "frequenza", "sottofrequenze", "saltati")
        )
        if "corner_ritardi_6_8.txt" not in page:
            page = page.replace("</details>", extra + "</details>", 1)
            Path("index.html").write_text(page, encoding="utf-8")
    c._patch_page = patch_page
    c._patch_page("")
    print("Sezione CORNER 6-8 / 9-11 / 12-14 aggiornata.", flush=True)

if __name__ == "__main__":
    main()
