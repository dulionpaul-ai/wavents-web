"""Génère le site public de Wavents : une page d'accueil (vitrine de l'appli,
sélection du week-end, carte, agenda filtrable des prochains jours autour de
Belleville), une page par événement à venir, le sitemap et llms.txt (présentation pour les assistants IA).

Lancé chaque jour par .github/workflows/pages.yml, puis publié sur GitHub
Pages. Lit les événements publiés (`status = 'active'`) avec la clé anon
de Supabase : la même que celle de l'appli, publique par nature (elle ne
donne accès qu'aux événements publiés).

Convention des dates (voir le dépôt Wavents, CLAUDE.md) : `start_date` et
`end_date` sont l'heure de Paris stockée avec un suffixe UTC. On les
affiche telles quelles et on les convertit en vrai fuseau de Paris pour le
balisage schema.org et les fichiers agenda (.ics).

Refonte du 27/09/2026 (demande de Paul : « un site de ouf », pop, qui
donne envie) : même adresse pour chaque événement (`e/<id>/`, liens déjà
partagés par l'appli), le reste est neuf. Styles dans static/style.css,
interactions (filtres, carte, partage) dans static/app.js.

Usage : python generate.py [dossier_de_sortie]   (défaut : site/)
Variable d'environnement : PAGES_BASE_URL (adresse publique du site).
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import shutil
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

SUPABASE_URL = "https://mzbxnwvrvrhlucjzbzls.supabase.co"
SUPABASE_ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im16Ynhud3ZydnJobHVjanpiemxzIiwicm9sZSI6"
    "ImFub24iLCJpYXQiOjE3ODQ2NDMwMzQsImV4cCI6MjEwMDIxOTAzNH0.wqoOLgNM92KmwZfbh4c56dVN13DDlCxJhuOOoqlH1is"
)
BASE_URL = os.environ.get("PAGES_BASE_URL", "https://wavents.fr").rstrip("/")
PLAY_STORE_URL = "https://play.google.com/store/apps/details?id=com.wavents.wavents"
# Passer à True le jour où l'appli est publiée sur le Play Store (test
# ouvert ou production) : avant, la fiche renvoie une erreur 404.
PLAY_STORE_PUBLISHED = False

PARIS = ZoneInfo("Europe/Paris")
HOME = (46.1075, 4.7536)  # centre de Belleville-en-Beaujolais, comme l'appli
HOME_RADIUS_KM = 30
HOME_DAYS = 21
AGENDA_DAY_PREVIEW = 12  # cartes affichées par jour avant « Voir les autres sorties » (static/app.js)

FIELDS = (
    "id,title,description,category_id,location_name,address,latitude,longitude,"
    "start_date,end_date,start_time_known,end_time_known,price_type,price_amount,price_confirmed,"
    "image_url,website_url,for_kids,source"
)
# id -> (icône, libellé, couleur) : mêmes icônes Material et mêmes couleurs
# que l'appli (lib/models/category.dart), pour qu'on se sente au même endroit.
def _icon(name: str) -> str:
    return f'<span class="ms" aria-hidden="true">{name}</span>'


CATEGORIES = {
    "concert": (_icon("music_note"), "Concert", "#E91E63"),
    "fete": (_icon("celebration"), "Fête", "#FF5252"),
    "marche": (_icon("storefront"), "Marché", "#FF9800"),
    "brocante": (_icon("sell"), "Brocante", "#D4A017"),
    "spectacle": (_icon("theater_comedy"), "Spectacle", "#8E24AA"),
    "sport": (_icon("sports_soccer"), "Sport", "#43A047"),
    "culture": (_icon("museum"), "Culture", "#7C4DFF"),
    "atelier": (_icon("brush"), "Atelier", "#8D6E63"),
    "visite": (_icon("explore"), "Visite", "#1E88E5"),
    "associatif_autre": (_icon("groups"), "Associatif", "#00BFA5"),
}
CATEGORY_PHOTO = {
    "concert": "concert", "fete": "fete", "marche": "marche", "brocante": "brocante", "spectacle": "spectacle",
    "sport": "sport", "culture": "culture", "atelier": "atelier", "visite": "visite",
    "associatif_autre": "associatif",
}
DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
DAYS_SHORT = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
MONTHS_SHORT = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
                "sept.", "oct.", "nov.", "déc."]

e = html.escape


# --- Données ---------------------------------------------------------------

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


def end_of(event: dict) -> datetime:
    return wall_time(event.get("end_date")) or wall_time(event["start_date"])


def is_long(event: dict) -> bool:
    """Expositions, animations permanentes : sur plusieurs jours d'affilée.
    Rangées à part pour ne pas noyer l'agenda du jour."""
    return (end_of(event).date() - wall_time(event["start_date"]).date()).days >= 6


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def distance_km(lat: float, lon: float, origin: tuple[float, float] = HOME) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (origin[0], origin[1], lat, lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


POSTCODE_TOWN = re.compile(r"\b\d{5}\s+([A-Za-zÀ-ÿ'’\- ]+?)\s*(?:,|$)")
NOT_A_TOWN = {"france", "france métropolitaine", "auvergne-rhône-alpes", "auvergne rhône alpes",
              "bourgogne-franche-comté", "rhône", "ain", "saône-et-loire"}
SMALL_WORDS = {"en", "sur", "de", "la", "le", "les", "du", "des", "et", "au", "aux"}


def commune_of(event: dict) -> str:
    """Commune de l'événement, lue dans l'adresse (« …, 69220 Belleville-en-
    Beaujolais ») ou, à défaut, dernière partie de l'adresse. Sert à
    distinguer les « Marché » de chaque village."""
    address = event.get("address") or ""
    m = POSTCODE_TOWN.search(address)
    if m:
        town = m.group(1)
    else:
        parts = [p.strip() for p in address.split(",") if p.strip()]
        while parts and (parts[-1].lower() in NOT_A_TOWN or re.fullmatch(r"\d{5}", parts[-1])):
            parts = parts[:-1]
        town = parts[-1] if parts else (event.get("location_name") or "")
    town = re.sub(r"^\d{5}\s*", "", re.sub(r"\s+", " ", town)).strip(" -")
    if town.isupper() or town.islower():
        words = re.split(r"([ -])", town.lower())
        town = "".join(w if (w in SMALL_WORDS and i) or w in " -" else w.capitalize() for i, w in enumerate(words))
    return town[:40]


def category(event: dict) -> tuple[str, str, str]:
    return CATEGORIES.get(event.get("category_id") or "", CATEGORIES["associatif_autre"])


def category_photo(category_id: str | None, root: str) -> str:
    return f"{root}categories/{CATEGORY_PHOTO.get(category_id or '', 'associatif')}.jpg"


def real_image(event: dict) -> str | None:
    """Photo de l'événement, sauf les images vides de chargement différé
    (« data:image/svg+xml… ») qu'un scraper aurait prises pour une photo."""
    url = event.get("image_url") or ""
    return url if url.startswith("http") else None


def image_for(event: dict, root: str = "") -> str:
    return real_image(event) or category_photo(event.get("category_id"), root)


def absolute_image(event: dict) -> str:
    return real_image(event) or f"{BASE_URL}/{category_photo(event.get('category_id'), '')}"


def event_path(event: dict) -> str:
    return f"e/{event['id']}/"


# --- Libellés ----------------------------------------------------------------

def day_label(moment: datetime | date) -> str:
    return f"{DAYS[moment.weekday()]} {moment.day} {MONTHS[moment.month - 1]}"


def hour_label(moment: datetime) -> str:
    return f"{moment.hour}h{moment.minute:02d}" if moment.minute else f"{moment.hour}h"


def cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def when_label(event: dict) -> str:
    start, end = wall_time(event["start_date"]), wall_time(event.get("end_date"))
    label = day_label(start)
    if start.year != datetime.now().year:
        label += f" {start.year}"
    if event.get("start_time_known"):
        label += f" à {hour_label(start)}"
    if end and end.date() != start.date():
        label = f"du {label} au {day_label(end)}"
    elif end and event.get("end_time_known") and event.get("start_time_known"):
        label += f" – {hour_label(end)}"
    return cap(label)


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


def is_free(event: dict) -> bool:
    return event.get("price_type") == "free" and bool(event.get("price_confirmed"))


def place_label(event: dict) -> str:
    name, address = event.get("location_name") or "", event.get("address") or ""
    # Adresse qui reprend déjà le nom du lieu : pas de doublon.
    if name and address.startswith(name):
        return address
    return ", ".join(p for p in (name, address) if p)


# --- Marchés regroupés (comme l'appli, lib/utils/event_series.dart) --------

def series_key(event: dict) -> str | None:
    if event.get("category_id") != "marche":
        return None
    return f"{fold(event['title']).strip()}|{event['latitude']:.3f}|{event['longitude']:.3f}"


def build_series(events: list[dict]) -> dict[str, list[dict]]:
    series: dict[str, list[dict]] = {}
    for ev in events:
        key = series_key(ev)
        if key:
            series.setdefault(key, []).append(ev)
    for items in series.values():
        items.sort(key=lambda ev: ev["start_date"])
    return series


def series_label(items: list[dict]) -> str | None:
    if len(items) < 2:
        return None
    weekdays = {wall_time(ev["start_date"]).weekday() for ev in items}
    if len(weekdays) == 1:
        return f"tous les {DAYS[weekdays.pop()]}s"
    return f"+ {len(items) - 1} dates"


def collapse_series(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for ev in events:
        key = series_key(ev)
        if key:
            if key in seen:
                continue
            seen.add(key)
        out.append(ev)
    return out


# --- Balisage ------------------------------------------------------------------

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
        "image": [absolute_image(event)],
        "description": (event.get("description") or "")[:500],
        "isAccessibleForFree": is_free(event),
        "url": f"{BASE_URL}/{event_path(event)}",
    }
    if end:
        data["endDate"] = end.replace(tzinfo=PARIS).isoformat() if event.get("end_time_known") else end.date().isoformat()
    if event.get("price_amount") and event.get("price_type") != "free":
        data["offers"] = {"@type": "Offer", "price": event["price_amount"], "priceCurrency": "EUR",
                          "url": event.get("website_url") or f"{BASE_URL}/{event_path(event)}"}
    elif is_free(event):
        # Prix « recommandé » par Google pour les résultats enrichis d'événements.
        data["offers"] = {"@type": "Offer", "price": 0, "priceCurrency": "EUR",
                          "url": f"{BASE_URL}/{event_path(event)}"}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def ics(event: dict) -> str:
    """Fichier agenda (« Ajouter à mon agenda ») : heure de Paris réelle,
    journée entière quand l'heure est inconnue."""
    start, end = wall_time(event["start_date"]), wall_time(event.get("end_date"))

    def esc(text: str) -> str:
        return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    if event.get("start_time_known"):
        dtstart = f"DTSTART;TZID=Europe/Paris:{start:%Y%m%dT%H%M%S}"
        stop = end if end and event.get("end_time_known") and end > start else start + timedelta(hours=2)
        dtend = f"DTEND;TZID=Europe/Paris:{stop:%Y%m%dT%H%M%S}"
    else:
        last = (end or start).date() + timedelta(days=1)
        dtstart = f"DTSTART;VALUE=DATE:{start:%Y%m%d}"
        dtend = f"DTEND;VALUE=DATE:{last:%Y%m%d}"
    url = f"{BASE_URL}/{event_path(event)}"
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Wavents//FR", "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{event['id']}@wavents",
        f"DTSTAMP:{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        dtstart, dtend,
        f"SUMMARY:{esc(event['title'])}",
        f"LOCATION:{esc(place_label(event))}",
        f"DESCRIPTION:{esc((event.get('description') or '')[:900] + chr(10) + chr(10) + url)}",
        f"URL:{url}",
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"


# --- Gabarit commun --------------------------------------------------------------

def play_cta(extra_class: str = "") -> str:
    if PLAY_STORE_PUBLISHED:
        return (f'<a class="store-badge {extra_class}" href="{PLAY_STORE_URL}">'
                f'<span class="store-icon">▶</span><span><small>Disponible sur</small>Google Play</span></a>' + app_store_soon(extra_class))
    return (f'<span class="store-badge soon {extra_class}"><span class="store-icon">▶</span>'
            f'<span><small>Bientôt sur</small>Google Play</span></span>' + app_store_soon(extra_class))


def app_store_soon(extra_class: str = "") -> str:
    """Badge « Bientôt sur l'App Store » (pas encore de version iPhone)."""
    return (f'<span class="store-badge soon {extra_class}"><span class="store-icon apple">{_icon("phone_iphone")}</span>'
            f"<span><small>Bientôt sur</small>l'App Store</span></span>")


def layout(title: str, body: str, *, description: str, url: str, image: str | None = None,
           extra_head: str = "", depth: int = 0, page_class: str = "") -> str:
    root = "../" * depth
    og_image = (f'<meta property="og:image" content="{e(image)}">\n  '
                f'<meta name="twitter:card" content="summary_large_image">') if image else ""
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}">
  <link rel="canonical" href="{e(url)}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="Wavents">
  <meta property="og:locale" content="fr_FR">
  <meta property="og:title" content="{e(title)}">
  <meta property="og:description" content="{e(description)}">
  <meta property="og:url" content="{e(url)}">
  {og_image}
  <meta name="theme-color" content="#065FBB">
  <link rel="icon" href="{root}favicon.ico" sizes="48x48">
  <link rel="icon" type="image/svg+xml" sizes="any" href="{root}img/favicon.svg">
  <link rel="icon" type="image/png" sizes="96x96" href="{root}img/favicon-96.png">
  <link rel="icon" type="image/png" sizes="192x192" href="{root}img/favicon-192.png">
  <link rel="apple-touch-icon" href="{root}img/apple-touch-icon.png">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Nunito:wght@700;800;900&family=Roboto:wght@400;500;700&display=swap">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,500,1,0&display=block">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css">
  <link rel="stylesheet" href="{root}style.css">
  {extra_head}
</head>
<body class="{page_class}">
  <header class="nav" id="top">
    <a href="{root}" class="nav-brand"><img src="{root}img/pin.png" alt="" width="56" height="56"><span>Wavents</span></a>
    <nav class="nav-links">
      <a href="{root}#week-end">Ce week-end</a>
      <a href="{root}#carte">Carte</a>
      <a href="{root}#agenda">Agenda</a>
      <a href="{root}#appli" class="nav-cta">L'appli</a>
    </nav>
  </header>
{body}
  <footer class="footer">
    <div class="footer-inner">
      <div class="footer-brand">
        <img src="{root}img/logo-256.png" alt="" width="64" height="64">
        <div><strong>Wavents</strong><p>Toutes les sorties autour de chez toi.<br>Fait avec ❤️ à Belleville-en-Beaujolais.</p></div>
      </div>
      <div class="footer-links">
        <a href="{root}#week-end">Ce week-end</a>
        <a href="{root}#agenda">Agenda</a>
        <a href="{root}#carte">Carte</a>
        <a href="{root}ville/">Par commune</a>
        <a href="{root}ce-week-end/">Ce week-end dans le Beaujolais</a>
        <a href="{root}marches/">Marchés</a>
        <a href="{root}brocantes/">Brocantes</a>
        <a href="{root}gratuit/">Sorties gratuites</a>
        <a href="{root}enfants/">Avec les enfants</a>
        <a href="{root}a-propos/">À propos</a>
        <a href="{root}faq/">Questions fréquentes</a>
        <a href="{root}confidentialite.html">Confidentialité</a>
        <a href="mailto:hello@wavents.fr">Contact</a>
      </div>
    </div>
    <p class="footer-note">Informations issues des agendas publics et des organisateurs, mises à jour chaque jour. Vérifie auprès de l'organisateur avant de te déplacer.</p>
  </footer>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js" defer></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js" defer></script>
  <script src="{root}app.js" defer></script>
  <script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</body>
</html>
"""


# --- Composants ------------------------------------------------------------------

def date_chip(event: dict) -> str:
    start = wall_time(event["start_date"])
    # Déjà commencé (expo, festival) : on affiche sa fin plutôt qu'une date passée.
    if start.date() < datetime.now(PARIS).date() and end_of(event).date() > start.date():
        end = end_of(event)
        return (f'<span class="date-chip until"><i>jusqu\'au</i><b>{end.day}</b>'
                f'<i>{MONTHS_SHORT[end.month - 1]}</i></span>')
    return (f'<span class="date-chip"><i>{DAYS_SHORT[start.weekday()]}</i><b>{start.day}</b>'
            f'<i>{MONTHS_SHORT[start.month - 1]}</i></span>')


def fallback(event: dict, root: str) -> str:
    """Photo cassée : la photo de la catégorie prend le relais."""
    return f"this.onerror=null;this.src='{category_photo(event.get('category_id'), root)}'"


def media(event: dict, root: str, css: str = "") -> str:
    """Photo de l'événement, ou celle de sa catégorie (comme EventImage dans
    l'appli)."""
    return f'<img class="{css}" src="{e(image_for(event, root))}" alt="" loading="lazy" onerror="{fallback(event, root)}">'


def card(event: dict, series: dict[str, list[dict]], root: str = "", *, size: str = "") -> str:
    emoji, label, color = category(event)
    start = wall_time(event["start_date"])
    recurring = series_label(series.get(series_key(event) or "", []))
    when = hour_label(start) if event.get("start_time_known") and (start.hour or start.minute) else ""
    meta = " · ".join(p for p in (when, commune_of(event)) if p)
    badges = []
    if is_free(event):
        badges.append('<span class="badge free">Gratuit</span>')
    if event.get("for_kids"):
        badges.append('<span class="badge kids">Enfants</span>')
    if recurring:
        badges.append(f'<span class="badge series">{e(recurring)}</span>')
    search = fold(f"{event['title']} {commune_of(event)} {event.get('location_name') or ''} {label}")
    return f"""<a class="card {size}" href="{root}{event_path(event)}" data-cat="{e(event.get('category_id') or '')}" data-free="{1 if is_free(event) else 0}" data-kids="{1 if event.get('for_kids') else 0}" data-q="{e(search)}" style="--cat:{color}">
  <div class="card-media">{media(event, root)}{date_chip(event)}<span class="cat-pill">{emoji} {e(label)}</span></div>
  <div class="card-body"><h3>{e(event['title'])}</h3><p class="card-meta">{e(meta)}</p><div class="badges">{''.join(badges)}</div></div>
</a>"""


def row_card(event: dict, series: dict[str, list[dict]], root: str = "") -> str:
    """Carte en ligne, calquée sur la fiche de l'appli
    (lib/widgets/event_preview_card.dart) : photo arrondie à gauche,
    pastille de catégorie, titre, date dans la couleur de la catégorie."""
    emoji, label, color = category(event)
    start = wall_time(event["start_date"])
    recurring = series_label(series.get(series_key(event) or "", []))
    if is_long(event) and start.date() < datetime.now(PARIS).date():
        end = end_of(event)
        when = f"jusqu'au {end.day} {MONTHS_SHORT[end.month - 1]}"
    else:
        when = f"{DAYS_SHORT[start.weekday()]} {start.day} {MONTHS_SHORT[start.month - 1]}"
        if event.get("start_time_known") and (start.hour or start.minute):
            when += f" · {hour_label(start)}"
    if recurring:
        when = recurring
    meta = " · ".join(p for p in (when, commune_of(event)) if p)
    badges = []
    if is_free(event):
        badges.append('<span class="badge free">Gratuit</span>')
    if event.get("for_kids"):
        badges.append('<span class="badge kids">Enfants</span>')
    search = fold(f"{event['title']} {commune_of(event)} {event.get('location_name') or ''} {label}")
    return f"""<a class="row-card" href="{root}{event_path(event)}" data-cat="{e(event.get('category_id') or '')}" data-free="{1 if is_free(event) else 0}" data-kids="{1 if event.get('for_kids') else 0}" data-q="{e(search)}" style="--cat:{color}">
  <span class="row-media">{media(event, root)}</span>
  <span class="row-body"><span class="cat-chip">{emoji} {e(label)}</span><b>{e(event['title'])}</b><span class="row-date">{e(meta)}</span>{f'<span class="badges">{"".join(badges)}</span>' if badges else ''}</span>
</a>"""


# --- Page d'accueil ------------------------------------------------------------

def weekend_range(today: date) -> tuple[date, date]:
    """Du vendredi au dimanche ; le week-end en cours s'il a commencé."""
    if today.weekday() >= 4:
        return today, today + timedelta(days=6 - today.weekday())
    friday = today + timedelta(days=4 - today.weekday())
    return friday, friday + timedelta(days=2)


def overlaps(event: dict, first: date, last: date) -> bool:
    return wall_time(event["start_date"]).date() <= last and end_of(event).date() >= first


def pick_weekend(events: list[dict], today: date) -> list[dict]:
    first, last = weekend_range(today)
    candidates = [ev for ev in collapse_series(events) if overlaps(ev, first, last)]

    def score(ev: dict) -> tuple:
        long_range = (end_of(ev) - wall_time(ev["start_date"])).days > 3
        return (
            ev.get("category_id") == "marche",       # les marchés en dernier
            long_range,                               # expositions longues ensuite
            not ev.get("image_url"),                  # d'abord ceux qui ont une vraie photo
            round(distance_km(ev["latitude"], ev["longitude"]) / 5),
            ev["start_date"],
        )
    candidates.sort(key=score)
    picked, seen_titles = [], set()
    for ev in candidates:
        key = re.sub(r"[^a-z0-9]", "", fold(ev["title"]))[:14]
        if key in seen_titles:
            continue
        seen_titles.add(key)
        picked.append(ev)
        if len(picked) == 12:
            break
    return picked


def phone_mockup(events: list[dict]) -> str:
    rows = []
    for ev in events[:3]:
        emoji, label, color = category(ev)
        start = wall_time(ev["start_date"])
        rows.append(f"""<div class="ph-card" style="--cat:{color}">
  <span class="ph-media" style="--cat:{color}">{media(ev, '')}</span>
  <div><span class="ph-date">{DAYS_SHORT[start.weekday()]} {start.day} {MONTHS_SHORT[start.month - 1]}</span><b>{e(ev['title'][:44])}</b><small>{emoji} {e(commune_of(ev))}</small></div>
</div>""")
    return f"""<div class="phone" aria-hidden="true">
  <div class="phone-notch"></div>
  <div class="phone-screen">
    <div class="ph-search">🔍 Rechercher un événement…</div>
    <div class="ph-chips"><span class="on">Ce week-end</span><span>Gratuit</span><span>Enfants</span></div>
    <div class="ph-map"><span class="pin p1"></span><span class="pin p2"></span><span class="pin p3"></span><span class="pin p4"></span><span class="pin p5"></span><span class="me"></span></div>
    {''.join(rows)}
  </div>
</div>"""


def home_page(events: list[dict], all_upcoming: list[dict], now: datetime) -> str:
    today = now.date()
    horizon = now + timedelta(days=HOME_DAYS)
    nearby = [
        ev for ev in events
        if distance_km(ev["latitude"], ev["longitude"]) <= HOME_RADIUS_KM
        and wall_time(ev["start_date"]) <= horizon
        and end_of(ev).date() >= today
    ]
    series = build_series(nearby)
    shown = collapse_series(nearby)
    ongoing = [ev for ev in shown if is_long(ev)]
    ongoing.sort(key=lambda ev: (not ev.get("image_url"), end_of(ev)))
    communes = {commune_of(ev) for ev in all_upcoming if commune_of(ev)}
    sources = {ev.get("source") for ev in all_upcoming if ev.get("source")}
    weekend = pick_weekend(nearby, today)
    first, last = weekend_range(today)
    weekend_title = "Ce week-end" if today.weekday() >= 3 else "Le week-end prochain"
    weekend_dates = (f"{DAYS[first.weekday()]} {first.day} → {DAYS[last.weekday()]} {last.day} {MONTHS[last.month - 1]}")

    counts: dict[str, int] = {}
    for ev in shown:
        cid = ev.get("category_id") or "associatif_autre"
        counts[cid] = counts.get(cid, 0) + 1
    cat_tiles = "".join(
        f'<a class="cat-tile" href="#agenda" data-filter-cat="{cid}" style="--cat:{color}">'
        f'<img src="categories/{CATEGORY_PHOTO[cid]}.jpg" alt="" loading="lazy">'
        f'<span class="cat-tile-label"><b><span class="cat-emoji">{emoji}</span>{label}</b><small>{counts.get(cid, 0)} à venir</small></span></a>'
        for cid, (emoji, label, color) in CATEGORIES.items() if counts.get(cid)
    )
    kids_count = sum(1 for ev in shown if ev.get("for_kids"))
    if kids_count:
        cat_tiles = (f'<a class="cat-tile kids" href="#agenda" data-filter-kids style="--cat:#E91E63">'
                     f'<span class="ms kids-art" aria-hidden="true">family_restroom</span>'
                     f'<span class="cat-tile-label"><b><span class="cat-emoji">{_icon("child_care")}</span>Pour les enfants</b>'
                     f'<small>{kids_count} à venir</small></span></a>') + cat_tiles
    chips = "".join(
        f'<button class="chip" data-cat="{cid}" style="--cat:{color}">{emoji} {label}</button>'
        for cid, (emoji, label, color) in CATEGORIES.items() if counts.get(cid)
    )

    by_day: dict[date, list[dict]] = {}
    for ev in shown:
        if is_long(ev):
            continue
        start = max(wall_time(ev["start_date"]).date(), today)
        by_day.setdefault(start, []).append(ev)
    day_sections, day_tabs = [], []
    for index, day in enumerate(sorted(by_day)):
        items = sorted(by_day[day], key=lambda ev: (ev.get("category_id") == "marche",
                                                    not ev.get("start_time_known"), ev["start_date"]))
        label = "Aujourd'hui" if day == today else "Demain" if day == today + timedelta(days=1) else cap(day_label(day))
        short = "Aujourd'hui" if day == today else "Demain" if day == today + timedelta(days=1) else f"{DAYS_SHORT[day.weekday()]} {day.day}"
        on = " is-on" if index == 0 else ""
        day_tabs.append(f'<button class="day-tab{on}" data-day="{index}"><b>{e(short)}</b><small>{len(items)}</small></button>')
        day_sections.append(
            f'<section class="day{on}" data-day="{index}"><h3 class="day-title">{e(label)}</h3>'
            f'<div class="grid">{"".join(card(ev, series) for ev in items)}</div>'
            f'<div class="day-more"><button class="btn btn-ghost" data-more>Voir les autres sorties</button></div></section>'
        )

    map_points = [
        {
            "t": ev["title"], "c": ev.get("category_id") or "associatif_autre",
            "la": round(ev["latitude"], 5), "lo": round(ev["longitude"], 5),
            "d": cap(day_label(wall_time(ev["start_date"]))), "p": commune_of(ev),
            "u": event_path(ev), "k": 1 if ev.get("for_kids") else 0,
        }
        for ev in shown
    ]
    categories_js = {cid: {"e": emoji, "l": label, "c": color} for cid, (emoji, label, color) in CATEGORIES.items()}
    payload = json.dumps({"points": map_points, "cats": categories_js, "home": HOME}, ensure_ascii=False)
    data_script = f'<script id="wv-data" type="application/json">{payload.replace("</", "<" + chr(92) + "/")}</script>'

    body = f"""  <section class="hero">
    <div class="hero-bg"><div class="sun"></div></div>
    <div class="hero-inner">
      <div class="hero-copy reveal">
        <p class="eyebrow"><span class="pulse"></span> {len(all_upcoming)} événements à venir en ce moment</p>
        <h1>Découvre les événements <span class="sunny">près de chez toi</span></h1>
        <p class="lead">Wavents trouve chaque jour des concerts, marchés, animations, brocantes… dans le Beaujolais et le nord de Lyon (pour l'instant&nbsp;!).</p>
        <p class="punch">Ne rate plus la fête de quartier en bas de chez toi juste parce que tu ne savais pas.</p>
        <div class="hero-actions">
          <a class="btn btn-sun" href="#week-end">Voir ce week-end</a>
          <div class="store-row">{play_cta()}</div>
        </div>
      </div>
      <div class="hero-visual reveal">
        {phone_mockup(weekend or shown)}
        <img class="hero-logo float" src="img/logo-256.png" alt="Wavents" width="150" height="150">
      </div>
    </div>
    <svg class="waves" viewBox="0 0 1440 160" preserveAspectRatio="none" overflow="visible" aria-hidden="true">
      <path class="w1" d="M-100,96 C160,150 420,40 700,80 C980,120 1240,40 1540,90 L1540,160 L-100,160 Z"/>
      <path class="w3" d="M-100,120 C180,90 460,150 740,120 C1020,90 1260,140 1540,115 L1540,160 L-100,160 Z"/>
    </svg>
  </section>

  <section class="stats">
    <div class="stat reveal" style="--c:#065FBB"><b data-count="{len(all_upcoming)}">{len(all_upcoming)}</b><span>événements à venir</span></div>
    <div class="stat reveal" style="--c:#0B81E1"><b data-count="{len(communes)}">{len(communes)}</b><span>communes couvertes</span></div>
    <div class="stat reveal" style="--c:#01A9D8"><b data-count="{len(sources)}">{len(sources)}</b><span>agendas suivis chaque jour</span></div>
    <div class="stat reveal" style="--c:#FF8A1E"><b>0 €</b><span>gratuit, sans pub</span></div>
  </section>

  <div class="band">
  <div class="band-sun" aria-hidden="true"></div>
  <section class="section" id="week-end">
    <div class="section-head reveal">
      <div><p class="kicker">{e(cap(weekend_dates))}</p><h2>{weekend_title}</h2></div>
      <div class="scroller-nav"><button data-scroll="-1" aria-label="Précédent">←</button><button data-scroll="1" aria-label="Suivant">→</button></div>
    </div>
    <div class="scroller reveal">{''.join(card(ev, series, size="big") for ev in weekend) or '<p class="empty">Rien de prévu pour l’instant : reviens bientôt !</p>'}</div>
  </section>

  {f'''<section class="section">
    <div class="section-head reveal">
      <div><p class="kicker">Expos, animations, festivals</p><h2>En ce moment</h2></div>
      <div class="scroller-nav"><button data-scroll="-1" aria-label="Précédent">←</button><button data-scroll="1" aria-label="Suivant">→</button></div>
    </div>
    <div class="scroller reveal">{''.join(card(ev, series) for ev in ongoing[:18])}</div>
  </section>''' if ongoing else ''}
  </div>

  <section class="section">
    <div class="section-head reveal"><div><p class="kicker">Envie de quoi ?</p><h2>Par catégorie</h2></div></div>
    <div class="cat-grid reveal">{cat_tiles}</div>
  </section>

  <div class="band band-map">
  <div class="band-sun" aria-hidden="true"></div>
  <section class="section map-section" id="carte">
    <div class="section-head reveal"><div><p class="kicker">{len(shown)} sorties dans les {HOME_DAYS} prochains jours</p><h2>Sur la carte</h2></div></div>
    <div class="map-wrap reveal"><div class="map-box"><div id="map" role="region" aria-label="Carte des événements"></div><div class="map-search" role="search"><span aria-hidden="true">🔍</span><input type="search" id="map-q" placeholder="Aller à une ville…" autocomplete="off" aria-label="Aller à une ville sur la carte"><ul class="map-sugg" hidden></ul></div></div>
    <div class="map-legend" role="group" aria-label="Filtrer la carte"><button class="map-chip is-on" data-map-cat="">Tout</button><button class="map-chip" data-map-kids style="--cat:#E91E63">{_icon("child_care")} Enfants</button>{''.join(f'<button class="map-chip" data-map-cat="{cid}" style="--cat:{c}">{em} {l}</button>' for cid, (em, l, c) in CATEGORIES.items() if counts.get(cid))}</div></div>
  </section>
  </div>

  <section class="section" id="agenda">
    <div class="section-head reveal"><div><p class="kicker">Mis à jour chaque jour</p><h2>L'agenda des {HOME_DAYS} prochains jours</h2></div></div>
    <div class="filters" id="filters">
      <label class="search"><span>🔍</span><input type="search" id="q" placeholder="Un village, un concert, un loto…" autocomplete="off"></label>
      <div class="chips">
        <button class="chip is-on" data-cat="">Tout</button>
        <button class="chip toggle" data-toggle="kids">{_icon("child_care")} Enfants</button>
        <button class="chip toggle" data-toggle="free">{_icon("money_off")} Gratuit</button>
        {chips}
      </div>
      <p class="result-count" id="count" aria-live="polite"></p>
    </div>
    <div class="day-tabs" role="tablist">{''.join(day_tabs)}</div>
    <div class="agenda">{''.join(day_sections)}</div>
    <p class="empty" id="empty" hidden>Aucune sortie ne correspond. Essaie un autre filtre&nbsp;!</p>
  </section>

  <section class="section how">
    <div class="section-head reveal center"><div><p class="kicker">Comment ça marche</p><h2>Ton agenda local, sans effort</h2></div></div>
    <div class="how-grid">
      <div class="how-card reveal" style="--c:#01BAEF"><span class="how-num">🔎</span><h3>On fouille pour toi</h3><p>Wavents trouve les événements des mairies, offices de tourisme, salles de spectacle, associations, billetteries… de la région. L'outil s'étend chaque jour avec de nouvelles zones et de nouvelles sources.</p></div>
      <div class="how-card reveal" style="--c:#FF8A1E"><span class="how-num">🪄</span><h3>On trie et on vérifie</h3><p>Doublons fusionnés, catégories, prix, « pour les enfants » : tout est rangé pour que tu trouves en deux secondes ce qui t'intéresse.</p></div>
      <div class="how-card reveal" style="--c:#FFD66B"><span class="how-num">🔔</span><h3>Tu ne rates plus rien</h3><p>Favoris, rappels avant l'événement et notifications quand ça bouge près de chez toi, dans les catégories que tu aimes.</p></div>
    </div>
  </section>

  <section class="section feedback">
    <div class="feedback-card reveal">
      <span class="feedback-emoji" aria-hidden="true">🫵</span>
      <div>
        <p class="kicker">On compte sur toi</p>
        <h2>Aide-nous à rendre Wavents parfaite</h2>
        <p>On veut l'appli la plus parfaite possible. Un bug, un événement qui manque ou qui est faux, une idée&nbsp;? Dis-le-nous, on lit tout.</p>
        <div class="hero-actions"><a class="btn btn-sun" href="mailto:hello@wavents.fr?subject=Mon%20retour%20sur%20Wavents">✉️ Envoyer un retour</a></div>
      </div>
    </div>
  </section>

  <section class="section app-band" id="appli">
    <div class="app-band-inner reveal">
      <img src="img/logo-256.png" alt="" width="140" height="140" class="float">
      <div>
        <p class="kicker light">L'appli Wavents</p>
        <h2>Toutes tes sorties, dans ta poche.</h2>
        <p>La carte, les filtres, les favoris, les rappels et les notifs. Tu organises un événement&nbsp;? Prends l'affiche en photo : l'IA remplit la fiche pour toi.</p>
        <div class="hero-actions">{play_cta('light')}<a class="btn btn-light" href="#agenda">Parcourir l'agenda</a></div>
      </div>
    </div>
  </section>
  {data_script}"""
    return layout(
        "Wavents – Que faire autour de toi ?",
        body,
        description="Concerts, marchés, fêtes de village, spectacles… Des dizaines de sorties chaque jour dans le Beaujolais et le nord de Lyon (pour l'instant !), près de chez toi.",
        url=f"{BASE_URL}/",
        image=f"{BASE_URL}/img/og.png?v=3",
        extra_head=(f'<script type="application/ld+json">{json.dumps(ORGANIZATION, ensure_ascii=False)}</script>'
                    f'<script type="application/ld+json">{json.dumps(WEBSITE, ensure_ascii=False)}</script>'),
        page_class="home",
    )


# --- Page événement ------------------------------------------------------------

def neighbours_of(event: dict, pool_by_day: dict[date, list[dict]]) -> list[dict]:
    """Autres sorties le même jour à moins de 15 km (marchés de la même
    série exclus), les plus proches et illustrées d'abord."""
    origin = (event["latitude"], event["longitude"])
    day = wall_time(event["start_date"]).date()
    own_series = series_key(event)
    candidates = [
        ev for ev in pool_by_day.get(day, [])
        if ev["id"] != event["id"]
        and (own_series is None or series_key(ev) != own_series)
        and distance_km(ev["latitude"], ev["longitude"], origin) <= 15
    ]
    candidates.sort(key=lambda ev: (not ev.get("image_url"), distance_km(ev["latitude"], ev["longitude"], origin)))
    return collapse_series(candidates)[:4]


NO_DESCRIPTION = ('<div class="panel soft"><h2>À propos</h2><p class="description">L\'organisateur n\'a pas '
                  'encore donné plus de détails. Toutes les infos pratiques sont juste à côté 👉</p></div>')


def event_page(event: dict, neighbours: list[dict], series: dict[str, list[dict]],
               town_page: str | None = None) -> str:
    root = "../../"
    emoji, label, color = category(event)
    url = f"{BASE_URL}/{event_path(event)}"
    when = when_label(event)
    place = place_label(event)
    town = commune_of(event)
    summary = f"{when} · {town or event.get('location_name') or ''}".strip(" ·")
    description = event.get("description") or ""
    lat, lon = event["latitude"], event["longitude"]
    maps = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
    image = image_for(event, root)

    actions = [f'<a class="btn btn-sun" href="event.ics" download="wavents-{event["id"][:8]}.ics">📅 Ajouter à mon agenda</a>',
               f'<a class="btn btn-ghost" href="{maps}" target="_blank" rel="noopener">🧭 Y aller</a>',
               '<button class="btn btn-ghost" data-share>🔗 Partager</button>']
    if (event.get("website_url") or "").startswith("http"):
        actions.append(f'<a class="btn btn-ghost" href="{e(event["website_url"])}" rel="nofollow noopener" target="_blank">🌐 Site de l\'organisateur</a>')

    other_dates = [ev for ev in series.get(series_key(event) or "", [])
                   if ev["id"] != event["id"] and end_of(ev) >= datetime.now()][:10]
    dates_html = ""
    if other_dates:
        dates_html = ('<div class="panel"><h2>Autres dates</h2><div class="date-list">'
                      + "".join(f'<a href="{root}{event_path(ev)}">{date_chip(ev)}</a>' for ev in other_dates)
                      + "</div></div>")
    badges = []
    if is_free(event):
        badges.append('<span class="badge free">💚 Gratuit</span>')
    if event.get("for_kids"):
        badges.append('<span class="badge kids">🧸 Pour les enfants</span>')
    recurring = series_label(series.get(series_key(event) or "", []))
    if recurring:
        badges.append(f'<span class="badge series">🔁 {e(recurring)}</span>')
    near_html = ""
    if neighbours:
        near_html = (f'<section class="section"><div class="section-head"><div><p class="kicker">Le même jour, pas loin</p>'
                     f'<h2>Aussi ce jour-là</h2></div></div>'
                     f'<div class="rows">{"".join(row_card(ev, series, root) for ev in neighbours)}</div></section>')
    body = f"""  <section class="ev-hero" style="--cat:{color}">
    <div class="ev-hero-inner">
      <a class="back" href="{root}#agenda">← Tout l'agenda</a>
      <div class="ev-head">
        <div class="ev-cover-wrap">{date_chip(event)}<div class="ev-cover">{media(event, root)}</div></div>
        <div class="ev-title">
          <p class="cat-pill big">{emoji} {e(label)}</p>
          <h1>{e(event['title'])}</h1>
          <p class="ev-when">🗓️ {e(when)}</p>
          <p class="ev-where">📍 {e(town or place)}{f' · <a class="town-more" href="{root}{town_page}">Toutes les sorties à {e(commune_page_name(event) or town)} →</a>' if town_page else ''}</p>
          <div class="badges">{''.join(badges)}</div>
        </div>
      </div>
    </div>
  </section>
  <section class="section ev-body">
    <div class="ev-main">
      <div class="actions">{''.join(actions)}</div>
      {f'<div class="panel"><h2>À propos</h2><div class="description">{e(description).replace(chr(10), "<br>")}</div></div>' if description else NO_DESCRIPTION}
      {dates_html}
    </div>
    <aside class="ev-side">
      <div class="panel facts">
        <div><span>📅</span><p><b>Quand</b>{e(when)}</p></div>
        <div><span>📍</span><p><b>Où</b>{e(place)}</p></div>
        <div><span>💶</span><p><b>Prix</b>{e(price_label(event))}</p></div>
      </div>
      <div class="panel mini-map-panel"><div id="mini-map" data-lat="{lat}" data-lon="{lon}" data-color="{color}" data-icon="{re.sub(r"<[^>]+>", "", emoji)}"></div></div>
      <div class="panel app-promo"><img src="{root}img/logo-256.png" alt="" width="56" height="56"><p><b>Ne rate plus rien près de chez toi</b>Favoris, rappels et notifs avec l'appli Wavents.</p>{play_cta('small')}</div>
    </aside>
  </section>
  {near_html}"""
    return layout(
        f"{event['title']} – Wavents",
        body,
        description=summary,
        url=url,
        image=absolute_image(event),
        extra_head=f'<script type="application/ld+json">{json_ld(event)}</script>',
        depth=2,
        page_class="event-page",
    )


# --- Pages par commune (référencement, 01/10/2026) -----------------------------
#
# « Que faire à <commune> » : une page par commune qui a au moins
# COMMUNE_MIN_EVENTS sorties à venir, régénérée chaque jour comme le reste.
# Ce sont les recherches réelles des gens (Google comme assistants IA) ;
# une page par événement ne ressort que si on cherche l'événement lui-même.

COMMUNE_MIN_EVENTS = 3
COMMUNE_NEAR_KM = 15


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(text)).strip("-") or "commune"


def commune_path(name: str) -> str:
    return f"ville/{slugify(name)}/"


NOT_A_COMMUNE_START = {"rue", "place", "montee", "avenue", "av", "chemin", "boulevard", "bd", "allee",
                       "impasse", "quai", "route", "residence", "hotel", "mediatheque", "theatre", "salle",
                       "espace", "parc", "square", "cours", "parking", "centre", "eglise", "chateau"}


def commune_page_name(event: dict) -> str | None:
    """Nom de commune propre pour les pages « Que faire à » : arrondissements
    de Lyon regroupés, restes d'adresse (« 162 rue … », « Place … »)
    écartés, doublons du type « Belleville Belleville-en-Beaujolais »."""
    name = commune_of(event)
    m = re.search(r"\d{5}\s+(.+)$", name)
    if m:
        name = m.group(1)
    name = re.sub(r"\s+\d+(?:er|e|ème)?\s+arrondissement$", "", name, flags=re.I).strip()
    m = re.match(r"^([^\s-]+)[\s-]+(\1\b.*)$", name, flags=re.I)
    if m:
        name = m.group(2)
    if not name or len(name) < 2 or re.search(r"\d", name):
        return None
    if fold(name.split(" ")[0].split("-")[0]) in NOT_A_COMMUNE_START:
        return None
    return name


def group_by_commune(upcoming: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for ev in upcoming:
        name = commune_page_name(ev)
        if name:
            groups.setdefault(name, []).append(ev)
    return {name: evs for name, evs in groups.items() if len(evs) >= COMMUNE_MIN_EVENTS}


def commune_center(events: list[dict]) -> tuple[float, float]:
    return (sum(ev["latitude"] for ev in events) / len(events),
            sum(ev["longitude"] for ev in events) / len(events))


def commune_page(name: str, events: list[dict], communes: dict[str, list[dict]],
                 series: dict[str, list[dict]], now: datetime) -> str:
    root = "../../"
    today = now.date()
    first, last = weekend_range(today)
    week_end = today + timedelta(days=7)
    ordered = sorted(collapse_series(events), key=lambda ev: wall_time(ev["start_date"]))
    long_ones = [ev for ev in ordered if is_long(ev)]
    short = [ev for ev in ordered if not is_long(ev)]
    today_list = [ev for ev in short if wall_time(ev["start_date"]).date() == today]
    weekend_list = [ev for ev in short if overlaps(ev, first, last) and ev not in today_list]
    week_list = [ev for ev in short if today < wall_time(ev["start_date"]).date() <= week_end
                 and ev not in weekend_list]
    later = [ev for ev in short if wall_time(ev["start_date"]).date() > week_end][:30]

    def block(kicker: str, title: str, items: list[dict]) -> str:
        if not items:
            return ""
        return (f'<section class="section"><div class="section-head"><div><p class="kicker">{e(kicker)}</p>'
                f'<h2>{e(title)}</h2></div></div>'
                f'<div class="rows">{"".join(row_card(ev, series, root) for ev in items)}</div></section>')

    center = commune_center(events)
    near = sorted(
        ((other, distance_km(*commune_center(evs), center)) for other, evs in communes.items() if other != name),
        key=lambda item: item[1],
    )
    near = [(other, km) for other, km in near if km <= COMMUNE_NEAR_KM][:12]
    near_html = ""
    if near:
        links = "".join(
            f'<a class="town-link" href="{root}{commune_path(other)}">{e(other)} <span>{len(communes[other])}</span></a>'
            for other, _ in near)
        near_html = (f'<section class="section"><div class="section-head"><div><p class="kicker">À moins de '
                     f'{COMMUNE_NEAR_KM} km</p><h2>Autour de {e(name)}</h2></div></div>'
                     f'<div class="town-links">{links}</div></section>')

    free_count = sum(1 for ev in events if is_free(ev))
    kids_count = sum(1 for ev in events if ev.get("for_kids"))
    facts = [f"{len(events)} sorties à venir"]
    if free_count:
        facts.append(f"{free_count} gratuites")
    if kids_count:
        facts.append(f"{kids_count} pour les enfants")
    intro = (f"Concerts, marchés, spectacles, expositions, fêtes… Toutes les sorties à {name} "
             f"et autour, mises à jour chaque jour : {', '.join(facts)}.")
    url = f"{BASE_URL}/{commune_path(name)}"
    item_list = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Wavents", "item": f"{BASE_URL}/"},
                {"@type": "ListItem", "position": 2, "name": "Communes", "item": f"{BASE_URL}/ville/"},
                {"@type": "ListItem", "position": 3, "name": name, "item": url},
            ]},
            {"@type": "ItemList", "name": f"Que faire à {name}", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": f"{BASE_URL}/{event_path(ev)}"}
                for i, ev in enumerate(ordered[:30])
            ]},
        ],
    }
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}ville/">← Toutes les communes</a>
    <p class="kicker">Agenda des sorties</p>
    <h1>Que faire à {e(name)} ?</h1>
    <p class="lead">{e(intro)}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  {block("Aujourd'hui", f"Aujourd'hui à {name}", today_list)}
  {block("Du vendredi au dimanche", f"Ce week-end à {name}", weekend_list)}
  {block("Les 7 prochains jours", "Cette semaine", week_list)}
  {block("Expositions, animations sur plusieurs jours", "En ce moment", long_ones[:20])}
  {block("Et ensuite", "Plus tard", later)}
  {near_html}"""
    return layout(
        f"Que faire à {name} ? Sorties, marchés, concerts – Wavents",
        body,
        description=intro,
        url=url,
        extra_head=f'<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>',
        depth=2,
        page_class="town-page",
    )


def communes_index(communes: dict[str, list[dict]]) -> str:
    root = "../"
    links = "".join(
        f'<a class="town-link" href="{root}{commune_path(name)}">{e(name)} <span>{len(evs)}</span></a>'
        for name, evs in sorted(communes.items(), key=lambda item: fold(item[0])))
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">{len(communes)} communes</p>
    <h1>Que faire près de chez toi ?</h1>
    <p class="lead">Choisis ta commune : toutes les sorties à venir, mises à jour chaque jour.</p>
  </div></section>
  <section class="section"><div class="town-links">{links}</div></section>"""
    return layout(
        "Que faire près de chez toi ? Sorties par commune – Wavents",
        body,
        description="Concerts, marchés, fêtes, spectacles : les sorties à venir commune par commune, "
                    "dans le Beaujolais et le nord de Lyon.",
        url=f"{BASE_URL}/ville/",
        depth=1,
        page_class="town-page",
    )


# --- Identité, pages thématiques, À propos, FAQ (02/10/2026) -----------------------
# Référencement (docs/referencement.md du dépôt Wavents) : une fiche
# d'identité schema.org pour que Google et les assistants IA sachent ce
# qu'est Wavents (Google Business n'accepte pas les services 100 % en
# ligne), des pages qui répondent aux recherches réelles (« que faire ce
# week-end dans le Beaujolais », « marchés », « brocantes »…), régénérées
# chaque jour, et une FAQ.

THEME_RADIUS_KM = 40

ORGANIZATION = {
    "@context": "https://schema.org",
    "@type": "Organization",
    "name": "Wavents",
    "url": f"{BASE_URL}/",
    "logo": f"{BASE_URL}/img/logo-256.png",
    "email": "hello@wavents.fr",
    "description": (
        "Wavents rassemble chaque jour les événements locaux du Beaujolais, du Val de Saône et "
        "du nord de Lyon : marchés, concerts, fêtes de village, spectacles, brocantes, sorties "
        "en famille. Agenda gratuit sur le site et dans l'appli Android."
    ),
    "areaServed": ["Beaujolais", "Val de Saône", "Villefranche-sur-Saône", "Mâcon", "Lyon"],
    "foundingLocation": "Belleville-en-Beaujolais",
    "sameAs": [PLAY_STORE_URL],
}

WEBSITE = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "Wavents",
    "url": f"{BASE_URL}/",
    "inLanguage": "fr-FR",
}

THEMES = [
    # slug, titre de page, h1, accroche, filtre, horizon (jours)
    ("ce-week-end", "Que faire ce week-end dans le Beaujolais ?", "Que faire ce week-end ?",
     "Ce week-end", None, None),
    ("marches", "Marchés du Beaujolais et du Val de Saône", "Les marchés près de chez toi",
     "Marchés", lambda ev: ev.get("category_id") == "marche", 14),
    ("brocantes", "Brocantes et vide-greniers dans le Beaujolais", "Brocantes et vide-greniers",
     "Brocantes", lambda ev: ev.get("category_id") == "brocante", 45),
    ("gratuit", "Sorties gratuites dans le Beaujolais", "Sorties gratuites",
     "Gratuit", is_free, 14),
    ("enfants", "Sorties avec les enfants dans le Beaujolais", "Sorties avec les enfants",
     "En famille", lambda ev: bool(ev.get("for_kids")), 21),
]


def theme_page(slug: str, page_title: str, h1: str, kicker: str, keep, days: int | None,
               upcoming: list[dict], series: dict[str, list[dict]], now: datetime) -> tuple[str, int]:
    root = "../"
    today = now.date()
    pool = [ev for ev in upcoming if distance_km(ev["latitude"], ev["longitude"]) <= THEME_RADIUS_KM]
    if slug == "ce-week-end":
        first, last = weekend_range(today)
        items = [ev for ev in pool if overlaps(ev, first, last)]
        span = f"du {day_label(first)} au {day_label(last)}"
        floor = max(first, today)
    else:
        horizon = today + timedelta(days=days)
        items = [ev for ev in pool if keep(ev) and wall_time(ev["start_date"]).date() <= horizon]
        span = f"dans les {days} prochains jours"
        floor = today
    ordered = sorted(collapse_series(items), key=lambda ev: (is_long(ev), wall_time(ev["start_date"])))
    by_day: dict[date, list[dict]] = {}
    long_ones = []
    for ev in ordered:
        if is_long(ev):
            long_ones.append(ev)
        else:
            # Déjà commencé (sur deux jours, par exemple) : rangé au premier
            # jour affiché, pas à sa date de début passée.
            by_day.setdefault(max(wall_time(ev["start_date"]).date(), floor), []).append(ev)

    # Mise en page du 02/10 (Paul : « c'est fouillis, une méga liste de
    # cartes ») : temps forts en tête, raccourcis vers les jours, pastilles
    # de filtre, marchés d'un même jour repliés sur une ligne. Toutes les
    # sorties restent dans la page (Google, IA), seules les vues changent.
    group_markets = slug != "marches"

    def rows(evs: list[dict], limit: int) -> str:
        markets = [ev for ev in evs if ev.get("category_id") == "marche"] if group_markets else []
        if len(markets) < 3:
            return f'<div class="rows">{"".join(row_card(ev, series, root) for ev in evs[:limit])}</div>'
        others_ = [ev for ev in evs if ev.get("category_id") != "marche"][:limit]
        towns = list(dict.fromkeys(commune_of(ev) for ev in markets if commune_of(ev)))
        towns_label = ", ".join(towns[:4]) + ("…" if len(towns) > 4 else "")
        emoji, _, color = CATEGORIES["marche"]
        folded = (f'<details class="market-fold" style="--cat:{color}"><summary>'
                  f'<span class="cat-chip">{emoji} Marché</span><b>{len(markets)} marchés</b>'
                  f'<span class="row-date">{e(towns_label)}</span><span class="fold-more">Voir</span></summary>'
                  f'<div class="rows">{"".join(row_card(ev, series, root) for ev in markets)}</div></details>')
        return f'<div class="rows">{"".join(row_card(ev, series, root) for ev in others_)}</div>{folded}'

    blocks, day_links = [], []
    for d in sorted(by_day):
        label = "Aujourd'hui" if d == today else ("Demain" if d == today + timedelta(days=1) else cap(day_label(d)))
        anchor = f"j{d:%Y%m%d}"
        day_links.append(f'<a class="chip day-link" href="#{anchor}">{e(label)}</a>')
        n = len(by_day[d])
        blocks.append(
            f'<section class="section theme-day" id="{anchor}"><div class="section-head"><div><p class="kicker">{e(label)}</p>'
            f'<h2><span class="t-count">{n}</span> sortie{"s" if n > 1 else ""}</h2></div></div>'
            f'{rows(by_day[d], 40)}</section>')
    if long_ones:
        day_links.append('<a class="chip day-link" href="#jlong">Sur plusieurs jours</a>')
        blocks.append(
            '<section class="section theme-day" id="jlong"><div class="section-head"><div><p class="kicker">Sur plusieurs jours</p>'
            '<h2>En ce moment</h2></div></div>'
            f'{rows(long_ones, 30)}</section>')
    if not blocks:
        blocks.append('<section class="section"><p class="lead">Rien pour l\'instant : reviens demain, '
                      'l\'agenda est mis à jour chaque jour.</p></section>')

    # Temps forts : vraies photos d'abord, hors marchés et expositions longues.
    highlights = []
    if slug != "marches" and len(ordered) > 12:
        seen = set()
        for ev in sorted(ordered, key=lambda ev: (
                ev.get("category_id") == "marche", is_long(ev), not real_image(ev), not ev.get("featured"),
                round(distance_km(ev["latitude"], ev["longitude"]) / 8), ev["start_date"])):
            key = re.sub(r"[^a-z0-9]", "", fold(ev["title"]))[:14]
            if key in seen or ev.get("category_id") == "marche" or is_long(ev):
                continue
            seen.add(key)
            highlights.append(ev)
            if len(highlights) == 6:
                break
    top = (f'<section class="section theme-top"><div class="section-head"><div><p class="kicker">À ne pas manquer</p>'
           f'<h2>Les temps forts</h2></div></div>'
           f'<div class="grid">{"".join(card(ev, series, root) for ev in highlights)}</div></section>'
           if len(highlights) >= 3 else "")

    # Pastilles : catégories présentes (sauf pages d'une seule catégorie),
    # Gratuit et Enfants (sauf sur leur propre page).
    chips = []
    if slug not in ("marches", "brocantes"):
        counts: dict[str, int] = {}
        for ev in ordered:
            counts[ev.get("category_id") or "associatif_autre"] = counts.get(ev.get("category_id") or "associatif_autre", 0) + 1
        present = [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1]) if c in CATEGORIES][:7]
        if len(present) > 1:
            chips.append('<button class="chip is-on" data-tcat="">Tout</button>')
            for c in present:
                emoji, label, color = CATEGORIES[c]
                chips.append(f'<button class="chip" data-tcat="{c}" style="--cat:{color}">{emoji} {e(label)}</button>')
    if slug != "gratuit":
        chips.append('<button class="chip toggle" data-ttoggle="free">Gratuit</button>')
    if slug != "enfants":
        chips.append('<button class="chip toggle" data-ttoggle="kids">Enfants</button>')
    bar = ""
    if chips or len(day_links) > 1:
        if len(day_links) > 8:
            # Pages sur plusieurs semaines (brocantes…) : un raccourci par semaine.
            day_links = []
            for d in sorted(by_day):
                monday = d - timedelta(days=d.weekday())
                if any(f'data-week="{monday:%Y%m%d}"' in l for l in day_links):
                    continue
                wlabel = ("Cette semaine" if monday <= today else
                          "Semaine prochaine" if monday <= today + timedelta(days=7) else
                          f"Sem. du {monday.day} {MONTHS_SHORT[monday.month - 1]}")
                day_links.append(f'<a class="chip day-link" data-week="{monday:%Y%m%d}" href="#j{d:%Y%m%d}">{e(wlabel)}</a>')
            if long_ones:
                day_links.append('<a class="chip day-link" href="#jlong">Sur plusieurs jours</a>')
        days_row = f'<div class="chips day-links">{"".join(day_links)}</div>' if len(day_links) > 1 else ""
        bar = (f'<div class="filters theme-filters">{days_row}<div class="chips">{"".join(chips)}</div>'
               f'<p class="t-empty" hidden>Aucune sortie avec ces filtres.</p></div>')
    others = "".join(
        f'<a class="town-link" href="{root}{t[0]}/">{e(t[2])}</a>' for t in THEMES if t[0] != slug)
    intro = (f"{len(ordered)} sorties {span} dans le Beaujolais, le Val de Saône et le nord de Lyon "
             f"(à moins de {THEME_RADIUS_KM} km de Belleville), mises à jour chaque jour.")
    url = f"{BASE_URL}/{slug}/"
    item_list = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": page_title,
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "url": f"{BASE_URL}/{event_path(ev)}"}
            for i, ev in enumerate(ordered[:50])
        ],
    }
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}">← Accueil</a>
    <p class="kicker">{e(kicker)}</p>
    <h1>{e(h1)}</h1>
    <p class="lead">{e(intro)}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  {top}
  <div class="theme-list">{bar}{"".join(blocks)}</div>
  <section class="section"><div class="section-head"><div><p class="kicker">Et aussi</p><h2>D'autres idées</h2></div></div>
    <div class="town-links">{others}<a class="town-link" href="{root}ville/">Par commune</a></div></section>"""
    return layout(
        f"{page_title} – Wavents",
        body,
        description=intro,
        url=url,
        extra_head=f'<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>',
        depth=1,
        page_class="town-page",
    ), len(ordered)


def about_page(upcoming: list[dict], communes: dict[str, list[dict]]) -> str:
    root = "../"
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">À propos</p>
    <h1>Wavents, l'agenda des sorties près de chez toi</h1>
    <p class="lead">Toutes les sorties locales au même endroit, sans compte et gratuitement.</p>
  </div></section>
  <section class="section prose">
    <h2>Qui sommes-nous ?</h2>
    <p>Wavents est né à Belleville-en-Beaujolais, d'une envie simple : ne plus rater la fête de
    quartier, le concert du village voisin ou la brocante du dimanche, juste parce qu'on n'était pas au courant.</p>
    <h2>Quelle zone ?</h2>
    <p>Le Beaujolais, le Val de Saône (côté Rhône et côté Ain), Villefranche-sur-Saône, Mâcon et le
    nord de Lyon. En ce moment : <strong>{len(upcoming)} sorties à venir</strong> dans
    <strong>{len(communes)} communes</strong>. La zone s'agrandit petit à petit.</p>
    <h2>D'où viennent les événements ?</h2>
    <p>Des agendas publics : mairies, offices de tourisme, médiathèques, salles de spectacle,
    associations, billetteries et presse locale. Ils sont relus chaque jour : doublons
    regroupés, dates, prix et catégories vérifiés, événements annulés retirés. Les organisateurs
    peuvent aussi ajouter leurs événements gratuitement depuis l'appli.</p>
    <h2>Combien ça coûte ?</h2>
    <p>Rien : le site et l'appli sont gratuits, sans compte. Wavents ne vend pas de billets ;
    chaque fiche renvoie vers l'organisateur.</p>
    <h2>Contact</h2>
    <p>Une erreur, un événement oublié, une idée ? Écris à <a href="mailto:hello@wavents.fr">hello@wavents.fr</a>
    ou signale-le depuis la fiche dans l'appli.</p>
    <div class="hero-actions">{play_cta()}</div>
  </section>"""
    return layout(
        "À propos de Wavents – l'agenda des sorties du Beaujolais",
        body,
        description="Wavents rassemble chaque jour les événements locaux du Beaujolais, du Val de Saône et du "
                    "nord de Lyon, depuis les agendas publics et les organisateurs. Gratuit, sans compte.",
        url=f"{BASE_URL}/a-propos/",
        extra_head=f'<script type="application/ld+json">{json.dumps(ORGANIZATION, ensure_ascii=False)}</script>',
        depth=1,
        page_class="town-page",
    )


def faq_page(upcoming: list[dict], communes: dict[str, list[dict]], theme_counts: dict[str, int]) -> str:
    root = "../"
    qa = [
        ("Que faire ce week-end dans le Beaujolais ?",
         f"Wavents liste {theme_counts.get('ce-week-end', 0)} sorties ce week-end à moins de "
         f"{THEME_RADIUS_KM} km de Belleville-en-Beaujolais : marchés, concerts, fêtes de village, "
         f"spectacles, brocantes. La liste complète, mise à jour chaque jour, est sur "
         f"{BASE_URL}/ce-week-end/."),
        ("Où trouver les marchés et brocantes près de chez moi ?",
         f"Les marchés sont sur {BASE_URL}/marches/ et les brocantes et vide-greniers sur "
         f"{BASE_URL}/brocantes/, avec la date, le lieu et les horaires. Dans l'appli, la carte les "
         "montre autour de toi."),
        ("Y a-t-il des sorties gratuites ou pour les enfants ?",
         f"Oui : {theme_counts.get('gratuit', 0)} sorties gratuites dans les 14 prochains jours "
         f"({BASE_URL}/gratuit/) et {theme_counts.get('enfants', 0)} sorties pensées pour les enfants "
         f"ou en famille ({BASE_URL}/enfants/)."),
        ("Quelle zone couvre Wavents ?",
         f"Le Beaujolais, le Val de Saône, Villefranche-sur-Saône, Mâcon et le nord de Lyon : "
         f"{len(communes)} communes ont des sorties à venir ({BASE_URL}/ville/)."),
        ("D'où viennent les événements ? Sont-ils fiables ?",
         "Wavents rassemble chaque jour les sorties annoncées publiquement dans la région, et celles que "
         "les habitants et les organisateurs ajoutent eux-mêmes. Chaque événement est vérifié avant "
         "publication, et chaque fiche renvoie vers l'organisateur. Une erreur ? Signale-la depuis la "
         "fiche dans l'appli, elle est corrigée rapidement."),
        ("Je suis organisateur, une association ou une mairie : comment ajouter mes événements ?",
         "Gratuitement, depuis l'appli Wavents : bouton « + », puis une photo de l'affiche suffit, la "
         "fiche se remplit toute seule. L'événement est publié après vérification, en général dans la "
         "journée."),
        ("Wavents est-il gratuit ?",
         "Oui, le site et l'appli sont gratuits et sans compte. Wavents ne vend pas de billets : "
         "chaque fiche renvoie vers l'organisateur."),
        ("L'appli est-elle disponible sur iPhone et Android ?",
         "L'appli Wavents arrive bientôt sur l'App Store et le Play Store. En attendant, toutes les "
         "sorties sont consultables sur ce site, depuis n'importe quel téléphone."),
        ("Comment être prévenu des nouvelles sorties ?",
         "Dans l'appli, fais une recherche (par exemple « loto » autour de ta ville) et enregistre-la : "
         "tu reçois chaque soir une notification quand de nouveaux événements correspondent. L'appli "
         "prévient aussi des nouveautés près de chez toi."),
    ]
    schema = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa
        ],
    }
    def linkify(text: str) -> str:
        return re.sub(r"(https://[^\s)]+?)(?=[.,]?(?:\s|\)|$))",
                      lambda m: f'<a href="{m.group(1)}">{m.group(1).replace(BASE_URL, "wavents.fr")}</a>', e(text))
    items = "".join(f"<h2>{e(q)}</h2><p>{linkify(a)}</p>" for q, a in qa)
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">Questions fréquentes</p>
    <h1>Tout savoir sur Wavents</h1>
  </div></section>
  <section class="section prose">{items}
    <div class="hero-actions">{play_cta()}</div>
  </section>"""
    return layout(
        "Questions fréquentes – Wavents, sorties dans le Beaujolais",
        body,
        description="Que faire ce week-end dans le Beaujolais, où trouver les marchés et brocantes, sorties "
                    "gratuites et en famille, comment ajouter un événement : les réponses.",
        url=f"{BASE_URL}/faq/",
        extra_head=f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>',
        depth=1,
        page_class="town-page",
    )


# --- Main --------------------------------------------------------------------------

def llms_txt(upcoming: list[dict], now: datetime, communes: dict[str, list[dict]] | None = None) -> str:
    """Présentation du site pour les assistants IA (convention llms.txt) :
    ce qu'est Wavents, puis les sorties des 7 prochains jours avec leur lien."""
    # Sorties qui COMMENCENT dans la semaine, autour de Belleville (même
    # rayon que l'accueil) : pas les marchés ou expositions à l'année.
    soon = sorted(
        (ev for ev in upcoming
         if now.date() <= wall_time(ev["start_date"]).date() <= (now + timedelta(days=7)).date()
         and distance_km(ev["latitude"], ev["longitude"]) <= HOME_RADIUS_KM),
        key=lambda ev: ev["start_date"],
    )
    lines = [
        "# Wavents",
        "",
        "> Agenda gratuit des sorties locales du Beaujolais et du nord de Lyon :",
        "> concerts, fêtes, marchés, spectacles, sport, visites, ateliers, brocantes.",
        "> Les événements sont relevés chaque jour sur les sites des mairies,",
        "> offices de tourisme et associations, puis vérifiés avant publication.",
        "",
        f"Site : {BASE_URL}/ (mis à jour chaque jour). Une page par événement,",
        f"avec date, lieu, prix et données schema.org Event. Liste complète : {BASE_URL}/sitemap.xml.",
        "Appli Android Wavents (carte, favoris, rappels, alertes) : bientôt sur Google Play.",
        f"Contact : hello@wavents.fr. Confidentialité : {BASE_URL}/confidentialite.html.",
        f"À propos : {BASE_URL}/a-propos/. Questions fréquentes : {BASE_URL}/faq/.",
        "",
        "## Pages thématiques (mises à jour chaque jour)",
        "",
        f"- [Que faire ce week-end dans le Beaujolais]({BASE_URL}/ce-week-end/)",
        f"- [Marchés du Beaujolais et du Val de Saône]({BASE_URL}/marches/)",
        f"- [Brocantes et vide-greniers]({BASE_URL}/brocantes/)",
        f"- [Sorties gratuites]({BASE_URL}/gratuit/)",
        f"- [Sorties avec les enfants]({BASE_URL}/enfants/)",
        "",
        f"## Sorties des 7 prochains jours (à moins de {HOME_RADIUS_KM} km de Belleville-en-Beaujolais)",
        "",
    ]
    for ev in soon:
        category = CATEGORIES.get(ev.get("category_id") or "", ("", "Sortie", ""))[1]
        # Certains scrapers laissent des entités HTML (« l&#8217;Evidence »).
        place = html.unescape(ev.get("location_name") or ev.get("address") or "")
        title = html.unescape(ev["title"]).replace("[", "(").replace("]", ")")
        lines.append(f"- [{title}]({BASE_URL}/{event_path(ev)}): {when_label(ev)} · {place} · {category}")
    if not soon:
        lines.append("- Aucune sortie publiée pour l'instant.")
    if communes:
        lines += ["", "## Que faire dans chaque commune (pages mises à jour chaque jour)", ""]
        for name, evs in sorted(communes.items(), key=lambda item: -len(item[1]))[:150]:
            lines.append(f"- [Que faire à {name}]({BASE_URL}/{commune_path(name)}): {len(evs)} sorties à venir")
    return "\n".join(lines) + "\n"


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "site")
    if out.exists():
        shutil.rmtree(out)
    (out / "e").mkdir(parents=True)
    static = Path(__file__).parent / "static"
    shutil.copytree(static, out, dirs_exist_ok=True)

    now = datetime.now(PARIS).replace(tzinfo=None)
    events = [
        ev for ev in fetch_events()
        if ev.get("start_date") and ev.get("latitude") is not None
        and end_of(ev) >= now - timedelta(days=1)
    ]
    upcoming = [ev for ev in events if end_of(ev).date() >= now.date()]
    series = build_series(events)
    by_day: dict[date, list[dict]] = {}
    for ev in events:
        by_day.setdefault(wall_time(ev["start_date"]).date(), []).append(ev)

    communes = group_by_commune(upcoming)
    for ev in events:
        folder = out / event_path(ev)
        folder.mkdir(parents=True, exist_ok=True)
        town = commune_page_name(ev)
        town_page = commune_path(town) if town in communes else None
        (folder / "index.html").write_text(event_page(ev, neighbours_of(ev, by_day), series, town_page), encoding="utf-8")
        (folder / "event.ics").write_text(ics(ev), encoding="utf-8")
    (out / "index.html").write_text(home_page(events, upcoming, now), encoding="utf-8")

    for name, evs in communes.items():
        folder = out / commune_path(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(commune_page(name, evs, communes, series, now), encoding="utf-8")
    (out / "ville").mkdir(exist_ok=True)
    (out / "ville" / "index.html").write_text(communes_index(communes), encoding="utf-8")

    theme_counts: dict[str, int] = {}
    for slug, page_title, h1, kicker, keep, days in THEMES:
        page, count = theme_page(slug, page_title, h1, kicker, keep, days, upcoming, series, now)
        theme_counts[slug] = count
        (out / slug).mkdir(exist_ok=True)
        (out / slug / "index.html").write_text(page, encoding="utf-8")
    (out / "a-propos").mkdir(exist_ok=True)
    (out / "a-propos" / "index.html").write_text(about_page(upcoming, communes), encoding="utf-8")
    (out / "faq").mkdir(exist_ok=True)
    (out / "faq" / "index.html").write_text(faq_page(upcoming, communes, theme_counts), encoding="utf-8")
    extra_pages = [f"{BASE_URL}/{t[0]}/" for t in THEMES] + [f"{BASE_URL}/a-propos/", f"{BASE_URL}/faq/"]

    urls = ([f"{BASE_URL}/", f"{BASE_URL}/ville/"] + extra_pages
            + [f"{BASE_URL}/{commune_path(name)}" for name in communes]
            + [f"{BASE_URL}/{event_path(ev)}" for ev in events])
    sitemap = "\n".join(f"  <url><loc>{html.escape(quote(u, safe=':/'))}</loc></url>" for u in urls)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}\n</urlset>\n',
        encoding="utf-8",
    )
    (out / "llms.txt").write_text(llms_txt(upcoming, now, communes), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    # IndexNow (Bing, et par lui ChatGPT/Copilot) : liste des pages à
    # signaler après la publication (étape du workflow pages.yml). Les pages
    # communes changent chaque jour ; les événements, les 2 000 plus proches.
    soonest = sorted(upcoming, key=lambda ev: ev["start_date"])[:2000]
    (out / "indexnow.json").write_text(json.dumps(
        [f"{BASE_URL}/", f"{BASE_URL}/ville/"] + extra_pages
        + [f"{BASE_URL}/{commune_path(name)}" for name in communes]
        + [f"{BASE_URL}/{event_path(ev)}" for ev in soonest]), encoding="utf-8")
    print(f"{len(events)} pages d'événements et {len(communes)} pages communes générées dans {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
