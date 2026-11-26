/* Wavents — jeux concours (26/11/2026, script SQL 268 du dépôt Wavents).
   Page d'un jeu (/gagner/<slug>/) : statut, compte à rebours, formulaire,
   écran « C'est noté » ; page /gagner/moi/?j=<jeton>(&c=1) : confirmation
   et tickets. Appelle jeu_public, jeu_participer, jeu_confirmer et
   jeu_ma_participation (clé anon). */
(function () {
  "use strict";

  function rpc(el, name, args) {
    var key = el.dataset.key;
    return fetch(el.dataset.supabase + "/rest/v1/rpc/" + name, {
      method: "POST",
      headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json" },
      body: JSON.stringify(args)
    }).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    });
  }
  function param(name) {
    try { return new URLSearchParams(location.search).get(name) || ""; } catch (err) { return ""; }
  }
  function $(id) { return document.getElementById(id); }
  function say(el, text, kind) { el.textContent = text; el.className = "lettre-status" + (kind ? " " + kind : ""); }
  function count(path) {
    if (window.goatcounter && window.goatcounter.count) window.goatcounter.count({ path: path, event: true });
  }
  function store(key, value) {
    try { if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null"); localStorage.setItem(key, JSON.stringify(value)); }
    catch (err) { return null; }
  }
  var JOURS = ["dimanche", "lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi"];
  var MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"];
  function jour(iso) {
    var d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
    return JOURS[d.getDay()] + " " + d.getDate() + (d.getDate() === 1 ? "er" : "") + " " + MOIS[d.getMonth()];
  }
  function tickets(n) { return n + " ticket" + (n > 1 ? "s" : ""); }

  function partage(btn, input, url, titre) {
    input.value = url;
    btn.addEventListener("click", function () {
      count("jeu-partage");
      var texte = titre + " : participe au tirage au sort sur Wavents, c'est gratuit !";
      if (navigator.share) {
        navigator.share({ title: titre, text: texte, url: url }).catch(function () {});
        return;
      }
      input.select();
      var fait = function () { btn.textContent = "Lien copié !"; setTimeout(function () { btn.textContent = "Envoyer mon lien"; }, 2500); };
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(fait, function () { document.execCommand("copy"); fait(); });
      else { document.execCommand("copy"); fait(); }
    });
  }

  // --- Page d'un jeu -----------------------------------------------------------
  var page = $("jeu");
  if (page) {
    var slug = page.dataset.slug;
    var titre = document.querySelector("h1").textContent.replace(/\s*!$/, "").trim();
    var parrain = param("p").toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 12);
    if (parrain) store("wv-jeu-parrain-" + slug, parrain); else parrain = store("wv-jeu-parrain-" + slug) || "";
    var canal = param("ref").toLowerCase().replace(/[^a-z0-9_:-]/g, "").slice(0, 40) || (parrain ? "ami" : "");

    var montrerOk = function (p, deja) {
      $("participer").hidden = true;
      $("jeu-go").hidden = false;
      $("jeu-go").textContent = "Mes tickets";
      $("jeu-go").href = "#jeu-ok";
      $("jeu-ok").hidden = false;
      $("jeu-ok-titre").textContent = deja ? "Tu participes déjà, " + p.prenom + " ! 🎉" : "C'est noté, " + p.prenom + " ! 🎉";
      $("jeu-ok-num").textContent = "Participation n° " + p.numero;
      partage($("jeu-partager"), $("jeu-lien"), page.dataset.base + "/gagner/" + slug + "/?p=" + p.code, titre);
    };

    rpc(page, "jeu_public", { p_slug: slug }).then(function (j) {
      if (!j || !j.ok) return;
      var compte = $("jeu-compte");
      var reste = (new Date(j.fin) - new Date()) / 86400000;
      if (j.statut === "ouvert") {
        compte.textContent = reste < 1 ? "⏳ Dernier jour pour jouer !" : "⏳ Plus que " + Math.ceil(reste) + " jours";
        var deja = store("wv-jeu-" + slug);
        if (deja && deja.jeton) { montrerOk(deja, true); return; }
        $("participer").hidden = false;
        $("jeu-go").hidden = false;
        return;
      }
      $("jeu-ferme").hidden = false;
      if (j.statut === "a_venir") {
        $("jeu-ferme-titre").textContent = "Le jeu ouvre bientôt";
        $("jeu-ferme-texte").textContent = (new Date(j.debut) < new Date(j.fin) ? "Il ouvre le " + jour(j.debut) + "." : "Il ouvre très bientôt.") + " Pour être prévenu, installe l'appli Wavents.";
      } else if (j.statut === "ferme") {
        compte.textContent = "Tirage le " + jour(j.tirage_le);
        $("jeu-ferme-titre").textContent = "Le jeu est terminé";
        $("jeu-ferme-texte").textContent = "Merci à tous les participants ! Le tirage au sort a lieu le " + jour(j.tirage_le) + ".";
      } else {
        compte.textContent = "🎉 Tirage effectué";
        $("jeu-ferme-titre").textContent = j.gagnants.length > 1 ? "Les gagnants" : "Le gagnant";
        $("jeu-ferme-texte").textContent = "Bravo à eux, et merci à tous les participants ! D'autres jeux arrivent bientôt.";
        $("jeu-gagnants").innerHTML = j.gagnants.map(function (g) {
          var li = document.createElement("li");
          li.textContent = "🎉 " + g.prenom + " (participation n° " + g.numero + ")";
          return li.outerHTML;
        }).join("");
      }
    }).catch(function () {
      $("participer").hidden = false; // la base répondra au moment de valider
    });

    var form = $("jeu-form");
    var status = $("jeu-status");
    var btn = form.querySelector(".lettre-go");
    var lettre = form.elements.lettre;
    var communeBloc = $("jeu-commune");
    lettre.addEventListener("change", function () {
      communeBloc.hidden = !lettre.checked;
      if (lettre.checked && !communeBloc.dataset.loaded) {
        communeBloc.dataset.loaded = "1";
        fetch(page.dataset.communes).then(function (r) { return r.json(); }).then(function (names) {
          $("jeu-communes").innerHTML = names.map(function (n) { var o = document.createElement("option"); o.value = n; return o.outerHTML; }).join("");
        }).catch(function () {});
      }
    });
    var ERREURS = {
      prenom: "Indique ton prénom.",
      email: "Cette adresse mail ne semble pas valide : vérifie-la.",
      choix: "Choisis la séance que tu voudrais si tu gagnes.",
      commune: "On ne trouve pas cette commune : écris son nom complet (ex. Saint-Georges-de-Reneins).",
      ferme: "Le jeu n'est pas ouvert en ce moment.",
      trop: "Trop d'essais pour aujourd'hui : réessaie demain.",
      jeu: "Ce jeu n'existe pas (ou plus)."
    };
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (form.elements.site_web.value) return; // piège à robots
      var prenom = form.elements.prenom.value.trim();
      var email = form.elements.email.value.trim();
      var choix = form.querySelector('input[name="choix"]:checked');
      var commune = form.elements.commune.value.trim();
      if (!prenom) { say(status, ERREURS.prenom, "err"); form.elements.prenom.focus(); return; }
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) { say(status, ERREURS.email, "err"); form.elements.email.focus(); return; }
      if (!choix) { say(status, ERREURS.choix, "err"); return; }
      if (lettre.checked && commune.length < 2) { say(status, "Indique ta commune pour la lettre (ou décoche la case).", "err"); form.elements.commune.focus(); return; }
      btn.disabled = true;
      say(status, "Un instant…", "busy");
      rpc(page, "jeu_participer", {
        p_slug: slug, p_prenom: prenom, p_email: email, p_choix: choix.value,
        p_lettre: lettre.checked, p_commune: lettre.checked ? commune : null,
        p_parrain: parrain || null, p_canal: canal || null
      }).then(function (res) {
        btn.disabled = false;
        if (res && res.ok && !res.deja) {
          var p = { jeton: res.jeton, numero: res.numero, code: res.code, prenom: res.prenom };
          store("wv-jeu-" + slug, p);
          count("jeu-participation-" + slug);
          montrerOk(p, false);
          window.scrollTo({ top: $("jeu-ok").getBoundingClientRect().top + window.scrollY - 90, behavior: "smooth" });
        } else if (res && res.ok) {
          say(status, "Tu participes déjà avec cette adresse : on vient de te renvoyer le mail avec ton lien (regarde aussi les indésirables).", "ok");
        } else {
          say(status, ERREURS[res && res.erreur] || "Oups, ça n'est pas passé. Vérifie ta connexion et réessaie.", "err");
        }
      }).catch(function () {
        btn.disabled = false;
        say(status, "Oups, ça n'est pas passé. Vérifie ta connexion et réessaie.", "err");
      });
    });
  }

  // --- Ma participation ----------------------------------------------------------
  var moi = $("jeu-moi");
  if (moi) {
    var jeton = param("j");
    var status2 = $("moi-status");
    if (!/^[0-9a-f-]{36}$/i.test(jeton)) { say(status2, "Ce lien est incomplet : ouvre-le depuis le mail reçu.", "err"); return; }
    var confirmer = param("c") === "1";
    rpc(moi, confirmer ? "jeu_confirmer" : "jeu_ma_participation", { p_jeton: jeton }).then(function (p) {
      if (!p || !p.ok) { say(status2, "Ce lien ne marche plus : la participation a peut-être été effacée (non confirmée sous 30 jours).", "err"); return; }
      if (confirmer) count("jeu-confirmation-" + p.slug);
      $("moi-titre").textContent = p.confirme ? (confirmer ? "C'est confirmé, " + p.prenom + " ! 🎉" : "Ta participation, " + p.prenom) : "Ta participation, " + p.prenom;
      $("moi-jeu").textContent = p.titre;
      $("moi-voir").href = "../" + p.slug + "/";
      $("moi-max").textContent = p.max_bonus;
      if (p.statut === "tire") {
        say(status2, p.gagnant ? "🎉 Bravo, tu fais partie des gagnants ! Regarde le mail reçu pour récupérer ton lot." : "Le tirage a eu lieu : pas cette fois… Merci d'avoir joué !", p.gagnant ? "ok" : "");
        $("moi-plus").hidden = true;
      } else if (!p.confirme) {
        say(status2, p.statut === "ouvert" ? "Pas encore confirmée : clique sur le lien du mail reçu pour être dans le tirage." : "Le jeu est terminé, et cette participation n'avait pas été confirmée.", "err");
      } else {
        say(status2, "Tu es dans le tirage du " + jour(p.tirage_le) + ".", "ok");
      }
      $("moi-sous").textContent = "Participation n° " + p.numero;
      $("moi-tickets").textContent = tickets(p.tickets);
      $("moi-amis").textContent = p.amis_confirmes + p.amis_en_attente === 0 ? "Aucun ami n'a encore participé avec ton lien."
        : p.amis_confirmes + " ami" + (p.amis_confirmes > 1 ? "s ont" : " a") + " participé avec ton lien"
          + (p.amis_en_attente ? " (et " + p.amis_en_attente + " en attente de confirmation)." : ".");
      if (p.statut !== "ouvert") $("moi-plus").hidden = true;
      partage($("jeu-partager"), $("jeu-lien"), moi.dataset.base + "/gagner/" + p.slug + "/?p=" + p.code, p.titre);
      $("moi-contenu").hidden = false;
    }).catch(function () { say(status2, "Oups, ça n'est pas passé. Vérifie ta connexion et réessaie.", "err"); });
  }
})();
