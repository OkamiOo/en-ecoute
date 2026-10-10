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
    $("pastille").className = "pastille ko";
    $("statut").textContent = "Pont injoignable sur le port " + port;
    afficherMorceau(null, 0);
  }
}

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

api.storage.local.get({ port }).then((r) => {
  port = r.port;
  $("port").value = port;
  interroger(port);
  chercherMiseAJour(port);
  setInterval(() => interroger(port), 1500);
});

$("enregistrer").addEventListener("click", () => {
  const choisi = parseInt($("port").value, 10);
  if (!(choisi >= 1024 && choisi <= 65535)) return;
  port = choisi;
  api.storage.local.set({ port }).then(() => interroger(port));
});
