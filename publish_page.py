#!/usr/bin/env python3
"""Pubblica solo la pagina cifrata.

index.html e i testi generati restano fuori dal repository e da GitHub Pages.
Nel sito pubblico c'è soltanto la maschera di login e il testo cifrato.
"""
import base64, gzip, json, os, re, shutil, sys
from pathlib import Path

from encrypt_page import encrypt

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs"
# Frasi della pagina vera: non possono comparire nel file pubblico (hanno spazi).
LEAKS = (
    "Ritardi Combo Doppia",
    "CORNER 9-11",
    "football-data.co.uk",
    "adamchoi.co.uk",
    "<!-- CORNER-SECTION -->",
)


def main():
    password = os.environ.get("NEWCOMBO_PASS") or ""
    if not password.strip():
        print(
            "ERRORE: manca NEWCOMBO_PASS. Aggiungi il secret in "
            "Settings → Secrets and variables → Actions. "
            "Senza password non pubblico la pagina, nemmeno in chiaro.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    page_path = ROOT / "index.html"
    if not page_path.is_file():
        print("ERRORE: manca index.html da cifrare.", file=sys.stderr)
        raise SystemExit(1)
    html = page_path.read_text(encoding="utf-8")
    files = {}
    for name in dict.fromkeys(re.findall(r'href="([^"]+\.txt)"', html)):
        path = ROOT / name
        if path.is_file():
            files[name] = path.read_text(encoding="utf-8")
        else:
            print(f"Testo non trovato, il link resta vuoto: {name}", flush=True)
    blob = json.dumps(files, ensure_ascii=False).replace("<", "\\u003c")
    script = (
        "<script>window.__NCFILES__=" + blob + ";\n"
        "document.addEventListener('click',function(e){"
        "var a=e.target&&e.target.closest&&e.target.closest('a[href]');"
        "if(!a)return;var href=a.getAttribute('href')||'';"
        "if(!/\\.txt$/.test(href))return;"
        "var name=href.split('/').pop();"
        "var body=window.__NCFILES__&&window.__NCFILES__[name];"
        "if(body==null)return;e.preventDefault();"
        "var b=new Blob([body],{type:'text/plain;charset=utf-8'});"
        "var u=URL.createObjectURL(b);"
        "var d=document.createElement('a');d.href=u;d.download=name;"
        "document.body.appendChild(d);d.click();d.remove();"
        "setTimeout(function(){URL.revokeObjectURL(u);},1500);});</script>"
    )
    if "</body>" in html:
        html = html.replace("</body>", script + "</body>", 1)
    else:
        html += script
    payload = base64.b64encode(
        gzip.compress(json.dumps({"html": html}, ensure_ascii=False).encode())
    ).decode("ascii")
    token = encrypt(password, payload)
    shell = (ROOT / "site" / "shell.html").read_text(encoding="utf-8").replace("__BLOB__", token)
    if "__BLOB__" in shell:
        raise SystemExit("ERRORE: il testo cifrato non è stato inserito.")
    if len(password) >= 16 and password in token:
        raise SystemExit("ERRORE: la password è finita nel file pubblicato.")
    for leak in LEAKS:
        if leak in shell:
            raise SystemExit(f"ERRORE: testo in chiaro nel file pubblicato ({leak}).")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    (OUT / "index.html").write_text(shell, encoding="utf-8")
    (OUT / ".nojekyll").write_text("", encoding="utf-8")
    published = sorted(p.name for p in OUT.iterdir())
    if published != [".nojekyll", "index.html"]:
        raise SystemExit(f"ERRORE: la cartella pubblica contiene {published}.")
    print(
        f"Pubblicato docs/index.html cifrato ({len(shell)} byte, {len(files)} testi dentro).",
        flush=True,
    )


if __name__ == "__main__":
    main()
