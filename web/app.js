/* ------------------------------------------------------------------
   En Écoute - logique de l'overlay
   Reçoit l'état via SSE (/api/events), retombe sur du polling si besoin.
   ------------------------------------------------------------------ */

(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const el = {
    card: $("card"), glow: $("glow"), art: $("art"), artFallback: $("artFallback"),
    title: $("title"), titleText: $("titleText"), artist: $("artist"),
    source: $("source"), fill: $("fill"), elapsed: $("elapsed"),
    total: $("total"), offline: $("offline"), eq: $("eq"),
  };

  // ---------------------------------------------------------- paramètres
  const q = new URLSearchParams(location.search);
  const opt = {
    layout: q.get("layout") || "bar",           // bar | card | minimal
    theme: q.get("theme") || "dark",            // dark | light | clean
    opacity: q.get("opacity"),                  // 0-100, null = opaque
    align: q.get("align") || "left",            // left | center | right
    accent: q.get("accent") || "auto",          // auto | #rrggbb
    art: q.get("art") !== "0",
    progress: q.get("progress") !== "0",
    time: q.get("time") !== "0",
    scale: parseFloat(q.get("scale") || "1"),
    width: parseInt(q.get("width") || "0", 10),   // 0 = largeur auto (suit le titre)
    label: q.get("label"),                      // texte du bandeau (def: nom du service)
    debug: q.get("debug") === "1",
    demo: q.get("demo") === "1",                // fausse piste, pour placer l'overlay
  };

  // "glass" et "solid" sont les anciens noms : ils pointent tous deux sur le
  // thème sombre, la translucidité étant désormais réglée par l'opacité.
  if (opt.theme === "glass" || opt.theme === "solid") opt.theme = "dark";
  document.body.dataset.theme = opt.theme;

  // Opacité du fond de la carte. Le thème "sans fond" n'a rien à opacifier.
  if (opt.theme !== "clean") {
    const raw = opt.opacity === null ? 100 : parseInt(opt.opacity, 10);
    const a = Math.max(0, Math.min(100, isNaN(raw) ? 100 : raw)) / 100;
    // style en ligne sur <body> : prioritaire sur les règles de thème
    document.body.style.setProperty("--bg-alpha", String(a));
    // le flou d'arrière-plan ne sert à rien sur un fond opaque (et coûte du GPU)
    document.body.style.setProperty("--blur", a >= 0.99 ? "0px" : "22px");
  }

  document.body.dataset.align = opt.align;
  document.body.dataset.art = opt.art ? "1" : "0";
  document.body.dataset.progress = opt.progress ? "1" : "0";
  document.body.dataset.time = opt.time ? "1" : "0";
  document.body.dataset.debug = opt.debug ? "1" : "0";
  el.card.dataset.layout = opt.layout;
  if (opt.scale !== 1) {
    document.documentElement.style.fontSize = 16 * opt.scale + "px";
    el.card.style.zoom = opt.scale;
  }
  if (opt.width > 0) {
    el.card.style.width = opt.width + "px";
    el.card.style.maxWidth = "100%";
  }
  if (opt.accent !== "auto") setAccent(opt.accent);

  // ------------------------------------------------------------- utils
  function fmt(sec) {
    if (!isFinite(sec) || sec < 0) sec = 0;
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
  }

  function setAccent(hex) {
    document.documentElement.style.setProperty("--accent", hex);
    document.documentElement.style.setProperty(
      "--accent-soft", hexToRgba(hex, 0.38));
  }

  function hexToRgba(hex, a) {
    const h = hex.replace("#", "");
    const n = parseInt(h.length === 3 ? h.split("").map(c => c + c).join("") : h, 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
  }

  /* Couleur dominante de la pochette : on échantillonne en 24x24 et on garde
     le pixel le plus saturé, puis on le remonte en luminosité pour rester lisible. */
  function dominantColor(img) {
    try {
      const c = document.createElement("canvas");
      c.width = c.height = 24;
      const ctx = c.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(img, 0, 0, 24, 24);
      const d = ctx.getImageData(0, 0, 24, 24).data;
      let best = null, bestScore = -1;
      for (let i = 0; i < d.length; i += 4) {
        const r = d[i], g = d[i + 1], b = d[i + 2];
        const max = Math.max(r, g, b), min = Math.min(r, g, b);
        const sat = max === 0 ? 0 : (max - min) / max;
        const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
        const score = sat * 1.6 + (1 - Math.abs(lum - 0.55)) * 0.7;
        if (score > bestScore) { bestScore = score; best = [r, g, b]; }
      }
      if (!best) return null;
      // remonte la luminosité si la couleur est trop sombre
      let [r, g, b] = best;
      const lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
      if (lum < 0.42) {
        const k = 0.42 / Math.max(lum, 0.05);
        r = Math.min(255, r * k); g = Math.min(255, g * k); b = Math.min(255, b * k);
      }
      const to = (v) => Math.round(v).toString(16).padStart(2, "0");
      return "#" + to(r) + to(g) + to(b);
    } catch (e) {
      return null;
    }
  }

  // ------------------------------------------------------- rendu du titre
  function applyTitle(text) {
    el.titleText.textContent = text;
    el.title.classList.remove("scroll");
    el.title.style.removeProperty("--scroll-dist");
    // mesure après peinture pour savoir si ça déborde
    requestAnimationFrame(() => {
      const over = el.titleText.scrollWidth - el.title.clientWidth;
      if (over > 6 && opt.layout !== "card") {
        el.title.style.setProperty("--scroll-dist", -(over + 48) + "px");
        el.title.style.setProperty("--scroll-time", Math.max(8, (over + 48) / 26) + "s");
        el.title.classList.add("scroll");
      }
    });
  }

  // couleur de repli quand la pochette manque
  const COULEURS = {
    spotify: "#1ed760",
    deezer: "#a238ff",
    "youtube-music": "#ff0033",
    "apple-music": "#fa243c",
  };

  // ------------------------------------------------------------- état
  let cur = null;
  let artShown;
  let baseline = { pos: 0, dur: 0, at: performance.now(), playing: false };

  function render(s) {
    const newTrack = !cur || cur.track_id !== s.track_id;
    cur = s;

    el.card.classList.toggle("hidden", !s.visible);
    el.card.classList.toggle("paused", s.status !== "playing");
    // position inconnue (mode strict) : mieux vaut pas de barre qu'une fausse
    el.card.classList.toggle("no-progress",
      s.position === null || s.position === undefined);
    el.source.textContent = opt.label || s.service || "En écoute";

    if (newTrack) {
      applyTitle(s.title || "—");
      // l'album n'est ajouté que s'il apporte une information (≠ du titre)
      let sub = s.artist || "";
      if (s.album && s.album !== s.title) {
        sub = sub ? sub + " · " + s.album : s.album;
      }
      el.artist.textContent = sub;
      el.artist.title = sub;
    }
    // la pochette peut arriver une seconde après le titre
    if (newTrack || s.art !== artShown) {
      artShown = s.art;
      loadArt(s.art, s.source);
    }

    const incoming = s.position || 0;
    let target = incoming;
    // Le serveur renvoie un point de repère toutes les ~2 s. Sur un écart
    // minime, on converge en douceur : sinon la barre sauterait sans arrêt.
    // Un écart franc (avance rapide, changement de piste) est appliqué net.
    if (!newTrack && baseline.playing && s.status === "playing") {
      const shown = currentPos();
      const diff = incoming - shown;
      if (Math.abs(diff) < 1.5) target = shown + diff * 0.35;
    }

    baseline = {
      pos: target,
      dur: s.duration || 0,
      at: performance.now(),
      playing: s.status === "playing",
    };
    tick();
  }

  function loadArt(url, service) {
    if (!url) {
      el.art.classList.remove("show");
      el.artFallback.classList.remove("hide");
      if (opt.accent === "auto") setAccent(COULEURS[service] || "#8f5bff");
      return;
    }
    const img = new Image();
    img.onload = () => {
      el.art.src = img.src;
      el.art.classList.add("show");
      el.artFallback.classList.add("hide");
      if (opt.accent === "auto") {
        const c = dominantColor(img);
        if (c) setAccent(c);
      }
    };
    img.onerror = () => {
      el.art.classList.remove("show");
      el.artFallback.classList.remove("hide");
    };
    img.src = url;
  }

  // position interpolée localement depuis le dernier point de repère du serveur
  function currentPos() {
    let pos = baseline.pos;
    if (baseline.playing) pos += (performance.now() - baseline.at) / 1000;
    if (baseline.dur > 0) pos = Math.min(pos, baseline.dur);
    return Math.max(0, pos);
  }

  function tick() {
    if (!cur || !cur.visible) return;
    let pos = currentPos();
    const dur = baseline.dur;
    if (dur > 0) {
      pos = Math.min(pos, dur);
      el.fill.style.width = (pos / dur * 100).toFixed(2) + "%";
      el.total.textContent = fmt(dur);
    } else {
      el.fill.style.width = "0%";
      el.total.textContent = "--:--";
    }
    el.elapsed.textContent = fmt(pos);
  }
  setInterval(tick, 250);

  // -------------------------------------------------------- transport SSE
  let es = null;
  let pollTimer = null;
  let offlineTimer = null;

  function markOnline() {
    clearTimeout(offlineTimer);
    el.offline.classList.remove("show");
  }
  function markOffline() {
    clearTimeout(offlineTimer);
    offlineTimer = setTimeout(() => el.offline.classList.add("show"), 2500);
    el.card.classList.add("hidden");
  }

  function connect() {
    try {
      es = new EventSource("/api/events");
      es.onmessage = (ev) => {
        markOnline();
        try { render(JSON.parse(ev.data)); } catch (e) { /* trame partielle */ }
      };
      es.onerror = () => {
        markOffline();
        es.close();
        setTimeout(connect, 2000);   // le serveur redémarre peut-être
      };
    } catch (e) {
      startPolling();
    }
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const r = await fetch("/api/now", { cache: "no-store" });
        render(await r.json());
        markOnline();
      } catch (e) {
        markOffline();
      }
    }, 1000);
  }

  // Mode démo : une fausse piste pour placer et dimensionner l'overlay dans
  // OBS sans avoir à lancer de musique.
  if (opt.demo) {
    render({
      visible: true, status: "playing", source: "spotify", service: "Spotify",
      title: "Titre de démonstration", artist: "Nom de l'artiste",
      album: "", duration: 213, position: 47, art: null, track_id: "demo",
    });
  } else {
    connect();
  }
})();
