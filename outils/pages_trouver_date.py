"""Pages conseils de l'outil « On se fait ça quand ? » (04/11/2026) : contenu
utile que Google et les assistants IA peuvent citer, chacune renvoyant vers
l'outil /trouver-une-date/. Écrit les pages dans static/trouver-une-date/<slug>/.

    python outils/pages_trouver_date.py
"""
from __future__ import annotations

import html
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "static" / "trouver-une-date"
BASE = "https://wavents.fr"

PAGES = [
    {
        "slug": "alternative-doodle",
        "title": "Alternative à Doodle gratuite et sans compte pour trouver une date",
        "h1": "Une alternative simple à Doodle",
        "desc": "Trouver une date commune sans compte, sans e-mail et sans publicité : un lien, un prénom, trois cases par date. Pensé pour le téléphone et les sorties entre amis.",
        "intro": "Pour une réunion de travail, les grands outils de planification font l'affaire. Pour une soirée entre amis ou un repas de famille, il faut surtout que ce soit rapide sur un téléphone, sans créer de compte.",
        "sections": [
            ("Ce que fait « On se fait ça quand ? »", [
                "L'organisateur donne un nom à la sortie, un emplacement, et touche les dates possibles dans un calendrier.",
                "Il envoie un seul lien dans le groupe (WhatsApp, SMS, Messenger, mail).",
                "Chacun écrit son prénom et coche, pour chaque date : dispo, peut-être ou non.",
                "La meilleure date ressort toute seule, en haut de la page.",
                "L'organisateur la retient, puis prévient le groupe avec un message tout prêt.",
            ]),
            ("Les différences qui comptent entre amis", [
                "Aucun compte, aucune adresse e-mail, ni pour l'organisateur ni pour les participants.",
                "Aucune publicité.",
                "Pensé d'abord pour le téléphone : tout tient sur un écran.",
                "Seul le téléphone qui a répondu peut modifier sa réponse : personne ne peut changer les dispos d'un autre.",
                "Pour chaque date, les sorties du jour près du lieu choisi (concerts, spectacles, marchés, fêtes), que chacun peut proposer au groupe. Disponible là où Wavents recense les événements, autour de Villefranche-sur-Saône et du Beaujolais pour l'instant.",
            ]),
            ("Quand préférer un autre outil", [
                "Pour caler des réunions sur des agendas professionnels (Outlook, Google Agenda), un outil relié aux calendriers sera plus adapté.",
                "Pour des créneaux à l'heure près sur toute une semaine, une grille horaire convient mieux.",
            ]),
        ],
        "faq": [
            ("Existe-t-il une alternative à Doodle sans compte ?", "Oui : sur wavents.fr/trouver-une-date, ni l'organisateur ni les participants n'ont besoin de compte ou d'e-mail. Un lien et un prénom suffisent, et c'est gratuit, sans publicité."),
            ("Peut-on répondre « peut-être » ?", "Oui : chaque date propose trois choix, dispo, peut-être ou non. La meilleure date est celle qui a le plus de « dispo », puis le plus de « peut-être »."),
        ],
    },
    {
        "slug": "soiree-entre-amis",
        "title": "Organiser une soirée entre amis quand personne n'est dispo en même temps",
        "h1": "Une soirée entre amis, enfin",
        "desc": "Groupe WhatsApp qui tourne en rond, personne de dispo le même soir : la méthode simple pour trouver une date commune et décider quoi faire.",
        "intro": "« On se fait un truc ? » « Oui ! » … et trois semaines plus tard, toujours rien. Le problème n'est presque jamais l'envie : c'est de trouver le soir où tout le monde est libre.",
        "sections": [
            ("La méthode qui marche", [
                "Une seule personne organise : c'est elle qui propose les dates et qui tranche à la fin.",
                "Proposer 4 à 6 dates, pas plus, sur les deux ou trois semaines qui viennent.",
                "Préciser le moment (midi ou soir) dès le départ, pour éviter les « oui mais à quelle heure ? ».",
                "Laisser 48 heures pour répondre, puis retenir la meilleure date même s'il manque quelqu'un.",
                "Annoncer la date retenue dans le groupe avec un message clair : jour, lieu, qui vient.",
            ]),
            ("Les erreurs classiques", [
                "Demander « vous êtes dispos quand ? » sans proposer de dates : chacun répond autre chose.",
                "Attendre que tout le monde soit libre : avec plus de cinq personnes, ça n'arrive presque jamais.",
                "Faire voter dans le fil de discussion : les réponses se perdent entre les messages.",
            ]),
            ("Et on fait quoi ?", [
                "Une fois la date trouvée, regarder ce qui se passe ce soir-là près de chez vous : concert, spectacle, soirée à thème, marché nocturne.",
                "Sur « On se fait ça quand ? », chaque date affiche les sorties du jour près du lieu choisi, et chacun peut en proposer une au groupe.",
            ]),
        ],
        "faq": [
            ("Comment trouver une date commune avec ses amis ?", "Proposer quelques dates dans un sondage, envoyer le lien dans le groupe, et laisser chacun cocher ses dispos. Sur wavents.fr/trouver-une-date, c'est gratuit et sans compte, et la meilleure date est mise en avant automatiquement."),
            ("Combien de dates proposer ?", "Entre 4 et 6 dates sur les deux ou trois semaines suivantes : assez pour trouver un créneau, pas trop pour que chacun réponde vite."),
        ],
    },
    {
        "slug": "repas-de-famille",
        "title": "Trouver une date pour un repas de famille ou une réunion d'association",
        "h1": "Repas de famille, réunion d'asso : trouver la date",
        "desc": "Famille dispersée, bénévoles aux emplois du temps chargés : comment fixer la date d'un repas de famille, d'un anniversaire ou d'une réunion d'association sans cinquante messages.",
        "intro": "Plus le groupe est grand, plus le choix de la date devient un casse-tête. Un sondage de dates évite les échanges interminables et laisse une trace claire de qui peut venir.",
        "sections": [
            ("Pour un repas de famille", [
                "Proposer des week-ends plutôt que des soirs de semaine, et indiquer « midi » si c'est un déjeuner.",
                "Envoyer le lien dans le groupe familial : les grands-parents peuvent répondre sur le téléphone d'un proche, il suffit d'écrire leur prénom.",
                "Fixer une date limite pour répondre, puis retenir la date où le plus de monde est dispo.",
            ]),
            ("Pour une association ou un club", [
                "Pour une réunion, une répétition ou une sortie de club : proposer plusieurs soirs et laisser chaque membre cocher.",
                "L'organisateur peut retirer une réponse en double ; chacun ne peut modifier que la sienne.",
                "Une fois la date retenue, le message « Prévenir le groupe » reprend la date, le lieu et la liste des présents.",
            ]),
            ("Et pour Noël", [
                "Une fois la date du repas de Noël fixée, le tirage au sort des cadeaux peut se faire avec le Secret Santa en ligne de Wavents.",
            ]),
        ],
        "faq": [
            ("Comment organiser un repas de famille quand tout le monde est loin ?", "Proposer quelques week-ends dans un sondage de dates, envoyer le lien au groupe familial, et retenir la date où le plus de monde est disponible. Sur wavents.fr/trouver-une-date, c'est gratuit et sans compte."),
            ("Peut-on utiliser l'outil pour une association ?", "Oui : jusqu'à 150 réponses et 60 dates par sondage, sans compte. L'organisateur peut retirer une réponse en double."),
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
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@600;700;800;900&display=swap" rel="stylesheet">
<script type="application/ld+json">{faq_ld}</script>
<script type="application/ld+json">{article_ld}</script>
<style>
  body {{ margin: 0; font-family: Nunito, system-ui, sans-serif; color: #0A2A3D; background: #f3f8fd; }}
  header {{ background: linear-gradient(160deg, #065FBB, #0B81E1 70%, #29a3ff); color: #fff; padding: 14px 18px 26px; }}
  header .in {{ max-width: 680px; margin: 0 auto; }}
  header a {{ display: inline-flex; align-items: center; gap: 6px; color: #fff; text-decoration: none; font-weight: 900; font-size: 14px; }}
  header a img {{ width: 26px; height: 26px; }}
  h1 {{ font-size: clamp(28px, 7vw, 40px); font-weight: 900; line-height: 1.02; letter-spacing: -.025em; margin: 14px 0 8px; }}
  header p {{ color: #e6f1ff; font-weight: 700; margin: 0; line-height: 1.5; }}
  main {{ max-width: 680px; margin: 0 auto; padding: 14px 18px 30px; line-height: 1.6; font-weight: 600; }}
  h2 {{ font-size: 20px; margin: 24px 0 6px; letter-spacing: -.01em; }}
  li {{ margin: 6px 0; }}
  .cta {{ display: block; text-align: center; margin: 26px 0; padding: 15px; border-radius: 10px; text-decoration: none; font: 900 17px Nunito; color: #fff; background: #065FBB; }}
  .more a, footer a {{ color: #065FBB; }}
  footer {{ text-align: center; color: #4B6475; font-weight: 700; font-size: 14px; padding: 0 18px 40px; }}
</style>
</head>
<body>
<header><div class="in">
  <a href="/"><img src="/img/logo-256.png" alt="" width="26" height="26">Wavents</a>
  <h1>{h1}</h1>
  <p>{intro}</p>
</div></header>
<main>
{sections}
<a class="cta" href="/trouver-une-date/" data-goatcounter-click="clic-conseil-date-{slug}">Trouver une date commune, gratuitement</a>
{faq_html}
<p class="more">À lire aussi : {more}</p>
</main>
<footer>Fait par <a href="/">Wavents</a>, l'appli gratuite des sorties près de chez toi. <a href="/app/">Télécharger l'appli</a></footer>
<script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</body>
</html>
"""


def main() -> None:
    e = html.escape
    for page in PAGES:
        url = f"{BASE}/trouver-une-date/{page['slug']}/"
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
        more = " · ".join([f'<a href="/trouver-une-date/{p["slug"]}/">{e(p["h1"])}</a>' for p in PAGES if p is not page]
                          + ['<a href="/secret-santa/">Secret Santa en ligne</a>'])
        out = OUT / page["slug"]
        out.mkdir(parents=True, exist_ok=True)
        (out / "index.html").write_text(TEMPLATE.format(
            title=e(page["title"]), desc=e(page["desc"]), url=url, h1=e(page["h1"]), intro=e(page["intro"]),
            sections=sections, faq_html=faq_html, faq_ld=faq_ld, article_ld=article_ld, more=more, slug=page["slug"]))
        print(url)


if __name__ == "__main__":
    main()
