"""HIPPOFUSION X1000 M1-M5 — publication prudente des audits.

Lit data/audit_latest.json et produit data/publication_latest.json.

Garanties :
- Aucun PV ou FIGÉ historique n'est créé, validé ou modifié.
- Aucun pronostic n'est inventé.
- Les moteurs M1 à M5 restent indépendants.
- Les données invalides empêchent la publication.
- L'écriture est atomique.
- Une publication précédente est préservée en cas d'échec.
"""

import json
import math
import os
import tempfile
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
MOTEURS = ("M1", "M2", "M3", "M4", "M5")


def _sans_constante_invalide(valeur):
    raise ValueError(
        f"Constante JSON interdite : {valeur}"
    )


def _sans_doublon_json(paires):
    resultat = {}
    for cle, valeur in paires:
        if cle in resultat:
            raise ValueError(
                f"Clé JSON en double : {cle}"
            )
        resultat[cle] = valeur
    return resultat


def _verifier_json(valeur):
    """Refuse les valeurs non conformes au JSON strict."""
    if valeur is None or isinstance(
        valeur, (str, bool, int)
    ):
        return

    if isinstance(valeur, float):
        if not math.isfinite(valeur):
            raise ValueError(
                "Nombre JSON non fini interdit."
            )
        return

    if isinstance(valeur, list):
        for element in valeur:
            _verifier_json(element)
        return

    if isinstance(valeur, dict):
        for cle, element in valeur.items():
            if not isinstance(cle, str):
                raise ValueError(
                    "Les clés JSON doivent être des chaînes."
                )
            _verifier_json(element)
        return

    raise ValueError(
        f"Type non compatible JSON : {type(valeur).__name__}"
    )


def _moteurs_valides(valeur):
    """Conserve les moteurs connus sans créer de prédictions."""
    if valeur is None:
        return {}

    resultat = {}

    if isinstance(valeur, dict):
        inconnus = set(valeur) - set(MOTEURS)
        if inconnus:
            raise ValueError(
                f"Moteurs inconnus : {sorted(inconnus)}"
            )

        for nom in MOTEURS:
            if nom in valeur:
                if not isinstance(valeur[nom], dict):
                    raise ValueError(
                        f"Moteur {nom} : objet JSON attendu."
                    )
                resultat[nom] = valeur[nom]

        return resultat

    if isinstance(valeur, list):
        for element in valeur:
            if not isinstance(element, dict):
                raise ValueError(
                    "Chaque moteur doit être un objet JSON."
                )

            nom = element.get(
                "moteur", element.get("nom")
            )

            if nom not in MOTEURS:
                raise ValueError(
                    f"Moteur inconnu : {nom}"
                )

            if nom in resultat:
                raise ValueError(
                    f"Moteur en double : {nom}"
                )

            resultat[nom] = element

        return {
            nom: resultat[nom]
            for nom in MOTEURS
            if nom in resultat
        }

    raise ValueError(
        "Le champ moteurs doit être un objet ou une liste."
    )


def publier(data_dir=DATA_DIR):
    """Publie un audit contrôlé ou renvoie None."""

    data_dir = Path(data_dir)
    source = data_dir / "audit_latest.json"
    cible = data_dir / "publication_latest.json"
    temporaire = None

    try:
        if not source.is_file():
            raise FileNotFoundError(
                f"Audit introuvable : {source}"
            )

        with source.open(
            "r", encoding="utf-8"
        ) as fichier:
            audit = json.load(
                fichier,
                parse_constant=_sans_constante_invalide,
                object_pairs_hook=_sans_doublon_json,
            )

        if not isinstance(audit, dict):
            raise ValueError(
                "L'audit doit être un objet JSON."
            )

        _verifier_json(audit)

        courses_source = audit.get("courses")

        if not isinstance(courses_source, list):
            raise ValueError(
                "Le champ courses doit être une liste."
            )

        courses = []
        identifiants = set()

        for index, course in enumerate(
            courses_source, start=1
        ):
            if not isinstance(course, dict):
                raise ValueError(
                    f"Course {index} : objet JSON attendu."
                )

            identifiant = course.get("id")

            if (
                not isinstance(identifiant, str)
                or not identifiant.strip()
            ):
                raise ValueError(
                    f"Course {index} : identifiant manquant."
                )

            identifiant = identifiant.strip()

            if identifiant in identifiants:
                raise ValueError(
                    f"Identifiant de course en double : "
                    f"{identifiant}"
                )

            identifiants.add(identifiant)

            statut = course.get("statut")

            if (
                statut is not None
                and not isinstance(statut, str)
            ):
                raise ValueError(
                    f"Course {identifiant} : statut invalide."
                )

            moteurs = _moteurs_valides(
                course.get("moteurs")
            )

            entree = dict(course)
            entree["id"] = identifiant
            entree["moteurs"] = moteurs

            courses.append(entree)

        publication = {
            "date": audit.get("date"),
            "genere_a": audit.get("genere_a"),
            "statut_collecte": audit.get(
                "statut_collecte"
            ),
            "courses": courses,
            "informations": [
                "Les PV et FIGÉS historiques ne sont pas modifiés.",
                "Une analyse provisoire n'est pas un FIGÉ.",
                "Les statuts publiés ne constituent pas "
                "une authentification des FIGÉS.",
                "Aucun pronostic absent n'a été inventé.",
            ],
        }

        contenu = json.dumps(
            publication,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"

        data_dir.mkdir(
            parents=True, exist_ok=True
        )

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=data_dir,
            prefix=".publication_",
            suffix=".tmp",
            delete=False,
        ) as fichier:
            temporaire = Path(fichier.name)
            fichier.write(contenu)
            fichier.flush()
            os.fsync(fichier.fileno())

        os.replace(temporaire, cible)
        temporaire = None

        print(
            f"HIPPOFUSION X1000 : "
            f"{len(courses)} course(s) publiée(s)."
        )
        print(f"Fichier : {cible}")

        return cible

    except (
        OSError,
        ValueError,
        TypeError,
    ) as erreur:
        print(
            f"ERREUR : publication impossible : {erreur}"
        )
        return None

    finally:
        if temporaire is not None:
            temporaire.unlink(missing_ok=True)


publish = publier


if __name__ == "__main__":
    if publier() is None:
        raise SystemExit(1)
