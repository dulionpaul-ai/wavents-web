"""Secret Santa de la famille de Paul (Noël 2026) : tirage et pages secrètes.

    python noel/tirage.py participants.json [--sortie static/noel]

`participants.json` (jamais dans le dépôt, qui est public) :
    {"noms": ["Paul", "Julie", ...],
     "interdits": [["Paul", "Julie"], ...],   # ne peuvent pas se tirer l'un l'autre
     "lieu": "Saint-Victor-la-Coste", "date": "28 décembre", "info": "Budget : 30 €"}

Chaque participant a sa page `/noel/<jeton>/` qui ne contient que son tirage,
chiffré (AES-GCM) ; la clé n'est que dans le lien (`#…`), jamais sur le
serveur ni dans le dépôt. Les liens à envoyer sont affichés à la fin.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import secrets
import sys
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HERE = Path(__file__).parent


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def draw(names: list[str], forbidden: set[frozenset[str]]) -> dict[str, str]:
    """Personne ne se tire soi-même ni un interdit ; tirage au hasard
    (essais répétés jusqu'à une permutation valide)."""
    rng = random.SystemRandom()
    for _ in range(100_000):
        targets = names[:]
        rng.shuffle(targets)
        pairs = dict(zip(names, targets))
        if all(g != t and frozenset((g, t)) not in forbidden for g, t in pairs.items()):
            return pairs
    sys.exit("Aucun tirage possible avec ces interdits.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("participants")
    ap.add_argument("--sortie", default=str(HERE.parent / "static" / "noel"))
    ap.add_argument("--base", default="https://wavents.fr/noel")
    args = ap.parse_args()

    conf = json.loads(Path(args.participants).read_text())
    names = conf["noms"]
    forbidden = {frozenset(p) for p in conf.get("interdits", [])}
    pairs = draw(names, forbidden)
    template = (HERE / "modele.html").read_text()
    out = Path(args.sortie)
    links = []
    for giver in names:
        token = secrets.token_urlsafe(8)
        key = AESGCM.generate_key(bit_length=128)
        payload = json.dumps({
            "giver": giver, "target": pairs[giver], "names": names,
            "place": conf.get("lieu"), "date": conf.get("date"), "info": conf.get("info"),
        }, ensure_ascii=False).encode()
        nonce = os.urandom(12)
        blob = nonce + AESGCM(key).encrypt(nonce, payload, None)
        page = out / token
        page.mkdir(parents=True, exist_ok=True)
        (page / "index.html").write_text(template.replace("__PAYLOAD__", b64(blob)))
        links.append((giver, f"{args.base}/{token}/#{b64(key)}"))
    print("Liens à envoyer (un par personne, en privé) :")
    for giver, link in links:
        print(f"  {giver} : {link}")


if __name__ == "__main__":
    main()
