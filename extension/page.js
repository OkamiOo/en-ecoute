/*
  Tourne dans la page elle-même (monde MAIN) : c'est le seul endroit où l'on
  voit les lecteurs audio que le site crée sans les ajouter au document.

  Source principale : navigator.mediaSession, que les quatre services
  remplissent pour les touches multimédia du clavier. La position vient de
  l'élément <audio>/<video> qui joue, ou à défaut de setPositionState().
*/
(() => {
  "use strict";

  const SERVICES = {
    "open.spotify.com": "spotify",
    "www.deezer.com": "deezer",
    "music.youtube.com": "youtube-music",
    "music.apple.com": "apple-music",
    "beta.music.apple.com": "apple-music",
  };
  const service = SERVICES[location.hostname];
  if (!service || window.top !== window || !("mediaSession" in navigator)) return;

  // Firefox réinjecte les scripts dans les onglets ouverts quand l'extension
  // se met à jour : la copie déjà en place continue seule
  const DEJA = Symbol.for("en-ecoute");
  if (window[DEJA]) return;
  window[DEJA] = true;

  // pubs des comptes gratuits : rien ne doit s'afficher pendant ce temps
  const PUB = {
    "youtube-music": () => document.querySelector(".html5-video-player.ad-showing") !== null,
  };

  const EVENEMENT = "en-ecoute:etat";
  const SURVEILLES = ["play", "playing", "pause", "ended", "seeked", "durationchange", "ratechange"];

  const medias = [];          // lecteurs vus, le plus récemment lancé en tête
  let positionSite = null;    // dernier appel à setPositionState()
  let changementTitre = 0;
  let envoiPrevu = false;

  function suivre(el) {
    const i = medias.indexOf(el);
    if (i === 0) return;
    if (i > 0) medias.splice(i, 1);
    SURVEILLES.forEach((ev) => el.addEventListener(ev, planifier));
    medias.unshift(el);
    if (medias.length > 8) medias.length = 8;
  }

  // Les vidéos d'ambiance (Canvas de Spotify, pochettes animées d'Apple
  // Music) tournent en boucle et sans le son : on les écarte.
  function lecteurMusique(el) {
    return !el.loop && !el.muted;
  }

  function mediaActif() {
    const utiles = medias.filter(lecteurMusique);
    return utiles.find((el) => !el.paused && !el.ended) || utiles[0] || null;
  }

  const proto = HTMLMediaElement.prototype;
  const jouer = proto.play;
  proto.play = function () {
    suivre(this);
    planifier();
    return jouer.apply(this, arguments);
  };
  document.addEventListener("play", (e) => {
    if (e.target instanceof HTMLMediaElement) {
      suivre(e.target);
      planifier();
    }
  }, true);

  const MS = window.MediaSession && MediaSession.prototype;
  if (MS && MS.setPositionState) {
    const fixer = MS.setPositionState;
    MS.setPositionState = function (etat) {
      const retour = fixer.apply(this, arguments);
      positionSite = etat && isFinite(etat.duration) && etat.duration > 0
        ? {
            duree: etat.duration,
            pos: etat.position || 0,
            vitesse: etat.playbackRate || 1,
            t: performance.now(),
          }
        : null;
      planifier();
      return retour;
    };
  }
  if (MS) {
    for (const nom of ["metadata", "playbackState"]) {
      const d = Object.getOwnPropertyDescriptor(MS, nom);
      if (!d || !d.set || !d.configurable) continue;
      Object.defineProperty(MS, nom, {
        ...d,
        set(v) {
          d.set.call(this, v);
          planifier();
        },
      });
    }
  }

  // ---------------------------------------------------------------- pochette

  function plusGrande(artwork) {
    let src = "";
    let max = -1;
    for (const a of artwork || []) {
      const taille = Math.max(0, ...String(a.sizes || "").split(/\s+/).map((s) => parseInt(s, 10) || 0));
      if (a.src && taille >= max) {
        max = taille;
        src = a.src;
      }
    }
    return src;
  }

  // une image en blob: n'existe que dans cet onglet : on l'envoie en data:
  const blobs = { src: "", data: "" };
  function pochette(src) {
    if (!src.startsWith("blob:")) return src;
    if (blobs.src === src) return blobs.data;
    blobs.src = src;
    blobs.data = "";
    fetch(src)
      .then((r) => r.blob())
      .then((b) => new Promise((ok) => {
        const lecteur = new FileReader();
        lecteur.onload = () => ok(lecteur.result);
        lecteur.readAsDataURL(b);
      }))
      .then((data) => {
        if (blobs.src === src) {
          blobs.data = data;
          planifier();
        }
      })
      .catch(() => {});
    return "";
  }

  // ------------------------------------------------------------------- état

  let titreVu = "";

  function lire() {
    if (PUB[service] && PUB[service]()) {
      return { service, titre: "", artiste: "", album: "", pochette: "", duree: 0,
        position: null, vitesse: 1, enLecture: false, pub: true };
    }
    const md = navigator.mediaSession.metadata;
    const titre = (md && md.title) || "";
    const artiste = (md && md.artist) || "";
    if (titre + "\n" + artiste !== titreVu) {
      titreVu = titre + "\n" + artiste;
      changementTitre = performance.now();
    }

    const el = mediaActif();
    let enLecture = null;
    let duree = 0;
    let position = null;
    let vitesse = 1;

    if (el) {
      enLecture = !el.paused && !el.ended;
      if (isFinite(el.duration) && el.duration > 0) {
        duree = el.duration;
        position = el.currentTime;
        vitesse = el.playbackRate || 1;
      }
    }

    // aucun lecteur vu : seul le site peut dire s'il joue (un morceau
    // restauré au chargement de la page est affiché mais pas lancé)
    if (enLecture === null) enLecture = navigator.mediaSession.playbackState === "playing";

    // setPositionState n'est pris que s'il date du morceau en cours
    if (position === null && positionSite && positionSite.t >= changementTitre - 2000) {
      duree = positionSite.duree;
      position = positionSite.pos;
      if (enLecture) position += (performance.now() - positionSite.t) / 1000 * positionSite.vitesse;
      position = Math.min(position, duree);
      vitesse = positionSite.vitesse;
    }

    return {
      service,
      titre,
      artiste,
      album: (md && md.album) || "",
      pochette: md ? pochette(plusGrande(md.artwork)) : "",
      duree,
      position,
      vitesse,
      enLecture: Boolean(md) && enLecture,
    };
  }

  // ------------------------------------------------------------------ envoi

  let empreinte = "";
  let dernierEnvoi = 0;
  let repere = null;

  function envoyer(etat) {
    document.dispatchEvent(new CustomEvent(EVENEMENT, { detail: JSON.stringify(etat) }));
  }

  function verifier() {
    let etat;
    try {
      etat = lire();
    } catch (e) {
      return;
    }
    const maintenant = performance.now();
    const cle = JSON.stringify([etat.titre, etat.artiste, etat.album, etat.pochette,
      etat.enLecture, Math.round(etat.duree)]);

    // la position a sauté (avance rapide) : on prévient tout de suite
    let saut = false;
    if (repere && etat.position !== null) {
      const attendu = repere.pos + (etat.enLecture ? (maintenant - repere.t) / 1000 * repere.vitesse : 0);
      saut = Math.abs(attendu - etat.position) > 2;
    }

    if (cle !== empreinte || saut || maintenant - dernierEnvoi > 2500) {
      empreinte = cle;
      dernierEnvoi = maintenant;
      repere = etat.position === null ? null : { pos: etat.position, t: maintenant, vitesse: etat.vitesse };
      envoyer(etat);
    }
  }

  function planifier() {
    if (envoiPrevu) return;
    envoiPrevu = true;
    setTimeout(() => {
      envoiPrevu = false;
      verifier();
    }, 150);
  }

  setInterval(verifier, 1000);
  window.addEventListener("pagehide", () => envoyer({ fermeture: true }));
})();
