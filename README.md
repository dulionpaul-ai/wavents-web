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
- Accueil : sélection du week-end, « En ce moment » (expos et animations
  sur plusieurs jours), catégories, carte, agenda filtrable des 21
  prochains jours (marchés regroupés comme dans l'appli). Page événement :
  mini-carte, « Ajouter à mon agenda » (`event.ics`), partage, sorties
  voisines le même jour.

Adresse d'une page : `<adresse du site>/e/<id de l'événement>/`. L'adresse
du site se règle avec la variable `PAGES_BASE_URL` (par défaut
`https://dulionpaul-ai.github.io/wavents-web`) ; avec un nom de domaine, la
changer ici et dans l'appli (`lib/utils/share_links.dart`).

Réglage à faire une fois : Settings > Pages > Source : « GitHub Actions ».
