const api = globalThis.browser || globalThis.chrome;
const $ = (id) => document.getElementById(id);

function afficherMorceau(etat, nbOnglets) {
  const zone = $("morceau");
  zone.textContent = "";
  if (!etat || !etat.title) {
    const vide = document.createElement("span");
    vide.className = "vide";
    vide.textContent = nbOnglets
      ? nbOnglets + " onglet(s) musical(aux) ouvert(s), rien en lecture"
      : "Rien en cours. Lance un morceau sur Deezer, Spotify, YouTube Music ou Apple Music.";
    zone.append(vide);
    return;
  }
  const lignes = [
    ["service", etat.service + (etat.status === "paused" ? " · en pause" : "")],
    ["titre", etat.title],
    ["artiste", etat.artist],
  ];
  for (const [classe, valeur] of lignes) {
    const div = document.createElement("div");
    div.className = classe;
    div.textContent = valeur;
    zone.append(div);
  }
}

let avecAkibot = false;

async function interroger(port) {
  const base = "http://127.0.0.1:" + port;
  try {
    const [etat, onglets] = await Promise.all([
      fetch(base + "/api/now", { cache: "no-store" }).then((r) => r.json()),
      fetch(base + "/api/onglets", { cache: "no-store" }).then((r) => r.json()),
    ]);
    $("pastille").className = "pastille ok";
    $("statut").textContent = "Pont connecté (port " + port + ")";
    afficherMorceau(etat, onglets.length);
  } catch (_) {
    // sans pont local, ce n'est une erreur que si Akibot n'est pas utilisé
    $("pastille").className = avecAkibot ? "pastille" : "pastille ko";
    $("statut").textContent = avecAkibot
      ? "Pont local non utilisé"
      : "Pont injoignable sur le port " + port;
    afficherMorceau(null, 0);
  }
}

// ------------------------------------------------------------------ Akibot

const LIEN_AKIBOT = /^https:\/\/akibot\.akiden\.fr\/\S+$/;

function afficherEtatAkibot(lien, statut) {
  const zone = $("etatAkibot");
  zone.className = "etat-akibot";
  if (!lien) {
    zone.textContent = "";
    return;
  }
  if (!statut) {
    zone.textContent = "En attente d'un morceau à envoyer.";
  } else if (statut.code >= 200 && statut.code < 300) {
    zone.className = "etat-akibot ok";
    zone.textContent = "Akibot reçoit la musique.";
  } else if (statut.code === 404) {
    zone.className = "etat-akibot ko";
    zone.textContent = "Lien refusé par Akibot : recopie-le depuis l'éditeur (il a peut-être été régénéré).";
  } else if (statut.code === 0) {
    zone.className = "etat-akibot ko";
    zone.textContent = "Akibot injoignable pour l'instant.";
  } else {
    zone.className = "etat-akibot ko";
    zone.textContent = "Akibot a répondu " + statut.code + ".";
  }
}

async function rafraichirAkibot() {
  const r = await api.storage.local.get({ akibot: "", akibotStatut: null });
  avecAkibot = Boolean(r.akibot);
  afficherEtatAkibot(r.akibot, r.akibotStatut);
}

$("enregistrerAkibot").addEventListener("click", () => {
  const lien = $("akibot").value.trim();
  if (lien && !LIEN_AKIBOT.test(lien)) {
    afficherEtatAkibot(lien, null);
    $("etatAkibot").className = "etat-akibot ko";
    $("etatAkibot").textContent = "Ce n'est pas un lien Akibot (il commence par https://akibot.akiden.fr/).";
    return;
  }
  const enregistrer = () => api.storage.local.set({ akibot: lien, akibotStatut: null }).then(rafraichirAkibot);
  // Firefox veut un accord avant que le contenu d'un site parte ailleurs que sur
  // la machine ; la demande doit partir directement du clic
  let demande = null;
  if (lien && api.runtime.getBrowserInfo) {
    try {
      demande = api.permissions.request({ data_collection: ["websiteContent"] });
    } catch (_) {
      demande = null;
    }
  }
  if (!demande) {
    enregistrer();
    return;
  }
  demande.then((accorde) => {
    if (accorde) enregistrer();
    else $("etatAkibot").textContent = "Sans ton accord, Firefox ne laisse rien partir vers Akibot.";
  }).catch(enregistrer);
});

// ---------------------------------------------------------- mises à jour

const DEPOT = "OkamiOo/en-ecoute";
const versionActuelle = api.runtime.getManifest().version;
$("version").textContent = "Version " + versionActuelle;

function plusRecente(a, b) {
  const pa = String(a).split(".").map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  }
  return false;
}

function annoncer(version, page, installeeParLePont) {
  const zone = $("maj");
  zone.textContent = "";
  zone.append("Version " + version + " disponible. ");
  if (installeeParLePont) {
    zone.append("Installe-la depuis OBS : Outils → Scripts → Vérifier les mises à jour.");
  } else {
    const lien = document.createElement("a");
    lien.href = page || "https://github.com/" + DEPOT + "/releases/latest";
    lien.target = "_blank";
    lien.textContent = "La télécharger";
    zone.append(lien);
  }
  zone.classList.add("visible");
}

// Le pont sait déjà s'il y a mieux ; sans lui, on demande à GitHub, au plus
// une fois toutes les six heures.
async function chercherMiseAJour(port) {
  try {
    const v = await fetch("http://127.0.0.1:" + port + "/api/version", { cache: "no-store" })
      .then((r) => r.json());
    if (v.disponible && plusRecente(v.disponible, versionActuelle)) annoncer(v.disponible, v.page, true);
    return;
  } catch (_) {
    // pas de pont : on passe à GitHub
  }
  let { derniere } = await api.storage.local.get({ derniere: null });
  if (!derniere || Date.now() - derniere.le > 6 * 3600 * 1000) {
    try {
      const r = await fetch("https://api.github.com/repos/" + DEPOT + "/releases/latest");
      const data = await r.json();
      derniere = { le: Date.now(), version: String(data.tag_name || "").replace(/^v/i, ""), page: data.html_url };
      await api.storage.local.set({ derniere });
    } catch (_) {
      return;
    }
  }
  if (derniere.version && plusRecente(derniere.version, versionActuelle)) {
    annoncer(derniere.version, derniere.page, false);
  }
}

let port = 4489;

api.storage.local.get({ port, akibot: "" }).then(async (r) => {
  port = r.port;
  $("port").value = port;
  $("akibot").value = r.akibot;
  await rafraichirAkibot();
  interroger(port);
  chercherMiseAJour(port);
  setInterval(() => {
    interroger(port);
    rafraichirAkibot();
  }, 1500);
});

$("enregistrer").addEventListener("click", () => {
  const choisi = parseInt($("port").value, 10);
  if (!(choisi >= 1024 && choisi <= 65535)) return;
  port = choisi;
  api.storage.local.set({ port }).then(() => interroger(port));
});
