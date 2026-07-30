"""Contrôle d'accès basé sur les rôles (RBAC), strict par défaut.

Cette variante « scan » n'a plus d'application de saisie : les BL entrent par
le pipeline (copieur -> mail -> job) et, à défaut de rapprochement certain, par
l'écran de validation. Il n'y a donc plus de matrice OPERATIONS_CREATION.

Les RÔLES d'un utilisateur (email Databricks) sont lus dans la table
`roles_utilisateurs` (gérée dans l'app Administration, Gestion ▸ Rôles).
La MATRICE des droits par vue est portée ici, dans le code : elle change avec
les évolutions fonctionnelles de la solution et est donc versionnée avec elle.

Niveaux : AUCUN (vue masquée), LECTURE (consultation seule),
MODIFICATION (toutes les actions). En mode strict, une table vide, une erreur
de lecture ou un utilisateur inconnu donne ZÉRO droit. Le bootstrap initial se
fait explicitement avec BL_BOOTSTRAP_ADMINS, puis par l'écran Rôles.
"""

import logging

from . import repository
from .config import get_settings

logger = logging.getLogger("bl.rbac")

ROLE_LOG = "LOG"
ROLE_APPROS = "APPROS"
ROLE_ADV = "ADV"
ROLE_FINANCE = "FINANCE"
ROLE_ADMIN = "ADMIN_METIER"
ROLES = [ROLE_LOG, ROLE_APPROS, ROLE_ADV, ROLE_FINANCE, ROLE_ADMIN]

AUCUN, LECTURE, MODIFICATION = "aucun", "lecture", "modification"
_ORDRE = {AUCUN: 0, LECTURE: 1, MODIFICATION: 2}

# --- App Administration : niveau par vue et par rôle (matrice RBAC ;
# Fournisseurs et Clients sont désormais dans Gestion, droits inchangés). ---
VUES_ADMINISTRATION = {
    "Tableau de bord": {ROLE_APPROS: LECTURE, ROLE_ADV: LECTURE,
                        ROLE_FINANCE: LECTURE, ROLE_ADMIN: MODIFICATION},
    # Rapports d'activité : consultables par tous les rôles métier ;
    # MODIFICATION = droit de (re)générer un rapport à la demande.
    "Rapports d'activité": {ROLE_APPROS: LECTURE, ROLE_ADV: LECTURE,
                            ROLE_FINANCE: LECTURE, ROLE_ADMIN: MODIFICATION},
    "BL réception": {ROLE_APPROS: MODIFICATION, ROLE_FINANCE: LECTURE,
                     ROLE_ADMIN: MODIFICATION},
    "DESADV achat": {ROLE_APPROS: LECTURE, ROLE_ADMIN: MODIFICATION},
    "Rapprochement achat": {ROLE_APPROS: LECTURE, ROLE_FINANCE: LECTURE,
                            ROLE_ADMIN: MODIFICATION},
    "BL expédition": {ROLE_ADV: MODIFICATION, ROLE_FINANCE: LECTURE,
                      ROLE_ADMIN: MODIFICATION},
    "DESADV vente": {ROLE_ADV: LECTURE, ROLE_ADMIN: MODIFICATION},
    "Rapprochement vente": {ROLE_ADV: LECTURE, ROLE_FINANCE: LECTURE,
                            ROLE_ADMIN: MODIFICATION},
    # Validation des scans que le pipeline n'a pas pu rapprocher :
    # APPROS côté achat, ADV côté vente. Créer un BL depuis un scan est une
    # écriture, d'où MODIFICATION.
    "Scans à valider (achat)": {ROLE_APPROS: MODIFICATION, ROLE_ADMIN: MODIFICATION},
    "Scans à valider (vente)": {ROLE_ADV: MODIFICATION, ROLE_ADMIN: MODIFICATION},
    # Journal du pipeline : lecture pour les métiers, dépôt manuel de secours
    # réservé aux administrateurs.
    "Scans reçus": {ROLE_APPROS: LECTURE, ROLE_ADV: LECTURE,
                    ROLE_FINANCE: LECTURE, ROLE_ADMIN: MODIFICATION},
    "Fournisseurs": {ROLE_ADMIN: MODIFICATION},
    "Clients": {ROLE_ADMIN: MODIFICATION},
    "Notifications": {ROLE_APPROS: LECTURE, ROLE_ADV: LECTURE, ROLE_ADMIN: LECTURE},
    # « Tout le reste » du module Gestion : administrateurs métier uniquement.
    "Gestionnaires": {ROLE_ADMIN: MODIFICATION},
    "Portefeuilles": {ROLE_ADMIN: MODIFICATION},
    "Quais": {ROLE_ADMIN: MODIFICATION},
    "Adresses": {ROLE_ADMIN: MODIFICATION},
    "Sites logistiques": {ROLE_ADMIN: MODIFICATION},
    "PLA": {ROLE_ADMIN: MODIFICATION},
    "Rôles": {ROLE_ADMIN: MODIFICATION},
    "Qualité IA": {ROLE_ADMIN: MODIFICATION},
}


def contexte_rbac(utilisateur: str) -> dict:
    """Retourne le contexte d'autorisation sans jamais ouvrir les droits sur
    une erreur technique."""
    settings = get_settings()
    normalized = (utilisateur or "").strip().lower()
    if settings.rbac_mode == "disabled":
        return {"actif": False, "roles": list(ROLES), "indisponible": False}
    if normalized in settings.bootstrap_admins:
        return {"actif": True, "roles": [ROLE_ADMIN], "indisponible": False}
    try:
        roles = repository.roles_utilisateur(normalized)
        invalid = sorted(set(roles) - set(ROLES))
        if invalid:
            logger.error("Rôles inconnus ignorés pour %s : %s", normalized, invalid)
        return {
            "actif": True,
            "roles": [role for role in roles if role in ROLES],
            "indisponible": False,
        }
    except Exception as exc:
        logger.exception("RBAC indisponible pour %s", normalized)
        return {
            "actif": True,
            "roles": [],
            "indisponible": True,
            "erreur": str(exc),
        }


def niveau_vue(vue: str, ctx: dict) -> str:
    """Niveau d'accès de l'utilisateur sur une vue de l'app Administration."""
    if not ctx["actif"]:
        return MODIFICATION
    droits = VUES_ADMINISTRATION.get(vue, {})
    niveaux = [droits.get(r, AUCUN) for r in ctx["roles"]] or [AUCUN]
    return max(niveaux, key=_ORDRE.get)


def exiger_vue(vue: str, ctx: dict, niveau: str = LECTURE) -> None:
    """Garde serveur réutilisable avant une action sensible."""
    obtenu = niveau_vue(vue, ctx)
    if _ORDRE[obtenu] < _ORDRE[niveau]:
        raise PermissionError(f"Accès {niveau} refusé pour la vue « {vue} ».")
