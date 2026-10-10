// Fond de l'extension : transmet le morceau en cours au pont local et, si un
// lien a été collé dans la fenêtre de l'extension, à Akibot.
const api = globalThis.browser || globalThis.chrome;
const PORT_DEFAUT = 4489;
const SITES = [
  "https://open.spotify.com/*",
  "https://www.deezer.com/*",
  "https://music.youtube.com/*",
  "https://music.apple.com/*",
  "https://beta.music.apple.com/*",
];
const LIEN_AKIBOT = /^https:\/\/akibot\.akiden\.fr\/\S+$/;
const version = api.runtime.getManifest().version;

// onglet -> { etat, recu, debut, derniere } : debut = quand il s'est mis à
// jouer, derniere = la dernière fois qu'on l'a vu jouer
const onglets = new Map();
let reglages = null;

// Un identifiant par navigateur : deux navigateurs ouverts en même temps
// peuvent avoir des onglets portant le même numéro.
function charger() {
  if (!reglages) {
    reglages = api.storage.local.get({ port: PORT_DEFAUT, id: "", akibot: "" }).then(async (r) => {
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
  if (zone !== "local") return;
  if (changes.port || changes.akibot) reglages = null;
  if (changes.akibot) {
    akibot = nouvelEnvoi();
    versAkibot();
  }
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
  if (!surDisque || !plusRecente(surDisque, version)) return;
  const { tentative } = await api.storage.local.get({ tentative: "" });
  if (tentative === surDisque) return;
  const soi = await api.management.getSelf();
  if (soi.installType !== "development") return;
  await api.storage.local.set({ tentative: surDisque });
  api.runtime.reload();
}

// ----------------------------------------------------------------- pont local

async function versPont(chemin, corps) {
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
    // pont arrêté ou absent : le prochain envoi, deux secondes plus tard, réessaiera
  }
}

// --------------------------------------------------------------------- Akibot

function nouvelEnvoi() {
  return { cle: "", le: 0, repere: null, vide: true, prevu: null, attente: 0 };
}
let akibot = nouvelEnvoi();
const autorisation = { valeur: true, le: 0 };

// Firefox demande un accord explicite avant d'envoyer le contenu d'un site
// ailleurs que sur la machine. Les autres navigateurs n'ont pas cette notion.
async function collecteAutorisee() {
  if (!api.runtime.getBrowserInfo) return true;
  if (Date.now() - autorisation.le < 60000) return autorisation.valeur;
  try {
    autorisation.valeur = await api.permissions.contains({ data_collection: ["websiteContent"] });
  } catch (_) {
    autorisation.valeur = true;
  }
  autorisation.le = Date.now();
  return autorisation.valeur;
}

// Même règle que le pont : l'onglet qui s'est mis à jouer en dernier, sinon le
// dernier à avoir joué.
function choisir() {
  const maintenant = Date.now();
  for (const [id, o] of onglets) {
    if (maintenant - o.recu > 20000) onglets.delete(id);
  }
  const avecTitre = [...onglets.values()].filter((o) => o.etat.titre || o.etat.artiste);
  if (!avecTitre.length) return null;
  const jouent = avecTitre.filter((o) => o.etat.enLecture);
  if (jouent.length) return jouent.reduce((a, b) => (b.debut > a.debut ? b : a));
  return avecTitre.reduce((a, b) => (b.derniere > a.derniere ? b : a));
}

async function noterStatut(code) {
  const { akibotStatut } = await api.storage.local.get({ akibotStatut: null });
  if (akibotStatut && akibotStatut.code === code && Date.now() - akibotStatut.le < 30000) return;
  await api.storage.local.set({ akibotStatut: { code, le: Date.now() } });
}

// Envoi à chaque changement (au plus une fois par seconde), et un battement
// toutes les dix secondes pour qu'Akibot sache que le navigateur est toujours là.
async function versAkibot() {
  const r = await charger();
  if (!LIEN_AKIBOT.test(r.akibot || "") || !(await collecteAutorisee())) return;
  const maintenant = Date.now();
  if (maintenant < akibot.attente) return;

  const o = choisir();
  if (!o && akibot.vide) return;

  let corps;
  let position = null;
  if (o) {
    const e = o.etat;
    position = e.position === null || e.position === undefined ? null
      : e.position + (e.enLecture ? (maintenant - o.recu) / 1000 * (e.vitesse || 1) : 0);
    corps = {
      v: 1, source: r.id, ext: version,
      service: e.service, titre: e.titre, artiste: e.artiste, album: e.album,
      pochette: /^https:\/\//.test(e.pochette || "") ? e.pochette : "",
      duree: e.duree || 0, position, vitesse: e.vitesse || 1, enLecture: Boolean(e.enLecture),
    };
  } else {
    corps = { v: 1, source: r.id, ext: version, vide: true };
  }

  const cle = o
    ? JSON.stringify([corps.service, corps.titre, corps.artiste, corps.album, corps.pochette, corps.enLecture, Math.round(corps.duree)])
    : "vide";
  let saut = false;
  if (o && akibot.repere && position !== null) {
    const attendu = akibot.repere.pos + (corps.enLecture ? (maintenant - akibot.repere.t) / 1000 * corps.vitesse : 0);
    saut = Math.abs(attendu - position) > 2;
  }
  if (cle === akibot.cle && !saut && maintenant - akibot.le < 10000) return;
  if (maintenant - akibot.le < 1000) {
    if (!akibot.prevu) akibot.prevu = setTimeout(() => { akibot.prevu = null; versAkibot(); }, 1000);
    return;
  }

  akibot.cle = cle;
  akibot.le = maintenant;
  akibot.vide = !o;
  akibot.repere = position === null ? null : { pos: position, t: maintenant };
  try {
    const reponse = await fetch(r.akibot, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(corps),
      signal: AbortSignal.timeout(5000),
    });
    noterStatut(reponse.status);
    if (!reponse.ok) {
      // lien refusé : inutile d'insister à chaque battement
      const delai = reponse.status === 429
        ? (parseInt(reponse.headers.get("Retry-After"), 10) || 10) * 1000
        : reponse.status === 404 ? 30000 : 10000;
      akibot.attente = Date.now() + delai;
      akibot.cle = "";
    }
  } catch (_) {
    noterStatut(0);
    akibot.attente = Date.now() + 10000;
    akibot.cle = "";
  }
}

// -------------------------------------------------------------- les onglets

api.runtime.onMessage.addListener((message, sender) => {
  const tab = sender.tab;
  if (!message || !tab || tab.id === undefined) return;
  charger().then((r) => {
    const onglet = r.id + "-" + tab.id;
    if (message.type === "etat") {
      const maintenant = Date.now();
      const ancien = onglets.get(tab.id);
      const enLecture = Boolean(message.etat.enLecture);
      onglets.set(tab.id, {
        etat: message.etat,
        recu: maintenant,
        debut: enLecture && !(ancien && ancien.etat.enLecture) ? maintenant : (ancien ? ancien.debut : 0),
        derniere: enLecture ? maintenant : (ancien ? ancien.derniere : 0),
      });
      versPont("/api/maj", Object.assign({}, message.etat, { onglet, ext: version }));
    } else if (message.type === "fermeture") {
      onglets.delete(tab.id);
      versPont("/api/fermer", { onglet });
    }
    versAkibot();
  });
});

api.tabs.onRemoved.addListener((tabId) => {
  if (!onglets.delete(tabId)) return;
  charger().then((r) => versPont("/api/fermer", { onglet: r.id + "-" + tabId }));
  versAkibot();
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
