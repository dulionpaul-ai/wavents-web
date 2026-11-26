/* Wavents — « Une info à corriger ? » sur les fiches du site (26/11/2026,
   Paul) : un vrai signalement, comme dans l'appli. Il est enregistré dans la
   table reports (clé anon, même insertion que l'appli), traité tout de suite
   par la fonction handle-report (Gemini, sur preuve), puis par la veille.
   Sans JavaScript, le lien garde son mail prérempli vers hello@wavents.fr. */
(function () {
  "use strict";
  var RAISONS = [
    ["Informations incorrectes (date, lieu, prix...)", "Une info est fausse", "date, heure, lieu, prix, intérieur ou extérieur…"],
    ["Événement annulé ou déjà terminé", "Annulé ou déjà terminé", ""],
    ["Doublon d'un autre événement", "C'est un doublon", ""],
    ["Contenu inapproprié", "Contenu inapproprié", ""],
    ["Autre", "Autre chose", ""]
  ];
  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0; return (c === "x" ? r : (r & 3 | 8)).toString(16);
    });
  }
  function el(tag, cls, html) { var n = document.createElement(tag); if (cls) n.className = cls; if (html) n.innerHTML = html; return n; }
  function ouvrir(link) {
    var dlg = el("dialog", "report-dlg");
    var form = el("form", "report-form");
    form.method = "dialog";
    form.appendChild(el("h2", "", "Une info à corriger&nbsp;?"));
    form.appendChild(el("p", "report-sub", "Dites-nous ce qui ne va pas : on vérifie et on corrige, en général dans la journée."));
    RAISONS.forEach(function (r, i) {
      var lab = el("label", "report-opt");
      lab.innerHTML = '<input type="radio" name="raison" value="' + i + '"' + (i === 0 ? " checked" : "") + '><span><b>' + r[1] + "</b>" + (r[2] ? "<small>" + r[2] + "</small>" : "") + "</span>";
      form.appendChild(lab);
    });
    var ta = el("textarea", "report-text");
    ta.name = "commentaire"; ta.maxLength = 500; ta.rows = 3;
    ta.placeholder = "Ex. : c'est en extérieur, l'entrée est à 5 €, ça commence à 20 h…";
    form.appendChild(ta);
    var pot = el("input", "report-pot"); pot.name = "site"; pot.tabIndex = -1; pot.autocomplete = "off"; pot.setAttribute("aria-hidden", "true");
    form.appendChild(pot);
    var status = el("p", "report-status");
    form.appendChild(status);
    var btns = el("div", "report-btns");
    var annuler = el("button", "report-cancel", "Annuler"); annuler.type = "button";
    var envoyer = el("button", "report-send", "Envoyer"); envoyer.type = "submit";
    btns.appendChild(annuler); btns.appendChild(envoyer);
    form.appendChild(btns);
    form.appendChild(el("p", "report-mail", 'Besoin d\'une réponse&nbsp;? Écrivez-nous à <a href="' + link.getAttribute("href") + '">hello@wavents.fr</a>.'));
    dlg.appendChild(form);
    document.body.appendChild(dlg);
    annuler.onclick = function () { dlg.close(); };
    dlg.addEventListener("close", function () { dlg.remove(); });
    form.onsubmit = function (ev) {
      ev.preventDefault();
      if (pot.value) { dlg.close(); return; }
      var r = RAISONS[+(form.querySelector("input[name=raison]:checked") || {}).value || 0];
      var texte = ta.value.trim();
      if (r[0] !== "Événement annulé ou déjà terminé" && r[0] !== "Contenu inapproprié" && !texte) {
        status.textContent = "Dites-nous en quelques mots ce qui est faux.";
        ta.focus();
        return;
      }
      envoyer.disabled = true;
      status.textContent = "Envoi…";
      var key = link.dataset.key;
      fetch(link.dataset.supabase + "/rest/v1/reports", {
        method: "POST",
        headers: { apikey: key, Authorization: "Bearer " + key, "Content-Type": "application/json", Prefer: "return=minimal" },
        body: JSON.stringify({ id: uuid(), event_id: link.dataset.event, reason: r[0], comment: ("[site] " + texte).trim() })
      }).then(function (res) {
        if (!res.ok) throw new Error("http " + res.status);
        form.innerHTML = '<h2>Merci&nbsp;! 👍</h2><p class="report-sub">C\'est bien reçu : on vérifie et on corrige la fiche, en général dans la journée.</p><div class="report-btns"><button class="report-send" type="button">Fermer</button></div>';
        form.querySelector("button").onclick = function () { dlg.close(); };
        if (window.goatcounter && goatcounter.count) goatcounter.count({ path: "signalement-envoye", event: true });
      }).catch(function () {
        envoyer.disabled = false;
        status.innerHTML = 'Envoi impossible pour l\'instant. Écrivez-nous à <a href="' + link.getAttribute("href") + '">hello@wavents.fr</a>.';
      });
    };
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute("open", "");
  }
  document.addEventListener("click", function (ev) {
    var link = ev.target.closest && ev.target.closest("a.report-link[data-event]");
    if (!link || !link.dataset.supabase || typeof HTMLDialogElement === "undefined") return;
    ev.preventDefault();
    ouvrir(link);
  });
})();
