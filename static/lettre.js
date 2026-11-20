/* Wavents — la lettre du jeudi (20/11/2026) : inscription (formulaires
   .lettre-form de l'accueil, des pages de fête et de /lettre/),
   confirmation (/lettre/confirmer/?j=…) et désinscription
   (/lettre/desinscription/?j=…). Appelle les fonctions lettre_inscrire,
   lettre_confirmer et lettre_desinscrire de la base (clé anon, script SQL 239
   du dépôt Wavents). Double opt-in : rien n'est envoyé sans confirmation. */
(function () {
  "use strict";
  if (window.wvLettre) return; // script inclus une fois par formulaire
  window.wvLettre = true;

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
  function say(el, text, kind) {
    el.textContent = text;
    el.className = "lettre-status" + (kind ? " " + kind : "");
  }
  var ERREURS = {
    email: "Cette adresse e-mail ne semble pas valide : vérifie-la.",
    commune: "On ne trouve pas cette commune. Écris son nom complet (ex. Saint-Georges-de-Reneins) ou celui d'une commune voisine.",
    rayon: "Choisis une distance dans la liste.",
    trop: "Trop d'essais pour aujourd'hui : réessaie demain."
  };
  var RESEAU = "Oups, ça n'est pas passé. Vérifie ta connexion et réessaie.";

  // Canal d'inscription : celui du bloc, ou ?c=… dans l'adresse (un lien par
  // groupe, école, mairie… pour savoir ce qui ramène du monde).
  var canalLien = param("c").toLowerCase().replace(/[^a-z0-9_:-]/g, "").slice(0, 40);

  function setupForm(form) {
    var status = form.querySelector(".lettre-status");
    var btn = form.querySelector(".lettre-go");
    var commune = form.elements.commune;
    var list = form.querySelector("datalist");
    var loaded = false;
    commune.addEventListener("focus", function () {
      if (loaded || !list) return;
      loaded = true;
      fetch(form.dataset.communes).then(function (r) { return r.json(); }).then(function (names) {
        list.innerHTML = names.map(function (n) {
          var o = document.createElement("option"); o.value = n; return o.outerHTML;
        }).join("");
      }).catch(function () {});
    });
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      if (form.elements.site_web.value) return; // piège à robots
      var email = form.elements.email.value.trim();
      var town = commune.value.trim();
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) { say(status, ERREURS.email, "err"); form.elements.email.focus(); return; }
      if (town.length < 2) { say(status, "Indique ta commune.", "err"); commune.focus(); return; }
      var rayon = form.elements.rayon ? parseInt(form.elements.rayon.value, 10) : 20;
      btn.disabled = true;
      say(status, "Un instant…", "busy");
      rpc(form, "lettre_inscrire", {
        p_email: email, p_commune: town, p_rayon_km: rayon, p_canal: canalLien || form.dataset.canal
      }).then(function (res) {
        btn.disabled = false;
        if (res && res.ok) {
          form.classList.add("is-done");
          say(status, "C'est noté pour " + res.commune + " ! Regarde ta boîte mail (et les indésirables) : "
            + "clique sur le lien reçu pour confirmer ton inscription.", "ok");
          if (window.goatcounter && window.goatcounter.count) {
            window.goatcounter.count({ path: "lettre-inscription-" + (canalLien || form.dataset.canal), event: true });
          }
        } else {
          say(status, ERREURS[res && res.erreur] || RESEAU, "err");
        }
      }).catch(function () {
        btn.disabled = false;
        say(status, RESEAU, "err");
      });
    });
  }

  function setupAction(box) {
    var msg = document.getElementById("lettre-msg");
    var after = document.getElementById("lettre-after");
    var jeton = param("j");
    var uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(jeton);
    if (box.dataset.kind === "confirmer") {
      if (!uuid) { say(msg, "Ce lien de confirmation est incomplet : ouvre-le directement depuis le mail reçu.", "err"); return; }
      rpc(box, "lettre_confirmer", { p_jeton: jeton }).then(function (res) {
        if (res && res.ok) {
          say(msg, "C'est confirmé ! Tu recevras la lettre chaque jeudi, avec les sorties du week-end autour de "
            + res.commune + ".", "ok");
        } else {
          say(msg, "Ce lien n'est plus valable (inscription effacée au bout de 30 jours sans confirmation). "
            + "Tu peux te réinscrire sur wavents.fr/lettre/.", "err");
        }
        after.hidden = false;
      }).catch(function () { say(msg, RESEAU + " (recharge la page)", "err"); });
      return;
    }
    var btn = document.getElementById("lettre-go");
    if (!uuid) {
      btn.style.display = "none";
      say(msg, "Ce lien de désinscription est incomplet : ouvre-le directement depuis la lettre reçue.", "err");
      return;
    }
    btn.addEventListener("click", function () {
      btn.disabled = true;
      say(msg, "Un instant…", "busy");
      rpc(box, "lettre_desinscrire", { p_jeton: jeton }).then(function () {
        btn.style.display = "none";
        document.getElementById("lettre-q").hidden = true;
        say(msg, "C'est fait : tu ne recevras plus la lettre, et ton adresse est effacée. À bientôt sur wavents.fr !", "ok");
        after.hidden = false;
      }).catch(function () {
        btn.disabled = false;
        say(msg, RESEAU, "err");
      });
    });
  }

  function init() {
    Array.prototype.forEach.call(document.querySelectorAll(".lettre-form"), setupForm);
    var box = document.getElementById("lettre-action");
    if (box) setupAction(box);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
