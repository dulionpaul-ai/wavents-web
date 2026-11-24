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

import gzip
import html
import io
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
# Fiche App Store : Wavents validé par Apple le 28/10/2026 (1.5.0).
APP_STORE_URL: str | None = "https://apps.apple.com/fr/app/wavents/id6820224748"

PARIS = ZoneInfo("Europe/Paris")
HOME = (46.1075, 4.7536)  # centre de Belleville-en-Beaujolais, comme l'appli
HOME_RADIUS_KM = 30
HOME_DAYS = 21
AGENDA_DAY_PREVIEW = 12  # cartes affichées par jour avant « Voir les autres sorties » (static/app.js)

FIELDS = (
    "id,title,description,category_id,location_name,address,latitude,longitude,"
    "start_date,end_date,start_time_known,end_time_known,price_type,price_amount,price_confirmed,"
    "image_url,additional_photo_urls,website_url,for_kids,source,published_at,recurring_weekdays,opening_hours,"
    # Intérieur / extérieur (script 211 du dépôt Wavents, 30/10/2026).
    "setting,setting_sure,"
    # Organisateur lu dans la description, pour la recherche (script 258, 23/11/2026).
    "organizer,source_url"
)
# Police d'icônes réduite aux seules icônes utilisées (audit du 20/11/2026 :
# 503 Ko sur chaque page avant, 4,5 Ko après). Toute nouvelle icône passée à
# _icon() ou à la mini-carte (« place ») doit être ajoutée ici, sinon elle
# s'affiche en toutes lettres. Ordre alphabétique exigé par Google Fonts.
MS_ICONS = {"brush", "celebration", "child_care", "explore", "groups", "money_off", "museum", "music_note",
            "phone_iphone", "place", "sell", "sports_soccer", "storefront", "theater_comedy"}
MS_ICON_NAMES = ",".join(sorted(MS_ICONS))


def _icon(name: str) -> str:
    assert name in MS_ICONS, f"icône {name} absente de MS_ICONS"
    return f'<span class="ms" aria-hidden="true">{name}</span>'


# id -> (icône, libellé, couleur) : mêmes icônes Material et mêmes couleurs
# que l'appli (lib/models/category.dart), pour qu'on se sente au même endroit.
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

def urlopen_gz(request: urllib.request.Request, timeout: int):
    """Lecture de la base compressée (20/11/2026, quota de bande passante
    Supabase dépassé) : urllib ne demande pas gzip de lui-même, et une page
    de 1 000 fiches pèse 1,09 Mo en clair contre 0,30 Mo compressée."""
    request.add_header("Accept-Encoding", "gzip")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
    return io.BytesIO(data)


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
        with urlopen_gz(request, timeout=60) as response:
            rows = json.load(response)
        events.extend(rows)
        if len(rows) < page:
            return events
        offset += page


def fetch_lettre_communes() -> list[str]:
    """Noms officiels des communes à moins de 50 km (suggestions du
    formulaire de la lettre du jeudi). Liste vide si la base ne répond pas :
    le formulaire marche sans."""
    request = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/rpc/lettre_communes_proches", data=b"{}", method="POST",
        headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urlopen_gz(request, timeout=30) as response:
            return [name for name in json.load(response) if isinstance(name, str)]
    except Exception as exc:  # noqa: BLE001
        print(f"Communes de la lettre illisibles : {exc}")
        return []


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
            with urlopen_gz(request, timeout=60) as response:
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
    # Jamais coupé au milieu d'un mot (24/11/2026 : « … / Centre cult »,
    # « Charnay-Lès-Mâcon Le ») ; un nom trop long s'arrête au mot entier et
    # ne finit pas par un article ou une barre.
    if len(town) > 40:
        town = town[:40].rsplit(" ", 1)[0]
    town = re.sub(r"(\s+(le|la|les|l'|du|de|des|et|/|-))+$", "", town, flags=re.I).strip(" -/,")
    return town


def category(event: dict) -> tuple[str, str, str]:
    return CATEGORIES.get(event.get("category_id") or "", CATEGORIES["associatif_autre"])


def category_photo(category_id: str | None, root: str) -> str:
    return f"{root}categories/{CATEGORY_PHOTO.get(category_id or '', 'associatif')}.jpg"


def real_image(event: dict) -> str | None:
    """Photo de l'événement, sauf les images vides de chargement différé
    (« data:image/svg+xml… ») qu'un scraper aurait prises pour une photo."""
    url = event.get("image_url") or ""
    return url if url.startswith("http") else None


LINK_RE = re.compile(r'(https?://[^\s<>«»"]+|www\.[^\s<>«»"]+)|([\w.+-]+@[\w-]+(?:\.[\w-]+)+)'
                     r'|((?:\+33\s?|0)[1-9](?:[\s.]?\d{2}){4})', re.I)


def linkify(text: str) -> str:
    """Description en HTML, liens, e-mails et téléphones cliquables (Paul,
    23/11/2026, comme dans l'appli) ; la ponctuation finale reste du texte."""
    out, last = [], 0
    for m in LINK_RE.finditer(text):
        raw, end = m.group(0), m.end()
        if m.group(1):
            trimmed = raw.rstrip(".,;:!?)]")
            end -= len(raw) - len(trimmed)
            raw = trimmed
            href = raw if raw.lower().startswith("http") else "https://" + raw
            link = f'<a href="{e(href)}" rel="nofollow noopener" target="_blank">{e(raw)}</a>'
        elif m.group(2):
            link = f'<a href="mailto:{e(raw)}">{e(raw)}</a>'
        else:
            link = f'<a href="tel:{re.sub(r"[\s.]", "", raw)}">{e(raw)}</a>'
        out.append(e(text[last:m.start()]) + link)
        last = end
    out.append(e(text[last:]))
    return "".join(out).replace("\n", "<br>")


def search_index(events: list[dict], canonical_ids: dict, now: datetime) -> str:
    """[chemin, titre, ligne date · commune · catégorie, texte de recherche]
    pour chaque sortie à venir (une par série), triées par date."""
    today = now.date()
    rows = []
    for ev in sorted(events, key=lambda ev: ev["start_date"]):
        if canonical_ids.get(ev["id"], ev["id"]) != ev["id"]:
            continue
        end = wall_time(ev.get("end_date") or ev["start_date"]).date()
        if end < today:
            continue
        _, label, _ = category(ev)
        start = wall_time(ev["start_date"])
        when = day_label(max(start.date(), today)) if not is_long(ev) else f"jusqu'au {day_label(end)}"
        meta = " · ".join(p for p in (cap(when), commune_of(ev), label) if p)
        q = fold(f"{ev['title']} {commune_of(ev)} {ev.get('location_name') or ''} {label} {ev.get('organizer') or ''}")
        rows.append([event_path(ev), ev["title"], meta, q])
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def more_photos(event: dict) -> str:
    """Photos en plus de la principale (affiche, visuel), comme dans l'appli
    (Paul, 23/11/2026) : vignettes entières qui ouvrent l'image."""
    urls = [u for u in (event.get("additional_photo_urls") or []) if isinstance(u, str) and u.startswith("http")]
    if not urls:
        return ""
    thumbs = "".join(f'<a class="ev-photo" href="{e(u)}" data-zoom target="_blank" rel="noopener">'
                     f'<img src="{e(u)}" alt="Photo {i + 2} : {e(event["title"])}" loading="lazy"></a>'
                     for i, u in enumerate(urls[:6]))
    return f'<div class="panel"><h2>Photos</h2><div class="ev-photos">{thumbs}</div></div>'


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


# Billetteries (Paul, 24/11/2026) : nos sources restent privées (c'est
# notre avantage sur la concurrence), on n'affiche donc plus le lien du
# site d'origine ; seul un lien de réservation (places, billets) est montré.
TICKETING = ("digitick.", "helloasso.", "billetweb.", "billetreduc.", "ticketac.", "weezevent.",
             "mapado.", "themisweb.", "fnacspectacles.", "ticketmaster.", "seetickets.", "eventbrite.",
             "shotgun.live", "yurplan.", "festik.", "francebillet.", "placeminute.", "billetterie.",
             "tickets.", "ticketing.", "infomaniak.events", "dice.fm", "ticketmaster.fr")


def booking_url(event: dict) -> str | None:
    """Lien de réservation de l'événement, s'il pointe vers une billetterie."""
    for key in ("website_url", "source_url"):
        link = (event.get(key) or "").strip()
        if not link.startswith("http"):
            continue
        host = link.split("//", 1)[-1].split("/", 1)[0].lower()
        if any(t in host for t in TICKETING) or "/billetterie" in link.lower():
            return link
    return None


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
    # « tous les … » seulement de semaine en semaine (signalement de Paul du
    # 27/10/2026 : deux vendredis à un mois d'écart affichés « tous les
    # vendredis », comme dans l'appli).
    days = [wall_time(ev["start_date"]).date() for ev in items]
    weekly = all((b - a).days == 7 for a, b in zip(days, days[1:]))
    if len(weekdays) == 1 and weekly:
        return f"tous les {DAYS[weekdays.pop()]}s"
    return f"+ {len(items) - 1} date{'s' if len(items) > 2 else ''}"


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
                          "url": booking_url(event) or f"{BASE_URL}/{event_path(event)}"}
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
    # Leaflet (carte) seulement sur les pages qui ont une carte (audit du
    # 20/11/2026 : 3 fichiers bloquants de moins sur les autres pages).
    has_map = 'id="map"' in body or 'id="mini-map"' in body
    leaflet_css = ('  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">\n'
                   '  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/MarkerCluster.css">\n'
                   ) if has_map else ""
    leaflet_js = ('  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js" defer></script>\n'
                  '  <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.markercluster/1.5.3/leaflet.markercluster.js" defer></script>\n'
                  ) if has_map else ""
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
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,500,1,0&icon_names={MS_ICON_NAMES}&display=block">
{leaflet_css}  <link rel="stylesheet" href="{root}style.css">
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
        <div class="footer-id"><img src="{root}img/logo-256.png" alt="" width="64" height="64">
          <div><strong>Wavents</strong><p>Suis la vague&nbsp;: ne rate plus rien près de chez toi.<br>Fait avec ❤️ à <span class="nowrap">Belleville-en-Beaujolais</span>.</p></div></div>
        <div class="footer-cta">
          <a class="footer-pill" href="{root}lettre/" data-goatcounter-click="clic-pied-lettre">💌 La lettre du jeudi</a>
          <a class="footer-pill coffee" href="{KOFI_URL}" target="_blank" rel="noopener" data-goatcounter-click="clic-pied-kofi">☕ Offre-nous un café</a>
        </div>
      </div>
      <nav class="footer-cols" aria-label="Plan du site">
        <div class="footer-col"><h2>Sorties</h2><ul>
          <li><a href="{root}#week-end">Ce week-end</a></li>
          <li><a href="{root}#agenda">Agenda</a></li>
          <li><a href="{root}#carte">Carte</a></li>
          <li><a href="{root}ville/">Par commune</a></li>
          <li><a href="{root}ce-week-end/">Ce week-end dans le Beaujolais</a></li>
          <li><a href="{root}marches/">Marchés</a></li>
          <li><a href="{root}brocantes/">Brocantes</a></li>
          <li><a href="{root}gratuit/">Sorties gratuites</a></li>
          <li><a href="{root}enfants/">En famille</a></li>
          <li><a href="{root}quand-il-pleut/">Quand il pleut</a></li>
        </ul></div>
        <div class="footer-col"><h2>Outils</h2><ul>
          <li><a href="{root}ajouter/">Ajouter un événement</a></li>
          <li><a href="{root}app/">L'appli Wavents</a></li>
          <li><a href="{root}secret-santa/" data-goatcounter-click="clic-pied-secret-santa">🎅 Secret Santa en ligne</a></li>
          <li><a href="{root}trouver-une-date/" data-goatcounter-click="clic-pied-trouver-date">📅 Trouver une date entre amis</a></li>
        </ul></div>
        <div class="footer-col"><h2>Wavents</h2><ul>
          <li><a href="{root}a-propos/">À propos</a></li>
          <li><a href="{root}faq/">Questions fréquentes</a></li>
          <li><a href="mailto:hello@wavents.fr">Contact</a></li>
        </ul></div>
        <div class="footer-col"><h2>Légal</h2><ul>
          <li><a href="{root}confidentialite.html">Confidentialité</a></li>
          <li><a href="{root}mentions-legales.html">Mentions légales</a></li>
          <li><a href="{root}cgu.html">Conditions d'utilisation</a></li>
        </ul></div>
      </nav>
    </div>
    <p class="footer-note">Informations issues des agendas publics et des organisateurs, mises à jour chaque jour. Vérifie auprès de l'organisateur avant de te déplacer.</p>
  </footer>
  {qr_card(root)}
  {"" if url.rstrip("/").endswith("/ajouter") else f'<a class="add-fab" href="{root}ajouter/?ref=bouton-flottant" data-goatcounter-click="clic-bouton-ajouter">+ Ajouter un événement</a>'}
{leaflet_js}  <script src="{root}app.js" defer></script>
  <script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</body>
</html>
"""


KOFI_URL = "https://ko-fi.com/wavents"


def kofi_box(where: str) -> str:
    """Encart sobre « gratuit et le restera » (audit du 20/11/2026) : don
    facultatif, rien de réservé à ceux qui donnent (règle de Paul). Clic
    compté dans GoatCounter comme clic-<emplacement>-kofi."""
    return (f'<aside class="kofi-box" aria-label="Soutenir Wavents"><span class="kofi-emoji" aria-hidden="true">☕</span>'
            f'<div><p class="kofi-title">Wavents est gratuit et le restera</p>'
            f'<p class="kofi-text">Sans pub et sans compte. Si une sortie trouvée ici t\'a plu, tu peux nous offrir un café\u00a0: '
            f'c\'est facultatif, et ça nous aide à continuer.</p></div>'
            f'<a class="btn kofi-btn" href="{KOFI_URL}" target="_blank" rel="noopener" '
            f'data-goatcounter-click="clic-{where}-kofi">☕ Offre-nous un café</a></aside>')


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


def thumb(url: str, width: int) -> str:
    """Vignette des listes servie par wsrv.nl (service libre d'images,
    derrière le cache de Cloudflare) pour les photos de notre stockage
    Supabase : réduite à la taille affichée et gardée en cache, au lieu de
    la photo entière téléchargée chez Supabase à chaque visite et à chaque
    robot (20/11/2026, quota de bande passante Supabase dépassé)."""
    if not url.startswith(f"{SUPABASE_URL}/storage/"):
        return url
    return f"https://wsrv.nl/?url={quote(url, safe='')}&w={width}&output=webp&q=72"


def media(event: dict, root: str, css: str = "", width: int = 600) -> str:
    """Photo de l'événement, ou celle de sa catégorie (comme EventImage dans
    l'appli). Vignette réduite (thumb) ; si elle ne vient pas, la photo
    d'origine, puis celle de la catégorie."""
    src = image_for(event, root)
    small = thumb(src, width)
    if small == src:
        return f'<img class="{css}" src="{e(src)}" alt="{e(event['title'])}" loading="lazy" onerror="{fallback(event, root)}">'
    retry = f"this.onerror=function(){{{fallback(event, root)}}};this.src='{src}'"
    return f'<img class="{css}" src="{e(small)}" alt="{e(event['title'])}" loading="lazy" onerror="{e(retry)}">'


def cover(event: dict, root: str) -> str:
    """Photo principale de la fiche, toujours entière (Paul, 23/11/2026 :
    une affiche en hauteur était coupée) : posée sur un fond flou de la même
    image, quel que soit son format. Photo de catégorie : recadrée comme
    avant."""
    src = real_image(event)
    if not src:
        return f'<div class="ev-cover">{media(event, root)}</div>'
    bg = thumb(src, 200).replace("'", "%27").replace('"', "%22").replace(")", "%29").replace("(", "%28")
    return (f'<a class="ev-cover fit" href="{e(src)}" data-zoom style="--bg:url(\'{e(bg)}\')" '
            f'aria-label="Agrandir la photo">{media(event, root, width=900)}</a>')


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
    search = fold(f"{event['title']} {commune_of(event)} {event.get('location_name') or ''} {label} {event.get('organizer') or ''}")
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
    if start.date() < datetime.now(PARIS).date() < end_of(event).date() or (is_long(event) and start.date() < datetime.now(PARIS).date()):
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
    search = fold(f"{event['title']} {commune_of(event)} {event.get('location_name') or ''} {label} {event.get('organizer') or ''}")
    return f"""<a class="row-card" href="{root}{event_path(event)}" data-cat="{e(event.get('category_id') or '')}" data-free="{1 if is_free(event) else 0}" data-kids="{1 if event.get('for_kids') else 0}" data-q="{e(search)}" data-lat="{event['latitude']:.3f}" data-lon="{event['longitude']:.3f}" style="--cat:{color}">
  <span class="row-media">{media(event, root, width=200)}</span>
  <span class="row-body"><span class="cat-chip">{emoji} {e(label)}</span><b>{e(event['title'])}</b><span class="row-date">{e(meta)}</span>{f'<span class="badges">{"".join(badges)}</span>' if badges else ''}</span>
</a>"""


# --- Page d'accueil ------------------------------------------------------------

def weekend_range(today: date) -> tuple[date, date]:
    """Du vendredi au dimanche ; le week-end en cours s'il a commencé."""
    if today.weekday() >= 4:
        return today, today + timedelta(days=6 - today.weekday())
    friday = today + timedelta(days=4 - today.weekday())
    return friday, friday + timedelta(days=2)


def weekend_label(today: date) -> str:
    """« du 21 au 23 novembre », « du 28 novembre au 1er décembre » : dates du
    week-end (vendredi → dimanche) en cours ou à venir, pour les titres des
    pages communes (21/11/2026 : titres datés, comme Vivop ; Google montre
    une page fraîche pour « que faire à X ce week-end »)."""
    friday = today - timedelta(days=today.weekday() - 4) if today.weekday() >= 4 else \
        today + timedelta(days=4 - today.weekday())
    sunday = friday + timedelta(days=2)
    def day(d: date) -> str:
        return "1er" if d.day == 1 else str(d.day)
    if friday.month == sunday.month:
        return f"du {day(friday)} au {day(sunday)} {MONTHS[sunday.month - 1]}"
    return f"du {day(friday)} {MONTHS[friday.month - 1]} au {day(sunday)} {MONTHS[sunday.month - 1]}"


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
            f'<section class="day{on}" data-day="{index}" data-date="{day.isoformat()}"><h3 class="day-title">{e(label)}</h3>'
            f'<div class="grid">{"".join(card(ev, series) for ev in items)}</div>'
            f'<div class="day-more"><button class="btn btn-ghost" data-more>Voir les autres sorties</button></div></section>'
        )

    map_points = [
        {
            "t": ev["title"], "c": ev.get("category_id") or "associatif_autre",
            "la": round(ev["latitude"], 5), "lo": round(ev["longitude"], 5),
            "d": cap(day_label(wall_time(ev["start_date"]))), "p": commune_of(ev),
            "u": event_path(ev), "k": 1 if ev.get("for_kids") else 0, "f": 1 if is_free(ev) else 0,
            "s": wall_time(ev["start_date"]).date().isoformat(), "e": end_of(ev).date().isoformat(),
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
        <form class="hero-search" role="search" action="#agenda" onsubmit="return false">
          <label><span aria-hidden="true">🔍</span><input type="search" id="hero-q" placeholder="Un village, un concert, un loto…" autocomplete="off" aria-label="Chercher une sortie"></label>
        </form>
        <div class="hero-actions">
          <a class="btn btn-sun" href="#week-end">Voir ce week-end</a>
          <div class="store-row">{play_cta()}</div>
        </div>
        <p class="hero-mail"><a href="#lettre" data-goatcounter-click="clic-hero-lettre">💌 Ou reçois les sorties du week-end chaque jeudi par mail&nbsp;→</a></p>
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


  <section class="section" id="agenda">
    <div class="section-head reveal"><div><p class="kicker">Mis à jour chaque jour</p><h2>Les sorties près de chez toi</h2></div></div>
    <div class="filters" id="filters">
      <label class="search"><span>🔍</span><input type="search" id="q" placeholder="Un village, un concert, un loto…" autocomplete="off"></label>
      <div class="chips">
        <label class="chip chip-when is-on"><span aria-hidden="true">📅</span><select id="when" aria-label="Quand ?"><option value="today">Aujourd'hui</option><option value="tomorrow">Demain</option><option value="weekend">Ce week-end</option><option value="week" selected>7 prochains jours</option><option value="all">Toutes les dates</option></select></label>
        <button class="chip is-on" data-cat="">Tout</button>
        <button class="chip toggle" data-toggle="kids">{_icon("child_care")} En famille</button>
        <button class="chip toggle" data-toggle="free">{_icon("money_off")} Gratuit</button>
        {chips}
      </div>
      <p class="result-count" id="count" aria-live="polite"></p>
    </div>
    <div class="view-toggle" role="group" aria-label="Affichage"><button class="vt is-on" data-view="map" aria-pressed="true">🗺️ Carte</button><button class="vt" data-view="list" aria-pressed="false" data-goatcounter-click="clic-vue-liste">📋 Liste</button></div>
    <div class="search-results" id="search-results" hidden aria-live="polite"></div>
    <div class="map-wrap" id="carte"><div class="map-box"><div id="map" role="region" aria-label="Carte des événements"></div><div class="map-search" role="search"><span aria-hidden="true">🔍</span><input type="search" id="map-q" placeholder="Aller à une ville…" autocomplete="off" aria-label="Aller à une ville sur la carte"><ul class="map-sugg" hidden></ul></div></div></div>
    <div class="agenda by-map">{''.join(day_sections)}</div>
    <p class="empty" id="empty" hidden>Aucune sortie ne correspond. Essaie un autre filtre&nbsp;!</p>
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
  </div>

{lettre_section("", "accueil")}
{orga_section()}
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


def town_letter(root: str, canal: str, town: str) -> str:
    """Lettre du jeudi en bas de fiche, commune déjà remplie (24/11/2026)."""
    return (f'<section class="section lettre-section"><div class="lettre-card"><span class="feedback-emoji lettre-emoji" aria-hidden="true">💌</span>'
            f'<div class="lettre-body"><p class="kicker">La lettre du jeudi</p>'
            f'<h2>Ne rate plus les sorties{" à " + e(town) if town else ""}</h2>'
            f'<p>Chaque jeudi, les sorties du week-end près de chez toi, dans ta boîte mail. Rien à installer.</p>'
            f'{lettre_form(root, canal, commune=town)}</div></div></section>')


def gone_page(event: dict, town_page: str | None, nearby: list[dict] | None = None,
              series: dict[str, list[dict]] | None = None) -> str:
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
    # Plus un cul-de-sac (Paul, 24/11/2026 : la fiche la plus vue du mois
    # était terminée) : les prochaines sorties de la commune, en cartes, et
    # la lettre du jeudi avec la commune déjà remplie.
    town = commune_page_name(event) or ""
    picks = (nearby or [])[:6]
    grid = ""
    if picks:
        heading = f"Les prochaines sorties à {e(town)}" if town and town_page else "Les prochaines sorties près de chez toi"
        grid = (f'<section class="section"><div class="section-head"><div><p class="kicker">Ne rate pas la prochaine</p>'
                f'<h2>{heading}</h2></div></div>'
                f'<div class="grid">{"".join(card(ev, series or {}, "../../") for ev in picks)}</div>'
                f'<p class="gone-more">{links}<a href="../../">Toutes les sorties près de chez toi</a></p></section>')
    else:
        grid = f'<section class="section prose"><p>{links}<a href="../../">Toutes les sorties près de chez toi</a></p></section>'
    letter = town_letter("../../", "fiche-terminee", town)
    body = (f'<section class="ev-hero town-hero"><div class="ev-hero-inner"><a class="back" href="../../">← Tout l\'agenda</a>'
            f'<p class="kicker">Sortie terminée</p><h1>{e(title)}</h1><p class="lead">{e(message)}</p></div></section>'
            f'{grid}{letter}')
    return layout(f"{title} — Wavents", body, description=message, url=url,
                  depth=2)


def setting_label(event: dict) -> str | None:
    """« En intérieur », « Probablement en extérieur »… comme l'appli
    (lib/utils/event_setting.dart) ; rien si inconnu."""
    sure = bool(event.get("setting_sure"))
    return {
        "interieur": "En intérieur" if sure else "Probablement en intérieur",
        "exterieur": "En extérieur" if sure else "Probablement en extérieur",
        "les_deux": "Intérieur et extérieur",
    }.get(event.get("setting") or "")


def setting_fact(event: dict) -> str:
    label = setting_label(event)
    if not label:
        return ""
    icon = "🌳" if event.get("setting") == "exterieur" else ("🏠" if event.get("setting") == "interieur" else "🏡")
    return f'<div><span>{icon}</span><p><b>Intérieur / extérieur</b>{e(label)}</p></div>'


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

    actions = [f'<a class="btn btn-sun" href="event.ics" rel="nofollow" download="wavents-{event["id"][:8]}.ics" data-goatcounter-click="clic-agenda">📅 + Agenda</a>',
               f'<a class="btn btn-ghost" href="{maps}" target="_blank" rel="noopener" data-goatcounter-click="clic-itineraire">🧭 Y aller</a>',
               '<button class="btn btn-ghost" data-share data-goatcounter-click="clic-partager">🔗 Partager</button>']
    booking = booking_url(event)
    if booking:
        actions.insert(0, f'<a class="btn btn-sun" href="{e(booking)}" rel="nofollow noopener" target="_blank" data-goatcounter-click="clic-reserver">🎟 Réserver</a>')
        actions[1] = actions[1].replace("btn btn-sun", "btn btn-ghost", 1)

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
        <div class="ev-cover-wrap">{date_chip(event)}{cover(event, root)}</div>
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
      {f'<div class="panel"><h2>À propos</h2><div class="description">{linkify(description)}</div></div>' if description else NO_DESCRIPTION}
      {more_photos(event)}
      {dates_html}
    </div>
    <aside class="ev-side">
      <div class="panel facts">
        <div><span>📅</span><p><b>Quand</b>{e(when)}</p></div>
        <div><span>📍</span><p><b>Où</b>{e(place)}</p></div>
        {setting_fact(event)}
        <div><span>💶</span><p><b>Prix</b>{e(price_label(event))}</p></div>
      </div>
      <div class="panel mini-map-panel"><div id="mini-map" data-lat="{lat}" data-lon="{lon}" data-color="{color}" data-icon="{re.sub(r"<[^>]+>", "", emoji)}"></div></div>
      <div class="panel app-promo"><img src="{root}img/logo-256.png" alt="" width="56" height="56"><p><b>Ne rate plus rien près de chez toi</b>Favoris, rappels et notifs avec l'appli Wavents.</p>{play_cta('small')}</div>
    </aside>
    <div class="ev-kofi">{kofi_box("fiche")}</div>
  </section>
  {near_html}
  {town_letter(root, "fiche", commune_page_name(event) or "")}"""
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
    where = f", {where}" if where and fold(where) not in fold(town) and fold(town) not in fold(where) else ""
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
AROUND_KM = 8
AROUND_MIN_EVENTS = 5


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
                 series: dict[str, list[dict]], now: datetime,
                 center: tuple[float, float] | None = None, around: bool = False) -> str:
    """Page « Que faire à <commune> ». `around` (21/11/2026, idée reprise de
    Vivop, qui a une page pour chaque commune) : petite commune sans assez de
    sorties à elle, la page montre celles à moins de AROUND_KM km de son
    centre (communes_zone.json)."""
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

    center = center or commune_center(events)
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
    facts = [f"{len(events)} sortie{'s' if len(events) > 1 else ''} à venir"]
    if free_count:
        facts.append(f"{free_count} gratuite{'s' if free_count > 1 else ''}")
    if kids_count:
        facts.append(f"{kids_count} en famille")
    wk_label = weekend_label(today)
    wk_count = len(weekend_list) + (len(today_list) if today.weekday() >= 4 else 0)
    wk_fact = f"{wk_count} ce week-end ({wk_label}), " if wk_count else ""
    intro = (f"Concerts, marchés, spectacles, expositions, fêtes… Toutes les sorties à {name} "
             f"et autour, mises à jour chaque jour : {wk_fact}{', '.join(facts)}.")
    if around:
        intro = (f"Concerts, marchés, spectacles, fêtes… Les sorties à {name} et à moins de {AROUND_KM} km, "
                 f"mises à jour chaque jour : {wk_fact}{', '.join(facts)}.")
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
    <h1>Que faire à {e(name)} ce week‑end ?</h1>
    <p class="kicker">{e(cap(wk_label))}</p>
    <p class="lead">{e(intro)}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  {market_link}
  {block("Aujourd'hui", f"Aujourd'hui à {name}", today_list)}
  {block(cap(wk_label), f"Ce week-end à {name}", weekend_list)}
  {block("Les 7 prochains jours", "Cette semaine", week_list)}
  {block("Expositions, animations sur plusieurs jours", "En ce moment", long_ones[:20])}
  {block("Et ensuite", "Plus tard", later)}
  <!--pluie-->
  {near_html}
  {town_letter(root, "commune", name if not around else "")}"""
    return layout(
        f"Que faire à {name} ce week-end ({wk_label}) ? Sorties, marchés, concerts – Wavents",
        body,
        description=intro,
        url=url,
        extra_head=f'<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>',
        depth=2,
        page_class="town-page",
    )


def communes_index(communes: dict[str, list[dict]], around: list[str] | None = None) -> str:
    root = "../"
    links = "".join(
        f'<a class="town-link" href="{root}{commune_path(name)}">{e(name)} <span>{len(evs)}</span></a>'
        for name, evs in sorted(communes.items(), key=lambda item: fold(item[0])))
    if around:
        links += "".join(f'<a class="town-link" href="{root}{commune_path(name)}">{e(name)}</a>'
                         for name in sorted(around, key=fold))
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">{len(communes) + len(around or [])} communes</p>
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
    # Jours de pluie (Paul, 23/11/2026, idée vue chez Yatoo) : sorties en
    # intérieur (sûr ou probable), et une page par ville (rain_pages).
    ("quand-il-pleut", "Que faire quand il pleut dans le Beaujolais ?", "Que faire quand il pleut ?",
     "À l'abri", lambda ev: ev.get("setting") == "interieur", 14),
]

# Pages « Que faire quand il pleut à <ville> » : villes de plus de RAIN_MIN_POP
# habitants (communes_zone.json) avec au moins RAIN_MIN_EVENTS sorties à
# l'abri à moins de RAIN_KM km dans les 14 jours.
RAIN_KM = 10
RAIN_MIN_POP = 2000
RAIN_MIN_EVENTS = 6


def rain_pages(out: Path, upcoming: list[dict], series: dict[str, list[dict]], now: datetime) -> list[str]:
    """« Que faire quand il pleut à <ville> ? » (Paul, 23/11/2026) : une page
    par ville assez grande, sous ville/<ville>/quand-il-pleut/. Rend les
    adresses écrites (pour le plan du site)."""
    zone_file = Path(__file__).parent / "communes_zone.json"
    if not zone_file.exists():
        return []
    keep = next(t[4] for t in THEMES if t[0] == "quand-il-pleut")
    horizon = now.date() + timedelta(days=14)
    paths = []
    for c in json.loads(zone_file.read_text(encoding="utf-8")):
        if (c.get("pop") or 0) < RAIN_MIN_POP:
            continue
        center = (c["lat"], c["lon"])
        n = sum(1 for ev in upcoming if keep(ev) and wall_time(ev["start_date"]).date() <= horizon
                and distance_km(ev["latitude"], ev["longitude"], center) <= RAIN_KM)
        if n < RAIN_MIN_EVENTS:
            continue
        path = f"{commune_path(c['name'])}quand-il-pleut/"
        page, _ = theme_page("quand-il-pleut", f"Que faire quand il pleut à {c['name']} ?",
                             f"Que faire à {c['name']} quand il pleut ?", "À l'abri", keep, 14,
                             upcoming, series, now, radius=RAIN_KM, center=center, path=path, place=c["name"])
        (out / path).mkdir(parents=True, exist_ok=True)
        (out / path / "index.html").write_text(page, encoding="utf-8")
        paths.append(path)
    return paths


def theme_page(slug: str, page_title: str, h1: str, kicker: str, keep, days: int | None,
               upcoming: list[dict], series: dict[str, list[dict]], now: datetime, *,
               radius: int = THEME_RADIUS_KM, intro: str | None = None, extra_head: str = "",
               near_chip: bool = False, after: str = "", center: tuple[float, float] = HOME,
               path: str | None = None, place: str | None = None) -> tuple[str, int]:
    """Options (20/11/2026, page /sejour/) : rayon, accroche, en-tête en plus
    (noindex), pastille « Près de moi », section ajoutée en fin de liste.
    `center`, `path`, `place` (23/11/2026) : la même page autour d'une ville
    (« Que faire quand il pleut à Villefranche »), à l'adresse `path`."""
    path = path or f"{slug}/"
    depth = path.strip("/").count("/") + 1
    root = "../" * depth
    today = now.date()
    pool = [ev for ev in upcoming if distance_km(ev["latitude"], ev["longitude"], center) <= radius]
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
                round(distance_km(ev["latitude"], ev["longitude"], center) / 8), ev["start_date"])):
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
    if near_chip:
        chips.append('<button class="chip toggle" data-ttoggle="near">📍 Près de moi</button>')
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
    where = (f"à moins de {radius} km de {place}" if place else
             f"dans le Beaujolais, le Val de Saône et le nord de Lyon (à moins de {radius} km de Belleville)")
    what = "sorties à l'abri, en intérieur," if slug == "quand-il-pleut" else "sorties"
    intro = intro or f"{len(ordered)} {what} {span} {where}, mises à jour chaque jour."
    url = f"{BASE_URL}/{path}"
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
  {after}
  <section class="section"><div class="section-head"><div><p class="kicker">Et aussi</p><h2>D'autres idées</h2></div></div>
    <div class="town-links">{others}<a class="town-link" href="{root}ville/">Par commune</a></div></section>"""
    return layout(
        f"{page_title} – Wavents",
        body,
        description=intro,
        url=url,
        extra_head=f'<script type="application/ld+json">{json.dumps(item_list, ensure_ascii=False)}</script>{extra_head}',
        depth=depth,
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
        # 29/10 : aussi les fêtes qui ne disent pas « Halloween » (bal des petits
        # monstres, marche hantée, fête des morts mexicaine…).
        "match": r"halloween|citrouille|sorciere|hantee|petits monstres|dia de los muertos|fete des morts",
        "when": "Samedi 31 octobre 2026",
        # Page partagée dans les groupes Facebook et aux sous des écoles (Paul,
        # 29/10) : le ton de Paul en tête, le programme avant l'histoire, le
        # grand soir coupé en deux (journée / soirée).
        "hook": "Pas encore d'idée pour Halloween ? Nous non plus, alors on a tout rassemblé : {n} animations "
                "autour de Belleville, de Villefranche et du Val de Saône. Il n'y a plus qu'à piocher.",
        "title": "Halloween {year} dans le Beaujolais : {n} animations, le programme",
        "story_after": True,
        # Avis de mairie qui citent la fête (rue barrée pour le défilé…).
        "exclude_title": r"rue barree|route barree|stationnement|deviation|circulation",
        "split_day": date(2026, 10, 31),
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


FEST_COUNTS: dict[str, int] = {}  # rendez-vous par page de fête (data/saisons.json, lettre du jeudi)


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
             and not (fest.get("exclude") and re.search(fest["exclude"], text_of(ev)))
             and not (fest.get("exclude_title") and re.search(fest["exclude_title"], fold(ev["title"])))]
    if not fest.get("town"):
        items = one_per_day(collapse_series(items))
    items.sort(key=lambda ev: wall_time(ev["start_date"]))
    FEST_COUNTS[fest["slug"]] = len(items)
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

    # Rangement par jour (et, le grand soir, journée puis soirée) ; les
    # animations déjà commencées (stages, expos des vacances) à part, en fin.
    today, split = now.date(), fest.get("split_day")

    def evening(ev: dict) -> bool:
        start = wall_time(ev["start_date"])
        if ev.get("start_time_known") and (start.hour or start.minute):
            return start.hour >= 17
        return bool(re.search(r"\b(soiree|soir|bal|nocturne|veillee)\b", fold(ev["title"])))

    by_slot: dict[tuple, list[dict]] = {}
    ongoing = []
    for ev in items:
        d = wall_time(ev["start_date"]).date()
        if d < today:
            ongoing.append(ev)
            continue
        by_slot.setdefault((d, 1 if d == split and evening(ev) else 0), []).append(ev)

    def unit(n: int) -> str:
        return fest.get("unit", "rendez-vous") if n > 1 else fest.get("unit1", "rendez-vous")

    blocks, day_links = [], []
    for (d, part), evs in sorted(by_slot.items()):
        label = "Aujourd'hui" if d == today else ("Demain" if d == today + timedelta(days=1) else cap(day_label(d)))
        short = "Aujourd'hui" if d == today else ("Demain" if d == today + timedelta(days=1)
                                                    else f"{cap(DAYS_SHORT[d.weekday()])} {d.day}")
        if part:
            label, short = f"{label}, le soir", f"{short} soir"
        anchor = f"j{d:%Y%m%d}{'s' if part else ''}"
        day_links.append(f'<a class="chip day-link" href="#{anchor}">{e(short)}</a>')
        blocks.append(
            f'<section class="section theme-day" id="{anchor}"><div class="section-head"><div><p class="kicker">{e(label)}</p>'
            f'<h2><span class="t-count">{len(evs)}</span> {unit(len(evs))}</h2></div></div>'
            f'<div class="rows">{"".join(row_card(ev, series, root) for ev in evs)}</div></section>')
    if ongoing:
        day_links.append('<a class="chip day-link" href="#jlong">Déjà commencé</a>')
        blocks.append(
            '<section class="section theme-day" id="jlong"><div class="section-head"><div><p class="kicker">'
            f'Sur plusieurs jours</p><h2><span class="t-count">{len(ongoing)}</span> {unit(len(ongoing))} '
            f'en ce moment</h2></div></div><div class="rows">{"".join(row_card(ev, series, root) for ev in ongoing)}'
            '</div></section>')
    chips = ['<button class="chip toggle" data-ttoggle="near">📍 Près de moi</button>',
             '<button class="chip toggle" data-ttoggle="kids">En famille</button>',
             '<button class="chip toggle" data-ttoggle="free">Gratuit</button>']
    days_row = f'<div class="chips day-links">{"".join(day_links)}</div>' if len(day_links) > 1 else ""
    bar = (f'<div class="filters theme-filters">{days_row}<div class="chips">{"".join(chips)}</div>'
           f'<p class="t-empty" hidden>Aucune animation avec ces filtres.</p></div>')
    days_html = f'<div class="theme-list">{bar}{"".join(blocks)}</div>'
    if not items:
        days_html = ('<section class="section"><p class="lead">Le programme n\'est pas encore publié : '
                     'reviens bientôt, cette page est mise à jour chaque jour.</p></section>')
    related = [f for f in FESTIVALS if f["slug"] in fest.get("related", [])]
    related_html = "".join(
        f'<a class="panel fest-related" href="{root}{f["slug"]}/"><b>{e(f["name"])} {f["first"].year}</b>'
        f'<span>{e(f["teaser"])}</span><span class="fest-more">Voir le programme →</span></a>' for f in related)
    # Pages de Noël : lien vers le Secret Santa en ligne (04/11/2026).
    if fest["slug"] in ("marches-de-noel", "fete-des-lumieres"):
        related_html += (f'<a class="panel fest-related" href="{root}secret-santa/" data-goatcounter-click="clic-fete-secret-santa">'
                         '<b>🎅 Secret Santa en ligne</b><span>Tirage au sort gratuit des cadeaux, révélé dans une '
                         'bande-annonce de film : famille, collègues, amis.</span><span class="fest-more">Organiser le mien →</span></a>')
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
    # Aperçu de partage fixe (24/11/2026, Paul) : la première affiche venue
    # donnait n'importe quoi (une vente d'huîtres en aperçu des marchés de
    # Noël). Image propre à la page si `og_image` (1200 × 630, dans static/),
    # sinon l'image Wavents de l'accueil.
    image = f"{BASE_URL}/{fest['og_image']}" if fest.get("og_image") else f"{BASE_URL}/img/og.png?v=3"
    intro = (f"{fest['name']} {year}, {fest.get('when', span).lower() if fest.get('when') else span} : "
             "c'est quoi, où, et tout le programme jour par jour, mis à jour chaque jour.")
    lead = fest["lead"]
    if fest.get("hook") and items:
        lead = fest["hook"].format(n=len(items))
        intro = f"{lead} Programme jour par jour, mis à jour chaque jour."
    story_block = (f'<section class="section"><div class="panel soft fest-story">{story_html}</div>'
                   + ("" if fest.get("story_after") else f'<dl class="fest-facts">{facts_html}</dl>') + '</section>')
    facts_block = f'<section class="section"><dl class="fest-facts">{facts_html}</dl></section>'
    title = (fest["title"].format(year=year, n=len(items)) if fest.get("title") and items
             else f"{fest['name']} {year} : programme et dates")
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}">← Accueil</a>
    <p class="kicker">Grand rendez-vous</p>
    <h1>{e(fest['name'])} {year}</h1>
    <p class="lead">{e(lead)}</p>
    <div class="hero-actions">{play_cta()}</div>
  </div></section>
  {facts_block if fest.get("story_after") else story_block}
  {related_html}
  {days_html}
  {nearby_html}
  {story_block if fest.get("story_after") else ""}
{lettre_section(root, "fete-" + fest["slug"])}
  <section class="section prose"><h2>Questions fréquentes</h2>{faq_html}</section>
  <section class="section"><div class="town-links">{links_html}</div></section>"""
    return layout(
        f"{title} – Wavents",
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
        ("Comment trouver une date pour sortir entre amis ?",
         f"Avec l'outil gratuit « On se fait ça quand ? » ({BASE_URL}/trouver-une-date/) : propose quelques dates, "
         "envoie le lien au groupe, chacun coche ses dispos et la meilleure date ressort toute seule. Pour chaque "
         "date, il montre les sorties du jour près du lieu choisi. Sans compte et sans publicité."),
        ("Wavents propose-t-il un Secret Santa ?",
         f"Oui : {BASE_URL}/secret-santa/ fait le tirage au sort des cadeaux en ligne, gratuitement et sans compte, "
         "et chacun découvre qui il gâte dans une bande-annonce de film."),
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
        "## Outil gratuit : Secret Santa en ligne",
        "",
        f"- [Secret Santa en ligne gratuit]({BASE_URL}/secret-santa/) : tirage au sort de cadeaux pour la famille,"
        " les collègues ou les amis, sans compte, sans e-mail et sans publicité. On saisit le nom du groupe,"
        " la date, le lieu, le budget, les participants et les couples qui ne doivent pas se tirer ;"
        " chacun reçoit son propre lien (WhatsApp, SMS, mail) et découvre qui il gâte dans une bande-annonce"
        " de film animée (suspense, roulement de tambour, feu d'artifice). Rien n'est enregistré sur un serveur :"
        " le tirage se fait dans le navigateur et chaque lien ne contient que le tirage de la personne qui le reçoit."
        " Fonctionne sur téléphone, tablette et ordinateur, pour 3 participants ou plus, aussi à distance.",
        f"- [Secret Santa entre collègues : règles et conseils]({BASE_URL}/secret-santa/collegues/)",
        f"- [Idées de cadeaux Secret Santa à 10, 20 et 30 €]({BASE_URL}/secret-santa/idees-cadeaux/)",
        f"- [Organiser un Secret Santa à distance]({BASE_URL}/secret-santa/a-distance/)",
        "",
        "## Outil gratuit : trouver une date commune entre amis",
        "",
        f"- [On se fait ça quand ? Sondage de dates gratuit]({BASE_URL}/trouver-une-date/) : alternative simple"
        " à Doodle pour les sorties entre amis, en famille ou en association, sans compte, sans e-mail et sans"
        " publicité. L'organisateur indique l'activité, l'emplacement, les dates possibles et le moment (journée,"
        " midi, soir) ; chacun donne son prénom et coche dispo, peut-être ou non pour chaque date. La meilleure"
        " date est mise en avant ; seul le téléphone qui a répondu peut modifier sa réponse. Pour chaque date,"
        " « Voir les sorties » montre les événements du jour près du lieu (là où Wavents est présent), avec"
        " catégorie, adresse et prix, et chacun peut proposer une sortie au groupe. L'organisateur choisit la date"
        " puis prévient le groupe par un message WhatsApp prêt à envoyer.",
        f"- [Organiser une soirée entre amis quand personne n'est dispo]({BASE_URL}/trouver-une-date/soiree-entre-amis/)",
        f"- [Trouver une date pour un repas de famille ou une réunion d'association]({BASE_URL}/trouver-une-date/repas-de-famille/)",
        f"- [Alternative à Doodle gratuite et sans compte]({BASE_URL}/trouver-une-date/alternative-doodle/)",
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


def demos_section(root: str = "", kicker: str = "En 6 secondes chacune") -> str:
    """Un téléphone par vidéo (Paul, 20/10) : celle qui est en avant joue,
    puis la suivante prend le relais toute seule (app.js). Cadre de
    téléphone comme celui du haut de l'accueil (audit du 20/11/2026) ;
    carrousel sur téléphone, rangée de 4 sur ordinateur. Images d'attente
    chargées seulement à l'approche de la rubrique (data-poster)."""
    phones = "".join(
        f'<figure class="dphone{" on" if i == 0 else ""}" data-demo="{k}"><div class="vphone">'
        f'<video muted playsinline preload="none" data-poster="{root}videos/{k}.jpg" width="360" height="640" aria-label="{e(t)}">'
        f'<source src="{root}videos/{k}.webm" type="video/webm"><source src="{root}videos/{k}.mp4" type="video/mp4"></video>'
        f'</div><figcaption>{e(t)}</figcaption></figure>'
        for i, (k, t) in enumerate(DEMOS))
    dots = "".join(f'<button type="button" aria-label="Vidéo {i + 1}"{" class=on" if i == 0 else ""}></button>'
                   for i in range(len(DEMOS)))
    return f"""  <section class="section demo-section" id="ce-que-fait-l-appli">
    <div class="section-head reveal"><div><p class="kicker">{e(kicker)}</p><h2>Ce que fait l'appli</h2></div>
      <div class="scroller-nav"><button data-demo-step="-1" aria-label="Précédente">←</button><button data-demo-step="1" aria-label="Suivante">→</button></div></div>
    <div class="demo-track reveal">{phones}</div>
    <div class="demo-dots">{dots}</div>
  </section>
"""


LETTRE_NOTE = ("Une lettre par semaine, le jeudi, gratuite. Tu confirmes ton adresse par mail, et tu te "
               "désinscris en un clic. Ton adresse ne sert qu'à ça.")


def lettre_form(root: str, canal: str, *, rayon: bool = False, commune: str = "") -> str:
    """Formulaire d'inscription à la lettre du jeudi (static/lettre.js,
    fonction lettre_inscrire du dépôt Wavents, script SQL 239)."""
    commune_value = f' value="{e(commune)}"' if commune else ""
    rayon_html = ("" if not rayon else
                  '<label class="lettre-f lettre-rayon"><span>Jusqu\'à</span><select name="rayon">'
                  '<option value="10">10 km</option><option value="20" selected>20 km</option>'
                  '<option value="30">30 km</option></select></label>')
    return (f'<form class="lettre-form" data-canal="{e(canal)}" data-supabase="{SUPABASE_URL}" '
            f'data-key="{SUPABASE_ANON_KEY}" data-communes="{root}data/communes.json" novalidate>'
            '<div class="lettre-fields">'
            '<label class="lettre-f"><span>Ton e-mail</span><input type="email" name="email" required '
            'autocomplete="email" inputmode="email" placeholder="toi@exemple.fr" maxlength="254"></label>'
            '<label class="lettre-f"><span>Ta commune</span><input name="commune" required autocomplete="address-level2" '
            f'placeholder="Belleville, Beaujeu…" maxlength="80" list="lettre-communes-{e(canal)}"'
            f'{commune_value}>'
            f'<datalist id="lettre-communes-{e(canal)}"></datalist></label>'
            f'{rayon_html}</div>'
            '<input name="site_web" tabindex="-1" autocomplete="off" class="piege" aria-hidden="true">'
            '<button class="btn btn-sun lettre-go" type="submit">Je m\'inscris</button>'
            '<p class="lettre-status" aria-live="polite"></p>'
            f'<p class="lettre-note">{e(LETTRE_NOTE)} <a href="{root}confidentialite.html#lettre">Confidentialité</a></p>'
            f'</form><script src="{root}lettre.js" defer></script>')


def lettre_section(root: str, canal: str) -> str:
    """Bloc « La lettre du jeudi » des pages d'accueil et de fête."""
    return f"""  <section class="section lettre-section" id="lettre">
    <div class="lettre-card reveal">
      <span class="feedback-emoji lettre-emoji" aria-hidden="true">💌</span>
      <div class="lettre-body">
        <p class="kicker">La lettre du jeudi</p>
        <h2>Ce week-end autour de chez toi, dans ta boîte mail</h2>
        <p>Chaque jeudi, les meilleures sorties du week-end près de ta commune&nbsp;: concerts, fêtes, marchés, spectacles. Rien à installer.</p>
        {lettre_form(root, canal)}
      </div>
    </div>
  </section>
"""


def lettre_page() -> str:
    root = "../"
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="{root}">← Accueil</a>
    <p class="kicker">Gratuit, chaque jeudi</p>
    <h1>La lettre du jeudi</h1>
    <p class="lead">Ce week-end autour de chez toi&nbsp;: les meilleures sorties près de ta commune, dans ta boîte mail, chaque jeudi.</p>
  </div></section>
  <main class="section lettre-page">
    <div class="lettre-card">
      <span class="feedback-emoji lettre-emoji" aria-hidden="true">💌</span>
      <div class="lettre-body">
        <h2>Inscris-toi en dix secondes</h2>
        {lettre_form(root, "lettre", rayon=True)}
      </div>
    </div>
    <div class="panel soft lettre-what">
      <h2>Ce que tu reçois</h2>
      <ul>
        <li>Chaque jeudi, 5 à 8 sorties du week-end près de chez toi, choisies parmi tout ce que Wavents trouve dans les agendas du coin.</li>
        <li>Pour chacune&nbsp;: la photo, le jour, l'heure, le lieu et le lien vers sa fiche.</li>
        <li>Le temps fort du moment (marchés de Noël, Fête des Lumières…) avec tout son programme.</li>
        <li>Pas de pub, pas de revente de ton adresse. Pour arrêter, un clic en bas de chaque lettre.</li>
      </ul>
      <p>Une lettre à faire suivre&nbsp;: transfère-la à tes voisins, à ta famille, à tes amis.</p>
    </div>
  </main>"""
    return layout("La lettre du jeudi : ce week-end autour de chez toi – Wavents", body,
                  description="Chaque jeudi, les meilleures sorties du week-end près de chez toi dans le Beaujolais et le "
                              "nord de Lyon, dans ta boîte mail. Gratuit, désinscription en un clic.",
                  url=f"{BASE_URL}/lettre/", depth=1, page_class="town-page")


def lettre_action_page(kind: str) -> str:
    """Pages /lettre/confirmer/ et /lettre/desinscription/ (jeton dans
    l'adresse, ?j=…), non indexées. La confirmation se fait à l'ouverture ;
    la désinscription demande un clic (les antivirus de messagerie ouvrent
    parfois les liens tout seuls)."""
    root = "../../"
    if kind == "confirmer":
        title, h1 = "Confirmer mon inscription – Wavents", "Ton inscription à la lettre du jeudi"
        inner = '<p class="lettre-status busy" id="lettre-msg" aria-live="polite">Confirmation en cours…</p>'
    else:
        title, h1 = "Me désinscrire – Wavents", "Te désinscrire de la lettre du jeudi"
        inner = ('<p id="lettre-q">Tu ne recevras plus la lettre du jeudi, et ton adresse sera effacée.</p>'
                 '<button class="btn btn-sun" type="button" id="lettre-go">Me désinscrire</button>'
                 '<p class="lettre-status" id="lettre-msg" aria-live="polite"></p>')
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">La lettre du jeudi</p>
    <h1>{e(h1)}</h1>
  </div></section>
  <main class="section lettre-page">
    <div class="panel lettre-action" id="lettre-action" data-kind="{kind}" data-supabase="{SUPABASE_URL}" data-key="{SUPABASE_ANON_KEY}">
      {inner}
      <p class="lettre-after" id="lettre-after" hidden><a class="btn btn-ghost" href="{root}#week-end">Voir les sorties du week-end</a></p>
    </div>
  </main>
  <script src="{root}lettre.js" defer></script>"""
    return layout(title, body, description="La lettre du jeudi de Wavents.",
                  url=f"{BASE_URL}/lettre/{kind}/", depth=2, page_class="town-page",
                  extra_head='<meta name="robots" content="noindex">')


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
    // Redirection vers le store comptée comme événement (29/10 : sans clic
    // sur un badge, le mail affichait « aucun clic vers les stores »).
    new Image().src = "https://wavents.goatcounter.com/count?e=true&p=" + (dest === t.android ? "redir-google-play" : "redir-app-store")
      + "&rnd=" + Math.random();
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
{demos_section("../")}
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


# --- Kit organisateur (plan PDG du 20/11/2026, pari n° 1) -----------------------
# Une page par organisateur joignable (contact lu dans ses fiches : email,
# site, page Facebook ; règles dans organizer_key, script 241 du dépôt
# Wavents) : /o/<slug>/ pour le public (ses sorties, cible du QR code) et
# /o/<slug>/kit/ pour lui (vues, visuel carré, QR code, texte à copier).
# Pas dans le plan du site et « noindex » au départ. Les emails ne sortent
# jamais de la base : la fonction ne renvoie que le slug et un libellé
# (domaine ou page Facebook ; rien pour un email personnel).

KIT_MIN_VIEWS = 5  # comme « Les plus vues » de l'appli : jamais « vue 1 fois »
NOINDEX = '<meta name="robots" content="noindex, nofollow">'


def fetch_organizer_kits() -> list[dict]:
    """Lignes (slug, label, kind, event_id, viewers) de organizer_kits_public.
    En cas d'échec, rien : le site part quand même, sans pages /o/."""
    rows: list[dict] = []
    offset, page = 0, 1000
    try:
        while True:
            request = urllib.request.Request(
                f"{SUPABASE_URL}/rest/v1/rpc/organizer_kits_public?order=event_id&limit={page}&offset={offset}",
                data=b"{}",
                headers={"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
                         "Content-Type": "application/json"})
            with urlopen_gz(request, timeout=60) as response:
                batch = json.load(response)
            rows.extend(batch)
            if len(batch) < page:
                return rows
            offset += page
    except Exception as exc:  # noqa: BLE001 — le site ne doit pas tomber pour ça
        print(f"organizer_kits_public : {exc}", file=sys.stderr)
        return []


def organizer_groups(rows: list[dict], events: list[dict]) -> dict[str, dict]:
    """slug -> {label, kind, events (triées), views {id: n}} ; seulement les
    sorties encore sur le site."""
    by_id = {ev["id"]: ev for ev in events}
    groups: dict[str, dict] = {}
    for row in rows:
        ev = by_id.get(row["event_id"])
        slug = row.get("slug") or ""
        if not ev or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", slug):
            continue
        g = groups.setdefault(slug, {"label": row.get("label"), "kind": row.get("kind"), "events": [], "views": {}})
        g["events"].append(ev)
        g["views"][ev["id"]] = int(row.get("viewers") or 0)
    for g in groups.values():
        g["events"].sort(key=lambda ev: (wall_time(ev["start_date"]), ev["title"]))
    return groups


def organizer_path(slug: str) -> str:
    return f"o/{slug}/"


def organizer_name(group: dict) -> str | None:
    return group["label"] if group["kind"] in ("site", "facebook") and group.get("label") else None


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def organizer_page(slug: str, group: dict, series: dict[str, list[dict]]) -> str:
    """Page publique (cible du QR code et du lien partagé)."""
    root = "../../"
    name = organizer_name(group)
    evs = collapse_series(group["events"])
    title = f"Les prochaines sorties de {name}" if name else "Nos prochaines sorties"
    lead = (f"{plural(len(evs), 'sortie à venir', 'sorties à venir')}, mises à jour chaque jour sur Wavents, "
            f"l'agenda gratuit des sorties du Beaujolais.")
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker light">Sur Wavents</p>
    <h1 class="kit-h1">{e(title)}</h1>
    <p class="lead">{e(lead)}</p>
    <div class="hero-actions">{play_cta('light')}</div>
  </div></section>
  <section class="section"><div class="rows">{"".join(row_card(ev, series, root) for ev in evs)}</div></section>
  <section class="section prose kit-foot"><p>Tu organises ces sorties&nbsp;? <a href="kit/">Ton kit pour les partager</a>.</p></section>
"""
    return layout(f"{title} – Wavents", body, description=lead, url=f"{BASE_URL}/{organizer_path(slug)}",
                  extra_head=NOINDEX, depth=2, page_class="town-page kit-page")


def short_when(ev: dict, long: bool = False) -> str:
    """« Sam. 22 nov. », ou « Jusqu'au 30 nov. » si la sortie a déjà commencé."""
    start, end = wall_time(ev["start_date"]), end_of(ev)
    months = MONTHS if long else MONTHS_SHORT
    if start.date() < datetime.now(PARIS).date() <= end.date():
        return f"Jusqu'au {end.day} {months[end.month - 1]}"
    days = DAYS if long else DAYS_SHORT
    return f"{cap(days[start.weekday()])} {start.day} {months[start.month - 1]}"


def share_text(slug: str, group: dict) -> str:
    """Texte prêt à coller sur Facebook : trois prochaines dates et le lien."""
    lines = ["📍 Retrouve toutes nos sorties sur Wavents, l'appli gratuite des sorties du Beaujolais :"]
    for ev in collapse_series(group["events"])[:3]:
        lines.append(f"• {short_when(ev, long=True)} : {ev['title']}")
    lines.append(f"👉 {BASE_URL}/{organizer_path(slug)}")
    return "\n".join(lines)


def kit_page(slug: str, group: dict, series: dict[str, list[dict]]) -> str:
    """Kit de l'organisateur (lien du mail) : ses chiffres, un visuel carré
    et un QR code dessinés dans le navigateur (static/kit.js, mêmes couleurs
    et police que l'image de partage share-card), le texte à copier."""
    root = "../../../"
    name = organizer_name(group)
    evs = collapse_series(group["events"])
    total = sum(group["views"].values())
    page_url = f"{BASE_URL}/{organizer_path(slug)}"
    facts = plural(len(evs), "sortie à venir", "sorties à venir")
    if total >= KIT_MIN_VIEWS:
        facts += f", vues {total} fois"
    lead = f"{facts} sur Wavents{' pour ' + name if name else ''}. Voici de quoi les partager, c'est gratuit."
    data = {"url": page_url, "name": name, "events": [{"when": short_when(ev), "title": ev["title"]} for ev in evs[:4]]}
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    rows = []
    for ev in evs:
        n = group["views"].get(ev["id"], 0)
        views = f'<span class="kit-views">👁 {n} vues</span>' if n >= KIT_MIN_VIEWS else ""
        rows.append(f'<div class="kit-row">{row_card(ev, series, root)}{views}</div>')
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <a class="back" href="../">← Ta page publique</a>
    <p class="kicker light">Kit organisateur</p>
    <h1 class="kit-h1">Tes sorties sont sur Wavents</h1>
    <p class="lead">{e(lead)}</p>
  </div></section>
  <section class="section kit">
    <div class="panel kit-panel">
      <h2>Ton visuel à partager</h2>
      <p>Une image carrée pour ta page Facebook, Instagram ou WhatsApp, avec tes prochaines dates et un QR code vers ta page.</p>
      <div class="kit-visual"><canvas id="kit-canvas" width="1080" height="1080" aria-label="Visuel « Retrouve nos sorties sur Wavents »"></canvas></div>
      <div class="kit-actions">
        <button class="btn btn-sun" type="button" id="kit-download" data-goatcounter-click="kit-visuel">Télécharger l'image</button>
        <button class="btn btn-ghost" type="button" id="kit-share" hidden>Partager</button>
      </div>
    </div>
    <div class="panel kit-panel">
      <h2>Ton QR code</h2>
      <p>À imprimer sur tes affiches et tes flyers : il ouvre ta page Wavents, toujours à jour.</p>
      <div class="kit-qr"><canvas id="kit-qr" width="600" height="600" aria-label="QR code vers ta page Wavents"></canvas></div>
      <p class="kit-link"><a href="../">{e(page_url)}</a></p>
      <div class="kit-actions"><button class="btn btn-ghost" type="button" id="kit-qr-download" data-goatcounter-click="kit-qr">Télécharger le QR code</button></div>
    </div>
    <div class="panel kit-panel">
      <h2>Le texte pour Facebook</h2>
      <textarea id="kit-text" class="kit-text" rows="8" readonly>{e(share_text(slug, group))}</textarea>
      <div class="kit-actions"><button class="btn btn-sun" type="button" id="kit-copy" data-goatcounter-click="kit-texte">Copier le texte</button>
      <span class="kit-status" id="kit-status" aria-live="polite"></span></div>
    </div>
  </section>
  <section class="section">
    <div class="section-head"><div><p class="kicker">{e(facts)}</p><h2>Tes sorties</h2></div></div>
    <div class="rows">{"".join(rows)}</div>
  </section>
  <section class="section prose kit-foot">
    <h2>Une erreur, une sortie qui manque&nbsp;?</h2>
    <p><a href="{root}ajouter/">Ajoute une sortie</a> en deux minutes (une photo de l'affiche suffit), ou écris-nous à
    <a href="mailto:hello@wavents.fr?subject={quote('Correction ' + page_url)}">hello@wavents.fr</a> pour corriger une fiche.
    C'est gratuit, et ça le restera.</p>
  </section>
  <script type="application/json" id="kit-data">{data_json}</script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js" defer></script>
  <script src="{root}kit.js" defer></script>
"""
    return layout("Ton kit organisateur – Wavents", body, description=lead, url=f"{page_url}kit/",
                  extra_head=NOINDEX, depth=3, page_class="town-page kit-page")


def unsubscribe_page() -> str:
    """« Ne plus recevoir » des mails organisateurs (lien des mails :
    /o/desinscription/?t=<jeton>, fonction organizer_unsubscribe)."""
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker light">Wavents</p>
    <h1 class="kit-h1">Ne plus recevoir nos messages</h1>
  </div></section>
  <section class="section prose">
    <p id="unsub-status" aria-live="polite">Un instant…</p>
    <p>Tes sorties restent sur Wavents. Pour les corriger ou les retirer : <a href="mailto:hello@wavents.fr">hello@wavents.fr</a>.</p>
  </section>
  <script>
  (function () {{
    var out = document.getElementById("unsub-status");
    var t = (location.search.match(/[?&]t=([0-9a-f-]{{36}})/i) || [])[1];
    if (!t) {{ out.textContent = "Ce lien est incomplet. Écris-nous à hello@wavents.fr, on te retire tout de suite."; return; }}
    fetch("{SUPABASE_URL}/rest/v1/rpc/organizer_unsubscribe", {{
      method: "POST",
      headers: {{ "apikey": "{SUPABASE_ANON_KEY}", "Authorization": "Bearer {SUPABASE_ANON_KEY}", "Content-Type": "application/json" }},
      body: JSON.stringify({{ p_token: t }})
    }}).then(function (r) {{ return r.json(); }}).then(function (ok) {{
      out.textContent = ok === true ? "C'est fait : tu ne recevras plus de message de Wavents."
        : "Ce lien n'est plus valable. Écris-nous à hello@wavents.fr, on te retire tout de suite.";
    }}).catch(function () {{
      out.textContent = "Ça n'a pas marché. Écris-nous à hello@wavents.fr, on te retire tout de suite.";
    }});
  }})();
  </script>
"""
    return layout("Ne plus recevoir nos messages – Wavents", body, description="Désinscription des messages de Wavents.",
                  url=f"{BASE_URL}/o/desinscription/", extra_head=NOINDEX, depth=2, page_class="town-page")


def write_organizer_pages(out: Path, events: list[dict], series: dict[str, list[dict]]) -> int:
    groups = organizer_groups(fetch_organizer_kits(), events)
    for slug, group in groups.items():
        folder = out / organizer_path(slug)
        (folder / "kit").mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(organizer_page(slug, group, series), encoding="utf-8")
        (folder / "kit" / "index.html").write_text(kit_page(slug, group, series), encoding="utf-8")
    (out / "o" / "desinscription").mkdir(parents=True, exist_ok=True)
    (out / "o" / "desinscription" / "index.html").write_text(unsubscribe_page(), encoding="utf-8")
    return len(groups)


# --- Kits de diffusion (20/11/2026) : affiches des commerçants, séjour --------
# Plan du 20/11 (dépôt Wavents, docs/plan_pdg_2026-11-20.md) : visibilité
# gratuite par les commerçants (affiche en vitrine) et les hébergeurs (QR
# dans la chambre). Pages noindex, hors plan du site. Les affiches ne
# portent aucune date ni aucun chiffre : imprimées une fois, elles restent
# justes ; le QR ouvre une page régénérée chaque jour. Communes : les 10 qui
# avaient le plus de sorties à venir à moins de 15 km de Belleville le
# 20/11/2026 (pages /ville/). Liste figée exprès : un QR imprimé ne doit pas
# changer de cible. QR (static/affiche/qr/, static/sejour/qr.svg) et PDF
# (static/affiche/pdf/, static/sejour/) : outils/kits_qr.py et
# outils/kits_pdf.py, à relancer seulement si cette liste ou le dessin change.
AFFICHE_COMMUNES = [
    "Villefranche-sur-Saône", "Belleville-en-Beaujolais", "Beaujeu", "Montmerle-sur-Saône",
    "Saint-Didier-sur-Chalaronne", "Arnas", "Saint-Georges-de-Reneins", "Thoissey", "Jassans-Riottier",
    "Saint-Lager",
]
SEJOUR_RADIUS_KM = 20


def affiche_target(name: str) -> str:
    """Cible du QR de l'affiche : la page de la commune, avec le canal."""
    return f"{BASE_URL}/{commune_path(name)}?ref=affiche"


SEJOUR_TARGET = f"{BASE_URL}/sejour/?ref=sejour"

PRINT_HEAD = """<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{description}">
  <meta name="robots" content="noindex, nofollow">
  <meta name="theme-color" content="#065FBB">
  <link rel="icon" href="{root}favicon.ico" sizes="48x48">
  <link rel="icon" type="image/svg+xml" sizes="any" href="{root}img/favicon.svg">
  <link rel="stylesheet" href="{root}affiche/affiche.css">
</head>
<body class="print-page">
"""


def _print_bar(root: str, back: str, back_label: str, pdf: str, extra: str = "") -> str:
    return (f'<div class="print-bar no-print"><a class="pb-back" href="{back}">← {e(back_label)}</a>'
            f'<div class="pb-actions">{extra}<button class="pb-btn" type="button" onclick="window.print()" '
            f'data-goatcounter-click="imprimer">🖨 Imprimer</button>'
            f'<a class="pb-btn ghost" href="{pdf}" download data-goatcounter-click="telecharger-pdf">Télécharger le PDF</a></div>'
            f'<p class="pb-note">Format A4, sans marge à régler : « Imprimer », puis l\'imprimante ou « Enregistrer en PDF ».</p></div>')


def _print_tail(root: str) -> str:
    return (f'  <script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>\n'
            '</body>\n</html>\n')


def _name_class(name: str) -> str:
    """Taille du nom de commune selon sa longueur : jamais tronqué, coupé
    au besoin après un trait d'union."""
    n = len(name)
    return "n-s" if n <= 10 else ("n-m" if n <= 17 else "n-l")


def affiche_page(name: str) -> str:
    """Affiche A4 « Que faire ce week-end autour de <commune> ? » pour les
    vitrines : dessin en unités relatives à la feuille (cqw), même rendu à
    l'écran (aperçu) et à l'impression."""
    root = "../../"
    slug = slugify(name)
    title = f"Affiche « Que faire ce week-end autour de {name} ? » – Wavents"
    sheet = f"""<main class="sheet-wrap"><article class="sheet poster" aria-label="Affiche A4">
  <header class="p-top">
    <div class="p-brand"><img src="{root}img/pin.png" alt="" width="56" height="56"><span>Wavents</span></div>
    <h1 class="p-title">Que faire ce week-end<br>autour de <em class="{_name_class(name)}">{e(name)}&nbsp;?</em></h1>
    <p class="p-chips"><span>Concerts</span><span>Marchés</span><span>Brocantes</span><span>En famille</span></p>
  </header>
  <section class="p-body">
    <div class="p-qr"><img src="{root}affiche/qr/{slug}.svg" alt="QR code vers les sorties autour de {e(name)}"><b>Scanne-moi</b></div>
    <div class="p-text">
      <p class="p-lead">Toutes les sorties près d'ici.</p>
      <ul class="p-points"><li>Mises à jour chaque jour</li><li>Gratuit, sans compte</li><li>Rien à installer</li></ul>
      <p class="p-url">ou sur wavents.fr</p>
    </div>
  </section>
  <footer class="p-foot">Une animation à annoncer&nbsp;? C'est gratuit : <b>wavents.fr/ajouter</b></footer>
</article></main>"""
    return (PRINT_HEAD.format(title=e(title), description=e(f"Affiche à imprimer pour les vitrines de {name}."), root=root)
            + _print_bar(root, "../", "Toutes les affiches", f"{root}affiche/pdf/wavents-affiche-{slug}.pdf")
            + sheet + _print_tail(root))


def affiches_index() -> str:
    root = "../"
    links = "".join(
        f'<a class="town-link" href="{slugify(n)}/">{e(n)}</a>' for n in AFFICHE_COMMUNES)
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker light">Pour les commerçants</p>
    <h1 class="kit-h1">Une affiche pour ta vitrine</h1>
    <p class="lead">«&nbsp;Que faire ce week-end autour de chez nous&nbsp;?&nbsp;»&nbsp;: une affiche A4 avec un QR code qui ouvre
    les sorties de ta commune, toujours à jour. Gratuite, à imprimer toi-même.</p>
  </div></section>
  <section class="section"><div class="section-head"><div><p class="kicker">Choisis ta commune</p><h2>Les affiches</h2></div></div>
    <div class="town-links">{links}</div>
    <p class="kit-note" style="margin-top:14px;color:var(--ink-soft)">Ta commune n'est pas dans la liste&nbsp;? Écris-nous à <a href="mailto:hello@wavents.fr?subject=Affiche%20Wavents">hello@wavents.fr</a>, on la prépare.</p>
  </section>
  <section class="section prose">
    <h2>Le principe</h2>
    <p>Tu poses l'affiche en vitrine ou sur ton comptoir. En échange, on met en ligne gratuitement tes animations
    (dégustations, dédicaces, marchés, soirées) : envoie-les sur <a href="{root}ajouter/">wavents.fr/ajouter</a>,
    une photo de ton affiche suffit.</p>
    <h2>Tu loues un gîte, des chambres, un emplacement&nbsp;?</h2>
    <p>Il y a aussi <a href="{root}sejour/imprimer/">une carte pour tes chambres</a>&nbsp;: «&nbsp;Que faire autour d'ici cette semaine&nbsp;?&nbsp;».</p>
  </section>
"""
    return layout("Affiches pour les commerçants – Wavents", body,
                  description="Affiches A4 gratuites « Que faire ce week-end autour de chez nous ? » avec QR code.",
                  url=f"{BASE_URL}/affiche/", extra_head=NOINDEX, depth=1, page_class="town-page kit-page")


def sejour_card(root: str) -> str:
    """Face du kit séjour pour les vacanciers (refaite le 23/11/2026 avec
    Paul : dire à un touriste à quoi ça sert, le vouvoyer). Un seul dessin,
    deux styles (classe de la feuille) : « photos » (vraies photos d'ici,
    3 étapes) et « vague » (couleurs de l'appli, gros QR)."""
    photos = "".join(f'<div style="background-image:url({root}categories/{n}.jpg)"></div>'
                     for n in ("fete", "marche", "concert"))
    return f"""<article class="s-card">
    <div class="s-photos" aria-hidden="true">{photos}</div>
    <div class="s-body">
      <div class="s-head"><div class="s-brand"><img src="{root}img/pin.png" alt="" width="56" height="56"><span>Wavents</span></div>
        <p class="s-host" hidden>Avec les compliments de <b class="s-host-name"></b></p></div>
      <h1 class="s-title">Que faire autour d'ici <em>cette semaine</em>&nbsp;?</h1>
      <p class="s-what">L'agenda de <b>tout ce qui se passe autour de votre hébergement</b>&nbsp;: fêtes de village,
        marchés, dégustations, concerts, sorties en famille. Avec l'heure, le lieu et l'itinéraire.</p>
      <p class="s-free">Gratuit · sans compte · rien à installer</p>
      <p class="s-pills"><span>🎉 Fêtes</span><span>🎵 Concerts</span><span>🧺 Marchés</span><span>🏰 Visites</span><span>👨‍👩‍👧 En famille</span></p>
      <div class="s-row">
        <div class="s-qr"><img src="{root}sejour/qr.svg" alt="QR code vers les sorties de la semaine"><b>Scannez-moi</b></div>
        <div class="s-side">
          <ol class="s-steps"><li><b>1</b>Scannez</li><li><b>2</b>Choisissez une sortie près d'ici</li><li><b>3</b>Itinéraire en un geste</li></ol>
          <p class="s-url">wavents.fr/sejour</p>
          <p class="s-bon">Bon séjour&nbsp;!</p>
        </div>
      </div>
    </div>
    <svg class="s-wave" viewBox="0 0 794 90" preserveAspectRatio="none" aria-hidden="true"><path d="M0,30 C150,60 300,0 470,25 C600,45 700,20 794,30 L794,90 L0,90 Z" fill="#fff"/></svg>
    <p class="s-foot"><b>wavents.fr/sejour</b> Gratuit, sans compte, rien à installer</p>
  </article>"""


def sejour_print_page() -> str:
    """Kit séjour à imprimer (20/11, refait le 23/11/2026 avec Paul) : chevalet
    A4 à plier en deux (par défaut, se lit des deux côtés) ou deux petites
    affichettes A5 à scotcher. Nom de l'hébergement et adresse facultatifs,
    tapés avant d'imprimer (jamais enregistrés) : avec l'adresse, le QR ouvre
    la page triée autour de l'hébergement (?p=lat,lon&n=nom). Message
    d'accueil à copier pour Airbnb et Booking."""
    root = "../../"
    field = ('<div class="pb-formats" role="radiogroup" aria-label="Style">'
             '<span class="pb-k">Style</span>'
             '<label><input type="radio" name="sty" value="photos" checked> Photos d\'ici</label>'
             '<label><input type="radio" name="sty" value="vague"> Vague Wavents</label></div>'
             '<div class="pb-formats" role="radiogroup" aria-label="Format">'
             '<span class="pb-k">Format</span>'
             '<label><input type="radio" name="fmt" value="chevalet" checked> Chevalet ou 2 affichettes</label>'
             '<label><input type="radio" name="fmt" value="page"> Page A4 (livret d\'accueil)</label>'
             '<span class="pb-hint" id="fmt-hint">Une feuille A4 : pliez-la en deux pour un chevalet qui tient debout, '
             'ou coupez-la sur le pointillé pour deux affichettes.</span></div>'
             '<label class="pb-field">Votre hébergement (facultatif) <input id="host-name" type="text" maxlength="40" '
             'placeholder="Gîte des Vignes" autocomplete="organization"></label>'
             '<label class="pb-field">Son adresse (facultatif : le QR ouvrira les sorties autour) '
             '<input id="host-addr" type="text" maxlength="120" placeholder="12 rue du Port, Belleville" '
             'autocomplete="street-address"></label><p class="pb-addr" id="host-addr-ok" hidden></p>')
    message = ('<section class="welcome-msg no-print"><h2>Vous louez sur Airbnb ou Booking&nbsp;?</h2>'
               '<p>Rien à imprimer : copiez cette phrase dans votre message d\'accueil ou votre livret numérique.</p>'
               '<textarea id="welcome-text" readonly rows="3">Pour savoir ce qui se passe autour pendant votre séjour '
               '(fêtes, concerts, marchés, sorties en famille), tout est ici, mis à jour chaque jour : '
               'https://wavents.fr/sejour/?ref=accueil</textarea>'
               '<button class="pb-btn" type="button" id="welcome-copy" data-goatcounter-click="copier-message-accueil">'
               'Copier</button></section>')
    sheet = f"""<main class="sheet-wrap"><div class="sheet sejour-sheet fmt-chevalet st-photos" aria-label="Kit séjour à imprimer">
  {sejour_card(root)}
  <div class="s-cut" aria-hidden="true"><span class="s-cut-scissors">✂</span><span class="s-cut-fold">plier ou couper ici</span></div>
  {sejour_card(root)}
</div></main>
{message}
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"></script>
<script>
(function () {{
  var sheet = document.querySelector(".sejour-sheet");
  var pdf = document.querySelector('a[download]');
  var cur = {{ fmt: "chevalet", sty: "photos" }};
  var qs = new URLSearchParams(location.search);
  function apply() {{
    sheet.className = "sheet sejour-sheet fmt-" + cur.fmt + " st-" + cur.sty;
    if (pdf) pdf.href = "{root}sejour/wavents-sejour-" + cur.fmt + "-" + cur.sty + ".pdf";
    var hint = document.getElementById("fmt-hint"); if (hint) hint.hidden = cur.fmt !== "chevalet";
    ["fmt", "sty"].forEach(function (k) {{
      var r = document.querySelector('input[name="' + k + '"][value="' + cur[k] + '"]'); if (r) r.checked = true;
    }});
  }}
  document.querySelectorAll('input[name="fmt"], input[name="sty"]').forEach(function (r) {{
    r.addEventListener("change", function () {{ cur[r.name] = r.value; apply(); }});
  }});
  // « affichettes » : même feuille que le chevalet (Paul, 23/11/2026).
  if (qs.get("f") === "page") cur.fmt = "page";
  if (/^(photos|vague)$/.test(qs.get("s") || "")) cur.sty = qs.get("s");
  apply();
  var name = document.getElementById("host-name"), addr = document.getElementById("host-addr");
  var ok = document.getElementById("host-addr-ok"), coords = null, timer = null;
  var base = "https://wavents.fr/sejour/?ref=sejour";
  function qr() {{
    var url = base;
    if (coords) url += "&p=" + coords[0].toFixed(4) + "," + coords[1].toFixed(4);
    var n = name.value.trim(); if (coords && n) url += "&n=" + encodeURIComponent(n);
    document.querySelectorAll(".s-qr img").forEach(function (img) {{
      if (!coords || !window.qrcode) {{ img.src = "{root}sejour/qr.svg"; return; }}
      var q = window.qrcode(0, "M"); q.addData(url); q.make();
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(q.createSvgTag({{ cellSize: 4, margin: 0, scalable: true }}));
    }});
  }}
  name.addEventListener("input", function () {{
    var v = name.value.trim();
    document.querySelectorAll(".s-host").forEach(function (p) {{ p.hidden = !v; }});
    document.querySelectorAll(".s-host-name").forEach(function (b) {{ b.textContent = v; }});
    qr();
  }});
  addr.addEventListener("input", function () {{
    clearTimeout(timer);
    timer = setTimeout(function () {{
      var q = addr.value.trim();
      if (q.length < 5) {{ coords = null; ok.hidden = true; qr(); return; }}
      fetch("https://data.geopf.fr/geocodage/search?limit=1&q=" + encodeURIComponent(q))
        .then(function (r) {{ return r.json(); }})
        .then(function (d) {{
          var f = d.features && d.features[0];
          if (!f) {{ coords = null; ok.textContent = "Adresse introuvable : le QR ouvre les sorties autour de Belleville."; ok.hidden = false; qr(); return; }}
          coords = [f.geometry.coordinates[1], f.geometry.coordinates[0]];
          ok.textContent = "✓ Le QR ouvrira les sorties autour de : " + f.properties.label; ok.hidden = false; qr();
        }})
        .catch(function () {{ coords = null; ok.hidden = true; qr(); }});
    }}, 500);
  }});
  var copy = document.getElementById("welcome-copy"), text = document.getElementById("welcome-text");
  if (copy) copy.addEventListener("click", function () {{
    text.select();
    (navigator.clipboard ? navigator.clipboard.writeText(text.value) : Promise.reject()).catch(function () {{ document.execCommand("copy"); }});
    copy.textContent = "Copié ✓"; setTimeout(function () {{ copy.textContent = "Copier"; }}, 2000);
  }});
}})();
</script>
"""
    return (PRINT_HEAD.format(title="Kit séjour « Que faire autour d'ici cette semaine ? » – Wavents",
                              description="Chevalet et affichettes à imprimer pour les gîtes, chambres d'hôtes, hôtels et campings.", root=root)
            + _print_bar(root, "../", "Les sorties de la semaine", f"{root}sejour/wavents-sejour-chevalet-photos.pdf", field)
            + sheet + _print_tail(root))


def sejour_page(upcoming: list[dict], series: dict[str, list[dict]], now: datetime) -> str:
    """« Que faire autour de chez nous cette semaine » pour les vacanciers
    (QR des hébergeurs) : 7 jours à moins de SEJOUR_RADIUS_KM de Belleville."""
    pool = [ev for ev in upcoming if wall_time(ev["start_date"]).date() <= now.date() + timedelta(days=6)
            and distance_km(ev["latitude"], ev["longitude"]) <= SEJOUR_RADIUS_KM]
    count = len(collapse_series(pool))
    intro = (f"{count} sorties dans les 7 prochains jours à moins de {SEJOUR_RADIUS_KM} km de "
             "Belleville-en-Beaujolais\u00a0: fêtes, concerts, marchés, visites, sorties en famille. "
             "Mises à jour chaque jour. Bon séjour\u00a0!")
    after = ('<section class="section prose kit-foot"><h2>Tu accueilles des vacanciers&nbsp;?</h2>'
             '<p>Gîte, chambres d\'hôtes, camping, office de tourisme : <a href="imprimer/">imprime le chevalet à QR code</a> '
             'pour tes chambres ou ton accueil, ou copie le message d\'accueil pour Airbnb et Booking. Gratuit, et toujours à jour.</p></section>')
    page, _ = theme_page("sejour", "Que faire autour de chez nous cette semaine ?",
                         "Que faire autour de chez nous cette semaine ?", "Pendant ton séjour",
                         lambda ev: True, 6, upcoming, series, now, radius=SEJOUR_RADIUS_KM, intro=intro,
                         extra_head=NOINDEX, near_chip=True, after=after)
    return page


def write_kit_pages(out: Path, upcoming: list[dict], series: dict[str, list[dict]], now: datetime) -> None:
    (out / "affiche").mkdir(exist_ok=True)
    (out / "affiche" / "index.html").write_text(affiches_index(), encoding="utf-8")
    for name in AFFICHE_COMMUNES:
        folder = out / "affiche" / slugify(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(affiche_page(name), encoding="utf-8")
        # Un QR imprimé ne doit jamais tomber sur une 404 : si la commune
        # n'a plus assez de sorties pour sa page, renvoi vers l'accueil.
        target = out / commune_path(name)
        if not (target / "index.html").exists():
            target.mkdir(parents=True, exist_ok=True)
            (target / "index.html").write_text(
                '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex">'
                '<meta http-equiv="refresh" content="0; url=/"><title>Wavents</title><a href="/">Wavents</a>',
                encoding="utf-8")
    (out / "sejour" / "imprimer").mkdir(parents=True, exist_ok=True)
    (out / "sejour" / "index.html").write_text(sejour_page(upcoming, series, now), encoding="utf-8")
    (out / "sejour" / "imprimer" / "index.html").write_text(sejour_print_page(), encoding="utf-8")


# --- Encart « Sorties près de chez vous » (20/11/2026, PDG 2) ----------------
# Plan PDG 2 (dépôt Wavents, docs/plan_pdg2_session_nad.md), pari n° 1 : un
# encart gratuit qu'un site (mairie, école, club, camping, média local) colle
# en une ligne, qui affiche les prochaines sorties de sa commune et autour.
# Leur public devient le nôtre. Une page statique par commune (`/encart/<slug>/`,
# régénérée avec le site, aucune requête vers la base chez le visiteur), CSS
# en ligne, noindex. Liens vers les fiches avec `?ref=encart-<slug>` (compté
# par app.js dans GoatCounter) ; la page de l'encart est elle-même comptée,
# avec le site hôte en provenance. Page d'installation : `/encart/`.
ENCART_RADIUS_KM = 12
ENCART_DAYS = 21
ENCART_MAX = 12


def encart_events(name: str, center: tuple[float, float], upcoming: list[dict], now: datetime) -> list[dict]:
    """Sorties de la commune d'abord, puis celles à moins de 12 km, sur 3
    semaines, par date ; les longues expositions après les dates fixes."""
    limit = now.date() + timedelta(days=ENCART_DAYS)
    own, near = [], []
    for ev in collapse_series(upcoming):
        start = wall_time(ev["start_date"])
        if start.date() > limit:
            continue
        km = distance_km(ev["latitude"], ev["longitude"], center)
        if slugify(commune_page_name(ev) or "") == slugify(name):
            own.append(ev)
        elif km <= ENCART_RADIUS_KM:
            near.append(ev)
    today = now.date()
    # Ce qui commence aujourd'hui ou après d'abord ; ce qui a déjà commencé à la fin.
    key = lambda ev: (is_long(ev) or wall_time(ev["start_date"]).date() < today, wall_time(ev["start_date"]))
    own.sort(key=key)
    near.sort(key=key)
    picked = own[:8]
    picked += near[:ENCART_MAX - len(picked)]
    return sorted(picked, key=key)


def encart_row(ev: dict, slug: str, now: datetime) -> str:
    _, label, color = category(ev)
    start = wall_time(ev["start_date"])
    if start.date() < now.date():
        end = end_of(ev)
        when = f"jusqu'au {end.day} {MONTHS_SHORT[end.month - 1]}"
    else:
        when = f"{DAYS_SHORT[start.weekday()]} {start.day} {MONTHS_SHORT[start.month - 1]}"
        if ev.get("start_time_known") and (start.hour or start.minute):
            when += f" · {hour_label(start)}"
    tags = []
    if is_free(ev):
        tags.append('<span class="t free">Gratuit</span>')
    if ev.get("for_kids"):
        tags.append('<span class="t kids">En famille</span>')
    href = f"{BASE_URL}/{event_path(ev)}?ref=encart-{slug}"
    return (f'<a class="r" href="{href}" target="_blank" rel="noopener" style="--c:{color}">'
            f'<span class="i" aria-hidden="true"></span>'
            f'<span class="b"><span class="k">{e(label)}</span><b>{e(ev["title"])}</b>'
            f'<span class="m">{e(when)} · {e(commune_page_name(ev) or commune_of(ev))}</span>'
            f'{"".join(tags)}</span></a>')


ENCART_CSS = """
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font:15px/1.35 Roboto,system-ui,-apple-system,"Segoe UI",sans-serif;color:#0b2545;background:#fff}
.w{border:1px solid #d6e2f0;border-radius:14px;overflow:hidden;background:#fff;height:100vh;display:flex;flex-direction:column}
.h{background:#065FBB;color:#fff;padding:10px 14px;display:flex;align-items:center;gap:10px;flex:none}
.h img{width:28px;height:28px;flex:none}
.h div{min-width:0}.h strong{display:block;font-size:16px;line-height:1.2}
.h small{display:block;opacity:.85;font-size:12.5px}
.l{flex:1;overflow-y:auto}
.r{display:flex;gap:10px;align-items:flex-start;padding:9px 14px;border-bottom:1px solid #eef3f9;text-decoration:none;color:inherit}
.r:hover,.r:focus{background:#f3f8fe}
.i{flex:none;width:4px;align-self:stretch;border-radius:4px;background:var(--c)}
.k{display:block;font-size:11.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:var(--c)}
.b{min-width:0;display:block}.b b{display:block;font-weight:700;overflow-wrap:anywhere}
.m{display:block;font-size:13px;color:#46607d;margin-top:1px}
.t{display:inline-block;font-size:11.5px;font-weight:700;border-radius:999px;padding:1px 8px;margin:4px 4px 0 0}
.t.free{background:#e3f6ea;color:#17703a}.t.kids{background:#fff1dc;color:#9a5a00}
.e{padding:18px 14px;color:#46607d}
.f{flex:none;display:flex;flex-wrap:wrap;gap:6px 12px;justify-content:space-between;align-items:center;padding:9px 14px;background:#f3f8fe;font-size:13px}
.f a{color:#065FBB;font-weight:700;text-decoration:none}.f a:hover{text-decoration:underline}
"""


def encart_page(name: str, slug: str, items: list[dict], now: datetime, has_page: bool = True) -> str:
    rows = "".join(encart_row(ev, slug, now) for ev in items) or (
        '<p class="e">Pas de sortie annoncée pour l\'instant. Une idée ? Ajoutez-la gratuitement.</p>')
    town = (f"{BASE_URL}/{commune_path(name)}?ref=encart-{slug}" if has_page
            else f"{BASE_URL}/?ref=encart-{slug}")
    add = f"{BASE_URL}/ajouter/?ref=encart-{slug}"
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sorties à {e(name)} et autour – Wavents</title>
  <meta name="robots" content="noindex, nofollow">
  <style>{ENCART_CSS}</style>
</head>
<body>
  <div class="w">
    <div class="h"><img src="{BASE_URL}/img/pin.png" alt="" width="28" height="28"><div>
      <strong>Sorties à {e(name)} et autour</strong><small>Mis à jour chaque jour par Wavents</small></div></div>
    <div class="l">{rows}</div>
    <div class="f"><a href="{town}" target="_blank" rel="noopener">Toutes les sorties →</a>
      <a href="{add}" target="_blank" rel="noopener">+ Ajouter un événement</a></div>
  </div>
  <script data-goatcounter="https://wavents.goatcounter.com/count" async src="//gc.zgo.at/count.js"></script>
</body>
</html>
"""


def encart_install_page(choices: list[tuple[str, str, int]]) -> str:
    options = "".join(f'<option value="{slug}">{e(name)}</option>' for name, slug, n in choices)
    first = choices[0][1] if choices else ""
    body = f"""  <section class="ev-hero town-hero"><div class="ev-hero-inner">
    <p class="kicker">Mairies, écoles, associations, campings, médias</p>
    <h1>L'agenda des sorties de votre commune, gratuit, sur votre site</h1>
    <p class="lead">Un encart qui affiche les prochaines sorties de votre commune et des alentours,
      mis à jour chaque jour, sans rien à saisir. Gratuit, sans publicité, sans compte.
      Une ligne à copier dans votre site.</p>
  </div></section>
  <section class="section encart-install">
    <div class="encart-steps">
      <h2>1. Choisissez votre commune</h2>
      <select id="encart-commune" aria-label="Commune">{options}</select>
      <h2>2. Copiez ce code dans votre site</h2>
      <p>Dans WordPress, Wix, Jimdo, Joomla, Campagne en ligne… : un bloc « HTML personnalisé » ou « code intégré ».</p>
      <textarea id="encart-code" rows="4" readonly></textarea>
      <button type="button" id="encart-copy" class="btn">Copier le code</button>
      <p>Pas de bloc HTML ? Mettez simplement un lien vers
        <a id="encart-link" href="{BASE_URL}/ville/">la page de votre commune</a>.</p>
      <h2>Questions</h2>
      <p><b>Ça coûte quelque chose ?</b> Non, et ça le restera.</p>
      <p><b>D'où viennent les sorties ?</b> Des agendas publics (communes, offices de tourisme,
        associations, salles) relus chaque jour, et des organisateurs qui les ajoutent sur Wavents.</p>
      <p><b>Une sortie manque ou est fausse ?</b> Ajoutez-la sur <a href="{BASE_URL}/ajouter/">wavents.fr/ajouter</a>
        ou écrivez à <a href="mailto:hello@wavents.fr">hello@wavents.fr</a> : elle apparaît dans l'encart le lendemain.</p>
    </div>
    <div class="encart-preview">
      <h2>Aperçu</h2>
      <iframe id="encart-frame" src="{BASE_URL}/encart/{first}/" title="Aperçu de l'encart" loading="lazy"
        style="width:100%;max-width:420px;height:520px;border:0"></iframe>
    </div>
  </section>
  <script>
  (function(){{
    var sel=document.getElementById('encart-commune'),code=document.getElementById('encart-code'),
        frame=document.getElementById('encart-frame'),link=document.getElementById('encart-link'),
        btn=document.getElementById('encart-copy'),base='{BASE_URL}';
    function up(){{
      var s=sel.value,u=base+'/encart/'+s+'/';
      code.value='<iframe src="'+u+'" title="Sorties près de chez vous – Wavents" loading="lazy" style="width:100%;max-width:480px;height:520px;border:0"></iframe>';
      frame.src=u;link.href=base+'/ville/'+s+'/?ref=encart-lien';
    }}
    sel.addEventListener('change',up);up();
    btn.addEventListener('click',function(){{
      code.select();
      (navigator.clipboard?navigator.clipboard.writeText(code.value):Promise.reject()).catch(function(){{document.execCommand('copy');}});
      btn.textContent='Copié ✓';setTimeout(function(){{btn.textContent='Copier le code';}},2000);
      if(window.goatcounter&&goatcounter.count)goatcounter.count({{path:'encart-copie-'+sel.value,title:'Encart copié',event:true}});
    }});
  }})();
  </script>"""
    return layout(
        "Agenda des sorties gratuit pour le site de votre commune – Wavents",
        body,
        description="Un encart gratuit, sans publicité, qui affiche sur votre site les sorties de votre commune "
                    "et des alentours, mises à jour chaque jour.",
        url=f"{BASE_URL}/encart/",
        depth=1,
        page_class="town-page",
    )


def write_encart_pages(out: Path, communes: dict[str, list[dict]], upcoming: list[dict], now: datetime) -> int:
    # Communes avec une page « Que faire à » (3 sorties à venir ou plus),
    # plus les mairies à moins de 15 km de Belleville (encart_communes.json,
    # centres de geo.api.gouv.fr) : une petite commune sans sortie à elle a
    # quand même un encart, rempli par ses voisines.
    targets = {slugify(name): (name, commune_center(evs), len(evs)) for name, evs in communes.items()}
    for extra in (Path(__file__).parent / "encart_communes.json", Path(__file__).parent / "communes_zone.json"):
        if extra.exists():
            for c in json.loads(extra.read_text(encoding="utf-8")):
                targets.setdefault(slugify(c["name"]), (c["name"], (c["lat"], c["lon"]), 0))
    choices = []
    for slug, (name, center, count) in targets.items():
        items = encart_events(name, center, upcoming, now)
        if not items:
            continue
        folder = out / "encart" / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(encart_page(name, slug, items, now, (out / commune_path(name) / "index.html").exists()),
                                             encoding="utf-8")
        choices.append((name, slug, count))
    choices.sort(key=lambda c: fold(c[0]))
    # Belleville en premier choix : c'est notre commune.
    choices.sort(key=lambda c: c[1] != "belleville-en-beaujolais")
    (out / "encart" / "index.html").write_text(encart_install_page(choices), encoding="utf-8")
    return len(choices)


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
    # Recherche sur toutes les sorties à venir (Paul, 24/11/2026 : « je veux
    # toutes les poteries, peu importe les dates ») : chargée seulement quand
    # on tape dans la recherche de l'accueil.
    (out / "data" / "recherche.json").write_text(search_index(events, canonical_ids, now), encoding="utf-8")

    # Fiches plus en ligne : une page plutôt qu'une 404 (25/10/2026).
    live_ids = {ev["id"] for ev in events}
    communes_now = group_by_commune(upcoming)
    # Repli des fiches terminées hors commune connue : les sorties à venir
    # près de Belleville, avec une vraie photo d'abord.
    weekend_fallback = sorted([x for x in upcoming if not is_long(x) and distance_km(x["latitude"], x["longitude"]) <= 15],
                              key=lambda x: (not real_image(x), x["start_date"]))[:6]
    gone = 0
    for ev in fetch_gone_events():
        folder = out / event_path(ev)
        if ev["id"] in live_ids or folder.exists():
            continue
        town = commune_page_name(ev)
        folder.mkdir(parents=True, exist_ok=True)
        nearby = [x for x in communes_now.get(town, []) if not is_long(x)] if town in communes_now else []
        if len(nearby) < 3:
            nearby = weekend_fallback
        (folder / "index.html").write_text(
            gone_page(ev, commune_path(town) if town in communes_now else None, nearby, series), encoding="utf-8")
        gone += 1
    print(f"{gone} pages de fiches plus en ligne")

    for name, evs in communes.items():
        folder = out / commune_path(name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(commune_page(name, evs, communes, series, now), encoding="utf-8")
    # Toutes les communes à 40 km de Belleville (communes_zone.json, geo.api.gouv.fr,
    # 21/11/2026, idée reprise de Vivop) : celles sans page ont une page
    # « et autour » si au moins 5 sorties sont à moins de 8 km.
    around_names: list[str] = []
    zone_file = Path(__file__).parent / "communes_zone.json"
    have = {slugify(name) for name in communes}
    if zone_file.exists():
        for c in json.loads(zone_file.read_text(encoding="utf-8")):
            if slugify(c["name"]) in have:
                continue
            center = (c["lat"], c["lon"])
            near = [ev for ev in upcoming if distance_km(ev["latitude"], ev["longitude"], center) <= AROUND_KM]
            if len(near) < AROUND_MIN_EVENTS:
                continue
            folder = out / commune_path(c["name"])
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "index.html").write_text(
                commune_page(c["name"], near, communes, series, now, center=center, around=True), encoding="utf-8")
            around_names.append(c["name"])
            have.add(slugify(c["name"]))
    print(f"{len(around_names)} pages communes « et autour »")
    (out / "ville").mkdir(exist_ok=True)
    (out / "ville" / "index.html").write_text(communes_index(communes, around_names), encoding="utf-8")

    markets = market_groups(events)
    for town, evs in markets.items():
        folder = out / market_path(town)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "index.html").write_text(
            market_page(town, evs, markets, commune_path(town) if town in communes else None), encoding="utf-8")
    (out / "marche").mkdir(exist_ok=True)
    (out / "marche" / "index.html").write_text(markets_index(markets), encoding="utf-8")

    theme_counts: dict[str, int] = {}
    rain_paths = rain_pages(out, upcoming, series, now)
    print(f"{len(rain_paths)} pages « quand il pleut » par ville")
    # Lien depuis la page de la commune (pages communes étoffées, 24/11/2026).
    for rp in rain_paths:
        town_index = out / rp / ".." / "index.html"
        if town_index.exists():
            html_text = town_index.read_text(encoding="utf-8")
            town_index.write_text(html_text.replace("<!--pluie-->", (
                '<section class="section"><div class="town-links"><a class="town-link" href="quand-il-pleut/">'
                "☔ S'il pleut : les sorties à l'abri</a></div></section>"), 1), encoding="utf-8")
    rain_towns = sorted(((p.split("/")[1], p) for p in rain_paths), key=lambda t: t[0])
    zone_names = {slugify(c["name"]): c["name"] for c in json.loads(
        (Path(__file__).parent / "communes_zone.json").read_text(encoding="utf-8"))} if rain_paths else {}
    rain_after = ('<section class="section"><div class="section-head"><div><p class="kicker">Par ville</p>'
                  '<h2>Quand il pleut près de chez toi</h2></div></div><div class="town-links">'
                  + "".join(f'<a class="town-link" href="../{p}">{e(zone_names.get(slug_, slug_))}</a>'
                            for slug_, p in sorted(rain_towns, key=lambda t: fold(zone_names.get(t[0], t[0]))))
                  + '</div></section>') if rain_paths else ""
    for slug, page_title, h1, kicker, keep, days in THEMES:
        page, count = theme_page(slug, page_title, h1, kicker, keep, days, upcoming, series, now,
                                 after=rain_after if slug == "quand-il-pleut" else "")
        theme_counts[slug] = count
        (out / slug).mkdir(exist_ok=True)
        (out / slug / "index.html").write_text(page, encoding="utf-8")
    for fest in FESTIVALS:
        (out / fest["slug"]).mkdir(exist_ok=True)
        (out / fest["slug"] / "index.html").write_text(festival_page(fest, upcoming, series, now), encoding="utf-8")
    # Lettre du jeudi (20/11/2026) : page d'inscription, confirmation,
    # désinscription ; pages de saison (lues par le script d'envoi du dépôt
    # Wavents) et communes proposées dans le formulaire.
    for sub in ("lettre/confirmer", "lettre/desinscription"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    (out / "lettre" / "index.html").write_text(lettre_page(), encoding="utf-8")
    (out / "lettre" / "confirmer" / "index.html").write_text(lettre_action_page("confirmer"), encoding="utf-8")
    (out / "lettre" / "desinscription" / "index.html").write_text(lettre_action_page("desinscription"), encoding="utf-8")
    (out / "data" / "saisons.json").write_text(json.dumps(
        [{"slug": f["slug"], "name": f["name"], "first": f["first"].isoformat(), "last": f["last"].isoformat(),
          "teaser": f["teaser"], "n": FEST_COUNTS.get(f["slug"], 0)} for f in FESTIVALS],
        ensure_ascii=False), encoding="utf-8")
    (out / "data" / "communes.json").write_text(json.dumps(fetch_lettre_communes(), ensure_ascii=False), encoding="utf-8")
    (out / "a-propos").mkdir(exist_ok=True)
    (out / "a-propos" / "index.html").write_text(about_page(upcoming, communes), encoding="utf-8")
    (out / "faq").mkdir(exist_ok=True)
    (out / "faq" / "index.html").write_text(faq_page(upcoming, communes, theme_counts), encoding="utf-8")
    (out / "ajouter").mkdir(exist_ok=True)
    (out / "ajouter" / "index.html").write_text(add_page(), encoding="utf-8")
    (out / "app").mkdir(exist_ok=True)
    (out / "app" / "index.html").write_text(app_page(upcoming, communes), encoding="utf-8")
    # Kits organisateurs (20/11/2026) : hors plan du site, noindex.
    print(f"{write_organizer_pages(out, upcoming, series)} pages organisateurs")
    # Kits de diffusion (20/11/2026) : affiches des commerçants, page et carte
    # « séjour » des hébergeurs ; noindex, hors plan du site.
    write_kit_pages(out, upcoming, series, now)
    # Encart « Sorties près de chez vous » pour les sites partenaires (20/11/2026).
    print(f"{write_encart_pages(out, communes, upcoming, now)} encarts")
    extra_pages = ([f"{BASE_URL}/{t[0]}/" for t in THEMES] + [f"{BASE_URL}/{f['slug']}/" for f in FESTIVALS]
                   + [f"{BASE_URL}/a-propos/", f"{BASE_URL}/faq/", f"{BASE_URL}/app/", f"{BASE_URL}/ajouter/", f"{BASE_URL}/lettre/", f"{BASE_URL}/encart/",
                      f"{BASE_URL}/secret-santa/", f"{BASE_URL}/secret-santa/collegues/",
                      f"{BASE_URL}/secret-santa/idees-cadeaux/", f"{BASE_URL}/secret-santa/a-distance/",
                      f"{BASE_URL}/trouver-une-date/", f"{BASE_URL}/trouver-une-date/alternative-doodle/",
                      f"{BASE_URL}/trouver-une-date/soiree-entre-amis/", f"{BASE_URL}/trouver-une-date/repas-de-famille/"])

    # Plan du site en quatre parties (26/10/2026) : la Search Console donne
    # alors le nombre de pages indexées par type (pages, communes, marchés,
    # sorties), et sitemap.xml devient l'index des quatre.
    parts = {
        "pages": [f"{BASE_URL}/", f"{BASE_URL}/ville/", f"{BASE_URL}/marche/"] + extra_pages,
        "communes": [f"{BASE_URL}/{commune_path(name)}" for name in list(communes) + around_names]
                    + [f"{BASE_URL}/{p}" for p in rain_paths],
        "marches": [f"{BASE_URL}/{market_path(town)}" for town in markets],
        "sorties": [f"{BASE_URL}/{event_path(ev)}" for ev in events if canonical_ids[ev["id"]] == ev["id"]],
    }
    # Date de mise à jour (26/10/2026) : les pages de listes changent chaque
    # jour ; une fiche, à sa publication.
    published = {f"{BASE_URL}/{event_path(ev)}": (ev.get("published_at") or "")[:10] for ev in events}
    today_iso = now.date().isoformat()
    for part, part_urls in parts.items():
        entries = "\n".join(
            f"  <url><loc>{html.escape(quote(u, safe=':/'))}</loc><lastmod>{published.get(u) or today_iso}</lastmod></url>"
            for u in part_urls)
        (out / f"sitemap-{part}.xml").write_text(
            f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{entries}\n</urlset>\n',
            encoding="utf-8")
    index = "\n".join(f"  <sitemap><loc>{BASE_URL}/sitemap-{part}.xml</loc><lastmod>{today_iso}</lastmod></sitemap>"
                      for part in parts)
    (out / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{index}\n</sitemapindex>\n',
        encoding="utf-8",
    )
    (out / "llms.txt").write_text(llms_txt(upcoming, now, communes, markets), encoding="utf-8")
    # Fichiers agenda (.ics) hors de Google (09/11/2026 : la Search Console
    # les comptait parmi les ~265 pages « explorées, non indexées »).
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\nDisallow: /*.ics$\nSitemap: {BASE_URL}/sitemap.xml\n", encoding="utf-8")
    # IndexNow (Bing, et par lui ChatGPT/Copilot) : liste des pages à
    # signaler après la publication (étape du workflow pages.yml). Les pages
    # communes changent chaque jour ; les événements, les 2 000 plus proches.
    soonest = sorted(upcoming, key=lambda ev: ev["start_date"])[:2000]
    (out / "indexnow.json").write_text(json.dumps(
        [f"{BASE_URL}/", f"{BASE_URL}/ville/", f"{BASE_URL}/marche/"] + extra_pages
        + [f"{BASE_URL}/{commune_path(name)}" for name in list(communes) + around_names]
        + [f"{BASE_URL}/{market_path(town)}" for town in markets]
        + [f"{BASE_URL}/{event_path(ev)}" for ev in soonest]), encoding="utf-8")
    print(f"{len(events)} pages d'événements et {len(communes)} pages communes générées dans {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
