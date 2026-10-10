#!/usr/bin/env python3
"""
En Écoute - pont entre l'extension navigateur et OBS.

L'extension envoie ici le morceau joué dans Deezer, Spotify, YouTube Music ou
Apple Music (version web). Le pont retient l'onglet qui joue, puis :

  GET  /               l'overlay pour la source Navigateur d'OBS
  GET  /api/now        l'état courant en JSON
  GET  /api/events     le même état, poussé à chaque changement (SSE)
  GET  /art/<clé>      la pochette
  GET  /api/version    versions installées et mise à jour disponible
  POST /api/maj        réception des données de l'extension

et écrit en continu titre.txt, artiste.txt, album.txt, service.txt,
morceau.txt et pochette.jpg dans le dossier de sortie.

Usage :
  python pont.py                      lancement normal (Ctrl+C pour arrêter)
  python pont.py --paquet             crée en-ecoute-extension.zip
  python pont.py --verifier-maj       cherche une nouvelle version tout de suite
  python pont.py --help               toutes les options
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import mimetypes
import os
import queue
import re
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import zipfile
import zlib
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DOSSIER = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(DOSSIER, "web")
EXTENSION = os.path.join(DOSSIER, "extension")
TEMP = os.environ.get("TEMP") or tempfile.gettempdir()
FICHIER_PID = os.path.join(TEMP, "en-ecoute.pid")
JOURNAL = os.path.join(TEMP, "en-ecoute.log")
FICHIER_IGNOREE = os.path.join(DOSSIER, ".maj-ignoree")
WINDOWS = sys.platform == "win32"
SANS_FENETRE = 0x08000000
API_GITHUB = os.environ.get("EN_ECOUTE_API", "https://api.github.com")


def lire_version(chemin):
    try:
        with open(chemin, "r", encoding="utf-8") as fh:
            return fh.read().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


def version_extension():
    try:
        with open(os.path.join(EXTENSION, "manifest.json"), "r", encoding="utf-8") as fh:
            return json.load(fh).get("version", "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


VERSION = lire_version(os.path.join(DOSSIER, "version.txt"))

SERVICES = {
    "spotify": "Spotify",
    "deezer": "Deezer",
    "youtube-music": "YouTube Music",
    "apple-music": "Apple Music",
    "navigateur": "Navigateur",
}

REGLAGES_DEFAUT = {
    "port": 4489,
    "sortie": "",
    "format": "{artiste} - {titre}",
    "texte_vide": "",
    "cacher_pause": True,
    "delai_masquage": 3,
    "secours_windows": False,
    "applis_secours": [
        "chrome", "msedge", "brave", "opera", "vivaldi", "firefox",
        "308046b0af4a39cb", "chromium", "arc", "yandex", "floorp",
        "librewolf", "waterfox", "zen",
    ],
    "quitter_avec_obs": True,
    "mises_a_jour": "demander",
    "depot": "OkamiOo/en-ecoute",
}

PAQUETS_WINRT = [
    "winrt-Windows.Media.Control",
    "winrt-Windows.Storage.Streams",
    "winrt-Windows.Foundation",
    "winrt-Windows.Foundation.Collections",
]


_verrou_journal = threading.Lock()


def journal(msg):
    """Console + fichier : lancé depuis OBS par pythonw, il n'y a pas de console."""
    ligne = "[" + time.strftime("%Y-%m-%d %H:%M:%S") + "] " + str(msg)
    try:
        print(ligne, flush=True)
    except Exception:
        pass
    with _verrou_journal:
        try:
            if os.path.exists(JOURNAL) and os.path.getsize(JOURNAL) > 512 * 1024:
                os.remove(JOURNAL)
            with open(JOURNAL, "a", encoding="utf-8") as fh:
                fh.write(ligne + "\n")
        except OSError:
            pass


def charger_reglages():
    cfg = dict(REGLAGES_DEFAUT)
    chemin = os.path.join(DOSSIER, "config.json")
    if os.path.exists(chemin):
        try:
            with open(chemin, "r", encoding="utf-8-sig") as fh:
                cfg.update(json.load(fh))
        except Exception as exc:
            journal("config.json illisible (" + str(exc) + "), réglages par défaut")
    return cfg


def texte(valeur, maxi=300):
    if not isinstance(valeur, str):
        return ""
    for c in "\r\n\t":
        valeur = valeur.replace(c, " ")
    return valeur.strip()[:maxi]


def nombre(valeur, defaut=0.0):
    try:
        v = float(valeur)
    except (TypeError, ValueError):
        return defaut
    return v if math.isfinite(v) and v >= 0 else defaut


def cle_morceau(*morceaux):
    return hashlib.sha1("|".join(morceaux).encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Onglets signalés par l'extension
# --------------------------------------------------------------------------

class Onglets:
    EXPIRATION = 20  # secondes sans nouvelles avant d'oublier un onglet

    def __init__(self):
        self._verrou = threading.Lock()
        self._onglets = {}

    def maj(self, d):
        cle = texte(d.get("onglet"), 80)
        if not cle:
            return
        service = texte(d.get("service"), 40)
        if service not in SERVICES:
            service = "navigateur"
        en_lecture = bool(d.get("enLecture"))
        position = d.get("position")
        maintenant = time.monotonic()

        with self._verrou:
            ancien = self._onglets.get(cle)
            debut = ancien["debut"] if ancien else 0.0
            derniere = ancien["derniere_lecture"] if ancien else 0.0
            if en_lecture:
                if not (ancien and ancien["en_lecture"]):
                    debut = maintenant
                derniere = maintenant
            self._onglets[cle] = {
                "onglet": cle,
                "service": service,
                "titre": texte(d.get("titre")),
                "artiste": texte(d.get("artiste")),
                "album": texte(d.get("album")),
                "pochette": d.get("pochette") if isinstance(d.get("pochette"), str) else "",
                "duree": nombre(d.get("duree")),
                "position": None if position is None else nombre(position),
                "vitesse": nombre(d.get("vitesse"), 1.0) or 1.0,
                "en_lecture": en_lecture,
                "extension": texte(d.get("ext"), 20),
                "recu": maintenant,
                "debut": debut,
                "derniere_lecture": derniere,
            }

    def fermer(self, cle):
        with self._verrou:
            self._onglets.pop(texte(cle, 80), None)

    def liste(self):
        maintenant = time.monotonic()
        with self._verrou:
            for cle in [c for c, o in self._onglets.items()
                        if maintenant - o["recu"] > self.EXPIRATION]:
                del self._onglets[cle]
            return [dict(o) for o in self._onglets.values()]

    def choisir(self):
        """L'onglet qui a démarré la lecture en dernier ; à défaut, le dernier
        à avoir joué (il est alors en pause)."""
        candidats = [o for o in self.liste() if o["titre"] or o["artiste"]]
        if not candidats:
            return None
        en_lecture = [o for o in candidats if o["en_lecture"]]
        if en_lecture:
            return max(en_lecture, key=lambda o: o["debut"])
        return max(candidats, key=lambda o: (o["derniere_lecture"], o["recu"]))


# --------------------------------------------------------------------------
# Pochettes : téléchargées une fois, gardées en mémoire
# --------------------------------------------------------------------------

def type_image(octets):
    if octets.startswith(b"\x89PNG"):
        return "image/png"
    if octets.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if octets[:4] == b"RIFF" and octets[8:12] == b"WEBP":
        return "image/webp"
    if octets[:3] == b"GIF":
        return "image/gif"
    return None


def png_transparent():
    def bloc(nom, data):
        return (struct.pack(">I", len(data)) + nom + data
                + struct.pack(">I", zlib.crc32(nom + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
            + bloc(b"IEND", b""))


class Pochettes:
    def __init__(self):
        self._verrou = threading.Lock()
        self._cache = OrderedDict()
        self._en_cours = set()
        self._echecs = {}

    def get(self, cle):
        with self._verrou:
            return self._cache.get(cle)

    def ajouter(self, cle, octets, ctype):
        with self._verrou:
            self._cache[cle] = (octets, ctype)
            self._cache.move_to_end(cle)
            while len(self._cache) > 12:
                self._cache.popitem(last=False)

    def demander(self, url):
        """Renvoie la clé si l'image est prête, sinon lance le téléchargement
        et renvoie None (elle sera prête à un prochain passage)."""
        if not url:
            return None
        cle = cle_morceau(url)
        with self._verrou:
            if cle in self._cache:
                return cle
            if cle in self._en_cours or time.monotonic() - self._echecs.get(cle, -99) < 30:
                return None
            self._en_cours.add(cle)

        if url.startswith("data:image/"):
            try:
                octets = base64.b64decode(url.split(",", 1)[1])
                ctype = type_image(octets)
                if ctype:
                    self.ajouter(cle, octets, ctype)
            finally:
                self._fin(cle)
            return cle if self.get(cle) else None

        if url.startswith("https://"):
            threading.Thread(target=self._telecharger, args=(cle, url), daemon=True).start()
        else:
            self._fin(cle, echec=True)
        return None

    def _fin(self, cle, echec=False):
        with self._verrou:
            self._en_cours.discard(cle)
            if echec:
                self._echecs[cle] = time.monotonic()

    def _telecharger(self, cle, url):
        ok = False
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Accept": "image/jpeg,image/png;q=0.9,image/*;q=0.5",
            })
            with urllib.request.urlopen(req, timeout=8) as rep:
                octets = rep.read(8 * 1024 * 1024)
            ctype = type_image(octets)
            if ctype:
                self.ajouter(cle, octets, ctype)
                ok = True
        except Exception as exc:
            journal("pochette indisponible (" + url[:90] + ") : " + str(exc))
        self._fin(cle, echec=not ok)


# --------------------------------------------------------------------------
# Fichiers pour les sources Texte et Image d'OBS
# --------------------------------------------------------------------------

class _Defaut(dict):
    def __missing__(self, cle):
        return ""


def ecrire_fichier(chemin, octets):
    """Écriture atomique : OBS ne doit jamais lire un fichier à moitié écrit."""
    tmp = chemin + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(octets)
    for _ in range(10):
        try:
            os.replace(tmp, chemin)
            return
        except PermissionError:
            time.sleep(0.05)   # OBS est peut-être en train de le lire
    with open(chemin, "wb") as fh:
        fh.write(octets)
    try:
        os.remove(tmp)
    except OSError:
        pass


class Sortie:
    def __init__(self, dossier, modele, texte_vide):
        self.dossier = dossier
        self.modele = modele or "{artiste} - {titre}"
        self.texte_vide = texte_vide or ""
        self._derniers = {}
        self._pochette = object()
        self._vide = png_transparent()
        os.makedirs(dossier, exist_ok=True)

    def _texte(self, nom, valeur):
        if self._derniers.get(nom) == valeur:
            return
        try:
            ecrire_fichier(os.path.join(self.dossier, nom), valeur.encode("utf-8"))
            self._derniers[nom] = valeur
        except OSError as exc:
            journal("écriture impossible de " + nom + " : " + str(exc))

    def ecrire(self, etat, pochettes):
        if etat["visible"]:
            champs = _Defaut(titre=etat["title"], artiste=etat["artist"],
                             album=etat["album"], service=etat["service"])
            try:
                ligne = self.modele.format_map(champs)
            except (ValueError, IndexError):
                ligne = champs["artiste"] + " - " + champs["titre"]
            ligne = ligne.strip(" -|·•")
            cle_art = etat["art"].rsplit("/", 1)[-1] if etat["art"] else None
        else:
            champs = _Defaut()
            ligne = self.texte_vide
            cle_art = None

        self._texte("titre.txt", champs["titre"])
        self._texte("artiste.txt", champs["artiste"])
        self._texte("album.txt", champs["album"])
        self._texte("service.txt", champs["service"])
        self._texte("morceau.txt", ligne)

        if cle_art != self._pochette:
            image = pochettes.get(cle_art) if cle_art else None
            try:
                ecrire_fichier(os.path.join(self.dossier, "pochette.jpg"),
                               image[0] if image else self._vide)
                self._pochette = cle_art
            except OSError as exc:
                journal("écriture impossible de pochette.jpg : " + str(exc))


# --------------------------------------------------------------------------
# État publié vers l'overlay
# --------------------------------------------------------------------------

CLES_SUIVIES = ("visible", "status", "source", "title", "artist", "album",
                "duration", "art", "track_id", "position_source")


class Etat:
    def __init__(self):
        self._verrou = threading.Lock()
        self._data = dict(ETAT_VIDE, version=0, server_time=time.time())
        self._abonnes = []
        self._dernier_envoi = 0.0

    def snapshot(self):
        with self._verrou:
            data = dict(self._data)
        data["server_time"] = time.time()
        return data

    def update(self, champs):
        with self._verrou:
            change = any(self._data.get(k) != champs.get(k) for k in CLES_SUIVIES)
            nouveau, ancien = champs.get("position"), self._data.get("position")
            if (nouveau is None) != (ancien is None):
                change = True
            elif nouveau is not None and abs(nouveau - ancien) > 1.2:
                change = True   # avance rapide dans le morceau

            # point de repère régulier pour que la barre ne dérive pas
            maintenant = time.time()
            if not change and champs.get("visible") and maintenant - self._dernier_envoi >= 2.0:
                change = True

            self._data.update(champs)
            if not change:
                return
            self._dernier_envoi = maintenant
            self._data["version"] += 1
            self._data["server_time"] = maintenant
            message = json.dumps(self._data, ensure_ascii=False)
            abonnes = list(self._abonnes)
        for q in abonnes:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass

    def abonner(self):
        q = queue.Queue(maxsize=32)
        with self._verrou:
            self._abonnes.append(q)
        return q

    def desabonner(self, q):
        with self._verrou:
            if q in self._abonnes:
                self._abonnes.remove(q)


ETAT_VIDE = {
    "visible": False, "status": "idle", "source": None, "service": "",
    "title": "", "artist": "", "album": "", "duration": 0.0,
    "position": None, "position_source": None, "art": None, "track_id": "",
}


def position_actuelle(m):
    if m["position"] is None:
        return None
    pos = m["position"]
    if m["en_lecture"]:
        pos += (time.monotonic() - m["recu"]) * m["vitesse"]
    if m["duree"] > 0:
        pos = min(pos, m["duree"])
    return max(0.0, pos)


def construire(m, pochettes):
    if m.get("pochette_cle"):
        cle_art = m["pochette_cle"] if pochettes.get(m["pochette_cle"]) else None
    else:
        cle_art = pochettes.demander(m["pochette"])
    position = position_actuelle(m)
    return {
        "visible": True,
        "status": "playing" if m["en_lecture"] else "paused",
        "source": m["service"],
        "service": SERVICES.get(m["service"], "Navigateur"),
        "title": m["titre"],
        "artist": m["artiste"],
        "album": m["album"],
        "duration": m["duree"],
        "position": position,
        "position_source": None if position is None else "timeline",
        "art": "/art/" + cle_art if cle_art else None,
        "track_id": cle_morceau(m["service"], m["titre"], m["artiste"], m["album"]),
    }


def boucle_principale(cfg, stop, onglets, secours, pochettes, sortie, etat):
    dernier = None
    dernier_visible = 0.0
    precedent = ("", "", "idle")
    while not stop.wait(0.25):
        try:
            o = onglets.choisir()
            s = secours.lire() if secours else None
            # un onglet suivi par l'extension passe avant les contrôles média
            # de Windows, qui ne disent pas de quel site vient le son
            if o and o["en_lecture"]:
                choisi = o
            elif s and s["en_lecture"]:
                choisi = s
            else:
                choisi = o or s

            maintenant = time.monotonic()
            affichable = choisi is not None and (choisi["en_lecture"] or not cfg["cacher_pause"])
            if affichable:
                dernier_visible = maintenant
                courant = construire(choisi, pochettes)
                dernier = courant
            elif dernier and maintenant - dernier_visible < cfg["delai_masquage"]:
                # petit sursis : un changement de piste passe souvent par une
                # pause d'une fraction de seconde
                courant = construire(choisi, pochettes) if choisi else dict(dernier)
                courant["visible"] = True
            else:
                courant = dict(ETAT_VIDE)
                dernier = None

            etat.update(courant)
            sortie.ecrire(courant, pochettes)

            resume = (courant["title"], courant["artist"], courant["status"])
            if resume != precedent:
                precedent = resume
                if courant["visible"]:
                    journal(courant["service"] + " (" + courant["status"] + ") : "
                            + courant["artist"] + " - " + courant["title"])
                else:
                    journal("Plus rien à afficher")
        except Exception as exc:
            journal("erreur : " + repr(exc))
            time.sleep(1)


# --------------------------------------------------------------------------
# Secours : contrôles média de Windows, pour un navigateur sans l'extension
# --------------------------------------------------------------------------

def winrt_present():
    try:
        import winrt.windows.media.control  # noqa: F401
        import winrt.windows.foundation.collections  # noqa: F401
        import winrt.windows.storage.streams  # noqa: F401
        return True
    except ImportError:
        return False


def installer_winrt():
    if winrt_present():
        return True
    journal("Installation des modules Windows pour le mode secours (une seule fois)...")
    cmd = [sys.executable, "-m", "pip", "install", "--user",
           "--disable-pip-version-check", "--quiet"] + PAQUETS_WINRT
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                              creationflags=SANS_FENETRE)
        if proc.returncode != 0:
            journal("pip a échoué : " + (proc.stderr or proc.stdout or "")[-600:])
    except Exception as exc:
        journal("installation impossible : " + repr(exc))

    import importlib
    import site
    importlib.reload(site)
    importlib.invalidate_caches()
    if winrt_present():
        return True
    journal("Modules introuvables. Pour les installer à la main : \"" + sys.executable
            + "\" -m pip install --user " + " ".join(PAQUETS_WINRT))
    return False


class Secours:
    """Lit la session média que Windows reçoit des navigateurs. Le site
    d'origine est inconnu : tout ce qui joue dans le navigateur remonte."""

    def __init__(self, cfg, pochettes):
        self.applis = ["".join(c for c in a.lower() if c.isalnum()) for a in cfg["applis_secours"]]
        self.pochettes = pochettes
        self._verrou = threading.Lock()
        self._dernier = None

    def lire(self):
        with self._verrou:
            if self._dernier and time.monotonic() - self._dernier["recu"] > 5:
                return None
            return dict(self._dernier) if self._dernier else None

    def demarrer(self, stop):
        def lancer():
            try:
                asyncio.run(self._boucle(stop))
            except Exception as exc:
                journal("mode secours arrêté : " + repr(exc))
        threading.Thread(target=lancer, daemon=True).start()

    def _accepte(self, session):
        app = "".join(c for c in (session.source_app_user_model_id or "").lower() if c.isalnum())
        return any(a and a in app for a in self.applis)

    async def _boucle(self, stop):
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as Gestionnaire,
        )
        gestionnaire = await Gestionnaire.request_async()
        journal("Mode secours actif (contrôles média de Windows)")
        cle_vue = None
        while not stop.is_set():
            try:
                sessions = [s for s in gestionnaire.get_sessions() if self._accepte(s)]
                session = next((s for s in sessions
                                if s.get_playback_info().playback_status == 4), None)
                session = session or (sessions[0] if sessions else None)
                if session is None:
                    with self._verrou:
                        self._dernier = None
                else:
                    props = await session.try_get_media_properties_async()
                    titre = texte(props.title)
                    artiste = texte(props.artist or props.album_artist)
                    album = texte(props.album_title)
                    cle = cle_morceau("secours", titre, artiste, album)
                    if cle != cle_vue:
                        cle_vue = cle
                        await self._pochette(props, cle)

                    tl = session.get_timeline_properties()
                    debut = tl.start_time.total_seconds()
                    duree = max(0.0, tl.end_time.total_seconds() - debut)
                    position = max(0.0, tl.position.total_seconds() - debut)
                    en_lecture = session.get_playback_info().playback_status == 4
                    with self._verrou:
                        self._dernier = {
                            "service": "navigateur", "titre": titre, "artiste": artiste,
                            "album": album, "pochette": "", "pochette_cle": cle,
                            "duree": duree, "position": position if duree else None,
                            "vitesse": 1.0, "en_lecture": en_lecture,
                            "recu": time.monotonic(),
                        }
            except Exception as exc:
                journal("erreur secours : " + repr(exc))
                await asyncio.sleep(2)
            await asyncio.sleep(1)

    async def _pochette(self, props, cle):
        if self.pochettes.get(cle) or props.thumbnail is None:
            return
        try:
            from winrt.windows.storage.streams import Buffer, InputStreamOptions
            flux = await props.thumbnail.open_read_async()
            if not flux.size:
                return
            tampon = Buffer(flux.size)
            await flux.read_async(tampon, flux.size, InputStreamOptions.NONE)
            octets = bytes(tampon)
            ctype = type_image(octets)
            if ctype:
                self.pochettes.ajouter(cle, octets, ctype)
        except Exception as exc:
            journal("pochette (secours) indisponible : " + str(exc))


# --------------------------------------------------------------------------
# Mises à jour : dernière Release publiée sur GitHub
# --------------------------------------------------------------------------

# fichiers de l'utilisateur, jamais écrasés par une mise à jour
A_GARDER = {"config.json"}


def version_tuple(v):
    nombres = [int(n) for n in re.findall(r"\d+", str(v).split("-")[0])[:3]]
    return tuple(nombres + [0] * (3 - len(nombres)))


def telecharger(url, maxi):
    hote = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") and hote != "127.0.0.1":
        raise ValueError("adresse refusée : " + url)
    req = urllib.request.Request(url, headers={
        "User-Agent": "en-ecoute/" + VERSION,
        "Accept": "application/vnd.github+json, application/octet-stream;q=0.9, */*;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=30) as rep:
        octets = rep.read(maxi + 1)
    if len(octets) > maxi:
        raise ValueError("fichier trop gros")
    return octets


def fenetre(message, boutons=0x40):
    """Boîte de dialogue Windows, au premier plan. Renvoie le bouton cliqué
    (6 = Oui, 7 = Non, 2 = Annuler) ou None hors Windows."""
    if not WINDOWS:
        return None
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, message, "En Écoute", boutons | 0x40000 | 0x10000)
    except Exception:
        return None


def chercher_mise_a_jour(depot):
    data = json.loads(telecharger(API_GITHUB + "/repos/" + depot + "/releases/latest", 1024 * 1024))
    version = str(data.get("tag_name") or "").lstrip("vV")
    if version_tuple(version) <= version_tuple(VERSION):
        return None
    paquet = next((a for a in data.get("assets") or []
                   if re.fullmatch(r"en-ecoute-[\w.\-]+\.zip", a.get("name") or "")), None)
    if not paquet:
        return None
    notes = re.sub(r"[#*`]+", "", data.get("body") or "").strip()
    if len(notes) > 700:
        notes = notes[:700].rsplit("\n", 1)[0] + "\n..."
    return {
        "version": version,
        "notes": notes,
        "zip": paquet.get("browser_download_url") or "",
        "empreinte": paquet.get("digest") or "",
        "page": data.get("html_url") or "",
    }


def installer_mise_a_jour(info):
    """Remplace les fichiers du dossier par ceux de la nouvelle version.
    En cas d'échec en cours de route, tout est remis comme avant.
    Renvoie True si le script OBS a changé (il faudra le recharger)."""
    octets = telecharger(info["zip"], 50 * 1024 * 1024)
    if info["empreinte"].startswith("sha256:"):
        if hashlib.sha256(octets).hexdigest() != info["empreinte"][7:].lower():
            raise ValueError("le zip téléchargé ne correspond pas à l'empreinte publiée")

    fichiers = {}
    with zipfile.ZipFile(io.BytesIO(octets)) as zf:
        noms = [n for n in zf.namelist() if not n.endswith("/")]
        # un zip peut tout ranger dans un dossier racine : on l'enlève
        racine = noms[0].split("/", 1)[0] + "/" if noms else ""
        if not all(n.startswith(racine) and "/" in n for n in noms):
            racine = ""
        for nom in noms:
            rel = nom[len(racine):]
            morceaux = rel.split("/")
            if not rel or rel.startswith("/") or ".." in morceaux or ":" in rel or "\\" in rel:
                raise ValueError("chemin refusé dans l'archive : " + nom)
            fichiers[rel] = zf.read(nom)
    if "pont.py" not in fichiers or "extension/manifest.json" not in fichiers:
        raise ValueError("archive incomplète")

    sauvegarde = {}
    script_obs = False
    try:
        for rel, contenu in fichiers.items():
            cible = os.path.join(DOSSIER, *rel.split("/"))
            existe = os.path.exists(cible)
            if rel in A_GARDER and existe:
                continue
            ancien = None
            if existe:
                with open(cible, "rb") as fh:
                    ancien = fh.read()
                if ancien == contenu:
                    continue
            sauvegarde[cible] = ancien
            os.makedirs(os.path.dirname(cible), exist_ok=True)
            ecrire_fichier(cible, contenu)
            if rel == "en-ecoute.lua":
                script_obs = True
    except Exception:
        for cible, ancien in sauvegarde.items():
            try:
                if ancien is None:
                    os.remove(cible)
                else:
                    ecrire_fichier(cible, ancien)
            except OSError:
                pass
        raise
    return script_obs


class MisesAJour:
    def __init__(self, cfg):
        self.depot = cfg["depot"]
        self.mode = cfg["mises_a_jour"]
        self.disponible = None

    def _ignoree(self):
        return lire_version(FICHIER_IGNOREE)

    def verifier(self, relancer, a_la_demande=False):
        """a_la_demande : bouton « Vérifier » d'OBS. On répond toujours, même
        pour dire qu'il n'y a rien, et une version ignorée est reproposée."""
        if self.mode == "non" and not a_la_demande:
            return
        try:
            info = chercher_mise_a_jour(self.depot)
        except Exception as exc:
            journal("Vérification des mises à jour impossible : " + str(exc))
            if a_la_demande:
                fenetre("Impossible de joindre GitHub pour l'instant.\n\n" + str(exc), 0x30)
            return
        if not info:
            journal("En Écoute est à jour (" + VERSION + ")")
            if a_la_demande:
                fenetre("Tu as déjà la dernière version (" + VERSION + ").")
            return

        self.disponible = info
        journal("Nouvelle version disponible : " + info["version"])
        if not a_la_demande and info["version"] == self._ignoree():
            return

        if self.mode != "auto" or a_la_demande:
            message = ("Une nouvelle version d'En Écoute est disponible : "
                       + VERSION + " → " + info["version"] + "\n\n")
            if info["notes"]:
                message += info["notes"] + "\n\n"
            message += ("Oui : l'installer maintenant\n"
                        "Non : ignorer cette version\n"
                        "Annuler : me le redemander au prochain lancement")
            choix = fenetre(message, 0x03 | 0x20)
            if choix == 7:
                try:
                    with open(FICHIER_IGNOREE, "w", encoding="utf-8") as fh:
                        fh.write(info["version"])
                except OSError:
                    pass
                return
            if choix != 6:
                return

        try:
            script_obs = installer_mise_a_jour(info)
        except Exception as exc:
            journal("La mise à jour a échoué : " + str(exc))
            fenetre("La mise à jour a échoué, rien n'a été modifié.\n\n" + str(exc), 0x10)
            return

        journal("Version " + info["version"] + " installée, redémarrage du pont")
        if self.mode != "auto" or a_la_demande:
            message = "Version " + info["version"] + " installée."
            if script_obs:
                message += ("\n\nLe script OBS a changé lui aussi : redémarre OBS, ou recharge "
                            "le script dans Outils → Scripts, pour en profiter.")
            fenetre(message)
        relancer()


# --------------------------------------------------------------------------
# Serveur HTTP
# --------------------------------------------------------------------------

def origine_extension(origine):
    """Seule une extension a le droit d'écrire : sans ce contrôle, n'importe
    quel site ouvert dans le navigateur pourrait afficher son texte en stream."""
    return origine.split("://", 1)[0].lower().endswith("extension")


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "EnEcoute/1.0"
    protocol_version = "HTTP/1.1"
    contexte = None

    def log_message(self, *args):
        pass

    def _envoyer(self, code, corps, ctype, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "public, max-age=3600" if cache else "no-store")
        origine = self.headers.get("Origin") or ""
        if origine and origine_extension(origine):
            self.send_header("Access-Control-Allow-Origin", origine)
        self.end_headers()
        try:
            self.wfile.write(corps)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _json(self, data, code=200):
        self._envoyer(code, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8")

    def do_OPTIONS(self):
        origine = self.headers.get("Origin") or ""
        self.send_response(204 if origine_extension(origine) else 403)
        if origine_extension(origine):
            self.send_header("Access-Control-Allow-Origin", origine)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        ctx = self.contexte
        chemin = self.path.split("?", 1)[0]
        if chemin in ("/", "/index.html"):
            return self._fichier("index.html")
        if chemin == "/api/now":
            return self._json(ctx["etat"].snapshot())
        if chemin == "/api/onglets":
            return self._json([{k: o[k] for k in ("service", "titre", "artiste", "en_lecture", "extension")}
                               for o in ctx["onglets"].liste()])
        if chemin == "/api/version":
            maj = ctx["maj"].disponible
            return self._json({
                "pont": VERSION,
                "extension": version_extension(),
                "disponible": maj["version"] if maj else None,
                "page": maj["page"] if maj else None,
            })
        if chemin == "/api/events":
            return self._sse()
        if chemin.startswith("/art/"):
            image = ctx["pochettes"].get(chemin[5:])
            if not image:
                return self._envoyer(404, b"", "text/plain")
            return self._envoyer(200, image[0], image[1], cache=True)
        if chemin == "/healthz":
            return self._envoyer(200, b"ok", "text/plain")
        return self._fichier(chemin.lstrip("/"))

    def do_POST(self):
        ctx = self.contexte
        origine = self.headers.get("Origin")
        chemin = self.path.split("?", 1)[0]
        # un navigateur envoie toujours Origin : sans elle, c'est un programme
        # local (le bouton « Vérifier les mises à jour » d'OBS)
        if chemin == "/api/redemarrer":
            if origine:
                return self._envoyer(403, b"", "text/plain")
            self._envoyer(200, b"ok", "text/plain")
            ctx["relancer"]()
            return
        if not origine_extension(origine or "x-extension://"):
            return self._envoyer(403, b"", "text/plain")
        try:
            taille = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            taille = 0
        if taille <= 0 or taille > 8 * 1024 * 1024:
            return self._envoyer(413 if taille else 400, b"", "text/plain")
        try:
            data = json.loads(self.rfile.read(taille).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self._envoyer(400, b"", "text/plain")
        if not isinstance(data, dict):
            return self._envoyer(400, b"", "text/plain")

        if chemin == "/api/maj":
            ctx["onglets"].maj(data)
            # l'extension compare avec sa propre version et se recharge si
            # une mise à jour a remplacé ses fichiers
            return self._json({"ok": True, "extension": ctx["version_extension"]})
        elif chemin == "/api/fermer":
            ctx["onglets"].fermer(data.get("onglet"))
        else:
            return self._envoyer(404, b"", "text/plain")
        self._envoyer(200, b"ok", "text/plain")

    def _fichier(self, rel):
        complet = os.path.normpath(os.path.join(WEB, rel))
        if not complet.startswith(WEB + os.sep) or not os.path.isfile(complet):
            return self._envoyer(404, b"", "text/plain")
        ctype = mimetypes.guess_type(complet)[0] or "application/octet-stream"
        if ctype.startswith("text/") or "javascript" in ctype:
            ctype += "; charset=utf-8"
        with open(complet, "rb") as fh:
            self._envoyer(200, fh.read(), ctype)

    def _sse(self):
        etat = self.contexte["etat"]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = etat.abonner()
        try:
            premier = json.dumps(etat.snapshot(), ensure_ascii=False)
            self.wfile.write(("data: " + premier + "\n\n").encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    bloc = "data: " + q.get(timeout=15) + "\n\n"
                except queue.Empty:
                    bloc = ": ping\n\n"
                self.wfile.write(bloc.encode("utf-8"))
                self.wfile.flush()
        except Exception:
            pass
        finally:
            etat.desabonner(q)


# --------------------------------------------------------------------------
# Windows : instance unique et arrêt avec OBS
# --------------------------------------------------------------------------

_mutex = None


def instance_unique(port):
    """Un mutex nommé plutôt qu'un test de port : deux ponts lancés dans la
    même seconde passeraient tous les deux le test."""
    global _mutex
    if not WINDOWS:
        return True
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wt.HANDLE
        k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        handle = k32.CreateMutexW(None, True, "Local\\EnEcoute_" + str(port))
        if not handle:
            return True
        if ctypes.get_last_error() == 183:   # ERROR_ALREADY_EXISTS
            return False
        _mutex = handle
        return True
    except Exception:
        return True


def liberer_instance():
    global _mutex
    if _mutex:
        import ctypes
        ctypes.windll.kernel32.ReleaseMutex(_mutex)
        ctypes.windll.kernel32.CloseHandle(_mutex)
        _mutex = None


def obs_tourne():
    try:
        sortie = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                                text=True, timeout=10, creationflags=SANS_FENETRE).stdout.lower()
    except Exception:
        return True
    return '"obs64.exe"' in sortie or '"obs32.exe"' in sortie or '"obs.exe"' in sortie


def surveiller_obs(stop, httpd):
    time.sleep(45)   # laisse OBS finir de démarrer
    absences = 0
    while not stop.wait(5):
        absences = 0 if obs_tourne() else absences + 1
        if absences >= 2:
            journal("OBS est fermé, arrêt du pont")
            stop.set()
            threading.Thread(target=httpd.shutdown, daemon=True).start()
            return


def port_libre(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", port)) != 0


def creer_paquet():
    cible = os.path.join(DOSSIER, "en-ecoute-extension.zip")
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED) as zf:
        for racine, _, fichiers in os.walk(EXTENSION):
            for nom in fichiers:
                complet = os.path.join(racine, nom)
                zf.write(complet, os.path.relpath(complet, EXTENSION).replace(os.sep, "/"))
    print("Paquet créé : " + cible)


def main():
    cfg = charger_reglages()
    p = argparse.ArgumentParser(description="En Écoute : morceau joué dans le navigateur, pour OBS")
    p.add_argument("--port", type=int, default=cfg["port"])
    p.add_argument("--sortie", default=cfg["sortie"], help="dossier des fichiers texte et de la pochette")
    p.add_argument("--format", default=cfg["format"], help='texte de morceau.txt, ex. "{artiste} - {titre}"')
    p.add_argument("--cacher-pause", choices=("oui", "non"),
                   default="oui" if cfg["cacher_pause"] else "non")
    p.add_argument("--secours", choices=("oui", "non"),
                   default="oui" if cfg["secours_windows"] else "non",
                   help="lit aussi les contrôles média de Windows (navigateur sans l'extension)")
    p.add_argument("--maj", choices=("demander", "auto", "non"), default=cfg["mises_a_jour"],
                   help="au lancement : proposer, installer seul, ou ne pas chercher de mise à jour")
    p.add_argument("--obs", action="store_true", help="s'arrête quand OBS se ferme")
    p.add_argument("--paquet", action="store_true", help="crée le zip de l'extension et quitte")
    p.add_argument("--verifier-maj", action="store_true",
                   help="cherche une mise à jour tout de suite et répond dans une fenêtre")
    args = p.parse_args()

    if args.paquet:
        creer_paquet()
        return 0

    cfg["mises_a_jour"] = args.maj
    if args.verifier_maj:
        # le pont déjà lancé doit repartir sur les nouveaux fichiers
        def relancer_pont():
            try:
                req = urllib.request.Request("http://127.0.0.1:" + str(args.port) + "/api/redemarrer",
                                             data=b"{}", method="POST")
                urllib.request.urlopen(req, timeout=5).read()
            except Exception:
                pass
        MisesAJour(cfg).verifier(relancer_pont, a_la_demande=True)
        return 0

    cfg["port"] = args.port
    cfg["format"] = args.format
    cfg["cacher_pause"] = args.cacher_pause == "oui"
    cfg["secours_windows"] = args.secours == "oui" and WINDOWS
    dossier_sortie = args.sortie or os.path.join(DOSSIER, "sortie")

    if not instance_unique(cfg["port"]):
        journal("Le pont tourne déjà, rien à faire.")
        return 0
    if not port_libre(cfg["port"]):
        journal("Le port " + str(cfg["port"]) + " est déjà pris, rien à faire.")
        return 0

    # le script OBS guette cette première ligne pour savoir que Python a démarré
    journal("Démarrage du pont (PID " + str(os.getpid()) + ", " + sys.executable + ")")
    try:
        with open(FICHIER_PID, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass

    stop = threading.Event()
    onglets = Onglets()
    pochettes = Pochettes()
    etat = Etat()
    sortie = Sortie(dossier_sortie, cfg["format"], cfg["texte_vide"])

    secours = None
    if cfg["secours_windows"]:
        if installer_winrt():
            secours = Secours(cfg, pochettes)
            secours.demarrer(stop)
        else:
            journal("Mode secours indisponible, seule l'extension sera écoutée.")

    httpd = ThreadingHTTPServer(("127.0.0.1", cfg["port"]), Gestionnaire)
    httpd.daemon_threads = True

    redemarrage = threading.Event()

    def relancer():
        redemarrage.set()
        stop.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    maj = MisesAJour(cfg)
    Gestionnaire.contexte = {"onglets": onglets, "pochettes": pochettes, "etat": etat,
                             "maj": maj, "relancer": relancer,
                             "version_extension": version_extension()}

    threading.Thread(target=boucle_principale,
                     args=(cfg, stop, onglets, secours, pochettes, sortie, etat),
                     daemon=True).start()
    if args.obs and cfg["quitter_avec_obs"] and WINDOWS:
        threading.Thread(target=surveiller_obs, args=(stop, httpd), daemon=True).start()

    # un peu après le démarrage : OBS a fini de se lancer, la fenêtre ne se
    # perd pas derrière son écran de chargement
    minuteur = threading.Timer(8, maj.verifier, args=(relancer,))
    minuteur.daemon = True
    minuteur.start()

    journal("Pont prêt sur http://127.0.0.1:" + str(cfg["port"]) + "/ (version " + VERSION + ")")
    journal("Fichiers écrits dans " + dossier_sortie)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        journal("Arrêt demandé.")
    finally:
        stop.set()
        httpd.server_close()
        try:
            os.remove(FICHIER_PID)
        except OSError:
            pass

    if redemarrage.is_set():
        liberer_instance()
        subprocess.Popen([sys.executable, os.path.join(DOSSIER, "pont.py")] + sys.argv[1:],
                         cwd=DOSSIER, close_fds=True,
                         creationflags=SANS_FENETRE if WINDOWS else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
