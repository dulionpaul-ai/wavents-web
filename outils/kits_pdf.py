"""PDF A4 des kits de diffusion (20/11/2026) : une affiche par commune
(static/affiche/pdf/wavents-affiche-<slug>.pdf) et la feuille de deux
cartes « séjour » (static/sejour/wavents-sejour.pdf). Imprimés par Chromium
depuis les pages du site généré, polices du site incluses. Les affiches ne
portent ni date ni chiffre : à relancer seulement si le dessin ou la liste
AFFICHE_COMMUNES change.

    pip install playwright && playwright install chromium
    python generate.py site && python outils/kits_pdf.py site
"""
from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate as g  # noqa: E402


def main() -> None:
    site = Path(sys.argv[1] if len(sys.argv) > 1 else "site").resolve()
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # noqa: D401 - pas de journal des requêtes
            pass

    handler = functools.partial(Quiet, directory=str(site))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/"
    jobs = [(f"affiche/{g.slugify(n)}/", ROOT / "static" / "affiche" / "pdf" / f"wavents-affiche-{g.slugify(n)}.pdf")
            for n in g.AFFICHE_COMMUNES]
    jobs.append(("sejour/imprimer/", ROOT / "static" / "sejour" / "wavents-sejour.pdf"))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for path, out in jobs:
            page = browser.new_page()
            page.route("https://gc.zgo.at/**", lambda r: r.abort())
            page.goto(base + path, wait_until="networkidle")
            page.emulate_media(media="print")
            page.evaluate("document.fonts.ready")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.pdf(path=str(out), prefer_css_page_size=True, print_background=True)
            page.close()
            print(f"{out.relative_to(ROOT)} ({out.stat().st_size // 1024} Ko)")
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
