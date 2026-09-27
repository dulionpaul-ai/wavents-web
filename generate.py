"""Génère le site public de Wavents : une page par événement à venir, une
page d'accueil (prochains jours autour de Belleville), le sitemap.

Lancé chaque jour par .github/workflows/pages.yml, puis publié sur GitHub
Pages. Lit les événements publiés (`status = 'active'`) avec la clé anon
de Supabase : la même que celle de l'appli, publique par nature (elle ne
donne accès qu'aux événements publiés).

Convention des dates (voir le dépôt Wavents, CLAUDE.md) : `start_date` et
`end_date` sont l'heure de Paris stockée avec un suffixe UTC. On les
affiche telles quelles et on les convertit en vrai fuseau de Paris pour le
balisage schema.org.

Usage : python generate.py [dossier_de_sortie]   (défaut : site/)
Variable d'environnement : PAGES_BASE_URL (adresse publique du site).
"""

from __future__ import annotations

import html
import json
import math
import os
import shutil
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

SUPABASE_URL = "https://mzbxnwvrvrhlucjzbzls.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im16Ynhud3ZydnJobHVjanpiemxzIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODQ2NDMwMzQsImV4cCI6MjEwMDIxOTAzNH0.wqoOLgNM92KmwZfbh4c56dVN13DDlCxJhuOOoqlH1is"
)
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://dulionpaul-ai.github.io/wavents-web").rstrip("/")
PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.wavents.wavents"
# Passer à True le jour où l'appli est publiée sur le Play Store (test
# ouvert ou production) : avant, la fiche renvoie une erreur 404.
PLAY_STORE_PUBLISHED = False


def play_store_link(text: str, css_class: str = "") -> str:
    if PLAY_STORE_PUBLISHED:
        cls = f' class="{css_class}"' if css_class else ""
        return f'<a{cls} href="{PLAY_STORE_URL}">{text}</a>'
    return '<span class="soon">Wavents arrive bientôt sur Google Play</span>'
PARIS = ZoneInfo("Europe/Paris")
HOME = (46.1075, 4.7536)  # centre de Belleville-en-Beaujolais, comme l'appli
HOME_RADIUS_KM = 30
HOME_DAYS = 21

FIELDS = (
    "id,title,description,category_id,location_name,address,latitude,longitude,"
    "start_date,end_date,start_time_known,end_time_known,price_type,price_amount,price_confirmed,"
    "image_url,website_url,for_kids"
)
CATEGORIES = {
    "fete": ("🎉", "Fête"),
    "sport": ("🏆", "Sport"),
    "concert": ("🎶", "Concert"),
    "marche": ("🧺", "Marché"),
    "culture": ("🎨", "Sortie culture"),
    "spectacle": ("🎭", "Spectacle"),
    "atelier": ("🖌️", "Atelier"),
    "visite": ("🧭", "Visite"),
    "associatif_autre": ("🤝", "Événement"),
}
CATEGORY_IMAGE = SUPABASE_URL + "/storage/v1/object/public/event-photos/categories/{}.jpg"
CATEGORY_IMAGE_FILE = {"associatif_autre": "associatif"}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]


def fetch_events() -> list[dict]:
    events: list[dict] = []
    offset, page = 0, 1000
    while True:
        url = (
            f"{SUPABASE_URL}/rest/v1/events?select={FIELDS}&status=eq.active"
            f"&order=start_date&limit={page}&offset={offset}"
        )
        request = urllib.request.Request(url, headers={
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        events.extend(rows)
        if len(rows) < page:
            return events
        offset += page


def wall_time(raw: str | None) -> datetime | None:
    """Heure murale de Paris (naïve) depuis la convention de stockage."""
    if not raw:
        return None
    return datetime.fromisoformat(raw).replace(tzinfo=None)


def day_label(moment: datetime) -> str:
    return f"{DAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def hour_label(moment: datetime) -> str:
    return f"{moment.hour}h{moment.minute:02d}" if moment.minute else f"{moment.hour}h"


def when_label(event: dict) -> str:
    start, end = wall_time(event["start_date"]), wall_time(event.get("end_date"))
    label = day_label(start)
    if event.get("start_time_known"):
        label += f" à {hour_label(start)}"
    if end and end.date() != start.date():
        label = f"du {label} au {day_label(end)}"
    elif end and event.get("end_time_known") and event.get("start_time_known"):
        label += f" – {hour_label(end)}"
    return label[:1].upper() + label[1:]


def price_label(event: dict) -> str:
    """« Gratuit » seulement si c'est confirmé : sans information de la
    source, la plupart des scrapers supposent « gratuit » (price_confirmed
    faux), ce qu'on n'affiche pas comme une certitude."""
    amount = event.get("price_amount")
    if event.get("price_type") == "paid" and amount:
        return f"{amount:g} €".replace(".", ",")
    if not event.get("price_confirmed"):
        return "Prix non communiqué"
    return "Gratuit" if event.get("price_type") == "free" else "Payant"


def image_for(event: dict) -> str:
    if event.get("image_url"):
        return event["image_url"]
    category = event.get("category_id") or "associatif_autre"
    return CATEGORY_IMAGE.format(CATEGORY_IMAGE_FILE.get(category, category))


def distance_km(lat: float, lon: float) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (HOME[0], HOME[1], lat, lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def event_path(event: dict) -> str:
    return f"e/{event['id']}/"


def json_ld(event: dict) -> str:
    start, end = wall_time(event["start_date"]), wall_time(event.get("end_date"))
    data = {
        "@context": "https://schema.org",
        "@type": "Event",
        "name": event["title"],
        "startDate": start.replace(tzinfo=PARIS).isoformat() if event.get("start_time_known") else start.date().isoformat(),
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": {
            "@type": "Place",
            "name": event.get("location_name") or "",
            "address": event.get("address") or "",
            "geo": {"@type": "GeoCoordinates", "latitude": event["latitude"], "longitude": event["longitude"]},
        },
        "image": [image_for(event)],
        "description": (event.get("description") or "")[:500],
        "isAccessibleForFree": event.get("price_type") == "free" and bool(event.get("price_confirmed")),
        "url": f"{BASE_URL}/{event_path(event)}",
    }
    if end:
        data["endDate"] = end.replace(tzinfo=PARIS).isoformat() if event.get("end_time_known") else end.date().isoformat()
    if event.get("price_amount") and event.get("price_type") != "free":
        data["offers"] = {"@type": "Offer", "price": event["price_amount"], "priceCurrency": "EUR",
                          "url": event.get("website_url") or f"{BASE_URL}/{event_path(event)}"}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def layout(title: str, body: str, *, description: str, url: str, image: str | None = None,
           extra_head: str = "", depth: int = 0) -> str:
    root = "../" * depth
    e = html.escape
    og_image = f'<meta property="og:image" content="{e(image)}">\n  <meta name="twitter:card" content="summary_large_image">' if image else ""
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}">
  <link rel="canonical" href="{e(url)}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Wavents">
  <meta property="og:title" content="{e(title)}">
  <meta property="og:description" content="{e(description)}">
  <meta property="og:url" content="{e(url)}">
  {og_image}
  <meta name="theme-color" content="#0B4F6C">
  <link rel="stylesheet" href="{root}style.css">
  {extra_head}
</head>
<body>
  <header class="top"><a href="{root}" class="brand">Wavents</a><span class="tagline">Les sorties autour de chez toi</span></header>
  <main>
{body}
  </main>
  <footer>
    <p>{play_store_link("Wavents sur Google Play")} · <a href="{root}confidentialite.html">Confidentialité</a></p>
    <p class="muted">Informations publiées par les organisateurs et les agendas publics ; vérifiez auprès de l'organisateur avant de vous déplacer.</p>
  </footer>
</body>
</html>
"""


def event_page(event: dict) -> str:
    e = html.escape
    emoji, label = CATEGORIES.get(event.get("category_id"), ("🤝", "Événement"))
    url = f"{BASE_URL}/{event_path(event)}"
    when = when_label(event)
    name, address = event.get("location_name") or "", event.get("address") or ""
    # Adresse qui reprend déjà le nom du lieu : pas de doublon.
    place = address if name and address.startswith(name) else ", ".join(p for p in (name, address) if p)
    summary = f"{when} · {event.get('location_name') or ''}".strip(" ·")
    description = event.get("description") or ""
    maps = f"https://www.openstreetmap.org/?mlat={event['latitude']}&mlon={event['longitude']}#map=16/{event['latitude']}/{event['longitude']}"
    links = [f'<a class="button primary" href="wavents://event/{e(event["id"])}">Ouvrir dans l\'appli Wavents</a>']
    if (event.get("website_url") or "").startswith("http"):
        links.append(f'<a class="button" href="{e(event["website_url"])}" rel="nofollow">Site de l\'organisateur</a>')
    links.append(f'<a class="button" href="{maps}">Voir sur la carte</a>')
    body = f"""    <article class="event">
      <img class="cover" src="{e(image_for(event))}" alt="" loading="eager">
      <p class="category">{emoji} {e(label)}{' · Pour les enfants' if event.get('for_kids') else ''}</p>
      <h1>{e(event['title'])}</h1>
      <ul class="facts">
        <li>📅 {e(when)}</li>
        <li>📍 {e(place)}</li>
        <li>💶 {e(price_label(event))}</li>
      </ul>
      <div class="actions">{''.join(links)}</div>
      {f'<div class="description">{e(description).replace(chr(10), "<br>")}</div>' if description else ''}
      <p class="muted">Tous les événements autour de toi, avec des rappels : {play_store_link("télécharge l'appli Wavents")}</p>
    </article>"""
    return layout(
        f"{event['title']} – Wavents",
        body,
        description=summary,
        url=url,
        image=image_for(event),
        extra_head=f'<script type="application/ld+json">{json_ld(event)}</script>',
        depth=2,
    )


def home_page(events: list[dict], now: datetime) -> str:
    e = html.escape
    horizon = now + timedelta(days=HOME_DAYS)
    nearby = [
        ev for ev in events
        if distance_km(ev["latitude"], ev["longitude"]) <= HOME_RADIUS_KM
        and wall_time(ev["start_date"]) <= horizon
    ]
    by_day: dict[str, list[dict]] = {}
    for ev in nearby:
        start = max(wall_time(ev["start_date"]), now.replace(hour=0, minute=0))
        by_day.setdefault(start.date().isoformat(), []).append(ev)
    sections = []
    for day in sorted(by_day):
        items = []
        for ev in by_day[day]:
            emoji, _ = CATEGORIES.get(ev.get("category_id"), ("🤝", ""))
            start = wall_time(ev["start_date"])
            hour = hour_label(start) if ev.get("start_time_known") and start.date().isoformat() == day else ""
            items.append(
                f'<li><a href="{event_path(ev)}">{emoji} {e(ev["title"])}</a>'
                f'<span class="muted"> {e(hour)} · {e(ev.get("location_name") or "")}</span></li>'
            )
        label = day_label(datetime.fromisoformat(day))
        sections.append(f'<section><h2>{e(label[:1].upper() + label[1:])}</h2><ul class="list">{"".join(items)}</ul></section>')
    body = f"""    <h1>Que faire autour de Belleville-en-Beaujolais ?</h1>
    <p class="muted">Les {len(nearby)} événements des {HOME_DAYS} prochains jours à moins de {HOME_RADIUS_KM} km. Mis à jour chaque jour.</p>
    <p>{play_store_link("Télécharger l'appli Wavents", "button primary")}</p>
{''.join(sections)}"""
    return layout(
        "Wavents – Sorties et événements autour de Belleville-en-Beaujolais",
        body,
        description="Fêtes, concerts, marchés, spectacles et sorties des prochains jours autour de Belleville-en-Beaujolais.",
        url=f"{BASE_URL}/",
    )


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    if out.exists():
        shutil.rmtree(out)
    (out / "e").mkdir(parents=True)
    static = Path(__file__).parent / "static"
    for item in static.iterdir():
        shutil.copy(item, out / item.name)

    now = datetime.now(PARIS).replace(tzinfo=None)
    events = [
        ev for ev in fetch_events()
        if ev.get("start_date") and ev.get("latitude") is not None
        and (wall_time(ev.get("end_date")) or wall_time(ev["start_date"])) >= now - timedelta(days=1)
    ]
    for ev in events:
        folder = out / event_path(ev)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(event_page(ev), encoding="utf-8")
    (out / "index.html").write_text(home_page(events, now), encoding="utf-8")

    urls = [f"{BASE_URL}/"] + [f"{BASE_URL}/{event_path(ev)}" for ev in events]
    sitemap = "\n".join(f"  <url><loc>{html.escape(quote(u, safe=':/'))}</loc></url>" for u in urls)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}\n</urlset>\n',
        encoding="utf-8",
    )
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    print(f"{len(events)} pages d'événements générées dans {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
