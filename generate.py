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
PLAY_STORE_PUBLISHED = True  # en production depuis le 21/10/2026 (Paul, 22/10 : « c'est officiel »)
# Adresse de la fiche App Store (https://apps.apple.com/fr/app/wavents/id…)
# à renseigner le jour où Apple publie l'appli.
APP_STORE_URL: str | None = None

PARIS = ZoneInfo("Europe/Paris")
HOME = (46.1075, 4.7536)  # centre de Belleville-en-Beaujolais, comme l'appli
HOME_RADIUS_KM = 30
HOME_DAYS = 21
AGENDA_DAY_PREVIEW = 12  # cartes affichées par jour avant « Voir les autres sorties » (static/app.js)

FIELDS = (
    "id,title,description,category_id,location_name,address,latitude,longitude,"
    "start_date,end_date,start_time_known,end_time_known,price_type,price_amount,price_confirmed,"
    "image_url,website_url,for_kids,source,published_at,recurring_weekdays,opening_hours"
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


def fetch_gone_events(days: int = 180) -> list[dict]:
    """Fiches publiées ces derniers mois et plus en ligne (passées, ou
    doublons retirés avec la fiche gardée) : fonction site_gone_events
    (script 198 du dépôt Wavents, 25/10/2026, 404 signalées par la Search
    Console). En cas d'échec, rien : le site part quand même."""
    rows: list[dict] = []
    offset, page = 0, 1000
    try:
        while True:
            request = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/rpc/site_gone_events?limit={page}&offset={offset}",
                data=json.dumps({"p_days": days}).encode(),
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=60) as response:
                batch = json.load(response)
            rows.extend(batch)
            if len(batch) < page:
                return rows
            offset += page
    except Exception as exc:  # noqa: BLE001 — le site ne doit pas tomber pour ça
        print(f"site_gone_events : {exc}", file=sys.stderr)
        return rows


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


def share_card(event: dict) -> str:
    """Image de partage (WhatsApp, Facebook…) : carte photo + titre + date +
    lieu + logo, fabriquée au premier partage par la fonction Supabase
    share-card (dépôt Wavents), puis gardée (19/10/2026)."""
    return f"{SUPABASE_URL}/functions/v1/share-card?id={event['id']}"


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


# --- Pages répétées (Search Console, 22/10/2026) --------------------------------
#
# Un même événement qui revient (marché, séance, atelier, stage : même titre,
# même lieu, autre date) a une page par date, presque identique : Google en
# gardait une à sa guise (« Google n'a pas choisi la même URL canonique »).
# Chaque date pointe désormais vers la page de la prochaine date, seule
# présente dans le sitemap ; les autres pages restent en ligne (liens
# partagés, appli).

def repeat_key(event: dict) -> str:
    # Description comprise : deux événements différents au même titre (deux
    # « Halloween » dans le même bourg) gardent chacun leur page.
    text = fold(event.get("description") or "")[:300]
    return f"{fold(event['title']).strip()}|{event['latitude']:.3f}|{event['longitude']:.3f}|{text}"


def repeat_groups(events: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for ev in events:
        groups.setdefault(repeat_key(ev), []).append(ev)
    for items in groups.values():
        items.sort(key=lambda ev: (ev["start_date"], ev["id"]))
    return groups


def repeat_canonicals(groups: dict[str, list[dict]], now: datetime) -> dict[str, str]:
    canonical: dict[str, str] = {}
    for items in groups.values():
        main = next((ev for ev in items if end_of(ev) >= now), items[0])
        for ev in items:
            canonical[ev["id"]] = main["id"]
    return canonical


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

STREET = re.compile(r"\b(rue|place|avenue|av|chemin|route|allee|boulevard|bd|quai|impasse|montee|cours|square|lieu-dit|hameau)\b")


def postal_address(event: dict) -> dict:
    """Adresse structurée (26/10/2026) : Google demande une PostalAddress
    (commune, code postal, pays) pour les résultats enrichis « Événements »."""
    raw = event.get("address") or ""
    address = {"@type": "PostalAddress", "addressCountry": "FR"}
    town = commune_page_name(event) or commune_of(event)
    if town:
        address["addressLocality"] = town
    postcode = re.search(r"\b(\d{5})\b", raw)
    if postcode:
        address["postalCode"] = postcode.group(1)
    street = next((p.strip() for p in raw.split(",") if STREET.search(fold(p)) and not re.search(r"\b\d{5}\b", p)), "")
    if street:
        address["streetAddress"] = street
    return address


def breadcrumb_ld(event: dict, town_page: str | None) -> str:
    """Fil d'Ariane (Accueil › commune › sortie), lu par Google et les IA."""
    items = [{"@type": "ListItem", "position": 1, "name": "Wavents", "item": f"{BASE_URL}/"}]
    if town_page:
        items.append({"@type": "ListItem", "position": 2, "name": f"Que faire à {commune_page_name(event)}",
                      "item": f"{BASE_URL}/{town_page}"})
    items.append({"@type": "ListItem", "position": len(items) + 1, "name": event["title"],
                  "item": f"{BASE_URL}/{event_path(event)}"})
    data = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": items}
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


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
            "name": event.get("location_name") or commune_of(event) or "",
            "address": postal_address(event),
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
        return (f'<a class="store-badge {extra_class}" href="{PLAY_STORE_URL}" data-goatcounter-click="clic-google-play">'
                f'<span class="store-icon">▶</span><span><small>Disponible sur</small>Google Play</span></a>' + app_store_soon(extra_class))
    return (f'<span class="store-badge soon {extra_class}"><span class="store-icon">▶</span>'
            f'<span><small>Bientôt sur</small>Google Play</span></span>' + app_store_soon(extra_class))


def app_store_soon(extra_class: str = "") -> str:
    """Badge App Store : lien si l'appli est publiée, sinon « Bientôt »."""
    if APP_STORE_URL:
        return (f'<a class="store-badge {extra_class}" href="{APP_STORE_URL}" data-goatcounter-click="clic-app-store"><span class="store-icon apple">{_icon("phone_iphone")}</span>'
                f"<span><small>Disponible sur</small>l'App Store</span></a>")
    return (f'<span class="store-badge soon {extra_class}"><span class="store-icon apple">{_icon("phone_iphone")}</span>'
            f"<span><small>Bientôt sur</small>l'App Store</span></span>")


def qr_card(root: str) -> str:
    """QR code pour installer l'appli, sur ordinateur seulement (Paul,
    22/10/2026) : petite carte en bas à droite, refermable ; jamais sur
    téléphone (CSS, écran large avec souris). Canal « site » dans
    GoatCounter et la Play Console."""
    if not PLAY_STORE_PUBLISHED:
        return ""
    stores = "Android" + (" et iPhone" if APP_STORE_URL else ", bientôt iPhone")
    return (f'<aside class="qr-card" aria-label="Installer l\'appli Wavents">'
            f'<button class="qr-close" type="button" aria-label="Fermer">×</button>'
            f'<img src="{root}img/qr_site.png" width="150" height="150" alt="QR code pour installer l\'appli Wavents">'
            f'<p><strong>L\'appli sur ton téléphone</strong>Scanne pour l\'installer ({stores})</p></aside>')


def layout(title: str, body: str, *, description: str, url: str, image: str | None = None,
           extra_head: str = "", depth: int = 0, page_class: str = "", canonical: str | None = None) -> str:
    root = "../" * depth
    og_image = (f'<meta property="og:image" content="{e(image)}">\n  '
                f'<meta property="og:image:width" content="1200">\n  '
                f'<meta property="og:image:height" content="630">\n  '
                f'<meta name="twitter:card" content="summary_large_image">') if image else ""
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <title>{e(title)}</title>
  <meta name="description" content="{e(description)}">
  <link rel="canonical" href="{e(canonical or url)}">
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
      <a href="{root}ajouter/" class="nav-cta nav-add" data-goatcounter-click="clic-menu-ajouter">+ Ajouter un événement</a>
      <a href="{root}app/" class="nav-cta" data-goatcounter-click="clic-menu-appli">L'appli</a>
    </nav>
  </header>
{body}
  <footer class="footer">
    <div class="footer-inner">
      <div class="footer-brand">
        <img src="{root}img/logo-256.png" alt="" width="64" height="64">
        <div><strong>Wavents</strong><p>Suis la vague&nbsp;: ne rate plus rien près de chez toi.<br>Fait avec ❤️ à Belleville-en-Beaujolais.</p></div>
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
        <a href="{root}enfants/">En famille</a>
        <a href="{root}a-propos/">À propos</a>
        <a href="{root}ajouter/">Ajouter un événement</a>
        <a href="{root}faq/">Questions fréquentes</a>
        <a href="{root}confidentialite.html">Confidentialité</a>
        <a href="{root}mentions-legales.html">Mentions légales</a>
        <a href="{root}cgu.html">Conditions d'utilisation</a>
        <a href="mailto:hello@wavents.fr">Contact</a>
      </div>
    </div>
    <p class="footer-note">Informations issues des agendas publics et des organisateurs, mises à jour chaque jour. Vérifie auprès de l'organisateur avant de te déplacer.</p>
  </footer>
  {qr_card(root)}
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
        badges.append('<span class="badge kids">En famille</span>')
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
        badges.append('<span class="badge kids">En famille</span>')
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
    <div class="ph-chips"><span class="on">Ce week-end</span><span>Gratuit</span><span>En famille</span></div>
    <div class="ph-map"><span class="pin p1"></span><span class="pin p2"></span><span class="pin p3"></span><span class="pin p4"></span><span class="pin p5"></span><span class="me"></span></div>
    {''.join(rows)}
  </div>
</div>"""


HOME_MAP_POINTS: list[dict] = []  # rempli par home_page, écrit dans data/carte.json


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
                     f'<span class="cat-tile-label"><b><span class="cat-emoji">{_icon("child_care")}</span>En famille</b>'
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
    # Points de la carte à part (data/carte.json, écrit par main) : 218 Ko de
    # moins dans la page d'accueil (26/10/2026, vitesse = référencement).
    payload = json.dumps({"cats": categories_js, "home": HOME}, ensure_ascii=False)
    data_script = (f'<script id="wv-data" type="application/json" data-src="data/carte.json?v={today:%Y%m%d}">'
                   f'{payload.replace("</", "<" + chr(92) + "/")}</script>')
    HOME_MAP_POINTS[:] = map_points

    # Intro de la première visite (styles « Intro » de style.css) : retirée
    # avant d'être peinte si elle a déjà été vue, si le stockage local est
    # bloqué ou si l'on préfère moins d'animations.
    intro = """  <div class="intro" id="wv-intro" aria-hidden="true">
    <div class="intro-stage">
      <div class="intro-pin"><span class="ring"></span><span class="ring r2"></span><img src="img/pin-card.png" alt=""></div>
      <div class="intro-words"><span class="i1">Une vague d'événements</span><span class="i2">a Wave of events</span><span class="i3">Wavents</span></div>
      <p class="intro-slogan">Suis la vague&nbsp;: ne rate plus rien près de chez toi</p>
    </div>
  </div>
  <script>(function () {
    var el = document.getElementById("wv-intro");
    try {
      if (localStorage.getItem("wv_intro") || matchMedia("(prefers-reduced-motion: reduce)").matches) { el.remove(); return; }
      localStorage.setItem("wv_intro", "1");
    } catch (err) { el.remove(); return; }
    function out() { el.classList.add("out"); setTimeout(function () { el.remove(); }, 500); }
    el.addEventListener("click", out);
    setTimeout(out, 3400);
  })();</script>
"""
    body = intro + f"""  <section class="hero">
    <div class="hero-bg"><div class="sun"></div></div>
    <div class="hero-inner">
      <div class="hero-copy reveal">
        <p class="eyebrow"><span class="pulse"></span> {len(all_upcoming)} événements à venir en ce moment</p>
        <h1>Suis la vague&nbsp;: <span class="sunny">ne rate plus rien près de chez toi</span></h1>
        <p class="lead">Wavents trouve chaque jour des concerts, marchés, animations, brocantes… dans le Beaujolais et le nord de Lyon (pour l'instant&nbsp;!).</p>
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

{demos_section()}
  <section class="section">
    <div class="section-head reveal"><div><p class="kicker">Envie de quoi ?</p><h2>Par catégorie</h2></div></div>
    <div class="cat-grid reveal">{cat_tiles}</div>
  </section>

  <div class="band band-map">
  <div class="band-sun" aria-hidden="true"></div>
  <section class="section map-section" id="carte">
    <div class="section-head reveal"><div><p class="kicker">{len(shown)} sorties dans les {HOME_DAYS} prochains jours</p><h2>Sur la carte</h2></div></div>
    <div class="map-wrap reveal"><div class="map-box"><div id="map" role="region" aria-label="Carte des événements"></div><div class="map-search" role="search"><span aria-hidden="true">🔍</span><input type="search" id="map-q" placeholder="Aller à une ville…" autocomplete="off" aria-label="Aller à une ville sur la carte"><ul class="map-sugg" hidden></ul></div></div>
    <div class="map-legend" role="group" aria-label="Filtrer la carte"><button class="map-chip is-on" data-map-cat="">Tout</button><button class="map-chip" data-map-kids style="--cat:#E91E63">{_icon("child_care")} En famille</button>{''.join(f'<button class="map-chip" data-map-cat="{cid}" style="--cat:{c}">{em} {l}</button>' for cid, (em, l, c) in CATEGORIES.items() if counts.get(cid))}</div></div>
  </section>
  </div>

  <section class="section" id="agenda">
    <div class="section-head reveal"><div><p class="kicker">Mis à jour chaque jour</p><h2>L'agenda des {HOME_DAYS} prochains jours</h2></div></div>
    <div class="filters" id="filters">
      <label class="search"><span>🔍</span><input type="search" id="q" placeholder="Un village, un concert, un loto…" autocomplete="off"></label>
      <div class="chips">
        <button class="chip is-on" data-cat="">Tout</button>
        <button class="chip toggle" data-toggle="kids">{_icon("child_care")} En famille</button>
        <button class="chip toggle" data-toggle="free">{_icon("money_off")} Gratuit</button>
        {chips}
      </div>
      <p class="result-count" id="count" aria-live="polite"></p>
    </div>
    <div class="day-tabs" role="tablist">{''.join(day_tabs)}</div>
    <div class="agenda">{''.join(day_sections)}</div>
    <p class="empty" id="empty" hidden>Aucune sortie ne correspond. Essaie un autre filtre&nbsp;!</p>
  </section>

{orga_section()}
  <section class="section how">
    <div class="section-head reveal center"><div><p class="kicker">Comment ça marche</p><h2>Ton agenda local, sans effort</h2></div></div>
    <div class="how-grid">
      <div class="how-card reveal" style="--c:#01BAEF"><span class="how-num">🔎</span><h3>On fouille pour toi</h3><p>Wavents trouve les événements des mairies, offices de tourisme, salles de spectacle, associations, billetteries… de la région. L'outil s'étend chaque jour avec de nouvelles zones et de nouvelles sources.</p></div>
      <div class="how-card reveal" style="--c:#FF8A1E"><span class="how-num">🪄</span><h3>On trie et on vérifie</h3><p>Doublons fusionnés, catégories, prix, « en famille » : tout est rangé pour que tu trouves en deux secondes ce qui t'intéresse.</p></div>
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
        <div class="hero-actions"><a class="btn btn-sun" href="mailto:hello@wavents.fr?subject=Mon%20retour%20sur%20Wavents" data-goatcounter-click="clic-retour">✉️ Envoyer un retour</a></div>
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
        "Wavents : agenda des sorties dans le Beaujolais et le nord de Lyon (concerts, marchés, brocantes)",
        body,
        description=f"Que faire ce week-end ou ce soir près de chez toi ? {len(all_upcoming)} sorties à venir dans le Beaujolais, le Val de Saône et le nord de Lyon : concerts, marchés, brocantes, fêtes de village, spectacles. Mis à jour chaque jour, gratuit.",
        url=f"{BASE_URL}/",
        image=f"{BASE_URL}/img/og.png?v=3",
        extra_head=(f'<script type="application/ld+json">{json.dumps(ORGANIZATION, ensure_ascii=False)}</script>'
                    f'<script type="application/ld+json">{json.dumps(WEBSITE, ensure_ascii=False)}</script>'
                    + app_ld()),
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


def gone_page(event: dict, town_page: str | None) -> str:
    """Page d'une fiche plus en ligne. Doublon retiré : redirection vers la
    fiche gardée (même sortie). Sortie passée ou retirée : page courte, non
    indexée (Google la retire sans erreur 404), avec un lien vers les
    sorties de la commune."""
    url = f"{BASE_URL}/{event_path(event)}"
    title = event.get("title") or "Sortie"
    kept = event.get("kept_id")
    if kept:
        target = f"{BASE_URL}/e/{kept}/"
        head = (f'<meta name="robots" content="noindex">\n  '
                f'<meta http-equiv="refresh" content="0; url={e(target)}">')
        body = (f'<section class="section prose"><h1>{e(title)}</h1>'
                f'<p>Cette fiche a été regroupée avec une autre. '
                f'<a href="{e(target)}">Voir la fiche à jour</a>.</p></section>')
        return layout(f"{title} — Wavents", body, description=f"{title} : fiche regroupée sur Wavents.",
                      url=url, canonical=target, extra_head=head, depth=2)
    start = wall_time(event.get("start_date"))
    when = f" du {day_label(start)} {start.year}" if start else ""
    past = event.get("status") == "expired"
    message = (f"Cette sortie{when} est terminée." if past
               else "Cette fiche n'est plus en ligne (annulée, ou retirée après vérification).")
    links = (f'<a href="../../{town_page}">Les prochaines sorties à {e(commune_page_name(event) or "")}</a> · '
             if town_page else "")
    body = (f'<section class="section prose"><h1>{e(title)}</h1><p>{e(message)}</p>'
            f'<p>{links}<a href="../../">Toutes les sorties près de chez toi</a></p></section>')
    return layout(f"{title} — Wavents", body, description=message, url=url,
                  extra_head='<meta name="robots" content="noindex">', depth=2)


def event_page(event: dict, neighbours: list[dict], series: dict[str, list[dict]],
               town_page: str | None = None, canonical: str | None = None,
               repeats: list[dict] | None = None) -> str:
    root = "../../"
    emoji, label, color = category(event)
    url = f"{BASE_URL}/{event_path(event)}"
    when = when_label(event)
    place = place_label(event)
    town = commune_of(event)
    summary = f"{when} · {town or event.get('location_name') or ''}".strip(" ·")
    description = event.get("description") or ""
    # Description pour Google et les IA (26/10/2026) : quand, où, et le début
    # du texte, plutôt que la seule date.
    excerpt = re.sub(r"\s+", " ", re.sub(r"^D'après l'affiche\s*:\s*", "", description)).strip()
    meta_description = summary + (f" — {excerpt[:150].rsplit(' ', 1)[0]}…" if len(excerpt) > 150 else f" — {excerpt}" if excerpt else "")
    start_day = wall_time(event["start_date"]).date()
    title_where = commune_page_name(event) or town or ""
    page_title = f"{event['title']} – {title_where}, {day_label(start_day)} {start_day.year}" if title_where else f"{event['title']} – {day_label(start_day)} {start_day.year}"
    lat, lon = event["latitude"], event["longitude"]
    maps = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
    image = image_for(event, root)

    actions = [f'<a class="btn btn-sun" href="event.ics" download="wavents-{event["id"][:8]}.ics" data-goatcounter-click="clic-agenda">📅 Ajouter à mon agenda</a>',
               f'<a class="btn btn-ghost" href="{maps}" target="_blank" rel="noopener" data-goatcounter-click="clic-itineraire">🧭 Y aller</a>',
               '<button class="btn btn-ghost" data-share data-goatcounter-click="clic-partager">🔗 Partager</button>']
    if (event.get("website_url") or "").startswith("http"):
        actions.append(f'<a class="btn btn-ghost" href="{e(event["website_url"])}" rel="nofollow noopener" target="_blank" data-goatcounter-click="clic-site-organisateur">🌐 Site de l\'organisateur</a>')

    # Marchés : toutes les dates du même marché ; autres événements qui
    # reviennent (atelier, séance, stage…, même titre, lieu et description) :
    # leurs autres dates aussi (Paul, 22/10/2026).
    same = series.get(series_key(event) or "") or repeats or []
    other_dates = [ev for ev in same
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
        badges.append('<span class="badge kids">🧸 En famille</span>')
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
        f"{page_title} | Wavents",
        body,
        description=meta_description,
        url=url,
        image=share_card(event),
        extra_head=(f'<script type="application/ld+json">{json_ld(event)}</script>\n  '
                    f'<script type="application/ld+json">{breadcrumb_ld(event, town_page)}</script>'),
        depth=2,
        page_class="event-page",
        canonical=canonical,
    )



# --- Marchés : jours et horaires par commune (26/10/2026) ----------------------
# « Marché de Tassin : quel jour, quelle heure ? » est une recherche très
# fréquente (Google comme assistants IA), et la réponse tient en une ligne :
# une page par commune qui a un marché hebdomadaire, avec FAQ balisée.

def market_path(town: str) -> str:
    return f"marche/{slugify(town)}/"


def _hour(text: str) -> str:
    h, m = text.split(":")[:2]
    return f"{int(h)}h{m if m != '00' else ''}"


def market_hours(ev: dict, weekday: int) -> str:
    slots = (ev.get("opening_hours") or {}).get(str(weekday)) or []
    return " et ".join(f"{_hour(a)} à {_hour(b)}" for a, b in slots)


def of_town(town: str) -> str:
    """« de Tassin », « d'Albigny » (élision devant une voyelle)."""
    return f"d'{town}" if fold(town[:1]) in "aeiouyh" and town[:1] else f"de {town}"


def market_sentence(town: str, ev: dict) -> str:
    days = sorted(ev.get("recurring_weekdays") or [])
    parts = []
    for d in days:
        hours = market_hours(ev, d)
        parts.append(f"le {DAYS[d - 1]}" + (f" de {hours}" if hours else ""))
    where = ev.get("location_name") or ""
    where = f", {where}" if where and fold(where) != fold(town) else ""
    return f"{' et '.join(parts)}{where}"


def market_groups(events: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for ev in events:
        if ev.get("category_id") != "marche" or not ev.get("recurring_weekdays"):
            continue
        town = commune_page_name(ev)
        if town:
            groups.setdefault(town, []).append(ev)
    return groups


def market_page(town: str, markets: list[dict], groups: dict[str, list[dict]], town_page: str | None) -> str:
    root = "../../"
    url = f"{BASE_URL}/{market_path(town)}"
    lines = [market_sentence(town, ev) for ev in markets]
    answer = (f"Le marché {of_town(town)} a lieu {lines[0]}." if len(lines) == 1
              else f"Il y a {len(lines)} marchés à {town} : " + " ; ".join(lines) + ".")
    known = all(market_hours(ev, d) for ev in markets for d in ev.get("recurring_weekdays") or [])
    if not known:
        answer += " Horaires non publiés par la commune pour l'instant."
    center = commune_center(markets)
    near = sorted(((other, distance_km(*commune_center(evs), center)) for other, evs in groups.items() if other != town),
                  key=lambda item: item[1])[:10]
    near_links = "".join(f'<a class="town-link" href="{root}{market_path(other)}">{e(other)} <span>{km:.0f} km</span></a>'
                         for other, km in near)
    rows = "".join(
        f'<li><a href="{root}{event_path(ev)}"><b>{e(ev["title"])}</b></a> : {e(market_sentence(town, ev))}</li>'
        for ev in markets)
    faq = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Wavents", "item": f"{BASE_URL}/"},
                {"@type": "ListItem", "position": 2, "name": "Marchés", "item": f"{BASE_URL}/marches/"},
                {"@type": "ListItem", "position": 3, "name": f"Marché {of_town(town)}", "item": url},
            ]},
            {"@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": f"Quel jour a lieu le marché {of_town(town)} ?",
                 "acceptedAnswer": {"@type": "Answer", "text": answer}},
            ]},
        ],
    }
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}marches/">← Tous les marchés</a>
    <p class="kicker">Jours et horaires</p>
    <h1>Marché {e(of_town(town))}</h1>
    <p class="lead">{e(answer)}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  <section class="section prose"><h2>Les marchés {e(of_town(town))}</h2><ul>{rows}</ul>
    <p>Horaires relevés sur les sites de la commune et des offices de tourisme, vérifiés chaque jour par Wavents.
    {f'<a href="{root}{town_page}">Toutes les sorties à {e(town)} →</a>' if town_page else ''}</p></section>
  {f'<section class="section"><div class="section-head"><div><p class="kicker">Pas loin</p><h2>Autres marchés autour de {e(town)}</h2></div></div><div class="town-links">{near_links}</div></section>' if near_links else ''}"""
    return layout(
        f"Marché {of_town(town)} : jours et horaires – Wavents",
        body,
        description=answer[:300],
        url=url,
        extra_head=f'<script type="application/ld+json">{json.dumps(faq, ensure_ascii=False)}</script>',
        depth=2,
        page_class="town-page",
    )


def markets_index(groups: dict[str, list[dict]]) -> str:
    root = "../"
    links = "".join(f'<a class="town-link" href="{root}{market_path(t)}">{e(t)} <span>{len(evs)}</span></a>'
                    for t, evs in sorted(groups.items(), key=lambda item: fold(item[0])))
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}marches/">← Les marchés à venir</a>
    <p class="kicker">Jours et horaires</p>
    <h1>Les marchés par commune</h1>
    <p class="lead">Quel jour, à quelle heure, où : les {len(groups)} marchés hebdomadaires du Beaujolais, du Val de Saône et du nord de Lyon.</p>
  </div></section>
  <section class="section"><div class="town-links">{links}</div></section>"""
    return layout("Marchés du Beaujolais et du nord de Lyon : jours et horaires par commune – Wavents", body,
                  description=f"Jours et horaires des {len(groups)} marchés hebdomadaires du Beaujolais, du Val de Saône et du nord de Lyon, commune par commune.",
                  url=f"{BASE_URL}/marche/", depth=1, page_class="town-page")


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
    # Même commune écrite de deux façons (« Belleville en Beaujolais » /
    # « Belleville-en-Beaujolais ») : même adresse de page, la petite
    # écrasait la grande (26/10/2026). Une seule orthographe par adresse.
    return COMMUNE_CANON.get(slugify(name), name)


COMMUNE_CANON: dict[str, str] = {}  # adresse de page -> orthographe retenue (canon_communes)


def canon_communes(events: list[dict]) -> None:
    """Retient, pour chaque adresse de page commune, l'orthographe la plus
    fréquente (à égalité, celle avec des traits d'union, la forme officielle)."""
    COMMUNE_CANON.clear()
    counts: dict[str, dict[str, int]] = {}
    for ev in events:
        name = commune_page_name(ev)
        if name:
            variants = counts.setdefault(slugify(name), {})
            variants[name] = variants.get(name, 0) + 1
    for slug, variants in counts.items():
        COMMUNE_CANON[slug] = max(variants, key=lambda v: (variants[v], v.count("-")))


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
        facts.append(f"{kids_count} en famille")
    intro = (f"Concerts, marchés, spectacles, expositions, fêtes… Toutes les sorties à {name} "
             f"et autour, mises à jour chaque jour : {', '.join(facts)}.")
    url = f"{BASE_URL}/{commune_path(name)}"
    market_link = (f'<section class="section"><div class="town-links"><a class="town-link" href="{root}{market_path(name)}">'
                   f'🧺 Marché {e(of_town(name))} : jours et horaires</a></div></section>'
                   if name in market_groups(events) else "")
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
  {market_link}
  {block("Aujourd'hui", f"Aujourd'hui à {name}", today_list)}
  {block("Du vendredi au dimanche", f"Ce week-end à {name}", weekend_list)}
  {block("Les 7 prochains jours", "Cette semaine", week_list)}
  {block("Expositions, animations sur plusieurs jours", "En ce moment", long_ones[:20])}
  {block("Et ensuite", "Plus tard", later)}
  {near_html}"""
    return layout(
        f"Que faire à {name} ce week-end ? Sorties, marchés, concerts – Wavents",
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
    # Comptes officiels (26/10/2026) : relient le site, l'appli et les réseaux
    # pour Google et les IA (graphe de connaissance).
    "sameAs": [PLAY_STORE_URL, "https://www.instagram.com/wavents_app/", "https://www.facebook.com/wavents_app"],
}

WEBSITE = {
    "@context": "https://schema.org",
    "@type": "WebSite",
    "name": "Wavents",
    "alternateName": "Wavents, les sorties près de toi",
    "url": f"{BASE_URL}/",
    "inLanguage": "fr-FR",
    "description": ("Agenda gratuit des sorties locales, mis à jour chaque jour : que faire ce week-end dans "
                    "le Beaujolais, le Val de Saône et le nord de Lyon, commune par commune."),
    "publisher": {"@type": "Organization", "name": "Wavents", "url": f"{BASE_URL}/"},
}

def app_ld() -> str:
    """L'appli décrite aux moteurs et aux assistants IA (22/10/2026, Paul :
    « l'appli et le site ») : schema.org MobileApplication, avec les stores
    où elle est publiée. Vide tant qu'aucun store ne la propose."""
    stores = [u for u in (PLAY_STORE_URL if PLAY_STORE_PUBLISHED else None, APP_STORE_URL) if u]
    if not stores:
        return ""
    systems = (["Android"] if PLAY_STORE_PUBLISHED else []) + (["iOS"] if APP_STORE_URL else [])
    data = {
        "@context": "https://schema.org",
        "@type": "MobileApplication",
        "name": "Wavents : sorties près de toi",
        "operatingSystem": ", ".join(systems),
        "applicationCategory": "LifestyleApplication",
        "inLanguage": "fr-FR",
        "description": ("L'appli gratuite des sorties locales du Beaujolais, du Val de Saône et du nord de "
                        "Lyon : carte des événements, filtres (gratuit, en famille, ce week-end), alertes et "
                        "rappels. Sans compte."),
        "url": f"{BASE_URL}/app/",
        "installUrl": stores,
        "offers": {"@type": "Offer", "price": "0", "priceCurrency": "EUR"},
        "publisher": {"@type": "Organization", "name": "Wavents", "url": f"{BASE_URL}/"},
    }
    return f'<script type="application/ld+json">{json.dumps(data, ensure_ascii=False)}</script>'


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
    ("enfants", "Sorties en famille et avec les enfants dans le Beaujolais", "Sorties en famille",
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
        chips.append('<button class="chip toggle" data-ttoggle="kids">En famille</button>')
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


# Pages « grands rendez-vous » (22/10, accord de Paul) : les assistants IA
# ne trouvent aucun programme à jour de ces fêtes (docs/referencement.md du
# dépôt Wavents). Adresse sans année, gardée d'une édition à l'autre ; les
# dates de l'édition sont à mettre à jour chaque année.
FESTIVALS = [
    {
        "slug": "sarmentelles-beaujeu",
        "name": "Les Sarmentelles de Beaujeu",
        "short": "Sarmentelles",
        "town": "Beaujeu",
        "first": date(2026, 11, 18),
        "last": date(2026, 11, 22),
        "match": r"sarmentelle",
        "where": "Beaujeu (Rhône)",
        "teaser": "À Beaujeu, la grande fête du Beaujolais nouveau : cinq jours, mise en perce à minuit.",
        "program_q": "Quel est le programme des Sarmentelles {year} ?",
        "faq": [("Quand ont lieu les Sarmentelles de Beaujeu {year} ?",
                 "Les Sarmentelles de Beaujeu {year} ont lieu du mercredi 18 au dimanche 22 novembre {year}, "
                 "à Beaujeu (Rhône). La mise en perce du Beaujolais nouveau a lieu à minuit, dans la nuit du "
                 "mercredi au jeudi.")],
        "related": ["beaujolais-nouveau"],
        "lead": "Le programme complet de la fête du Beaujolais nouveau à Beaujeu, jour par jour.",
        # Encart « C'est quoi ? » : sources vérifiées le 22/10/2026 (site
        # officiel sarmentelles.fr, agenda de la Ville de Lyon). L'année de la
        # première édition n'est donnée par aucune source : ne pas l'écrire.
        "story": [
            ("C'est quoi ?",
             "La grande fête du Beaujolais nouveau, organisée par l'association Les Sarmentelles de Beaujeu "
             "la veille du troisième jeudi de novembre, jour de sortie du Beaujolais nouveau. À minuit pile, "
             "après un défilé aux flambeaux, le vin nouveau est mis en perce place de l'Hôtel-de-Ville, avec "
             "feu d'artifice et dégustation du nouveau millésime. Autour, cinq jours de fête : parcours "
             "dégustation des 12 appellations du Beaujolais, soirées et repas sous le grand chapiteau, marché "
             "gourmand, salon des vins, journée en famille. L'édition 2026 est la 38e."),
            ("Pourquoi à Beaujeu ?",
             "Beaujeu est la capitale historique du Beaujolais : la seigneurie des sires de Beaujeu, connue "
             "depuis le Xe siècle, a donné son nom à toute la région. La fête du vin nouveau s'y tient depuis "
             "près de quarante ans."),
            ("Où ?",
             "À Beaujeu (Rhône), au cœur du Haut-Beaujolais, à une quinzaine de kilomètres à l'ouest de "
             "Belleville-en-Beaujolais et à une cinquantaine au nord de Lyon. La fête occupe le centre du "
             "bourg : place de l'Hôtel-de-Ville, promenade des Sarmentelles (grand chapiteau), théâtre et "
             "salle du stade."),
        ],
        "official": "https://www.sarmentelles.fr/",
        "nearby_title": "Le Beaujolais nouveau ailleurs",
        "nearby_match": r"beaujolais[- ]nouveaux?|beaujolais primeurs?|primeurs? du beaujolais",
    },
    {
        "slug": "beaujolais-nouveau",
        "name": "Le Beaujolais nouveau",
        "short": "Beaujolais nouveau",
        "town": None,
        # Sortie le 3e jeudi de novembre (19/11/2026) ; soirées du week-end
        # d'avant à celui d'après.
        "first": date(2026, 11, 13),
        "last": date(2026, 11, 29),
        "match": r"beaujolais[- ]nouveaux?|beaujolais primeurs?|primeurs? du beaujolais",
        "exclude": r"sarmentelle",
        "when": "Jeudi 19 novembre 2026, à minuit",
        "where": "Tout le Beaujolais",
        "teaser": "Toutes les soirées du Beaujolais nouveau dans les villages autour, jour par jour.",
        "unit": "soirées et fêtes",
        "unit1": "soirée ou fête",
        "lead": "Toutes les soirées du Beaujolais nouveau autour de chez toi : mises en perce, repas, bals, "
                "portes ouvertes, jour par jour.",
        # Sources vérifiées le 22/10/2026 : idealwine.net, europe1.fr,
        # icalendrier.fr (arrêté de 1951, décret du 29/10/1985, date 2026).
        "story": [
            ("C'est quoi ?",
             "Le Beaujolais nouveau, ou Beaujolais primeur, est le premier vin de l'année : un vin rouge de "
             "gamay, vendangé à la fin de l'été et mis en bouteille quelques semaines plus tard, à boire jeune. "
             "Il sort chaque année le troisième jeudi de novembre, à minuit : en 2026, dans la nuit du "
             "mercredi 18 au jeudi 19 novembre."),
            ("Un peu d'histoire",
             "En 1951, un arrêté interdit de vendre les vins d'appellation avant le 15 décembre. Les vignerons "
             "du Beaujolais obtiennent de pouvoir vendre le leur dès le 15 novembre, à condition d'écrire "
             "« nouveau » sur l'étiquette. Depuis un décret de 1985, la sortie est fixée au troisième jeudi "
             "de novembre. La fête a fait le tour du monde, mais c'est dans le Beaujolais qu'on la vit au plus "
             "près des vignerons."),
            ("Où le fêter ?",
             "Partout dans le Beaujolais et autour : la grande mise en perce des Sarmentelles à Beaujeu, et des "
             "dizaines de soirées dans les villages, caveaux et salles des fêtes, souvent avec repas, musique "
             "ou feu d'artifice. Ci-dessous, toutes celles que Wavents connaît à moins de 40 km de "
             "Belleville-en-Beaujolais."),
        ],
        "program_q": "Où fêter le Beaujolais nouveau {year} dans le Beaujolais ?",
        "faq": [("Quelle est la date du Beaujolais nouveau {year} ?",
                 "Le jeudi 19 novembre {year}. Comme chaque année, le troisième jeudi de novembre, il est mis en "
                 "vente à minuit, dans la nuit du mercredi 18 au jeudi 19."),
                ("Pourquoi le troisième jeudi de novembre ?",
                 "Depuis 1951, le Beaujolais nouveau peut être vendu avant les autres vins d'appellation. Sa "
                 "date de sortie, d'abord le 15 novembre, est fixée au troisième jeudi de novembre depuis un "
                 "décret de 1985."),
                ("Où est la plus grande fête du Beaujolais nouveau ?",
                 "À Beaujeu, capitale historique du Beaujolais, avec les Sarmentelles : cinq jours de fête et "
                 "la mise en perce à minuit. Programme complet : wavents.fr/sarmentelles-beaujeu/.")],
        "related": ["sarmentelles-beaujeu"],
    },
    {
        "slug": "halloween",
        "name": "Halloween",
        "short": "Halloween",
        "town": None,
        "first": date(2026, 10, 22),
        "last": date(2026, 11, 1),
        "match": r"halloween",
        "when": "Samedi 31 octobre 2026",
        "where": "Autour de Belleville",
        "unit": "animations",
        "unit1": "animation",
        "teaser": "Défilés costumés, chasses aux bonbons, ateliers et soirées frissons autour de chez toi.",
        "lead": "Défilés costumés, chasses aux bonbons, ateliers, contes et soirées frissons : toutes les "
                "animations d'Halloween autour de chez toi, jour par jour.",
        # Sources vérifiées le 22/10/2026 : brut.media, consoglobe.com,
        # vikidia (Samhain, Jack O'Lantern, années 1990) ; vacances : onisep.fr.
        "story": [
            ("C'est quoi ?",
             "Halloween, c'est la soirée du 31 octobre, veille de la Toussaint : on se déguise en sorcière, en "
             "fantôme ou en citrouille, les enfants font la tournée des maisons pour réclamer des bonbons, et "
             "l'on creuse des citrouilles en lanternes. Autour de chez nous, ce sont surtout des fêtes pour les "
             "enfants, pendant les vacances de la Toussaint (du 17 octobre au 2 novembre 2026)."),
            ("Un peu d'histoire",
             "Halloween vient de Samhain, une fête celte d'Irlande et d'Écosse qui marquait la fin des "
             "récoltes : on croyait alors que la frontière entre les vivants et les esprits s'amincissait. Son "
             "nom vient de « All Hallows' Eve », la veille de la Toussaint. Les émigrés irlandais l'ont emportée "
             "aux États-Unis, où le navet creusé de la lanterne est devenu citrouille. En France, la fête s'est "
             "surtout répandue à la fin des années 1990."),
            ("Où le fêter ?",
             "Dans les villages, les médiathèques, les fermes et les salles des fêtes du Beaujolais et du Val "
             "de Saône. Ci-dessous, toutes les animations d'Halloween que Wavents connaît à moins de 40 km de "
             "Belleville-en-Beaujolais."),
        ],
        "program_q": "Que faire pour Halloween {year} dans le Beaujolais ?",
        "count_q": "Y a-t-il des animations d'Halloween pour les enfants, et des gratuites ?",
        "faq": [("Quand est Halloween {year} ?",
                 "Le samedi 31 octobre {year}, veille de la Toussaint. Beaucoup d'animations ont lieu pendant "
                 "les vacances de la Toussaint, du 17 octobre au 2 novembre {year}.")],
        "related": [],
    },
    {
        "slug": "marches-de-noel",
        "name": "Les marchés de Noël",
        "short": "marchés de Noël",
        "town": None,
        "first": date(2026, 10, 31),
        "last": date(2026, 12, 24),
        "match": r"marches? (de|du) noel|marche de l.avent|noel des createurs",
        "exclude": r"halloween",
        "when": "De novembre au 24 décembre 2026",
        "where": "Beaujolais et Val de Saône",
        "unit": "marchés",
        "unit1": "marché",
        "teaser": "Chalets, vin chaud, artisans et idées cadeaux : tous les marchés de Noël autour de chez toi.",
        "lead": "Chalets, vin chaud, artisans et idées cadeaux : tous les marchés de Noël du Beaujolais et du "
                "Val de Saône, week-end par week-end.",
        # Sources vérifiées le 22/10/2026 : noel.strasbourg.eu (Christkindelsmärik, 1570).
        "story": [
            ("C'est quoi ?",
             "Chalets, vin chaud, artisans, idées cadeaux et parfois le Père Noël : les marchés de Noël "
             "rythment l'Avent. Dans le Beaujolais et autour, ce sont souvent des marchés de village, d'école "
             "ou d'association, le temps d'un week-end, dans une salle des fêtes ou sur la place."),
            ("Un peu d'histoire",
             "La tradition vient d'Alsace et des pays germaniques : le marché de Noël de Strasbourg, le "
             "Christkindelsmärik (« marché de l'Enfant Jésus » en alsacien), remonte à 1570 et compte parmi "
             "les plus anciens d'Europe. Il a longtemps été le seul en France ; les marchés de Noël se sont "
             "depuis répandus dans tout le pays."),
            ("Où les trouver ?",
             "Ci-dessous, tous les marchés de Noël que Wavents connaît à moins de 40 km de "
             "Belleville-en-Beaujolais, jour par jour. La liste s'allonge chaque jour à l'approche de "
             "décembre."),
        ],
        "program_q": "Où trouver un marché de Noël dans le Beaujolais en {year} ?",
        "faq": [("Quand ont lieu les marchés de Noël {year} ?",
                 "Surtout les week-ends de fin novembre et de décembre, jusqu'au 24 décembre {year}. Chaque "
                 "marché a ses dates et ses horaires sur sa fiche.")],
        "related": ["fete-des-lumieres"],
    },
    {
        "slug": "fete-des-lumieres",
        "name": "La Fête des Lumières",
        "short": "Fête des Lumières",
        "town": None,
        "first": date(2026, 12, 4),
        "last": date(2026, 12, 9),
        "match": r"fete des lumieres|lumignons?|illuminations? du 8 decembre|fete du 8 decembre|course des lumieres",
        "when": "Mardi 8 décembre 2026 (à Lyon, du 5 au 8)",
        "where": "Lyon et ses environs",
        "unit": "rendez-vous",
        "unit1": "rendez-vous",
        "teaser": "Lumignons aux fenêtres, lanternes et illuminations du 8 décembre, à Lyon et dans les villages.",
        "lead": "Lumignons, lanternes, illuminations et vin chaud : la Fête des Lumières à Lyon et dans les "
                "villages autour, jour par jour.",
        # Sources vérifiées le 22/10/2026 : herodote.net, lyoncapitale.fr,
        # visiterlyon.com (1852, lumignons, festival depuis 1999), icalendrier.fr (5-8/12/2026).
        "story": [
            ("C'est quoi ?",
             "Le 8 décembre, Lyon et sa région posent des lumignons, de petites bougies dans des verres, sur "
             "le bord de leurs fenêtres. À Lyon, la Fête des Lumières dure quatre jours, du 5 au 8 décembre "
             "2026, avec des créations lumineuses dans toute la ville. Dans les villages autour, on fête le "
             "8 décembre plus simplement : lanternes, processions, illuminations et vin chaud."),
            ("Un peu d'histoire",
             "Le 8 décembre 1852, Lyon devait inaugurer la statue dorée de la Vierge, sur la colline de "
             "Fourvière. Le mauvais temps a fait annuler les illuminations prévues ; quand le ciel s'est "
             "dégagé, les Lyonnais ont spontanément posé des bougies à leurs fenêtres. La tradition des "
             "lumignons était née ; le grand festival de lumière de Lyon s'y est ajouté à partir de 1999."),
            ("Où la fêter ?",
             "À Lyon bien sûr, mais aussi dans les communes du Beaujolais, du Val de Saône et des Monts d'Or. "
             "Ci-dessous, tous les rendez-vous que Wavents connaît à moins de 40 km de "
             "Belleville-en-Beaujolais."),
        ],
        "program_q": "Où fêter le 8 décembre {year} autour de Lyon et dans le Beaujolais ?",
        "faq": [("Quand a lieu la Fête des Lumières {year} ?",
                 "À Lyon, du samedi 5 au mardi 8 décembre {year}. Le 8 décembre, jour de la fête, les "
                 "habitants de toute la région posent des lumignons à leurs fenêtres."),
                ("D'où vient la Fête des Lumières ?",
                 "Du 8 décembre 1852 : ce soir-là, les Lyonnais ont illuminé leurs fenêtres pour l'inauguration "
                 "de la statue de la Vierge de Fourvière.")],
        "related": ["marches-de-noel"],
    },
    {
        "slug": "conscrits-villefranche",
        "name": "La fête des Conscrits de Villefranche",
        "short": "Conscrits",
        "town": None,
        "town_link": "Villefranche-sur-Saône",
        "first": date(2027, 1, 15),
        "last": date(2027, 2, 7),
        "match": r"conscrit",
        "when": "Fin janvier 2027 (dates pas encore annoncées)",
        "where": "Villefranche-sur-Saône",
        "unit": "rendez-vous",
        "unit1": "rendez-vous",
        "teaser": "La grande fête de Villefranche, fin janvier : la Vague, le défilé des classes.",
        "lead": "La grande fête de Villefranche-sur-Saône, fin janvier : la Vague, le défilé des classes, et "
                "les fêtes des conscrits dans les villages autour.",
        # Sources vérifiées le 22/10/2026 : fiche de l'inventaire du patrimoine
        # culturel immatériel (culture.gouv.fr), lyoncapitale.fr. Dates 2027
        # non publiées le 22/10 : à renseigner dès qu'elles le sont.
        "story": [
            ("C'est quoi ?",
             "La fête des Conscrits est la grande fête de Villefranche-sur-Saône, chaque année pendant six "
             "jours fin janvier. Les hommes d'une même classe, c'est-à-dire nés une année se terminant par le "
             "même chiffre, la fêtent ensemble à 20, 30, 40, 50 ans et plus. Temps forts : la Vague, le grand "
             "défilé des classes dans les rues, et le banquet."),
            ("Un peu d'histoire",
             "Elle est née sous la Troisième République, autour du conseil de révision et du tirage au sort qui "
             "désignaient les jeunes de 20 ans appelés au service militaire. Leurs aînés s'y sont peu à peu "
             "joints, jusqu'à en faire la fête de toute la ville et de ses environs. Elle est inscrite depuis "
             "2020 à l'inventaire national du patrimoine culturel immatériel."),
            ("Où ?",
             "À Villefranche-sur-Saône, dans le centre-ville. Beaucoup de villages du Beaujolais ont aussi "
             "leur fête des conscrits ou des classes : Wavents les liste ci-dessous dès qu'elles sont "
             "annoncées."),
        ],
        "program_q": "Quelles fêtes des conscrits en janvier {year} ?",
        "faq": [("Quand a lieu la fête des Conscrits de Villefranche {year} ?",
                 "Chaque année fin janvier, pendant six jours. Les dates de {year} n'ont pas encore été "
                 "annoncées : cette page sera mise à jour dès qu'elles le seront.")],
        "related": [],
    },
]


def festival_page(fest: dict, upcoming: list[dict], series: dict[str, list[dict]], now: datetime) -> str:
    root = "../"
    first, last = fest["first"], fest["last"]
    year = first.year
    in_window = [ev for ev in upcoming
                 if wall_time(ev["start_date"]).date() <= last and end_of(ev).date() >= first - timedelta(days=1)]

    def text_of(ev: dict) -> str:
        return fold(f"{ev['title']} {ev.get('description') or ''} {ev.get('location_name') or ''}")

    def one_per_day(evs: list[dict]) -> list[dict]:
        # Même titre le même jour (fiche en double pas encore fusionnée) : une seule ligne.
        return list({(fold(ev["title"]), ev["start_date"][:10]): ev for ev in reversed(evs)}.values())[::-1]

    def near(ev: dict) -> bool:
        return distance_km(ev["latitude"], ev["longitude"]) <= THEME_RADIUS_KM

    # Fête d'une commune (town) : toutes ses fiches ; fête de toute la région :
    # celles à moins de THEME_RADIUS_KM, sans celles d'une autre page (exclude).
    items = [ev for ev in in_window if re.search(fest["match"], text_of(ev))
             and (fest.get("town") or near(ev))
             and not (fest.get("exclude") and re.search(fest["exclude"], text_of(ev)))]
    if not fest.get("town"):
        items = one_per_day(collapse_series(items))
    items.sort(key=lambda ev: wall_time(ev["start_date"]))
    nearby = []
    if fest.get("nearby_match"):
        nearby = one_per_day(sorted((ev for ev in collapse_series(in_window) if ev not in items and near(ev)
                                     and re.search(fest["nearby_match"], text_of(ev))),
                                    key=lambda ev: wall_time(ev["start_date"])))

    span = (f"du {DAYS[first.weekday()]} {first.day} au {DAYS[last.weekday()]} {last.day} "
            f"{MONTHS[last.month - 1]} {year}")
    amounts = sorted(ev["price_amount"] for ev in items
                     if ev.get("price_type") == "paid" and ev.get("price_amount"))
    facts = [("Quand", fest.get("when") or cap(span)), ("Où", fest["where"])]
    if items:
        facts.append(("Au programme", f"{len(items)} {fest.get('unit', 'rendez-vous')} sur Wavents"))
    if fest.get("town") and len(amounts) > 1:
        facts.append(("Tarifs", f"de {amounts[0]:g} € à {amounts[-1]:g} € selon les rendez-vous"))
    facts_html = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in facts)

    by_day: dict[date, list[dict]] = {}
    for ev in items:
        # Déjà commencé (animation sur toutes les vacances…) : rangé à aujourd'hui.
        by_day.setdefault(max(wall_time(ev["start_date"]).date(), now.date()), []).append(ev)
    days_html = "".join(
        f'<section class="section"><div class="section-head"><div><p class="kicker">{e(cap(day_label(d)))}</p>'
        f'<h2>{len(evs)} {fest.get("unit", "rendez-vous") if len(evs) > 1 else fest.get("unit1", "rendez-vous")}</h2></div></div>'
        f'<div class="rows">{"".join(row_card(ev, series, root) for ev in evs)}</div></section>'
        for d, evs in sorted(by_day.items()))
    if not items:
        days_html = ('<section class="section"><p class="lead">Le programme n\'est pas encore publié : '
                     'reviens bientôt, cette page est mise à jour chaque jour.</p></section>')
    related = [f for f in FESTIVALS if f["slug"] in fest.get("related", [])]
    related_html = "".join(
        f'<a class="panel fest-related" href="{root}{f["slug"]}/"><b>{e(f["name"])} {f["first"].year}</b>'
        f'<span>{e(f["teaser"])}</span><span class="fest-more">Voir le programme →</span></a>' for f in related)
    if related_html:
        related_html = f'<section class="section">{related_html}</section>'
    nearby_html = ""
    if nearby:
        nearby_html = (f'<section class="section"><div class="section-head"><div><p class="kicker">Même semaine, '
                       f'à moins de {THEME_RADIUS_KM} km</p><h2>{e(fest["nearby_title"])}</h2></div></div>'
                       f'<div class="rows">{"".join(row_card(ev, series, root) for ev in nearby)}</div></section>')

    def towns(evs: list[dict], n: int) -> str:
        return ", ".join(list(dict.fromkeys(commune_of(ev) for ev in evs if commune_of(ev)))[:n])

    qa = [(q.format(year=year), a.format(year=year)) for q, a in fest.get("faq", [])]
    if items:
        listed = items[:20]
        qa.append((fest["program_q"].format(year=year),
                   "Au programme : " + " ; ".join(
                       f"{day_label(wall_time(ev['start_date']))}"
                       + (f" à {hour_label(wall_time(ev['start_date']))}" if ev.get("start_time_known")
                          and (wall_time(ev['start_date']).hour or wall_time(ev['start_date']).minute) else "")
                       + f", {ev['title']}" + ("" if fest.get("town") else f" ({commune_of(ev)})")
                       for ev in listed)
                   + (f", et {len(items) - len(listed)} autres sur cette page." if len(items) > len(listed) else ".")))
    if fest.get("count_q") and items:
        kids = sum(1 for ev in items if ev.get("for_kids"))
        free = sum(1 for ev in items if is_free(ev))
        qa.append((fest["count_q"].format(year=year),
                   f"Oui : sur les {len(items)} {fest.get('unit', 'rendez-vous')} de cette page, {kids} sont "
                   f"signalés « En famille » et {free} sont gratuits. Les badges sur chaque ligne aident à choisir."))
    if fest.get("town") and len(amounts) > 1:
        qa.append((f"Combien coûtent les {fest['short']} ?",
                   f"Les tarifs relevés vont de {amounts[0]:g} € à {amounts[-1]:g} € selon les soirées et "
                   "les repas. Le détail est sur chaque fiche, avec le lien vers l'organisateur."))
    if nearby:
        qa.append(("Où fêter le Beaujolais nouveau ailleurs dans le Beaujolais ?",
                   f"Wavents liste {len(nearby)} autres soirées et animations la même semaine, à moins de "
                   f"{THEME_RADIUS_KM} km de Belleville-en-Beaujolais, par exemple à {towns(nearby, 6)}."))
    story_html = "".join(f"<h2>{e(t)}</h2><p>{e(p)}</p>" for t, p in fest["story"])
    if fest.get("official"):
        story_html += (f'<p class="fest-source">Site officiel : <a href="{e(fest["official"])}" rel="noopener">'
                       f'{e(fest["official"].split("//")[1].strip("/").removeprefix("www."))}</a></p>')
    faq_html = "".join(f"<h3>{e(q)}</h3><p>{e(a)}</p>" for q, a in qa)
    town_link = fest.get("town_link") or fest.get("town")
    links = [(f"{root}{commune_path(town_link)}", f"Que faire à {town_link}")] if town_link else []
    links += [(f"{root}{f['slug']}/", f["name"]) for f in FESTIVALS
              if f["slug"] != fest["slug"] and f not in related]
    links.append((f"{root}ce-week-end/", "Ce week-end dans le Beaujolais"))
    links_html = "".join(f'<a class="town-link" href="{h}">{e(t)}</a>' for h, t in links)

    url = f"{BASE_URL}/{fest['slug']}/"
    schema = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "ItemList", "name": f"{fest['name']} {year} : programme", "itemListElement": [
                {"@type": "ListItem", "position": i + 1, "url": f"{BASE_URL}/{event_path(ev)}"}
                for i, ev in enumerate(items[:50])
            ]},
            {"@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa
            ]},
        ],
    }
    image = next((real_image(ev) for ev in items if real_image(ev)), None)
    intro = (f"{fest['name']} {year}, {fest.get('when', span).lower() if fest.get('when') else span} : "
             "c'est quoi, où, et tout le programme jour par jour, mis à jour chaque jour.")
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}">← Accueil</a>
    <p class="kicker">Grand rendez-vous</p>
    <h1>{e(fest['name'])} {year}</h1>
    <p class="lead">{e(fest['lead'])}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  <section class="section"><div class="panel soft fest-story">{story_html}</div>
    <dl class="fest-facts">{facts_html}</dl></section>
  {related_html}
  {days_html}
  {nearby_html}
  <section class="section prose"><h2>Questions fréquentes</h2>{faq_html}</section>
  <section class="section"><div class="town-links">{links_html}</div></section>"""
    return layout(
        f"{fest['name']} {year} : programme et dates – Wavents",
        body,
        description=intro,
        url=url,
        image=image,
        extra_head=f'<script type="application/ld+json">{json.dumps(schema, ensure_ascii=False)}</script>',
        depth=1,
        page_class="town-page",
    )


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
    peuvent aussi ajouter leurs événements gratuitement sur <a href="../ajouter/">wavents.fr/ajouter</a> ou depuis l'appli.</p>
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
         "Gratuitement, sur wavents.fr/ajouter depuis un ordinateur, ou depuis l'appli Wavents (bouton « + ») : "
         "une photo de l'affiche ou le lien de ta page suffit, la fiche se remplit toute seule. L'événement est publié après vérification, en général dans la "
         "journée."),
        ("Wavents est-il gratuit ?",
         "Oui, le site et l'appli sont gratuits et sans compte. Wavents ne vend pas de billets : "
         "chaque fiche renvoie vers l'organisateur."),
        ("L'appli est-elle disponible sur iPhone et Android ?",
         ("L'appli Wavents est disponible gratuitement sur Android (Google Play) et arrive bientôt sur "
          "iPhone (App Store). Toutes les sorties sont aussi sur ce site, depuis n'importe quel téléphone."
          if PLAY_STORE_PUBLISHED and not APP_STORE_URL else
          "L'appli Wavents est disponible gratuitement sur Android (Google Play) et sur iPhone (App Store)."
          if PLAY_STORE_PUBLISHED else
          "L'appli Wavents arrive bientôt sur l'App Store et le Play Store. En attendant, toutes les "
          "sorties sont consultables sur ce site, depuis n'importe quel téléphone.")),
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

def llms_txt(upcoming: list[dict], now: datetime, communes: dict[str, list[dict]] | None = None,
             markets: dict[str, list[dict]] | None = None) -> str:
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
        (f"Appli Wavents (carte, favoris, rappels, alertes), gratuite : Android {PLAY_STORE_URL}"
         + (f" ; iPhone {APP_STORE_URL}." if APP_STORE_URL else " ; iPhone bientôt sur l'App Store.")
         if PLAY_STORE_PUBLISHED else "Appli Android Wavents (carte, favoris, rappels, alertes) : bientôt sur Google Play."),
        f"Contact : hello@wavents.fr. Confidentialité : {BASE_URL}/confidentialite.html.",
        f"À propos : {BASE_URL}/a-propos/. Questions fréquentes : {BASE_URL}/faq/.",
        "",
        "## Pages thématiques (mises à jour chaque jour)",
        "",
        f"- [Que faire ce week-end dans le Beaujolais]({BASE_URL}/ce-week-end/)",
        f"- [Marchés du Beaujolais et du Val de Saône]({BASE_URL}/marches/)",
        f"- [Brocantes et vide-greniers]({BASE_URL}/brocantes/)",
        f"- [Sorties gratuites]({BASE_URL}/gratuit/)",
        f"- [Sorties en famille]({BASE_URL}/enfants/)",
        *[f"- [{f['name']} {f['first'].year} : programme]({BASE_URL}/{f['slug']}/)" for f in FESTIVALS],
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
    if markets:
        # La réponse directe (jour, heure, lieu) : ce que les assistants citent.
        lines += ["", "## Marchés hebdomadaires : jours et horaires", ""]
        for town, evs in sorted(markets.items(), key=lambda item: fold(item[0])):
            lines.append(f"- [Marché {of_town(town)}]({BASE_URL}/{market_path(town)}): "
                         + " ; ".join(market_sentence(town, ev) for ev in evs))
    return "\n".join(lines) + "\n"


# Rubrique « Ce que fait l'appli » (maquette validée par Paul le 20/10/2026) :
# les animations des stories Wavents (scripts/story_wavents.py du dépôt de
# l'appli), réduites en 360 x 640 sans le son dans static/videos/ (WebM, et MP4 pour Safari).
DEMOS = [("affiche", "Une affiche en photo, la fiche se remplit"), ("alertes", "Une alerte, et tu ne rates plus rien"),
         ("weekend", "Ton week-end en un coup d'œil"), ("itineraire", "Le GPS t'y emmène"),
         ("agenda", "Dans ton agenda en un toucher"), ("partage", "Partage à tes amis"),
         ("sombre", "Le mode sombre"), ("sources", "D'où viennent les sorties")]


def demos_section() -> str:
    """Un téléphone par vidéo, qu'on fait glisser (Paul, 20/10) : celle du
    milieu joue, puis la suivante arrive toute seule (app.js)."""
    phones = "".join(
        f'<figure class="dphone{" on" if i == 0 else ""}" data-demo="{k}"><div class="vphone">'
        f'<video muted playsinline preload="none" poster="videos/{k}.jpg" aria-label="{e(t)}">'
        f'<source src="videos/{k}.webm" type="video/webm"><source src="videos/{k}.mp4" type="video/mp4"></video>'
        f'</div><figcaption>{e(t)}</figcaption></figure>'
        for i, (k, t) in enumerate(DEMOS))
    dots = "".join(f'<button type="button" aria-label="Vidéo {i + 1}"{" class=on" if i == 0 else ""}></button>'
                   for i in range(len(DEMOS)))
    return f"""  <section class="section demo-section" id="ce-que-fait-l-appli">
    <div class="section-head reveal"><div><p class="kicker">En 6 secondes chacune</p><h2>Ce que fait l'appli</h2></div>
      <div class="scroller-nav"><button data-demo-step="-1" aria-label="Précédente">←</button><button data-demo-step="1" aria-label="Suivante">→</button></div></div>
    <div class="demo-track reveal">{phones}</div>
    <div class="demo-dots">{dots}</div>
  </section>
"""


def orga_section() -> str:
    return """  <section class="section">
    <div class="orga reveal"><span class="orga-ico" aria-hidden="true">📣</span>
      <div><p class="kicker">Mairie, association, comité des fêtes, commerçant</p><h2>Tu organises un événement&nbsp;?</h2>
      <p>Ajoute-le gratuitement depuis ton ordinateur : une photo de l'affiche suffit, on s'occupe du reste.</p></div>
      <a class="btn btn-sun" href="ajouter/" data-goatcounter-click="clic-accueil-ajouter">+ Ajouter un événement</a></div>
  </section>
"""


def add_page() -> str:
    """Formulaire « Ajouter un événement » (static/ajouter.js) : même chemin
    que le formulaire de l'appli, fiche masquée jusqu'à vérification."""
    cats = "".join(f'<option value="{cid}">{e(label)}</option>' for cid, (_, label, _) in CATEGORIES.items())
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">Gratuit, sans compte</p>
    <h1>Ajoute ton événement</h1>
    <p class="lead">Mairies, associations, comités des fêtes, commerçants : ton événement apparaît sur l'appli Wavents
    et sur wavents.fr, près de ceux qui habitent autour.</p>
  </div></section>
  <main class="section add-page">
    <form id="add-form" data-supabase="{SUPABASE_URL}" data-key="{SUPABASE_ANON_KEY}" novalidate>
      <div class="modes" role="group" aria-label="Comment veux-tu ajouter ton événement ?">
        <button type="button" class="mode on" data-mode="affiche" aria-pressed="true"><span aria-hidden="true">📷</span><div>J'ai une affiche<small>Le plus rapide</small></div></button>
        <button type="button" class="mode" data-mode="lien" aria-pressed="false"><span aria-hidden="true">🔗</span><div>J'ai un lien ou un texte<small>Ta page, ta publication</small></div></button>
        <button type="button" class="mode" data-mode="main" aria-pressed="false"><span aria-hidden="true">✍️</span><div>Je remplis<small>Tout à la main</small></div></button>
      </div>

      <div id="step-affiche">
        <label class="drop" id="drop">
          <input type="file" id="file" accept="image/jpeg,image/png,image/webp" hidden>
          <img id="drop-img" alt="" hidden>
          <div><b id="drop-name">Glisse ton affiche ici</b><p>ou clique pour la choisir (JPG, PNG).</p>
          <p class="add-status" id="affiche-status" aria-live="polite"></p></div>
        </label>
      </div>

      <div id="step-lien" hidden>
        <textarea id="lien" rows="3" aria-label="Lien de la page ou texte de la publication"
          placeholder="Le lien de ta page (https://…), ou le texte de ta publication Facebook, de ton e-mail…"></textarea>
        <button type="button" class="btn btn-sun lien-go" id="lien-go">Lire</button>
        <p class="add-status" id="lien-status" aria-live="polite"></p>
      </div>

      <div class="card-form" id="fiche" hidden>
        <h2>Vérifie la fiche</h2>
        <p class="hint" id="fiche-hint"></p>
        <div id="occurrences" class="occurrences" hidden></div>
        <label class="f"><span>Titre *</span><input id="titre" maxlength="300" required></label>
        <div class="grid2">
          <label class="f"><span>Catégorie *</span><select id="categorie" required><option value="">Choisis…</option>{cats}</select></label>
          <label class="f"><span>Lieu</span><input id="lieu" placeholder="Salle des fêtes, place du marché…"></label>
        </div>
        <label class="f adresse"><span>Adresse *</span><input id="adresse" autocomplete="off" placeholder="Rue et commune">
          <ul class="sugg" id="adresse-sugg" hidden></ul></label>
        <div class="grid2">
          <label class="f"><span>Date *</span><input type="date" id="date" required></label>
          <label class="f"><span>Heure</span><input type="time" id="heure"></label>
          <label class="f"><span>Date de fin (si plusieurs jours)</span><input type="date" id="date-fin"></label>
          <label class="f"><span>Heure de fin</span><input type="time" id="heure-fin"></label>
        </div>
        <fieldset class="f prix"><span>Prix</span>
          <label><input type="radio" name="prix" value="gratuit"> Gratuit</label>
          <label><input type="radio" name="prix" value="payant"> Payant</label>
          <label><input type="radio" name="prix" value="inconnu" checked> Je ne sais pas</label>
          <label class="montant" id="montant-box" hidden>Prix en €<input id="montant" inputmode="decimal" placeholder="5"></label>
        </fieldset>
        <label class="check"><input type="checkbox" id="famille"> En famille</label>
        <label class="f"><span>Description</span><textarea id="description" rows="4" maxlength="10000"></textarea></label>
        <div class="grid2">
          <label class="f"><span>Lien (site, billetterie)</span><input type="url" id="site"></label>
          <label class="f"><span>E-mail de contact (affiché sur la fiche)</span><input type="email" id="email"></label>
          <label class="f"><span>Téléphone (affiché sur la fiche)</span><input type="tel" id="tel"></label>
        </div>
        <input id="piege" name="site_web" tabindex="-1" autocomplete="off" class="piege" aria-hidden="true">
        <button class="btn btn-sun send" id="envoyer" type="submit">Envoyer mon événement</button>
        <p class="add-status" id="envoi-status" aria-live="polite"></p>
        <p class="note">Chaque fiche est vérifiée avant publication, en général dans la journée.</p>
      </div>
    </form>

    <div class="card-form merci" id="merci" hidden>
      <h2>Merci&nbsp;! 🎉</h2>
      <p id="merci-n"></p>
      <p>On la vérifie, en général dans la journée.</p>
      <p id="merci-lien">Une fois publiée, elle sera ici : <a id="merci-a" href="#"></a></p>
      <p><a class="btn btn-sun" href="./">Ajouter un autre événement</a></p>
    </div>
  </main>
  <script src="../ajouter.js" defer></script>"""
    return layout(
        "Ajouter un événement – Wavents",
        body,
        description="Mairies, associations, organisateurs : ajoutez gratuitement votre événement sur Wavents, "
                    "l'agenda des sorties du Beaujolais. Une photo de l'affiche suffit.",
        url=f"{BASE_URL}/ajouter/",
        depth=1,
        page_class="town-page",
    )


def app_page(upcoming: list[dict], communes: dict[str, list[dict]]) -> str:
    """Page d'installation (cible du QR code des affiches) : envoie vers le
    bon store selon le téléphone dès que l'appli y est publiée."""
    targets = {"android": PLAY_STORE_URL if PLAY_STORE_PUBLISHED else None, "ios": APP_STORE_URL}
    redirect = f"""<script>
(function () {{
  var t = {json.dumps(targets)};
  var ua = navigator.userAgent || "";
  var dest = /android/i.test(ua) ? t.android : (/iphone|ipad|ipod/i.test(ua) ? t.ios : null);
  // Canal d'arrivée (?ref=presse, mairies, facebook, affiche… ; 19/10) :
  // compté par GoatCounter avant la redirection (sinon la visite partait
  // vers le store sans être comptée) et transmis au Play Store, qui
  // l'affiche dans ses sources d'installation.
  var ref = (location.search.match(/[?&](?:ref|utm_source)=([^&]+)/) || [])[1] || "";
  if (dest) {{
    new Image().src = "https://wavents.goatcounter.com/count?p=" + encodeURIComponent("/app/" + (ref ? "?ref=" + ref : ""))
      + "&r=" + encodeURIComponent(document.referrer) + (ref ? "&q=" + encodeURIComponent("ref=" + ref) : "") + "&rnd=" + Math.random();
    if (ref && dest === t.android) dest += "&referrer=" + encodeURIComponent("utm_source=" + ref);
    setTimeout(function () {{ location.replace(dest); }}, 350);
  }}
}})();
</script>"""
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">L'appli Wavents</p>
    <h1>Toutes les sorties autour de chez toi, dans ta poche</h1>
    <p class="lead">{len(upcoming)} sorties à venir dans {len(communes)} communes : concerts, marchés,
    brocantes, spectacles, sorties en famille. Gratuit, sans compte.</p>
    <div class="hero-actions">{play_cta('light')}</div>
  </div></section>
  <section class="section prose">
    <h2>Pourquoi l'appli ?</h2>
    <p><strong>La carte des sorties</strong> autour de toi : aujourd'hui, ce week-end ou cette semaine.</p>
    <p><strong>Des alertes</strong> : une recherche enregistrée te prévient quand une sortie qui te plaît est publiée.</p>
    <p><strong>Des rappels</strong> pour ne plus rater la brocante du dimanche ou le concert du village voisin.</p>
    <p>En attendant, tout l'agenda est aussi sur le site : <a href="../#agenda">parcourir les sorties</a>.</p>
  </section>"""
    return layout(
        "Télécharger l'appli Wavents – les sorties du Beaujolais",
        body,
        description="L'appli gratuite des sorties autour de Belleville-en-Beaujolais, Villefranche et Mâcon : "
                    "carte, alertes et rappels. Sans compte.",
        url=f"{BASE_URL}/app/",
        extra_head=redirect + app_ld(),
        depth=1,
        page_class="town-page",
    )


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
    canon_communes(events)
    series = build_series(events)
    by_day: dict[date, list[dict]] = {}
    for ev in events:
        by_day.setdefault(wall_time(ev["start_date"]).date(), []).append(ev)

    communes = group_by_commune(upcoming)
    repeats = repeat_groups(events)
    canonical_ids = repeat_canonicals(repeats, now)
    for ev in events:
        folder = out / event_path(ev)
        folder.mkdir(parents=True, exist_ok=True)
        town = commune_page_name(ev)
        town_page = commune_path(town) if town in communes else None
        main_id = canonical_ids[ev["id"]]
        canonical = None if main_id == ev["id"] else f"{BASE_URL}/e/{main_id}/"
        (folder / "index.html").write_text(event_page(ev, neighbours_of(ev, by_day), series, town_page, canonical,
                                                      repeats.get(repeat_key(ev))), encoding="utf-8")
        (folder / "event.ics").write_text(ics(ev), encoding="utf-8")
    (out / "index.html").write_text(home_page(events, upcoming, now), encoding="utf-8")
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "carte.json").write_text(json.dumps(HOME_MAP_POINTS, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Fiches plus en ligne : une page plutôt qu'une 404 (25/10/2026).
    live_ids = {ev["id"] for ev in events}
    communes_now = group_by_commune(upcoming)
    gone = 0
    for ev in fetch_gone_events():
        folder = out / event_path(ev)
        if ev["id"] in live_ids or folder.exists():
            continue
        town = commune_page_name(ev)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(
            gone_page(ev, commune_path(town) if town in communes_now else None), encoding="utf-8")
        gone += 1
    print(f"{gone} pages de fiches plus en ligne")

    for name, evs in communes.items():
        folder = out / commune_path(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(commune_page(name, evs, communes, series, now), encoding="utf-8")
    (out / "ville").mkdir(exist_ok=True)
    (out / "ville" / "index.html").write_text(communes_index(communes), encoding="utf-8")

    markets = market_groups(events)
    for town, evs in markets.items():
        folder = out / market_path(town)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(
            market_page(town, evs, markets, commune_path(town) if town in communes else None), encoding="utf-8")
    (out / "marche").mkdir(exist_ok=True)
    (out / "marche" / "index.html").write_text(markets_index(markets), encoding="utf-8")

    theme_counts: dict[str, int] = {}
    for slug, page_title, h1, kicker, keep, days in THEMES:
        page, count = theme_page(slug, page_title, h1, kicker, keep, days, upcoming, series, now)
        theme_counts[slug] = count
        (out / slug).mkdir(exist_ok=True)
        (out / slug / "index.html").write_text(page, encoding="utf-8")
    for fest in FESTIVALS:
        (out / fest["slug"]).mkdir(exist_ok=True)
        (out / fest["slug"] / "index.html").write_text(festival_page(fest, upcoming, series, now), encoding="utf-8")
    (out / "a-propos").mkdir(exist_ok=True)
    (out / "a-propos" / "index.html").write_text(about_page(upcoming, communes), encoding="utf-8")
    (out / "faq").mkdir(exist_ok=True)
    (out / "faq" / "index.html").write_text(faq_page(upcoming, communes, theme_counts), encoding="utf-8")
    (out / "ajouter").mkdir(exist_ok=True)
    (out / "ajouter" / "index.html").write_text(add_page(), encoding="utf-8")
    (out / "app").mkdir(exist_ok=True)
    (out / "app" / "index.html").write_text(app_page(upcoming, communes), encoding="utf-8")
    extra_pages = ([f"{BASE_URL}/{t[0]}/" for t in THEMES] + [f"{BASE_URL}/{f['slug']}/" for f in FESTIVALS]
                   + [f"{BASE_URL}/a-propos/", f"{BASE_URL}/faq/", f"{BASE_URL}/app/", f"{BASE_URL}/ajouter/"])

    urls = ([f"{BASE_URL}/", f"{BASE_URL}/ville/", f"{BASE_URL}/marche/"] + extra_pages
            + [f"{BASE_URL}/{market_path(town)}" for town in markets]
            + [f"{BASE_URL}/{commune_path(name)}" for name in communes]
            + [f"{BASE_URL}/{event_path(ev)}" for ev in events if canonical_ids[ev["id"]] == ev["id"]])
    # Date de mise à jour (26/10/2026) : les pages de listes changent chaque
    # jour ; une fiche, à sa publication.
    published = {f"{BASE_URL}/{event_path(ev)}": (ev.get("published_at") or "")[:10] for ev in events}
    today_iso = now.date().isoformat()
    sitemap = "\n".join(
        f"  <url><loc>{html.escape(quote(u, safe=':/'))}</loc><lastmod>{published.get(u) or today_iso}</lastmod></url>"
        for u in urls)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sitemap}\n</urlset>\n',
        encoding="utf-8",
    )
    (out / "llms.txt").write_text(llms_txt(upcoming, now, communes, markets), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    # IndexNow (Bing, et par lui ChatGPT/Copilot) : liste des pages à
    # signaler après la publication (étape du workflow pages.yml). Les pages
    # communes changent chaque jour ; les événements, les 2 000 plus proches.
    soonest = sorted(upcoming, key=lambda ev: ev["start_date"])[:2000]
    (out / "indexnow.json").write_text(json.dumps(
        [f"{BASE_URL}/", f"{BASE_URL}/ville/", f"{BASE_URL}/marche/"] + extra_pages
        + [f"{BASE_URL}/{commune_path(name)}" for name in communes]
        + [f"{BASE_URL}/{market_path(town)}" for town in markets]
        + [f"{BASE_URL}/{event_path(ev)}" for ev in soonest]), encoding="utf-8")
    print(f"{len(events)} pages d'événements et {len(communes)} pages communes générées dans {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
