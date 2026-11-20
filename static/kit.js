// Kit organisateur (pages /o/<slug>/kit/, 20/11/2026) : visuel carré
// « Retrouve nos sorties sur Wavents » et QR code dessinés dans le
// navigateur, mêmes couleurs et police que l'image de partage des fiches
// (fonction share-card du dépôt Wavents) ; copie du texte pour Facebook.
(function () {
  "use strict";
  var dataEl = document.getElementById("kit-data");
  if (!dataEl) return;
  var data = JSON.parse(dataEl.textContent);
  var base = (document.currentScript && document.currentScript.src) || location.href;
  var asset = function (path) { return new URL(path, base).href; };

  var NAVY = [11, 37, 69], DEEP = [6, 95, 187];
  var SUN = "#FFC857", SOFT = "#D6E8FA", WHITE = "#FFFFFF";
  var W = 1080, M = 72;

  function qrMatrix(text) {
    if (typeof qrcode !== "function") return null;
    var qr = qrcode(0, "M");
    qr.addData(text);
    qr.make();
    return qr;
  }

  function drawQr(ctx, qr, x, y, size) {
    var n = qr.getModuleCount();
    var cell = Math.floor(size / n);
    var off = Math.floor((size - cell * n) / 2);
    ctx.fillStyle = "#0B2545";
    for (var r = 0; r < n; r++) {
      for (var c = 0; c < n; c++) {
        if (qr.isDark(r, c)) ctx.fillRect(x + off + c * cell, y + off + r * cell, cell, cell);
      }
    }
  }

  function roundRect(ctx, x, y, w, h, rad) {
    ctx.beginPath();
    ctx.moveTo(x + rad, y);
    ctx.arcTo(x + w, y, x + w, y + h, rad);
    ctx.arcTo(x + w, y + h, x, y + h, rad);
    ctx.arcTo(x, y + h, x, y, rad);
    ctx.arcTo(x, y, x + w, y, rad);
    ctx.closePath();
  }

  // Coupe en lignes de largeur maximale ; null si ça ne tient pas en
  // maxLines (jamais de texte tronqué : on réduit ou on n'affiche pas).
  function wrap(ctx, text, width, maxLines) {
    var words = text.split(/\s+/), lines = [], line = "";
    for (var i = 0; i < words.length; i++) {
      var test = line ? line + " " + words[i] : words[i];
      if (ctx.measureText(test).width <= width) { line = test; continue; }
      if (!line) return null; // un mot plus large que la ligne
      lines.push(line);
      line = words[i];
      if (ctx.measureText(line).width > width) return null;
    }
    if (line) lines.push(line);
    return lines.length <= maxLines ? lines : null;
  }

  function fit(ctx, text, weight, sizes, width, maxLines) {
    for (var i = 0; i < sizes.length; i++) {
      ctx.font = weight + " " + sizes[i] + "px KitNunito";
      var lines = wrap(ctx, text, width, maxLines);
      if (lines) return { size: sizes[i], lines: lines };
    }
    return null;
  }

  function drawVisual(canvas, pin, qr) {
    var ctx = canvas.getContext("2d");
    var g = ctx.createLinearGradient(0, 0, 0, W);
    g.addColorStop(0, "rgb(" + DEEP.join(",") + ")");
    g.addColorStop(1, "rgb(" + NAVY.join(",") + ")");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, W);

    // Trois vagues en bas, comme l'image de partage.
    var waves = [["#01BAEF", 16, W - 74, 0], ["#1E88E5", 18, W - 50, 1], ["#065FBB", 14, W - 28, 2]];
    waves.forEach(function (wv) {
      ctx.beginPath();
      ctx.moveTo(0, W);
      for (var x = 0; x <= W; x += 6) ctx.lineTo(x, wv[2] + wv[1] * Math.sin(x / 90 + wv[3]));
      ctx.lineTo(W, W);
      ctx.closePath();
      ctx.fillStyle = wv[0];
      ctx.fill();
    });

    // Marque.
    ctx.textBaseline = "alphabetic";
    var pinH = 84, pinW = pin ? Math.round(pin.width * pinH / pin.height) : 0;
    if (pin) ctx.drawImage(pin, M, M, pinW, pinH);
    ctx.fillStyle = WHITE;
    ctx.font = "900 58px KitNunito";
    ctx.fillText("Wavents", M + pinW + (pin ? 18 : 0), M + 62);

    // Titre.
    var y = M + pinH + 70;
    var title = fit(ctx, "Retrouve nos sorties sur Wavents", "900", [96, 88, 80], W - 2 * M, 3);
    if (title) {
      ctx.font = "900 " + title.size + "px KitNunito";
      title.lines.forEach(function (l) { y += title.size; ctx.fillText(l, M, y); y += Math.round(title.size * 0.12); });
    }
    if (data.name) {
      var nm = fit(ctx, data.name, "800", [36, 32, 28], W - 2 * M, 1);
      if (nm) { ctx.font = "800 " + nm.size + "px KitNunito"; ctx.fillStyle = SOFT; y += 16 + nm.size; ctx.fillText(nm.lines[0], M, y); }
    }

    // QR code en bas à droite, dans un cadre blanc.
    var box = 300, bx = W - M - box, by = W - 130 - box - 52;
    if (qr) {
      ctx.fillStyle = WHITE;
      roundRect(ctx, bx, by, box, box, 28);
      ctx.fill();
      drawQr(ctx, qr, bx + 22, by + 22, box - 44);
      ctx.fillStyle = WHITE;
      ctx.font = "800 30px KitNunito";
      var cap = "Scanne pour tout voir";
      ctx.fillText(cap, bx + (box - ctx.measureText(cap).width) / 2, by + box + 44);
    }

    // Prochaines dates, à gauche du QR code.
    y += 56;
    var colW = qr ? bx - M - 36 : W - 2 * M, bottom = W - 110;
    for (var i = 0; i < data.events.length; i++) {
      var ev = data.events[i];
      var t = fit(ctx, ev.title, "800", [40, 36, 32], colW, 2);
      if (!t) continue;
      var h = 40 + t.lines.length * Math.round(t.size * 1.15) + 26;
      if (y + h > bottom) break;
      ctx.fillStyle = SUN;
      ctx.font = "800 32px KitNunito";
      y += 32;
      ctx.fillText(ev.when, M, y);
      ctx.fillStyle = WHITE;
      ctx.font = "800 " + t.size + "px KitNunito";
      y += 8;
      t.lines.forEach(function (l) { y += Math.round(t.size * 1.15); ctx.fillText(l, M, y); });
      y += 26;
    }
  }

  function drawQrCard(canvas, qr) {
    var ctx = canvas.getContext("2d");
    ctx.fillStyle = WHITE;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (qr) drawQr(ctx, qr, 40, 40, canvas.width - 80);
  }

  function download(canvas, name) {
    canvas.toBlob(function (blob) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      document.body.appendChild(a);
      a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
    }, "image/png");
  }

  function loadImage(src) {
    return new Promise(function (resolve) {
      var img = new Image();
      img.onload = function () { resolve(img); };
      img.onerror = function () { resolve(null); };
      img.src = src;
    });
  }

  function loadFonts() {
    if (!window.FontFace) return Promise.resolve();
    var faces = [["900", "fonts/Nunito-900.ttf"], ["800", "fonts/Nunito-800.ttf"]];
    return Promise.all(faces.map(function (f) {
      var face = new FontFace("KitNunito", "url(" + asset(f[1]) + ")", { weight: f[0] });
      return face.load().then(function (loaded) { document.fonts.add(loaded); }).catch(function () {});
    }));
  }

  var visual = document.getElementById("kit-canvas");
  var qrCanvas = document.getElementById("kit-qr");
  var qr = qrMatrix(data.url);
  Promise.all([loadFonts(), loadImage(asset("img/pin-card.png"))]).then(function (res) {
    drawVisual(visual, res[1], qr);
    drawQrCard(qrCanvas, qr);
  });

  document.getElementById("kit-download").addEventListener("click", function () { download(visual, "wavents-nos-sorties.png"); });
  document.getElementById("kit-qr-download").addEventListener("click", function () { download(qrCanvas, "wavents-qr-code.png"); });

  var shareBtn = document.getElementById("kit-share");
  if (navigator.canShare && window.File) {
    var probe = new File([""], "x.png", { type: "image/png" });
    if (navigator.canShare({ files: [probe] })) {
      shareBtn.hidden = false;
      shareBtn.addEventListener("click", function () {
        visual.toBlob(function (blob) {
          var file = new File([blob], "wavents-nos-sorties.png", { type: "image/png" });
          navigator.share({ files: [file], text: document.getElementById("kit-text").value }).catch(function () {});
        }, "image/png");
      });
    }
  }

  // Texte entier visible, sans barre de défilement.
  var textArea = document.getElementById("kit-text");
  var grow = function () { textArea.style.height = "auto"; textArea.style.height = (textArea.scrollHeight + 4) + "px"; };
  grow();
  window.addEventListener("resize", grow);

  var status = document.getElementById("kit-status");
  document.getElementById("kit-copy").addEventListener("click", function () {
    var area = document.getElementById("kit-text");
    var done = function () { status.textContent = "Copié ! Colle-le dans ta publication."; };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(area.value).then(done, function () { area.select(); document.execCommand("copy"); done(); });
    } else {
      area.select();
      document.execCommand("copy");
      done();
    }
  });
})();
