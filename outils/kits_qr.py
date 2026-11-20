"""QR codes des kits de diffusion (20/11/2026) : un par affiche de commune
(static/affiche/qr/<slug>.svg, vers la page de la commune) et celui de la
carte « séjour » (static/sejour/qr.svg, vers /sejour/). SVG simples, bleu
nuit sur blanc, correction d'erreur Q (affiche abîmée, reflets de vitrine).
Chaque code est relu (zxing-cpp) avant d'être gardé.

    pip install segno zxing-cpp pillow
    python outils/kits_qr.py
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import segno

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import generate as g  # noqa: E402

NAVY = "#0B2545"


def check(qr: segno.QRCode, url: str) -> None:
    try:
        import zxingcpp
        from PIL import Image
    except ImportError:
        print("zxing-cpp ou Pillow absent : codes non relus", file=sys.stderr)
        return
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=6, border=4)
    buf.seek(0)
    found = [r.text for r in zxingcpp.read_barcodes(Image.open(buf))]
    if found != [url]:
        raise SystemExit(f"QR illisible pour {url} : {found}")


def write(url: str, path: Path) -> None:
    qr = segno.make(url, error="q", boost_error=False)
    check(qr, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    qr.save(str(path), kind="svg", dark=NAVY, light="#ffffff", border=2, scale=10, xmldecl=False,
            svgns=True, title=url, nl=False, omitsize=True)
    print(f"{path.relative_to(ROOT)}  {url}  (version {qr.version})")


def main() -> None:
    for name in g.AFFICHE_COMMUNES:
        write(g.affiche_target(name), ROOT / "static" / "affiche" / "qr" / f"{g.slugify(name)}.svg")
    write(g.SEJOUR_TARGET, ROOT / "static" / "sejour" / "qr.svg")


if __name__ == "__main__":
    main()
