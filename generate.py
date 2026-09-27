"""Génère le site public de Wavents : une page d'accueil (vitrine de l'appli,
sélection du week-end, carte, agenda filtrable des prochains jours autour de
Belleville), une page par événement à venir, le sitemap.

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
    "spectacle": (_icon("theater_comedy"), "Spectacle", "#8E24AA"),
    "sport": (_icon("sports_soccer"), "Sport", "#43A047"),
    "culture": (_icon("museum"), "Culture", "#7C4DFF"),
    "atelier": (_icon("brush"), "Atelier", "#8D6E63"),
    "visite": (_icon("explore"), "Visite", "#1E88E5"),
    "associatif_autre": (_icon("groups"), "Associatif", "#00BFA5"),
}
CATEGORY_PHOTO = {
    "concert": "concert", "fete": "fete", "marche": "marche", "spectacle": "spectacle",
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
                f'<span class="store-icon">▶</span><span><small>Disponible sur</small>Google Play</span></a>')
    return (f'<span class="store-badge soon {extra_class}"><span class="store-icon">▶</span>'
            f'<span><small>Bientôt sur</small>Google Play</span></span>')


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
  <link rel="icon" type="image/png" href="{root}img/favicon.png">
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
    <a href="{root}" class="nav-brand"><img src="{root}img/favicon.png" alt="" width="34" height="34"><span>Wavents</span></a>
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
        <a href="{root}confidentialite.html">Confidentialité</a>
      </div>
    </div>
    <p class="footer-note">Informations issues des agendas publics et des organisateurs, mises à jour chaque jour. Vérifie auprès de l'organisateur avant de te déplacer.</p>
  </footer>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js" defer></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js" defer></script>
  <script src="{root}app.js" defer></script>
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
            "u": event_path(ev),
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
        <p class="lead">Concerts, marchés, fêtes de village, spectacles, lotos, brocantes… Wavents fouille chaque nuit des dizaines d'agendas du Beaujolais et du nord de Lyon pour que tu ne rates plus rien.</p>
        <div class="hero-actions">
          <a class="btn btn-sun" href="#week-end">Voir ce week-end</a>
          {play_cta()}
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
    <div class="stat reveal" style="--c:#01A9D8"><b data-count="{len(sources)}">{len(sources)}</b><span>agendas suivis chaque nuit</span></div>
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
    <div class="map-wrap reveal"><div id="map" role="region" aria-label="Carte des événements"></div>
    <div class="map-legend">{''.join(f'<span style="--cat:{c}">{em} {l}</span>' for cid, (em, l, c) in CATEGORIES.items() if counts.get(cid))}</div></div>
  </section>
  </div>

  <section class="section" id="agenda">
    <div class="section-head reveal"><div><p class="kicker">Mis à jour chaque jour</p><h2>L'agenda des {HOME_DAYS} prochains jours</h2></div></div>
    <div class="filters" id="filters">
      <label class="search"><span>🔍</span><input type="search" id="q" placeholder="Un village, un concert, un loto…" autocomplete="off"></label>
      <div class="chips">
        <button class="chip is-on" data-cat="">Tout</button>
        {chips}
        <button class="chip toggle" data-toggle="free">💚 Gratuit</button>
        <button class="chip toggle" data-toggle="kids">🧸 Enfants</button>
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
      <div class="how-card reveal" style="--c:#01BAEF"><span class="how-num">🔎</span><h3>On fouille pour toi</h3><p>Chaque nuit, Wavents lit les agendas des mairies, offices de tourisme, salles de spectacle, associations et billetteries de la région.</p></div>
      <div class="how-card reveal" style="--c:#FF8A1E"><span class="how-num">🪄</span><h3>On trie et on vérifie</h3><p>Doublons fusionnés, catégories, prix, « pour les enfants » : tout est rangé pour que tu trouves en deux secondes.</p></div>
      <div class="how-card reveal" style="--c:#FFD66B"><span class="how-num">🔔</span><h3>Tu ne rates plus rien</h3><p>Favoris, rappels avant l'événement et notifications quand ça bouge près de chez toi, dans les catégories que tu aimes.</p></div>
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
        "Wavents – Que faire autour de Belleville-en-Beaujolais ?",
        body,
        description=f"Concerts, marchés, fêtes, spectacles et sorties des {HOME_DAYS} prochains jours autour de Belleville-en-Beaujolais, dans le Beaujolais et le nord de Lyon.",
        url=f"{BASE_URL}/",
        image=f"{BASE_URL}/img/og.png",
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


def event_page(event: dict, neighbours: list[dict], series: dict[str, list[dict]]) -> str:
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
          <p class="ev-where">📍 {e(town or place)}</p>
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


# --- Main --------------------------------------------------------------------------

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

    for ev in events:
        folder = out / event_path(ev)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(event_page(ev, neighbours_of(ev, by_day), series), encoding="utf-8")
        (folder / "event.ics").write_text(ics(ev), encoding="utf-8")
    (out / "index.html").write_text(home_page(events, upcoming, now), encoding="utf-8")

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
