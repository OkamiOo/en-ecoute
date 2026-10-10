// Fait passer les données de page.js (monde de la page) vers le fond de
// l'extension, le seul à pouvoir joindre le pont sur 127.0.0.1.
(() => {
  // après une mise à jour, l'extension réinjecte ce script : une seule copie
  if (globalThis.__enEcouteRelais) return;
  globalThis.__enEcouteRelais = true;
  const api = globalThis.browser || globalThis.chrome;

  document.addEventListener("en-ecoute:etat", (e) => {
    if (typeof e.detail !== "string") return;
    let etat;
    try {
      etat = JSON.parse(e.detail);
    } catch (_) {
      return;
    }
    const message = etat && etat.fermeture ? { type: "fermeture" } : { type: "etat", etat };
    try {
      const envoi = api.runtime.sendMessage(message);
      if (envoi && typeof envoi.catch === "function") envoi.catch(() => {});
    } catch (_) {
      // extension rechargée ou désactivée : ce script n'a plus personne à qui parler
    }
  });
})();
