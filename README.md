# wavents-web

Site public de [Wavents](https://github.com/dulionpaul-ai/Wavents) : une
page par événement à venir (lien de partage, aperçu sur les messageries,
référencement Google), une page d'accueil avec les prochains jours autour
de Belleville-en-Beaujolais, et la politique de confidentialité de l'appli.

- `generate.py` lit les événements publiés dans Supabase (clé anon, la même
  que l'appli) et écrit le site dans `site/`.
- `.github/workflows/pages.yml` le régénère chaque jour à 11h15 UTC (et à
  chaque push) puis le publie sur GitHub Pages.
- `static/` : feuille de style (direction « pop », refonte du 27/09/2026),
  `app.js` (filtres de l'agenda, carte Leaflet, partage), logo, photos des
  catégories, page 404, confidentialité.
- Ajouter un événement (20/10/2026) : page `ajouter/` (`static/ajouter.js`) :
  affiche, lien ou texte collé lus par les fonctions `parse-event-photo` /
  `parse-event-text` de l'appli, fiche envoyée masquée (`source = 'app'`),
  vérifiée comme celles de l'appli. Rubrique « Ce que fait l'appli » de
  l'accueil : vidéos des stories dans `static/videos/`.
- Accueil : sélection du week-end, « En ce moment » (expos et animations
  sur plusieurs jours), catégories, carte, agenda filtrable des 21
  prochains jours (marchés regroupés comme dans l'appli). Page événement :
  mini-carte, « Ajouter à mon agenda » (`event.ics`), partage, sorties
  voisines le même jour.

Adresse d'une page : `<adresse du site>/e/<id de l'événement>/`. L'adresse
du site se règle avec la variable `PAGES_BASE_URL` (par défaut
`https://wavents.fr`, domaine branché le 28/09/2026 : zone DNS OVH vers
GitHub Pages, domaine personnalisé dans Settings > Pages). L'ancienne
adresse github.io redirige vers wavents.fr ; à changer aussi dans l'appli
(`lib/utils/share_links.dart`) si le domaine change.

Kits de diffusion (20/11/2026, pages noindex, hors plan du site) :
`affiche/` (affiche A4 « Que faire ce week-end autour de <commune> ? »
pour les vitrines, une par commune de `AFFICHE_COMMUNES`), `sejour/` (les
sorties des 7 prochains jours pour les vacanciers) et `sejour/imprimer/`
(cartes de chambre à QR). QR et PDF dans `static/affiche/` et
`static/sejour/`, refaits par `outils/kits_qr.py` et `outils/kits_pdf.py`
seulement si la liste ou le dessin change. Les QR ajoutent `?ref=<canal>`,
compté dans GoatCounter comme événement `ref-<canal>` (`static/app.js`).

Réglage à faire une fois : Settings > Pages > Source : « GitHub Actions ».

Audit du 20/11/2026 (détail : `docs/audit_site_2026-11-20.md` du dépôt
Wavents) : pied de page en colonnes titrées, encart « gratuit et le
restera » (Ko-fi, `kofi_box()`) sur l'accueil et les fiches, vidéos de
l'appli dans un cadre de téléphone (accueil et `/app/`). La police
d'icônes est réduite aux icônes de `MS_ICONS` (`generate.py`) : toute
nouvelle icône doit y être ajoutée. Leaflet n'est chargé que sur les
pages qui ont une carte (`id="map"` ou `id="mini-map"`).

Pages « Que faire quand il pleut » (23/11/2026, Paul, idée vue chez Yatoo) :
`quand-il-pleut/` (sorties en intérieur, sûr ou probable, des 14 prochains
jours à 40 km) et `ville/<ville>/quand-il-pleut/` pour chaque ville de plus de
2 000 habitants (`communes_zone.json`) qui a au moins 6 sorties à l'abri à
moins de 10 km (`rain_pages`, `RAIN_*`) : 111 villes le 23/11. Dans le plan
du site (partie communes) et le pied de page.

Kit séjour refait le 23/11/2026 (Paul) : `sejour/imprimer/` propose un
chevalet A4 à plier en deux (par défaut, `wavents-sejour-chevalet.pdf`) ou
deux affichettes A5 (`?f=affichettes`, `wavents-sejour.pdf`), le nom de
l'hébergement et son adresse (facultatifs, jamais enregistrés ; géocodage
data.geopf.fr dans le navigateur, QR fait sur place par qrcode-generator) :
le QR ouvre alors `sejour/?ref=sejour&p=lat,lon&n=nom`, triée autour de
l'hébergement avec « Bienvenue ! Avec les compliments de … ». Message
d'accueil à copier pour Airbnb et Booking (`?ref=accueil`). PDF refaits par
`outils/kits_pdf.py site sejour`.
