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

  /* Apparitions au défilement */
  const reveals = $$(".reveal");
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) { entry.target.classList.add("is-in"); io.unobserve(entry.target); }
      });
    }, { threshold: 0.12, rootMargin: "0px 0px -40px 0px" });
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

  /* Agenda : filtres */
  const agenda = $(".agenda");
  if (agenda) {
    const state = { cat: "", free: false, kids: false, q: "" };
    const cards = $$(".agenda [data-cat]");
    const days = $$(".agenda .day");
    const count = $("#count");
    const empty = $("#empty");
    const more = $("#more");
    const moreBox = more && more.parentElement;
    let opened = false;

    const apply = () => {
      const filtering = state.cat || state.free || state.kids || state.q;
      let shown = 0;
      cards.forEach((c) => {
        const ok = (!state.cat || c.dataset.cat === state.cat)
          && (!state.free || c.dataset.free === "1")
          && (!state.kids || c.dataset.kids === "1")
          && (!state.q || c.dataset.q.includes(state.q));
        c.hidden = !ok;
        if (ok) shown++;
      });
      days.forEach((d) => {
        const any = $$("[data-cat]", d).some((c) => !c.hidden);
        d.hidden = !any;
        d.classList.toggle("is-open", Boolean(filtering) || opened);
      });
      if (moreBox) moreBox.hidden = Boolean(filtering) || opened || !days.some((d) => d.classList.contains("is-later"));
      if (empty) empty.hidden = shown > 0;
      if (count) count.textContent = filtering ? `${shown} sortie${shown > 1 ? "s" : ""} trouvée${shown > 1 ? "s" : ""}` : "";
    };

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
    if (more) more.addEventListener("click", () => { opened = true; apply(); });

    /* Tuiles de catégorie : filtrent l'agenda */
    $$("[data-filter-cat]").forEach((tile) => tile.addEventListener("click", (ev) => {
      const chip = $(`.chip[data-cat="${tile.dataset.filterCat}"]`);
      if (chip) { ev.preventDefault(); chip.click(); $("#agenda").scrollIntoView({ behavior: "smooth" }); }
    }));
    apply();
  }

  /* Cartes Leaflet (chargé en différé) */
  const whenLeaflet = (fn) => {
    if (window.L) return fn();
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

      // Regroupement maison par grille : léger, sans extension.
      const layer = L.layerGroup().addTo(map);
      const pinIcon = (cat) => L.divIcon({
        className: "",
        html: `<div class="wv-pin" style="--cat:${cat.c}"><span>${cat.e}</span></div>`,
        iconSize: [34, 34], iconAnchor: [17, 34], popupAnchor: [0, -30],
      });
      const draw = () => {
        layer.clearLayers();
        const zoom = map.getZoom();
        const cell = zoom >= 14 ? 0 : 64;
        const buckets = new Map();
        data.points.forEach((p) => {
          const pt = map.project([p.la, p.lo], zoom);
          const key = cell ? `${Math.floor(pt.x / cell)}:${Math.floor(pt.y / cell)}` : `${p.la}:${p.lo}:${p.u}`;
          if (!buckets.has(key)) buckets.set(key, []);
          buckets.get(key).push(p);
        });
        buckets.forEach((pts) => {
          if (pts.length === 1) {
            const p = pts[0];
            const cat = data.cats[p.c] || data.cats.associatif_autre;
            L.marker([p.la, p.lo], { icon: pinIcon(cat) })
              .bindPopup(`<div class="wv-pop"><b>${esc(p.t)}</b><small>${cat.e} ${esc(p.d)} · ${esc(p.p)}</small><a href="${p.u}">Voir la sortie →</a></div>`)
              .addTo(layer);
            return;
          }
          const la = pts.reduce((s, p) => s + p.la, 0) / pts.length;
          const lo = pts.reduce((s, p) => s + p.lo, 0) / pts.length;
          const size = Math.min(64, 38 + Math.sqrt(pts.length) * 4);
          L.marker([la, lo], {
            icon: L.divIcon({ className: "", html: `<div class="wv-cluster" style="width:${size}px;height:${size}px">${pts.length}</div>`, iconSize: [size, size] }),
          }).on("click", () => {
            const bounds = L.latLngBounds(pts.map((p) => [p.la, p.lo]));
            if (map.getZoom() >= 15 || bounds.getNorthEast().equals(bounds.getSouthWest())) {
              map.setView([la, lo], Math.max(map.getZoom(), 16));
              const list = pts.slice(0, 12).map((p) => `<li><a href="${p.u}">${(data.cats[p.c] || {}).e || ""} ${esc(p.t)}</a></li>`).join("");
              L.popup().setLatLng([la, lo]).setContent(`<div class="wv-pop"><b>${pts.length} sorties ici</b><ul style="padding-left:16px;margin:6px 0 0">${list}</ul></div>`).openOn(map);
            } else {
              map.fitBounds(bounds.pad(0.3));
            }
          }).addTo(layer);
        });
      };
      map.on("zoomend", draw);
      draw();
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
      icon: L.divIcon({ className: "", html: `<div class="wv-pin" style="--cat:${mini.dataset.color}"><span>${mini.dataset.emoji || "📍"}</span></div>`, iconSize: [34, 34], iconAnchor: [17, 34] }),
    }).addTo(map);
  });

  /* Partage */
  $$("[data-share]").forEach((btn) => btn.addEventListener("click", async () => {
    const data = { title: document.title, url: location.href };
    try {
      if (navigator.share) { await navigator.share(data); return; }
      await navigator.clipboard.writeText(location.href);
      const label = btn.textContent;
      btn.textContent = "✅ Lien copié !";
      setTimeout(() => { btn.textContent = label; }, 2000);
    } catch (e) { /* partage annulé */ }
  }));
})();
