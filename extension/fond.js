// Fond de l'extension : transmet l'état de chaque onglet musical au pont local.
const api = globalThis.browser || globalThis.chrome;
const PORT_DEFAUT = 4489;
const SITES = [
  "https://open.spotify.com/*",
  "https://www.deezer.com/*",
  "https://music.youtube.com/*",
  "https://music.apple.com/*",
  "https://beta.music.apple.com/*",
];
const onglets = new Set();
let reglages = null;

// Un identifiant par navigateur : deux navigateurs ouverts en même temps
// peuvent avoir des onglets portant le même numéro.
function charger() {
  if (!reglages) {
    reglages = api.storage.local.get({ port: PORT_DEFAUT, id: "" }).then(async (r) => {
      if (!r.id) {
        r.id = Math.random().toString(36).slice(2, 10);
        await api.storage.local.set({ id: r.id });
      }
      return r;
    });
  }
  return reglages;
}

api.storage.onChanged.addListener((changes, zone) => {
  if (zone === "local" && changes.port) reglages = null;
});

function plusRecente(a, b) {
  const pa = String(a).split(".").map((n) => parseInt(n, 10) || 0);
  const pb = String(b).split(".").map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) > (pb[i] || 0);
  }
  return false;
}

// Le pont annonce la version de l'extension présente dans son dossier. Si une
// mise à jour l'a remplacée, une extension chargée depuis ce dossier n'a qu'à
// se recharger. Une seule tentative par version : si l'extension a été chargée
// depuis un autre dossier, recharger ne changerait rien et tournerait en boucle.
async function suivreVersion(surDisque) {
  if (!surDisque || !plusRecente(surDisque, api.runtime.getManifest().version)) return;
  const { tentative } = await api.storage.local.get({ tentative: "" });
  if (tentative === surDisque) return;
  const soi = await api.management.getSelf();
  if (soi.installType !== "development") return;
  await api.storage.local.set({ tentative: surDisque });
  api.runtime.reload();
}

async function poster(chemin, corps) {
  const r = await charger();
  try {
    const reponse = await fetch("http://127.0.0.1:" + r.port + chemin, {
      method: "POST",
      headers: { "Content-Type": "text/plain" },
      body: JSON.stringify(corps),
      signal: AbortSignal.timeout(3000),
    });
    if (chemin === "/api/maj" && reponse.ok) {
      const info = await reponse.json().catch(() => null);
      if (info) suivreVersion(info.extension).catch(() => {});
    }
  } catch (_) {
    // pont arrêté : le prochain envoi, deux secondes plus tard, réessaiera
  }
}

api.runtime.onMessage.addListener((message, sender) => {
  const tab = sender.tab;
  if (!message || !tab || tab.id === undefined) return;
  charger().then((r) => {
    const onglet = r.id + "-" + tab.id;
    if (message.type === "etat") {
      onglets.add(tab.id);
      poster("/api/maj", Object.assign({}, message.etat, { onglet }));
    } else if (message.type === "fermeture") {
      onglets.delete(tab.id);
      poster("/api/fermer", { onglet });
    }
  });
});

api.tabs.onRemoved.addListener((tabId) => {
  if (!onglets.delete(tabId)) return;
  charger().then((r) => poster("/api/fermer", { onglet: r.id + "-" + tabId }));
});

// Après une mise à jour, les onglets déjà ouverts ont perdu leur relais vers
// l'extension. Le script de la page, lui, tourne toujours : on lui redonne un
// relais pour que la musique en cours continue de remonter sans recharger.
api.runtime.onInstalled.addListener(async (details) => {
  if (details.reason !== "update") return;
  const ouverts = await api.tabs.query({ url: SITES });
  for (const t of ouverts) {
    api.scripting.executeScript({ target: { tabId: t.id }, files: ["relais.js"] }).catch(() => {});
  }
});
