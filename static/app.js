/* Wavents — interactions du site public : navigation, apparitions, filtres
   de l'agenda, carte des sorties (Leaflet + regroupement), mini-carte et
   partage des pages événement. Tout reste lisible sans JavaScript. */
(function () {
  "use strict";
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
  const fold = (s) => (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();

  /* Navigation : bordure au défilement */
  const nav = $(".nav");
  const onScroll = () => nav && nav.classList.toggle("scrolled", window.scrollY > 10);
  window.addEventListener("scroll", onScroll, { passive: true });
  onScroll();

  /* Jeu de mots du logo (30/09, idée de Paul) : de temps en temps,
     « Wavents » se déplie en « Wave · events », « Vague · événements »,
     « Une vague d'événements », puis revient au départ par les mêmes étapes,
     à la même vitesse. Rare exprès : une première fois après 5 à 10 s, puis
     une pause de 15 à 20 s entre deux passages. « Vague » et « événements »
     arrivent directement à leur place finale : ils ne bougent plus quand
     « Une » et « d' » apparaissent. */
  const brand = $(".nav-brand");
  const mark = brand && $("span", brand);
  const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (mark && !calm && window.CSS && CSS.supports("overflow", "clip") && Element.prototype.animate) {
    const T = 700, HOLD = 900, PEAK = 1400; // transition, pause, pause sur la phrase
    const shut = ' style="width:0;opacity:0"';
    const EVENTS = '<span class="wm-slot wm-eve">eve</span>nts';
    const CAP = '<span class="wm-cap"><span>V</span><span>v</span></span>ague';
    brand.setAttribute("aria-label", "Wavents");
    mark.classList.add("wm");
    mark.style.setProperty("--wm-t", T + "ms");
    mark.innerHTML = '<span class="wm-base">Wavents</span><span class="wm-layer" aria-hidden="true">' +
      '<span class="wm-slot wm-une"' + shut + '><span class="wm-in">Une&thinsp;</span></span>' +
      '<span class="wm-slot wm-w1"><span class="wm-in">Wave</span></span>' +
      '<span class="wm-slot wm-sp"' + shut + '><span class="wm-in">&nbsp;</span></span>' +
      '<span class="wm-slot wm-de"' + shut + '><span class="wm-in">d’</span></span>' +
      '<span class="wm-slot wm-w2"><span class="wm-in"><span class="wm-slot wm-eve"' + shut + '>eve</span>nts</span></span>' +
      '</span><span class="wm-ruler"></span>';
    const layer = $(".wm-layer", mark);
    const ruler = $(".wm-ruler", mark);
    const [une, w1, sp, de, w2] = ["une", "w1", "sp", "de", "w2"].map((k) => $(".wm-" + k, mark));
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));
    const eve = () => $(".wm-eve", w2);
    const cap = () => $(".wm-cap", w1);
    const resize = (s, to) => {
      s.style.width = s.offsetWidth + "px";
      void s.offsetWidth;
      s.style.width = to + "px";
    };
    // Ouvre ou ferme un morceau ; `shown` à faux garde sa place, invisible.
    const open = (s, on, shown = on) => {
      resize(s, on ? s.firstChild.offsetWidth : 0);
      s.style.opacity = shown ? 1 : 0;
    };
    const eveOpen = (on) => {
      const e = eve();
      resize(e, on ? e.scrollWidth : 0);
      e.style.opacity = on ? 1 : 0;
    };
    // Le mot sort par le haut, le nouveau entre par le bas, la largeur suit
    // en continu sur toute la durée.
    const flip = async (s, html, after) => {
      const inner = s.firstChild;
      ruler.innerHTML = html;
      resize(s, ruler.offsetWidth);
      await inner.animate([{ opacity: 1, transform: "none" }, { opacity: 0, transform: "translateY(-.3em)" }],
        { duration: T * 0.45, easing: "cubic-bezier(.5,0,.75,0)", fill: "forwards" }).finished;
      inner.innerHTML = html;
      if (after) after();
      await inner.animate([{ opacity: 0, transform: "translateY(.3em)" }, { opacity: 1, transform: "none" }],
        { duration: T * 0.55, easing: "cubic-bezier(.25,1,.5,1)", fill: "forwards" }).finished;
    };
    // Sur petit écran, le texte rétrécit pour ne pas toucher le bouton.
    const fit = (text) => {
      const links = $(".nav-links");
      if (!links) return;
      ruler.textContent = text;
      const room = links.getBoundingClientRect().left - mark.getBoundingClientRect().left - 12;
      const scale = Math.min(1, room / ruler.offsetWidth);
      layer.style.transform = scale < 1 ? `scale(${scale.toFixed(3)})` : "";
    };
    const play = async () => {
      mark.classList.add("wm-play");
      // 1 → 2 : Wave · events
      fit("Wave events"); open(sp, true); eveOpen(true);
      await wait(T + HOLD);
      // 2 → 3 : Vague · événements, déjà placés comme dans la phrase
      fit("Une vague d’événements"); open(une, true, false); open(de, true, false);
      await Promise.all([flip(w1, CAP), flip(w2, "événements")]);
      await wait(HOLD);
      // 3 → 4 : Une vague d'événements (seuls « Une », « d' » et le V changent)
      une.style.opacity = de.style.opacity = 1; cap().classList.add("low");
      await wait(T + PEAK);
      // 4 → 3
      une.style.opacity = de.style.opacity = 0; cap().classList.remove("low");
      await wait(T + HOLD);
      // 3 → 2
      fit("Wave events"); open(une, false); open(de, false);
      await Promise.all([flip(w1, "Wave"), flip(w2, EVENTS, () => { eve().style.width = "auto"; })]);
      await wait(HOLD);
      // 2 → 1 : Wavents
      fit("Wavents"); open(sp, false); eveOpen(false);
      await wait(T);
      mark.classList.remove("wm-play");
    };
    const later = (min, max) => setTimeout(run, min + Math.random() * (max - min));
    const run = async () => {
      if (!document.hidden) await play();
      later(15000, 20000);
    };
    later(5000, 10000);
  }

  /* Apparitions au défilement */
  const reveals = $$(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) { entry.target.classList.add("is-in"); io.unobserve(entry.target); }
      });
    // Seuil à 0 (30/09, retour de Paul) : sur mobile, le haut du téléphone
    // de l'accueil dépasse à peine sous les boutons ; avec 12 % exigés, il
    // restait invisible au premier affichage et on ne devinait pas la suite.
    }, { threshold: 0, rootMargin: "0px" });
    reveals.forEach((el) => io.observe(el));
  } else {
    reveals.forEach((el) => el.classList.add("is-in"));
  }

  /* Chiffres qui défilent */
  $$("[data-count]").forEach((el) => {
    const target = parseInt(el.dataset.count, 10);
    if (!target || !("IntersectionObserver" in window)) return;
    el.textContent = "0";
    const io = new IntersectionObserver(([entry]) => {
      if (!entry.isIntersecting) return;
      io.disconnect();
      const t0 = performance.now(), dur = 1400;
      const step = (t) => {
        const k = Math.min(1, (t - t0) / dur);
        el.textContent = Math.round(target * (1 - Math.pow(1 - k, 3))).toLocaleString("fr-FR");
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    });
    io.observe(el);
  });

  /* Carrousel du week-end */
  $$("[data-scroll]").forEach((btn) => btn.addEventListener("click", () => {
    const scroller = $(".scroller", btn.closest(".section"));
    if (!scroller) return;
    scroller.scrollBy({ left: parseInt(btn.dataset.scroll, 10) * scroller.clientWidth * 0.85, behavior: "smooth" });
  }));

  /* Agenda : un onglet par jour, filtres, « voir les autres » */
  const agenda = $(".agenda");
  if (agenda) {
    const PREVIEW = 12;
    const state = { cat: "", free: false, kids: false, q: "", day: "0" };
    const days = $$(".agenda .day");
    const tabs = $$(".day-tab");
    const count = $("#count");
    const empty = $("#empty");
    const expanded = new Set();

    const matches = (c) => (!state.cat || c.dataset.cat === state.cat)
      && (!state.free || c.dataset.free === "1")
      && (!state.kids || c.dataset.kids === "1")
      && (!state.q || c.dataset.q.includes(state.q));

    const apply = () => {
      const filtering = state.cat || state.free || state.kids || state.q;
      let total = 0, firstWithResults = null;
      days.forEach((d) => {
        const key = d.dataset.day;
        let n = 0;
        $$("[data-cat]", d).forEach((c) => {
          const ok = matches(c);
          if (ok) n++;
          c.hidden = !ok || (!expanded.has(key) && n > PREVIEW);
        });
        total += n;
        if (n && firstWithResults === null) firstWithResults = key;
        const tab = tabs.find((t) => t.dataset.day === key);
        if (tab) { tab.hidden = n === 0; $("small", tab).textContent = n; }
        const more = $(".day-more", d);
        if (more) {
          more.hidden = expanded.has(key) || n <= PREVIEW;
          $("button", more).textContent = `Voir les ${n - PREVIEW} autres sorties`;
        }
        d.dataset.count = n;
      });
      // Jour affiché : celui choisi, ou le premier qui a des résultats.
      const current = days.find((d) => d.dataset.day === state.day);
      if (!current || current.dataset.count === "0") state.day = firstWithResults ?? "0";
      days.forEach((d) => d.classList.toggle("is-on", d.dataset.day === state.day));
      tabs.forEach((t) => t.classList.toggle("is-on", t.dataset.day === state.day));
      if (empty) empty.hidden = total > 0;
      if (count) count.textContent = filtering ? `${total} sortie${total > 1 ? "s" : ""} trouvée${total > 1 ? "s" : ""}` : "";
    };

    tabs.forEach((tab) => tab.addEventListener("click", () => {
      state.day = tab.dataset.day;
      apply();
      tab.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }));
    $$("[data-more]").forEach((btn) => btn.addEventListener("click", () => {
      expanded.add(btn.closest(".day").dataset.day);
      apply();
    }));
    $$(".chip[data-cat]").forEach((chip) => chip.addEventListener("click", () => {
      state.cat = chip.dataset.cat;
      $$(".chip[data-cat]").forEach((c) => c.classList.toggle("is-on", c === chip));
      apply();
    }));
    $$(".chip[data-toggle]").forEach((chip) => chip.addEventListener("click", () => {
      const key = chip.dataset.toggle;
      state[key] = !state[key];
      chip.classList.toggle("is-on", state[key]);
      apply();
    }));
    const q = $("#q");
    if (q) q.addEventListener("input", () => { state.q = fold(q.value.trim()); apply(); });

    /* Tuile « Pour les enfants » : active le filtre Enfants */
    $$("[data-filter-kids]").forEach((tile) => tile.addEventListener("click", (ev) => {
      const chip = $('.chip[data-toggle="kids"]');
      if (chip) { ev.preventDefault(); if (!state.kids) chip.click(); $("#agenda").scrollIntoView({ behavior: "smooth" }); }
    }));
    /* Tuiles de catégorie : filtrent l'agenda */
    $$("[data-filter-cat]").forEach((tile) => tile.addEventListener("click", (ev) => {
      const chip = $(`.chip[data-cat="${tile.dataset.filterCat}"]`);
      if (chip) { ev.preventDefault(); chip.click(); $("#agenda").scrollIntoView({ behavior: "smooth" }); }
    }));
    apply();
  }

  /* Cartes Leaflet (chargé en différé) */
  const whenLeaflet = (fn) => {
    if (window.L && window.L.markerClusterGroup) return fn();
    window.addEventListener("load", () => window.L && fn());
  };
  const tiles = () => window.L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 19,
  });
  const esc = (s) => (s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const mapEl = $("#map");
  const dataEl = $("#wv-data");
  if (mapEl && dataEl) {
    const data = JSON.parse(dataEl.textContent);
    let started = false;
    const start = () => whenLeaflet(() => {
      if (started) return;
      started = true;
      const L = window.L;
      const map = L.map(mapEl, { scrollWheelZoom: false, zoomControl: true }).setView(data.home, 11);
      tiles().addTo(map);
      map.on("focus", () => map.scrollWheelZoom.enable());
      map.on("blur", () => map.scrollWheelZoom.disable());
      L.marker(data.home, { icon: L.divIcon({ className: "", html: '<div class="wv-me"></div>', iconSize: [18, 18] }), interactive: false }).addTo(map);

      // Regroupement : extension MarkerCluster (aucun chevauchement, éventail
      // pour les points au même endroit). Pastilles comme dans l'appli
      // (EventPinCluster) : couleur de la catégorie la plus présente.
      const pinIcon = (cat) => L.divIcon({
        className: "",
        html: `<div class="wv-pin" style="--cat:${cat.c}">${cat.e}</div>`,
        iconSize: [36, 46], iconAnchor: [18, 46], popupAnchor: [0, -40],
      });
      const clusters = L.markerClusterGroup({
        maxClusterRadius: 56,
        showCoverageOnHover: false,
        spiderfyOnMaxZoom: true,
        iconCreateFunction: (cluster) => {
          const tally = {};
          cluster.getAllChildMarkers().forEach((m) => { tally[m.options.cat] = (tally[m.options.cat] || 0) + 1; });
          const top = Object.keys(tally).sort((a, b) => tally[b] - tally[a])[0];
          const color = (data.cats[top] || data.cats.associatif_autre).c;
          const n = cluster.getChildCount();
          const size = Math.min(52, 36 + Math.sqrt(n) * 2.5);
          return L.divIcon({ className: "", html: `<div class="wv-cluster" style="--cat:${color};width:${size}px;height:${size}px"><b>${n}</b></div>`, iconSize: [size, size] });
        },
      });
      const markers = data.points.map((p) => {
        const cat = data.cats[p.c] || data.cats.associatif_autre;
        return L.marker([p.la, p.lo], { icon: pinIcon(cat), cat: p.c, kids: p.k === 1 })
          .bindPopup(`<div class="wv-pop"><b>${esc(p.t)}</b><small>${esc(p.d)} · ${esc(p.p)}</small><a href="${p.u}">Voir la sortie →</a></div>`);
      });
      const show = (cat, kids) => {
        clusters.clearLayers();
        clusters.addLayers(markers.filter((m) => (!cat || m.options.cat === cat) && (!kids || m.options.kids)));
      };
      show("");
      map.addLayer(clusters);
      // Filtres sous la carte : une catégorie à la fois (« Tout » pour revenir).
      $$(".map-chip").forEach((chip) => chip.addEventListener("click", () => {
        $$(".map-chip").forEach((c) => c.classList.toggle("is-on", c === chip));
        show(chip.dataset.mapCat || "", "mapKids" in chip.dataset);
      }));
    });
    if ("IntersectionObserver" in window) {
      const io = new IntersectionObserver(([entry]) => { if (entry.isIntersecting) { io.disconnect(); start(); } }, { rootMargin: "300px" });
      io.observe(mapEl);
    } else start();
  }

  /* Mini-carte d'une page événement */
  const mini = $("#mini-map");
  if (mini) whenLeaflet(() => {
    const L = window.L;
    const lat = parseFloat(mini.dataset.lat), lon = parseFloat(mini.dataset.lon);
    const map = L.map(mini, { scrollWheelZoom: false, dragging: !L.Browser.mobile, zoomControl: false }).setView([lat, lon], 14);
    tiles().addTo(map);
    L.marker([lat, lon], {
      icon: L.divIcon({ className: "", html: `<div class="wv-pin big" style="--cat:${mini.dataset.color}"><span class="ms">${mini.dataset.icon || "place"}</span></div>`, iconSize: [46, 58], iconAnchor: [23, 58] }),
    }).addTo(map);
  });

  /* Partage : feuille de partage du téléphone, sinon copie du lien (avec un
     retour visible dans tous les cas, jamais d'échec silencieux). */
  const copyLink = async (url) => {
    try { await navigator.clipboard.writeText(url); return true; } catch (e) { /* repli ci-dessous */ }
    const ta = document.createElement("textarea");
    ta.value = url; ta.setAttribute("readonly", ""); ta.style.cssText = "position:fixed;opacity:0";
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    return ok;
  };
  const mobile = window.matchMedia("(pointer: coarse)").matches;
  $$("[data-share]").forEach((btn) => btn.addEventListener("click", async () => {
    const url = location.href;
    if (mobile && navigator.share) {
      try { await navigator.share({ title: document.title, url }); return; }
      catch (e) { if (e && e.name === "AbortError") return; }
    }
    const label = btn.innerHTML;
    if (await copyLink(url)) {
      btn.textContent = "✅ Lien copié !";
      setTimeout(() => { btn.innerHTML = label; }, 2200);
    } else {
      window.prompt("Copie ce lien pour le partager :", url);
    }
  }));
})();
