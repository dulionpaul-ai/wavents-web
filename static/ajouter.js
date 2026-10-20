/* Wavents — formulaire « Ajouter un événement » du site (maquette validée par
   Paul le 20/10/2026). Même chemin que le formulaire de l'appli : lecture de
   l'affiche ou du lien par les fonctions parse-event-photo / parse-event-text
   (Gemini), photo dans le stockage event-photos, fiche insérée avec
   source = 'app' : elle reste masquée et passe par les mêmes contrôles
   (règles, Gemini, veille) avant publication. */
(function () {
  "use strict";
  var form = document.getElementById("add-form");
  if (!form) return;
  var SUPA = form.dataset.supabase, KEY = form.dataset.key;
  var HEAD = { apikey: KEY, Authorization: "Bearer " + KEY };
  var GEO = "https://data.geopf.fr/geocodage";
  // Recherche d'adresse orientée autour de Belleville (sinon « Beaujeu »
  // tombe dans les Alpes-de-Haute-Provence) ; au-delà de 150 km, on demande
  // de choisir l'adresse dans la liste.
  var PRES = "&lat=46.108&lon=4.749", LOIN_KM = 150;
  function km(lat, lon) {
    var r = Math.PI / 180, dl = (lat - 46.108) * r, dn = (lon - 4.749) * r;
    var a = Math.sin(dl / 2) * Math.sin(dl / 2) + Math.cos(46.108 * r) * Math.cos(lat * r) * Math.sin(dn / 2) * Math.sin(dn / 2);
    return 12742 * Math.asin(Math.sqrt(a));
  }
  var MAX_PAR_JOUR = 10;

  var $ = function (id) { return document.getElementById(id); };
  var state = { mode: "affiche", photo: null, photoUrl: null, occurrences: [], lieu: null };

  function show(id, on) { $(id).hidden = !on; }
  function status(id, text, kind) {
    var el = $(id); el.textContent = text || ""; el.className = "add-status" + (kind ? " " + kind : "");
  }

  // --- Mode ------------------------------------------------------------------
  document.querySelectorAll("[data-mode]").forEach(function (b) {
    b.addEventListener("click", function () {
      state.mode = b.dataset.mode;
      document.querySelectorAll("[data-mode]").forEach(function (x) {
        x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", x === b ? "true" : "false");
      });
      show("step-affiche", state.mode === "affiche");
      show("step-lien", state.mode === "lien");
      if (state.mode === "main") revealForm(false);
    });
  });

  // --- Affiche -----------------------------------------------------------------
  var drop = $("drop");
  ["dragover", "dragenter"].forEach(function (t) { drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("over"); }); });
  ["dragleave", "drop"].forEach(function (t) { drop.addEventListener(t, function () { drop.classList.remove("over"); }); });
  drop.addEventListener("drop", function (e) { e.preventDefault(); if (e.dataTransfer.files[0]) lireAffiche(e.dataTransfer.files[0]); });
  $("file").addEventListener("change", function () { if (this.files[0]) lireAffiche(this.files[0]); });

  // Photo réduite à 1600 px en JPEG (comme l'appli) : envoi plus léger,
  // même image pour l'IA et pour la fiche.
  function reduire(file) {
    return new Promise(function (ok, ko) {
      var img = new Image();
      img.onload = function () {
        var k = Math.min(1, 1600 / Math.max(img.width, img.height));
        var c = document.createElement("canvas");
        c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(img.src);
        c.toBlob(function (b) { b ? ok(b) : ko(new Error("image")); }, "image/jpeg", 0.85);
      };
      img.onerror = function () { ko(new Error("Ce fichier n'est pas une image lisible (JPG ou PNG).")); };
      img.src = URL.createObjectURL(file);
    });
  }

  function base64(blob) {
    return new Promise(function (ok) {
      var r = new FileReader();
      r.onload = function () { ok(String(r.result).split(",")[1]); };
      r.readAsDataURL(blob);
    });
  }

  function lireAffiche(file) {
    status("affiche-status", "Lecture de l'affiche…", "busy");
    reduire(file).then(function (blob) {
      state.photo = blob; state.photoUrl = null;
      $("drop-img").src = URL.createObjectURL(blob);
      $("drop-img").hidden = false;
      $("drop-name").textContent = file.name;
      return base64(blob);
    }).then(function (data) {
      return appel("parse-event-photo", { images: [{ data: data, mimeType: "image/jpeg" }] });
    }).then(function (res) {
      status("affiche-status", "✓ Affiche lue : vérifie la fiche ci-dessous.", "ok");
      remplir(res);
    }).catch(function (err) {
      status("affiche-status", (err.message || "Lecture impossible") + " Tu peux remplir la fiche à la main.", "err");
      revealForm(false);
    });
  }

  // --- Lien --------------------------------------------------------------------
  $("lien-go").addEventListener("click", function () {
    // Un lien seul : la page est lue par le serveur. Facebook et Instagram
    // ne se lisent pas (pas de lecture de Facebook, règle de Paul) : on
    // demande d'en coller le texte. Sinon, le texte collé est lu tel quel.
    var v = $("lien").value.trim();
    if (!v) { status("lien-status", "Colle un lien ou un texte.", "err"); return; }
    var lien = /^https?:\/\/\S+$/.test(v);
    if (lien && /(^|\.)(facebook\.com|fb\.me|fb\.com|instagram\.com)\//i.test(v.replace(/^https?:\/\//, ""))) {
      status("lien-status", "Les liens Facebook et Instagram ne peuvent pas être lus : copie plutôt le texte de ta publication ici, ou envoie ton affiche.", "err");
      return;
    }
    if (!lien && v.length < 30) { status("lien-status", "Texte trop court : colle toute la publication, ou le lien complet (https://…).", "err"); return; }
    status("lien-status", lien ? "Lecture de la page…" : "Lecture du texte…", "busy");
    appel("parse-event-text", lien ? { url: v } : { text: v }).then(function (res) {
      state.photo = null; state.photoUrl = res.pageImage || null;
      status("lien-status", "✓ Lu : vérifie la fiche ci-dessous.", "ok");
      remplir(res);
    }).catch(function (err) {
      status("lien-status", (err.message || "Lecture impossible") + " Tu peux remplir la fiche à la main.", "err");
      revealForm(false);
    });
  });

  function appel(fn, body) {
    return fetch(SUPA + "/functions/v1/" + fn, {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, HEAD), body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok || j.error) throw new Error(j.error ? String(j.error) + "." : "Service indisponible.");
        return j;
      });
    });
  }

  // --- Remplissage ---------------------------------------------------------------
  var F = ["titre", "description", "categorie", "date", "heure", "date-fin", "heure-fin", "lieu", "adresse", "montant", "site", "email", "tel"];

  function poser(id, v) {
    var el = $(id);
    if (v === null || v === undefined || v === "") return;
    el.value = v; el.classList.add("ai");
  }

  function remplir(res) {
    F.forEach(function (id) { $(id).classList.remove("ai"); });
    var occ = (res.occurrences || []).filter(function (o) { return o && (o.title || o.startDate); });
    state.occurrences = occ;
    poser("categorie", res.categoryId);
    poser("site", res.websiteUrl);
    poser("email", res.contactEmail);
    poser("tel", res.contactPhone);
    if (res.isFree === true) prix("gratuit", res);
    else if (res.priceAmount != null) { prix("payant", res); poser("montant", String(res.priceAmount).replace(".", ",")); }
    else prix("inconnu", res);
    if (res.forKids === true) $("famille").checked = true;
    choixOccurrences(res);
    revealForm(true);
  }

  function prix(v) { var el = form.querySelector('input[name="prix"][value="' + v + '"]'); if (el) { el.checked = true; majPrix(); } }

  function appliquer(o, res) {
    ["titre", "description", "date", "heure", "date-fin", "heure-fin", "lieu", "adresse"].forEach(function (id) { $(id).value = ""; $(id).classList.remove("ai"); });
    state.lieu = null;
    poser("titre", o.title);
    poser("description", o.description);
    var d = (o.startDate || "").split("T"), f = (o.endDate || "").split("T");
    poser("date", d[0]);
    if (o.startTimeKnown && d[1]) poser("heure", d[1].slice(0, 5));
    if (f[0] && f[0] !== d[0]) poser("date-fin", f[0]);
    if (o.endTimeKnown && f[1]) poser("heure-fin", f[1].slice(0, 5));
    poser("lieu", o.locationName || res.locationName);
    poser("adresse", o.address || res.address);
  }

  // Plusieurs dates ou événements lus : un seul à la fois dans la fiche, ou
  // toutes les dates d'un même événement d'un coup.
  function choixOccurrences(res) {
    var box = $("occurrences"), occ = state.occurrences;
    box.innerHTML = "";
    state.toutes = false;
    if (occ.length) appliquer(occ[0], res); else appliquer({}, res);
    if (occ.length < 2) { box.hidden = true; return; }
    var memeTitre = occ.every(function (o) { return (o.title || "") === (occ[0].title || ""); });
    var p = document.createElement("p");
    p.className = "occ-intro";
    p.textContent = memeTitre ? occ.length + " dates trouvées pour cet événement :" : occ.length + " événements trouvés : choisis celui à ajouter (un à la fois).";
    box.appendChild(p);
    function bouton(texte, on, action) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "occ" + (on ? " on" : ""); b.textContent = texte;
      b.addEventListener("click", function () {
        box.querySelectorAll(".occ").forEach(function (x) { x.classList.remove("on"); });
        b.classList.add("on"); action();
      });
      box.appendChild(b);
    }
    if (memeTitre) bouton("Toutes les dates (" + occ.length + ")", true, function () { state.toutes = true; appliquer(occ[0], res); });
    occ.forEach(function (o, i) {
      bouton((memeTitre ? "" : (o.title || "Sans titre") + " · ") + jour(o.startDate), !memeTitre && i === 0,
        function () { state.toutes = false; appliquer(o, res); });
    });
    state.toutes = memeTitre;
    box.hidden = false;
  }

  function jour(iso) {
    if (!iso) return "date à préciser";
    var p = iso.split("T")[0].split("-");
    var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
    return d.toLocaleDateString("fr-FR", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  }

  function revealForm(lu) {
    show("fiche", true);
    $("fiche-hint").textContent = lu ? "Remplie d'après ta source (en bleu) : corrige si besoin." : "Remplis les champs ci-dessous.";
  }

  // --- Prix ----------------------------------------------------------------------
  function majPrix() {
    var v = (form.querySelector('input[name="prix"]:checked') || {}).value;
    show("montant-box", v === "payant");
  }
  form.querySelectorAll('input[name="prix"]').forEach(function (r) { r.addEventListener("change", majPrix); });

  // --- Adresse (Géoplateforme) ---------------------------------------------------------
  var sugg = $("adresse-sugg"), timer = null;
  $("adresse").addEventListener("input", function () {
    state.lieu = null;
    clearTimeout(timer);
    var q = this.value.trim();
    if (q.length < 4) { sugg.hidden = true; return; }
    timer = setTimeout(function () {
      fetch(GEO + "/search?limit=5" + PRES + "&q=" + encodeURIComponent(q)).then(function (r) { return r.json(); }).then(function (j) {
        sugg.innerHTML = "";
        (j.features || []).forEach(function (f) {
          var li = document.createElement("li");
          li.textContent = f.properties.label;
          li.addEventListener("mousedown", function (e) {
            e.preventDefault();
            $("adresse").value = f.properties.label;
            state.lieu = { lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0], label: f.properties.label };
            sugg.hidden = true;
          });
          sugg.appendChild(li);
        });
        sugg.hidden = !sugg.children.length;
      }).catch(function () { sugg.hidden = true; });
    }, 250);
  });
  $("adresse").addEventListener("blur", function () { setTimeout(function () { sugg.hidden = true; }, 150); });

  function geocoder() {
    if (state.lieu) return Promise.resolve(state.lieu);
    var q = $("adresse").value.trim() || $("lieu").value.trim();
    if (!q) return Promise.reject(new Error("Indique l'adresse du lieu."));
    return fetch(GEO + "/search?limit=1" + PRES + "&q=" + encodeURIComponent(q))
      .then(function (r) { return r.json(); }).then(function (j) {
        var f = (j.features || [])[0];
        if (!f || f.properties.score < 0.4 || km(f.geometry.coordinates[1], f.geometry.coordinates[0]) > LOIN_KM) throw new Error("Adresse introuvable : choisis-la dans la liste qui s'affiche quand tu la tapes.");
        return { lat: f.geometry.coordinates[1], lon: f.geometry.coordinates[0], label: f.properties.label };
      });
  }

  // --- Envoi -----------------------------------------------------------------------
  function compteur() {
    try {
      var k = "wv_ajouts_" + new Date().toISOString().slice(0, 10);
      return { n: +(localStorage.getItem(k) || 0), plus: function (x) { localStorage.setItem(k, String(this.n + x)); } };
    } catch (e) { return { n: 0, plus: function () {} }; }
  }

  // Heure de Paris avec le suffixe UTC (convention de la base, voir
  // apply_local_time dans le dépôt de l'appli).
  function stamp(d, h) { return d + "T" + (h || "00:00") + ":00+00:00"; }

  function televerser() {
    if (!state.photo) return Promise.resolve(state.photoUrl);
    var nom = Date.now() + "_site_affiche.jpg";
    return fetch(SUPA + "/storage/v1/object/event-photos/" + nom, {
      method: "POST", headers: Object.assign({ "Content-Type": "image/jpeg" }, HEAD), body: state.photo
    }).then(function (r) {
      if (!r.ok) throw new Error("Envoi de la photo impossible.");
      return SUPA + "/storage/v1/object/public/event-photos/" + nom;
    });
  }

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, function (c) {
      return (c ^ crypto.getRandomValues(new Uint8Array(1))[0] & 15 >> c / 4).toString(16);
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    if ($("piege").value) { fini([]); return; } // robot : on fait comme si
    var c = compteur();
    if (c.n >= MAX_PAR_JOUR) { status("envoi-status", "Tu as déjà envoyé beaucoup d'événements aujourd'hui : réessaie demain.", "err"); return; }
    var titre = $("titre").value.trim(), date = $("date").value, cat = $("categorie").value;
    var lieu = $("lieu").value.trim() || $("adresse").value.trim();
    if (!titre || !date || !cat || !lieu) { status("envoi-status", "Il manque le titre, la date, la catégorie ou l'adresse.", "err"); return; }
    var v = (form.querySelector('input[name="prix"]:checked') || {}).value || "inconnu";
    var montant = parseFloat(($("montant").value || "").replace(",", "."));
    if (v === "payant" && !(montant > 0)) montant = null;
    var bouton = $("envoyer");
    bouton.disabled = true;
    status("envoi-status", "Envoi en cours…", "busy");

    geocoder().then(function (pos) {
      return televerser().then(function (photo) {
        var dates = state.toutes && state.occurrences.length > 1
          ? state.occurrences.filter(function (o) { return o.startDate; }).map(function (o) {
              var d = o.startDate.split("T"), f = (o.endDate || "").split("T");
              return { debut: stamp(d[0], o.startTimeKnown ? (d[1] || "").slice(0, 5) : ""), hd: !!o.startTimeKnown,
                       fin: f[0] ? stamp(f[0], o.endTimeKnown ? (f[1] || "").slice(0, 5) : "") : null, hf: !!o.endTimeKnown };
            })
          : [{ debut: stamp(date, $("heure").value), hd: !!$("heure").value,
               fin: ($("date-fin").value || $("heure-fin").value) ? stamp($("date-fin").value || date, $("heure-fin").value) : null,
               hf: !!$("heure-fin").value }];
        var lignes = dates.map(function (d) {
          return {
            id: uuid(), title: titre.slice(0, 300), description: $("description").value.trim() || null,
            category_id: cat, location_name: lieu, address: $("adresse").value.trim() || pos.label,
            latitude: pos.lat, longitude: pos.lon, start_date: d.debut, end_date: d.fin,
            start_time_known: d.hd, end_time_known: d.hf,
            price_type: v === "payant" ? "paid" : "free", price_amount: v === "payant" ? montant : null,
            price_confirmed: v !== "inconnu", for_kids: $("famille").checked ? true : null,
            image_url: photo || null, additional_photo_urls: [], source: "app",
            website_url: $("site").value.trim() || null, contact_email: $("email").value.trim() || null,
            contact_phone: $("tel").value.trim() || null
          };
        });
        return fetch(SUPA + "/rest/v1/events", {
          method: "POST", headers: Object.assign({ "Content-Type": "application/json", Prefer: "return=minimal" }, HEAD),
          body: JSON.stringify(lignes)
        }).then(function (r) {
          if (!r.ok) return r.json().catch(function () { return {}; }).then(function (j) {
            throw new Error(/attente de mod/.test(j.message || "") ? "Trop d'événements en attente de vérification : réessaie plus tard." : "Envoi impossible, réessaie dans un moment.");
          });
          c.plus(lignes.length);
          fini(lignes);
        });
      });
    }).catch(function (err) {
      bouton.disabled = false;
      status("envoi-status", err.message || "Envoi impossible.", "err");
    });
  });

  function fini(lignes) {
    form.hidden = true;
    var lien = lignes.length ? location.origin + "/e/" + lignes[0].id + "/" : "";
    $("merci-lien").hidden = !lien;
    if (lien) { $("merci-a").href = lien; $("merci-a").textContent = lien.replace(/^https?:\/\//, ""); }
    $("merci-n").textContent = lignes.length > 1 ? "Tes " + lignes.length + " dates sont bien arrivées." : "Ton événement est bien arrivé.";
    show("merci", true);
    $("merci").scrollIntoView({ behavior: "smooth", block: "start" });
  }
})();
