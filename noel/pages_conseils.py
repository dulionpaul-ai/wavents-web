"""Pages conseils du Secret Santa (04/11/2026) : contenu utile que Google et
les assistants IA peuvent citer, chacune renvoyant vers l'outil
/secret-santa/. Écrit les pages dans static/secret-santa/<slug>/.

    python noel/pages_conseils.py
"""
from __future__ import annotations

import html
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "secret-santa"
BASE = "https://wavents.fr"

PAGES = [
    {
        "slug": "collegues",
        "title": "Secret Santa entre collègues : règles, budget et conseils",
        "h1": "Secret Santa entre collègues",
        "desc": "Comment organiser un Secret Santa au bureau : budget, règles, date, télétravail, idées de cadeaux. Tirage au sort gratuit en ligne.",
        "intro": "Le Secret Santa est la façon la plus simple de fêter Noël au bureau : chacun offre un seul cadeau, à un collègue tiré au sort, sans se ruiner.",
        "sections": [
            ("Les règles qui marchent au bureau", [
                "Un budget fixe et modeste, le même pour tous (10 à 20 € le plus souvent).",
                "Une date annoncée trois semaines à l'avance, souvent le dernier jour avant les congés.",
                "Pas de cadeau gênant : rien de trop personnel ni de trop intime.",
                "Le secret jusqu'au bout : on ne dit à personne qui on a tiré.",
                "Une solution pour les absents : le cadeau peut être remis au retour, ou envoyé.",
            ]),
            ("Qui peut participer ?", [
                "Tous ceux qui le souhaitent : le Secret Santa doit rester facultatif.",
                "Les équipes en télétravail ou sur plusieurs sites : chacun reçoit son lien et découvre son tirage où qu'il soit.",
                "Les couples de collègues peuvent être exclus l'un de l'autre au moment du tirage.",
            ]),
            ("Idées de cadeaux entre collègues", [
                "Un mug ou une tasse amusante, avec un bon café ou un thé.",
                "Une plante facile pour le bureau.",
                "Un jeu de cartes ou un petit jeu de société à partager à la pause.",
                "Des chocolats ou des spécialités de la région.",
                "Une sortie : une place de spectacle, de concert ou un atelier près du bureau.",
            ]),
        ],
        "faq": [
            ("Quel budget pour un Secret Santa entre collègues ?", "Entre 10 et 20 € en général. L'important est que tout le monde ait le même budget, annoncé à l'avance."),
            ("Comment faire le tirage au sort sans qu'une personne ne connaisse tout ?", "Avec un outil en ligne comme celui de Wavents : chaque collègue reçoit son propre lien et découvre seul qui il gâte. Même l'organisateur ne connaît pas le tirage."),
        ],
    },
    {
        "slug": "idees-cadeaux",
        "title": "Idées de cadeaux Secret Santa à 10, 20 et 30 €",
        "h1": "Idées de cadeaux Secret Santa",
        "desc": "Des idées de cadeaux pour un Secret Santa à 10 €, 20 € ou 30 € : pour un collègue, un ami, un membre de la famille. Et le tirage au sort gratuit.",
        "intro": "Le budget est fixé, le nom est tiré : il reste à trouver le cadeau. Voici des idées simples, rangées par budget, qui font plaisir à presque tout le monde.",
        "sections": [
            ("Moins de 10 €", [
                "Des chocolats, des papillotes ou un pot de pâte à tartiner artisanale.",
                "Une paire de chaussettes de Noël bien kitsch.",
                "Un petit carnet et un joli stylo.",
                "Une bougie parfumée.",
                "Un jeu de cartes (Uno, Dobble, jeu de 54 cartes illustré).",
            ]),
            ("Autour de 20 €", [
                "Un jeu de société rapide à jouer à plusieurs.",
                "Un plaid ou une écharpe douce.",
                "Un panier de produits locaux : vin, terrines, confitures.",
                "Un livre de cuisine ou une BD.",
                "Une place pour un spectacle, un concert ou une soirée près de chez lui.",
            ]),
            ("Autour de 30 €", [
                "Une enceinte Bluetooth de poche.",
                "Un atelier à faire ensemble : poterie, cuisine, œnologie, escape game.",
                "Un abonnement d'un mois à un service de musique ou de lecture.",
                "Un coffret de bières ou de thés du monde.",
                "Deux places pour une sortie : théâtre, cinéma, match.",
            ]),
            ("Le cadeau qui marque : une sortie", [
                "Offrir un moment plutôt qu'un objet : un spectacle, un concert, un marché de Noël, un atelier.",
                "Sur Wavents, toutes les sorties près de chez toi sont rassemblées, avec la date, le lieu et le prix : de quoi trouver une idée dans le budget.",
            ]),
        ],
        "faq": [
            ("Quel est le budget habituel d'un Secret Santa ?", "Le plus souvent entre 10 et 30 €. Entre collègues, 10 à 20 € ; en famille ou entre amis proches, jusqu'à 30 € ou plus."),
            ("Que faire si on ne connaît pas bien la personne tirée au sort ?", "Choisir un cadeau qui se partage ou se consomme (chocolats, jeu de société, produits locaux), ou une sortie à faire quand on veut."),
        ],
    },
    {
        "slug": "a-distance",
        "title": "Organiser un Secret Santa à distance (famille, amis, télétravail)",
        "h1": "Un Secret Santa à distance",
        "desc": "Famille éloignée, amis dans plusieurs villes, équipe en télétravail : comment organiser un Secret Santa à distance, du tirage au sort à la remise des cadeaux.",
        "intro": "Pas besoin d'être tous dans la même pièce pour tirer au sort : chacun peut découvrir son tirage sur son téléphone, où qu'il soit.",
        "sections": [
            ("Le tirage au sort à distance", [
                "Crée le Secret Santa en ligne : nom du groupe, date, budget, participants.",
                "Ajoute les couples qui ne doivent pas se tirer.",
                "Envoie à chacun son lien par WhatsApp, SMS ou mail : il découvre seul qui il gâte.",
            ]),
            ("La remise des cadeaux", [
                "Tous ensemble le jour J, si la famille se retrouve pour les fêtes.",
                "Par la poste ou en livraison directe, avec une date limite commune.",
                "En visio : chacun ouvre son cadeau devant les autres, et on devine qui l'a offert.",
            ]),
            ("Nos conseils", [
                "Fixe une date d'envoi au moins une semaine avant la remise.",
                "Partage les adresses de livraison à l'avance (en privé).",
                "Pense aux cadeaux numériques ou aux bons pour une sortie près de chez la personne.",
            ]),
        ],
        "faq": [
            ("Peut-on faire un Secret Santa sans se voir ?", "Oui : le tirage se fait en ligne, chacun reçoit son lien, et les cadeaux sont envoyés ou remis en visio."),
            ("Comment éviter que l'organisateur connaisse le tirage ?", "Avec l'outil de Wavents, chaque lien ne contient que le tirage de la personne qui le reçoit, et rien n'est enregistré sur un serveur."),
        ],
    },
]

TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} | Wavents</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{url}">
<link rel="icon" href="/img/favicon.svg">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@900&family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
<script type="application/ld+json">{faq_ld}</script>
<script type="application/ld+json">{article_ld}</script>
<style>
  body {{ margin: 0; font-family: Nunito, system-ui, sans-serif; color: #0A2A3D; background: #f3f8fd; }}
  header {{ background: linear-gradient(160deg, #05070d, #0b1a33 60%, #3a0a10); color: #fff; padding: 30px 18px 34px; text-align: center; }}
  header a {{ color: #cfd8e6; text-decoration: none; font-weight: 800; font-size: 14px; }}
  h1 {{ font-family: Cinzel, Georgia, serif; font-size: clamp(28px, 7vw, 42px); margin: 16px 0 8px;
    background: linear-gradient(180deg, #ffefb0, #f7d36a 50%, #b8862b); -webkit-background-clip: text; background-clip: text; color: transparent; }}
  header p {{ color: #cfd8e6; font-weight: 700; max-width: 560px; margin: 0 auto; line-height: 1.5; }}
  main {{ max-width: 680px; margin: 0 auto; padding: 18px 18px 40px; line-height: 1.6; font-weight: 600; }}
  h2 {{ font-size: 20px; margin: 26px 0 8px; }}
  li {{ margin: 6px 0; }}
  .cta {{ display: block; text-align: center; margin: 28px 0; padding: 18px; border-radius: 999px; text-decoration: none; font: 900 18px Nunito;
    color: #2a1b00; background: linear-gradient(180deg, #ffefb0, #f7d36a); box-shadow: 0 10px 26px rgba(247,180,40,.45); }}
  .more a {{ color: #065FBB; }}
  footer {{ text-align: center; color: #4B6475; font-weight: 700; font-size: 14px; padding: 0 18px 40px; }}
  footer a {{ color: #065FBB; }}
</style>
</head>
<body>
<header>
  <a href="/secret-santa/">🎅 Secret Santa · Wavents</a>
  <h1>{h1}</h1>
  <p>{intro}</p>
</header>
<main>
{sections}
<a class="cta" href="/secret-santa/" data-goatcounter-click="clic-conseil-{slug}">🎲 Organiser mon Secret Santa gratuitement</a>
{faq_html}
<p class="more">À lire aussi : {more}</p>
</main>
<footer>Fait avec ❤️ par <a href="/">Wavents</a>, l'appli gratuite des sorties près de chez toi. <a href="/app/">Télécharger l'appli</a></footer>
<script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</body>
</html>
"""


def main() -> None:
    e = html.escape
    for page in PAGES:
        url = f"{BASE}/secret-santa/{page['slug']}/"
        sections = "\n".join(
            f"<h2>{e(h)}</h2>\n<ul>{''.join(f'<li>{e(x)}</li>' for x in items)}</ul>" for h, items in page["sections"])
        faq_html = "\n".join(f"<h2>{e(q)}</h2>\n<p>{e(a)}</p>" for q, a in page["faq"])
        faq_ld = json.dumps({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in page["faq"]]},
            ensure_ascii=False)
        article_ld = json.dumps({"@context": "https://schema.org", "@type": "Article", "headline": page["title"],
                                 "description": page["desc"], "inLanguage": "fr", "url": url,
                                 "publisher": {"@type": "Organization", "name": "Wavents", "url": BASE + "/"}},
                                ensure_ascii=False)
        more = " · ".join(f'<a href="/secret-santa/{p["slug"]}/">{e(p["h1"])}</a>' for p in PAGES if p is not page)
        out = OUT / page["slug"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(TEMPLATE.format(
            title=e(page["title"]), desc=e(page["desc"]), url=url, h1=e(page["h1"]), intro=e(page["intro"]),
            sections=sections, faq_html=faq_html, faq_ld=faq_ld, article_ld=article_ld, more=more, slug=page["slug"]))
        print(url)


if __name__ == "__main__":
    main()
