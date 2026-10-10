#!/usr/bin/env python3
"""
Publie une nouvelle version d'En Écoute sur GitHub.

  python publier.py 1.1.0 "Ce qui change"
  python publier.py 1.1.0 --notes-fichier notes.md
  python publier.py 1.1.0 --sans-publier          (zip seul, rien n'est envoyé)

Dans l'ordre : version.txt et extension/manifest.json passent à la nouvelle
version, le zip est construit avec les fichiers suivis par git, puis commit,
étiquette v1.1.0, push, et Release GitHub avec le zip en pièce jointe.
Chaque installation la trouve au démarrage suivant de son pont.
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile

DOSSIER = os.path.dirname(os.path.abspath(__file__))
# outils de publication : inutiles chez les utilisateurs
EXCLUS = {"publier.py", ".gitignore", ".gitattributes"}


def git(*args):
    return subprocess.run(["git", *args], cwd=DOSSIER, check=True,
                          capture_output=True, text=True).stdout


def version_tuple(v):
    return tuple(int(n) for n in v.split("."))


def construire_zip(version):
    os.makedirs(os.path.join(DOSSIER, "dist"), exist_ok=True)
    cible = os.path.join(DOSSIER, "dist", "en-ecoute-" + version + ".zip")
    fichiers = [f for f in git("ls-files").splitlines() if f and f not in EXCLUS]
    with zipfile.ZipFile(cible, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in fichiers:
            zf.write(os.path.join(DOSSIER, *rel.split("/")), rel)
    return cible, len(fichiers)


def main():
    p = argparse.ArgumentParser(description="Publie une version d'En Écoute sur GitHub")
    p.add_argument("version", help="ex. 1.1.0")
    p.add_argument("notes", nargs="?", default="", help="ce qui change, affiché aux utilisateurs")
    p.add_argument("--notes-fichier", help="notes lues dans un fichier")
    p.add_argument("--sans-publier", action="store_true", help="construit le zip sans rien envoyer")
    args = p.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit("Version attendue sous la forme 1.2.3")
    with open(os.path.join(DOSSIER, "version.txt"), encoding="utf-8") as fh:
        actuelle = fh.read().strip()
    if version_tuple(args.version) < version_tuple(actuelle):
        sys.exit("La version doit être au moins " + actuelle)
    etiquette = "v" + args.version
    if git("tag", "--list", etiquette).strip():
        sys.exit(etiquette + " existe déjà : choisis un numéro plus grand.")

    sale = git("status", "--porcelain").strip()
    if sale and not args.sans_publier:
        sys.exit("Des changements ne sont pas commités :\n" + sale
                 + "\nCommite-les d'abord : la version publiée doit être celle du dépôt.")

    with open(os.path.join(DOSSIER, "version.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(args.version + "\n")
    chemin_manifeste = os.path.join(DOSSIER, "extension", "manifest.json")
    with open(chemin_manifeste, encoding="utf-8") as fh:
        manifeste = fh.read()
    manifeste = re.sub(r'("version"\s*:\s*")[^"]*(")', r"\g<1>" + args.version + r"\g<2>", manifeste, count=1)
    with open(chemin_manifeste, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(manifeste)

    zip_, nb = construire_zip(args.version)
    print("Zip : " + zip_ + " (" + str(nb) + " fichiers)")
    if args.sans_publier:
        return 0

    notes = args.notes
    if args.notes_fichier:
        with open(args.notes_fichier, encoding="utf-8") as fh:
            notes = fh.read()

    if git("status", "--porcelain").strip():
        git("add", "version.txt", "extension/manifest.json")
        git("commit", "-m", "Version " + args.version)
    git("tag", "-a", etiquette, "-m", "Version " + args.version)
    git("push", "origin", "HEAD", "--follow-tags")
    subprocess.run(["gh", "release", "create", etiquette, zip_,
                    "--title", "En Écoute " + args.version, "--notes", notes or "Version " + args.version],
                   cwd=DOSSIER, check=True)
    print("Version " + args.version + " publiée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
