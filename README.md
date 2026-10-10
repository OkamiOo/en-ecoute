# En Écoute

Affiche dans OBS le morceau que tu écoutes dans ton navigateur, sur **Deezer**,
**Spotify**, **YouTube Music** ou **Apple Music** (versions web).

Deux pièces travaillent ensemble :

- une **extension de navigateur** qui lit le morceau en cours dans l'onglet
  (titre, artiste, album, pochette, position) ;
- un **petit pont local** (`pont.py`) qui reçoit ces infos et les met à
  disposition d'OBS : un overlay prêt à l'emploi, et des fichiers texte pour
  ceux qui préfèrent faire leur propre mise en page.

```
onglet Deezer / Spotify / YT Music / Apple Music
        │  extension
        ▼
pont.py (127.0.0.1:4489) ──► source Navigateur d'OBS
        │
        └──► sortie/titre.txt, artiste.txt, morceau.txt, pochette.jpg ...
```

Tout reste sur ta machine : le pont n'écoute que sur `127.0.0.1` et n'accepte
les données que de l'extension.

Si tu utilises [Akibot](https://akibot.akiden.fr), tu peux te passer du pont et
de Python : l'extension envoie directement au widget **Musique en cours** de
l'éditeur d'overlays (voir [Avec Akibot](#avec-akibot)).

## Installation

Télécharge le zip de la [dernière version](https://github.com/OkamiOo/en-ecoute/releases/latest)
et décompresse-le là où il restera. Garde le dossier entier au même endroit :
le script OBS cherche `pont.py`, `web/` et `extension/` à côté de lui, et les
mises à jour s'installent dedans.

### 1. Python

Il faut Python 3.9 ou plus récent ([python.org](https://www.python.org/downloads/)
ou Microsoft Store). Rien d'autre à installer : le pont n'utilise que la
bibliothèque standard.

### 2. OBS

1. **Outils → Scripts → `+`**, puis choisis `en-ecoute.lua`.
2. Clique sur **Ajouter la source dans la scène active**.

C'est tout. Le pont démarre avec OBS et s'arrête avec lui, sans fenêtre.
Le champ Python se remplit tout seul.

### 3. L'extension

**Chrome, Brave, Edge, Opera, Opera GX, Vivaldi** (même méthode partout)

1. Ouvre la page des extensions : `chrome://extensions`, `brave://extensions`,
   `edge://extensions`, `opera://extensions` ou `vivaldi://extensions`.
2. Active le **Mode développeur**.
3. **Charger l'extension non empaquetée** (ou « décompressée »), puis choisis le
   dossier `extension`. Le bouton **Ouvrir le dossier de l'extension** du
   panneau OBS t'y emmène directement.

L'extension reste installée après redémarrage du navigateur. Ne déplace pas le
dossier ensuite, sinon le navigateur la perd.

**Firefox** (version 128 ou plus récente)

Firefox n'installe durablement que les extensions signées par Mozilla. Deux
possibilités :

- *Pour essayer* : `about:debugging#/runtime/this-firefox` → **Charger un module
  complémentaire temporaire** → choisis `extension/manifest.json`. Elle disparaît
  à la fermeture de Firefox.
- *Pour de bon* : fais-la signer gratuitement par Mozilla.
  1. Crée le paquet : `python pont.py --paquet` (donne `en-ecoute-extension.zip`).
  2. Sur [addons.mozilla.org/developers](https://addons.mozilla.org/developers/),
     soumets un nouveau module en choisissant de le distribuer toi-même
     (option « On your own », hors catalogue), puis envoie le zip.
  3. La validation est automatique et prend quelques minutes. Télécharge le
     `.xpi` signé et glisse-le dans Firefox. Ce fichier peut ensuite être donné à
     qui tu veux.

**Safari**

Safari n'existe plus sur Windows depuis 2012. Sur Mac, l'extension se convertit
avec Xcode (`xcrun safari-web-extension-converter extension`), puis le pont se
lance avec `python3 pont.py`. Le script OBS, lui, ne fonctionne que sous Windows.

**Un autre navigateur, ou sans extension**

Coche **Mode secours** dans le panneau OBS. Le pont lit alors les contrôles
média de Windows, que presque tous les navigateurs alimentent. Contrepartie :
Windows ne dit pas de quel site vient le son, donc une vidéo YouTube ou un live
Twitch remontera aussi. Avec l'extension, seuls les quatre services sont pris en
compte, et c'est toujours elle qui a la priorité.

Au premier lancement en mode secours, le pont installe tout seul les modules
Python nécessaires (une vingtaine de secondes).

### 4. Vérifier

Lance un morceau, puis clique sur l'icône de l'extension : elle doit afficher
**Pont connecté** et le titre en cours.

## Avec Akibot

Pas besoin de Python ni du script OBS dans ce cas, seulement de l'extension.

1. Dans l'éditeur d'overlays d'Akibot, ajoute le widget **Musique en cours** et
   copie le **lien pour l'extension En Écoute**.
2. Clique sur l'icône de l'extension, colle le lien dans **Lien Akibot**, puis
   **OK**. Firefox te demande alors l'autorisation d'envoyer le contenu des
   pages de musique à Akibot.
3. Lance un morceau : la fenêtre de l'extension affiche **Akibot reçoit la
   musique**, et le widget s'anime dans OBS.

Ce lien est un secret, comme l'adresse d'un overlay : quelqu'un qui l'a peut
afficher n'importe quel titre sur ton stream. S'il fuite, régénère-le dans
l'éditeur et recolle le nouveau.

Seuls le service, le titre, l'artiste, l'album, l'adresse de la pochette et la
position de lecture partent vers Akibot, et rien du tout tant qu'aucun lien n'est
collé. Le pont local et Akibot peuvent fonctionner en même temps.

## Dans OBS

**L'overlay** : c'est la source Navigateur créée par le bouton. Tous les
réglages du panneau (disposition, thème, couleur...) s'y appliquent en direct.

**Les fichiers texte**, pour ta propre mise en page, sont dans le dossier
`sortie` (ou celui que tu as choisi) :

| Fichier | Contenu |
|---|---|
| `morceau.txt` | la ligne formatée, par défaut `Artiste - Titre` |
| `titre.txt`, `artiste.txt`, `album.txt` | chaque info séparément |
| `service.txt` | Spotify, Deezer, YouTube Music ou Apple Music |
| `pochette.jpg` | la pochette (image transparente quand rien ne joue) |

Source **Texte (GDI+)** → coche **Lire depuis un fichier** → `morceau.txt`.
Source **Image** → `pochette.jpg`. OBS recharge les deux tout seul.

Quand rien ne joue, les fichiers sont vidés (et l'overlay se cache).

## Réglages

### Panneau du script OBS

| Réglage | Effet |
|---|---|
| Disposition | barre horizontale, carte verticale ou minimal |
| Thème / Opacité | sombre, clair ou sans fond ; opacité du fond de 0 à 100 % |
| Couleur d'accent | `auto` prend la couleur de la pochette, sinon `#rrggbb` |
| Texte du bandeau | vide = nom du service ; sinon le texte de ton choix |
| Échelle | agrandit le rendu sans flou |
| Mode démo | fausse piste, pour placer l'overlay sans lancer de musique |
| Cacher quand la musique est en pause | sinon l'overlay reste affiché, figé |
| Texte de morceau.txt | modèle avec `{titre}`, `{artiste}`, `{album}`, `{service}` |
| Dossier des fichiers texte | vide = `sortie` à côté du script |
| Mode secours | voir plus haut |
| Mises à jour | voir plus bas |
| Port local | à changer seulement si 4489 est déjà pris, et à reporter dans l'extension (clic sur son icône) |

### Sans OBS

```
python pont.py
```

L'overlay est alors sur `http://127.0.0.1:4489/` et les fichiers dans `sortie/`.
Les réglages se font dans `config.json` :

| Clé | Défaut | Effet |
|---|---|---|
| `port` | `4489` | port local |
| `sortie` | `""` | dossier des fichiers (vide = `sortie/`) |
| `format` | `"{artiste} - {titre}"` | contenu de `morceau.txt` |
| `texte_vide` | `""` | contenu de `morceau.txt` quand rien ne joue |
| `cacher_pause` | `true` | masque tout pendant une pause |
| `delai_masquage` | `3` | secondes avant de masquer (évite un clignotement entre deux morceaux) |
| `secours_windows` | `false` | mode secours |
| `quitter_avec_obs` | `true` | lancé depuis OBS, le pont s'arrête avec lui |
| `mises_a_jour` | `"demander"` | `demander`, `auto` ou `non` |
| `depot` | `"OkamiOo/en-ecoute"` | dépôt GitHub où chercher les nouvelles versions |

`python pont.py --help` liste les options de la ligne de commande, qui passent
avant `config.json`.

### Paramètres de l'overlay

Pour une source Navigateur faite à la main :

```
http://127.0.0.1:4489/?layout=bar&theme=dark&opacity=100&align=left&accent=auto
                      &art=1&progress=1&time=1&scale=1&width=0&label=&demo=0&debug=0
```

`width=520` fige la largeur de la carte, `debug=1` affiche un message si le pont
ne répond pas.

## Mises à jour

À chaque démarrage, le pont regarde si une version plus récente est sortie sur
ce dépôt. Ce qu'il fait ensuite dépend du réglage **Mises à jour** du panneau
OBS :

- **Me demander dans une fenêtre** (par défaut) : une fenêtre affiche les
  nouveautés. *Oui* installe, *Non* ignore cette version, *Annuler* repose la
  question au prochain lancement.
- **Les installer toutes seules** : sans rien demander.
- **Ne pas vérifier**.

Le bouton **Vérifier les mises à jour** fait la même chose tout de suite, et
repropose une version que tu avais ignorée.

À l'installation, les fichiers du dossier sont remplacés (ton `config.json` n'est
jamais touché), le pont redémarre, et l'extension se recharge d'elle-même si tu
l'as chargée depuis ce dossier. Les onglets de musique déjà ouverts continuent
de remonter sans qu'il faille les recharger. Seul le script OBS attend le
prochain démarrage d'OBS (ou un clic sur la flèche de rechargement dans
Outils → Scripts).

Avec la version signée de Firefox, l'extension ne peut pas se remplacer seule :
sa fenêtre signale la nouvelle version et donne le lien pour la télécharger.

## Bon à savoir

- Si deux onglets jouent en même temps, c'est le dernier lancé qui s'affiche.
- Les pubs de YouTube Music sont ignorées. Celles de Spotify et Deezer (comptes
  gratuits) peuvent apparaître comme un morceau.
- Si le lecteur web de Spotify pilote un autre appareil (Spotify Connect), le son
  ne passe pas par l'onglet et rien ne remonte.
- Le pont garde la position à jour entre deux messages : la barre de
  progression avance de façon fluide.

## Dépannage

- **L'icône de l'extension indique « Pont injoignable »** : OBS est fermé, ou le
  script n'est pas chargé, ou le port diffère entre OBS et l'extension.
- **Pont connecté mais rien ne s'affiche** : recharge l'onglet de musique (une
  extension tout juste installée n'agit que sur les pages ouvertes après).
- **Le journal** : `%TEMP%\en-ecoute.log`, ou bouton **Ouvrir le journal** dans
  le panneau OBS.
- **Python introuvable** : colle le chemin de `pythonw.exe` dans le champ Python
  (section Avancé), puis **Redémarrer le pont**.

## Ajouter un autre site

Si le site remplit les infos média du navigateur (c'est le cas de la plupart des
lecteurs), il suffit de l'ajouter à trois endroits :

1. `extension/manifest.json` : son adresse dans les deux listes `matches` ;
2. `extension/page.js` : son nom d'hôte dans `SERVICES` ;
3. `pont.py` : son nom affiché dans `SERVICES`.

Puis recharge l'extension depuis la page des extensions du navigateur.

## Publier une version

Pour le mainteneur, avec `gh` connecté au compte GitHub :

```
python publier.py 1.1.0 "Ce qui change, en deux mots"
```

Le numéro passe dans `version.txt` et dans le manifeste de l'extension, le zip
est construit avec les fichiers suivis par git, puis commit, étiquette, push et
Release. Les installations la voient à leur démarrage suivant.

Une règle à garder : les options de `pont.py` doivent rester compatibles d'une
version à l'autre, car le script OBS d'avant la mise à jour continue de lancer
le pont jusqu'au redémarrage d'OBS.

## Fichiers

| Fichier | Rôle |
|---|---|
| `en-ecoute.lua` | script OBS : démarre et arrête le pont, crée la source, panneau de réglages |
| `pont.py` | le pont local, et l'installation des mises à jour |
| `config.json` | réglages du pont lancé sans OBS |
| `version.txt` | version installée |
| `web/` | l'overlay affiché par OBS |
| `extension/` | l'extension de navigateur |
| `publier.py` | publication d'une version (mainteneur) |
