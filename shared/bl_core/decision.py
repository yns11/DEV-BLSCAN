"""Création automatique ou validation humaine : la règle de décision.

Un scan arrive, le modèle en extrait des champs. Faut-il créer le BL
directement, ou le soumettre à un gestionnaire ?

Principe directeur
------------------
**Un BL créé à tort coûte plus cher qu'un BL à valider.** Une création
erronée pollue le rapprochement DESADV, fausse les indicateurs et se corrige
à la main ; une validation de trop coûte trente secondes à un gestionnaire.
La règle est donc volontairement **conservatrice** : en cas de doute, on
demande.

Les trois niveaux de confiance, du plus fort au plus faible :

1. **desadv** — le numéro lu correspond à un avis d'expédition actif de l'ERP
   pour ce sens. C'est un recoupement avec un système tiers : le tiers est
   alors pris **dans le DESADV**, pas dans la lecture du modèle. Confiance
   maximale, création automatique.
2. **code** — pas de DESADV, mais le modèle a lu un *code tiers*
   (`S-000000` / `C-000000`) qui existe au référentiel. Un code est bien plus
   robuste qu'une raison sociale : peu de caractères, format contraint. La
   création automatique est possible mais **désactivée par défaut**
   (`BL_SCAN_AUTO_SANS_DESADV`), le temps de mesurer la précision réelle sur
   parc de fournisseurs.
3. **nom** ou **aucun** — rapprochement par raison sociale, ou tiers non
   identifié : validation humaine, toujours.

Ce module ne fait aucun accès base ni aucun appel réseau : les dépendances
(recherche DESADV, disponibilité du numéro) sont **injectées**, ce qui le rend
testable de bout en bout.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import extraction

logger = logging.getLogger("bl.decision")

# Niveaux de confiance, du plus fort au plus faible.
CONFIANCE_DESADV = "desadv"
CONFIANCE_CODE = "code"
CONFIANCE_NOM = "nom"
CONFIANCE_AUCUNE = "aucun"


@dataclass
class Decision:
    """Verdict pour un scan analysé.

    `automatique` : le BL peut être créé sans intervention.
    `motif` : raison de la validation humaine (affichée au gestionnaire).
    `champs` : valeurs retenues, prêtes pour l'insertion ou le pré-remplissage.
    """
    automatique: bool
    confiance: str
    motif: str = ""
    champs: dict = field(default_factory=dict)

    @property
    def statut_scan(self) -> str:
        return "TRAITE_AUTO" if self.automatique else "A_VALIDER"


def sens_du_destinataire(destinataires: list[str], alias_achat: str,
                         alias_vente: str) -> Optional[str]:
    """ACHAT / VENTE déduit de l'alias qui a reçu le mail.

    Deux alias distincts sur la même boîte suffisent à router les scans sans
    rien demander au réceptionniste : il appuie sur « BL Réception » ou
    « BL Expédition » sur l'écran du copieur. None si aucun alias ne
    correspond — le scan est alors écarté plutôt que rangé au hasard, une
    erreur de sens étant coûteuse à rattraper."""
    for adresse in destinataires:
        cible = (adresse or "").lower()
        if alias_achat and alias_achat in cible:
            return "ACHAT"
        if alias_vente and alias_vente in cible:
            return "VENTE"
    return None


def _date_lue(texte: str) -> Optional[datetime.date]:
    try:
        return datetime.date.fromisoformat((texte or "")[:10])
    except Exception:
        return None


def plage_horaire_de(horodatage: datetime.datetime, plages: list[str]) -> str:
    """Plage horaire correspondant à l'heure du scan.

    Le réceptionniste scanne le bordereau au moment de la réception : l'heure
    d'arrivée du mail est donc un bon estimateur de la plage horaire, bien
    meilleur qu'une valeur par défaut."""
    heure = horodatage.hour
    if heure < 6:
        return plages[0]
    if heure >= 20:
        return plages[-1]
    debut = 6 + ((heure - 6) // 2) * 2
    attendu = f"{debut:02d}h-{debut + 2:02d}h"
    return attendu if attendu in plages else plages[0]


def decider(infos: dict, sens: str, type_operation: str, tiers_connus: list[str],
            recu_le: datetime.datetime, *,
            desadv_pour_numero: Callable[[str], Optional[str]],
            numero_disponible: Callable[[str], bool],
            quai_du_tiers: Callable[[str], Optional[str]],
            quai_defaut: str,
            plages: list[str],
            auto_sans_desadv: bool = False) -> Decision:
    """Décide du sort d'un scan analysé et prépare les champs du BL.

    `infos` sort de `extraction.extraire_infos_bl` (toutes les pages du
    bordereau analysées ensemble). Les fonctions injectées interrogent la base :

    * ``desadv_pour_numero(numero)`` -> tiers annoncé par l'ERP, ou None ;
    * ``numero_disponible(numero)``  -> False si le numéro existe déjà ;
    * ``quai_du_tiers(tiers)``       -> quai du protocole logistique, ou None.
    """
    numero = (infos.get("numero_bl") or "").strip()
    tiers_reconnu, fiabilite = extraction.rapprocher_tiers(
        infos.get("code_tiers"), infos.get("tiers"), tiers_connus)

    # --- Champs communs, quelle que soit l'issue -------------------------
    date_bl = _date_lue(infos.get("date", "")) or recu_le.date()
    est_nok = extraction.statut_est_nok(infos.get("statut"))
    champs = {
        "numero": numero,
        "fournisseur": tiers_reconnu or "",
        "date_reception": date_bl,
        "plage_horaire": plage_horaire_de(recu_le, plages),
        "statut_bl": "0" if est_nok else "1",   # 0 = EDI NOK, 1 = OK
        "commentaire": (infos.get("commentaire") or "").strip(),
        "date_lue": bool(_date_lue(infos.get("date", ""))),
        "fiabilite_tiers": fiabilite,
    }

    def _refuser(motif: str, confiance: str) -> Decision:
        champs["quai"] = _quai(champs["fournisseur"], quai_du_tiers, quai_defaut)
        return Decision(False, confiance, motif, champs)

    # --- Contrôles bloquants ---------------------------------------------
    if not numero:
        return _refuser("Numéro de BL illisible sur le scan.", CONFIANCE_AUCUNE)
    try:
        if not numero_disponible(numero):
            return _refuser(
                f"Le numéro « {numero} » existe déjà en base : doublon probable "
                "(scan envoyé deux fois) ou erreur de lecture.",
                CONFIANCE_AUCUNE)
    except Exception as exc:
        # Base indisponible : on ne crée RIEN à l'aveugle.
        return _refuser(f"Contrôle d'unicité impossible ({type(exc).__name__}).",
                        CONFIANCE_AUCUNE)

    # --- Voie 1 : recoupement avec un avis d'expédition de l'ERP ----------
    try:
        tiers_desadv = desadv_pour_numero(numero)
    except Exception as exc:
        logger.warning("Consultation DESADV impossible pour %s : %s", numero, exc)
        tiers_desadv = None

    if tiers_desadv:
        # Le DESADV fait foi sur le tiers : c'est la donnée de l'ERP.
        champs["fournisseur"] = tiers_desadv
        champs["quai"] = _quai(tiers_desadv, quai_du_tiers, quai_defaut)
        if tiers_reconnu and tiers_reconnu != tiers_desadv:
            # Divergence à signaler sans bloquer : l'ERP reste la référence,
            # mais le gestionnaire doit pouvoir le savoir.
            champs["ecart_tiers"] = (
                f"Le scan indique « {tiers_reconnu} », l'avis d'expédition "
                f"« {tiers_desadv} ».")
        return Decision(True, CONFIANCE_DESADV, "", champs)

    # --- Voie 2 : code tiers reconnu au référentiel -----------------------
    champs["quai"] = _quai(champs["fournisseur"], quai_du_tiers, quai_defaut)
    if fiabilite == "code" and tiers_reconnu:
        if not auto_sans_desadv:
            return _refuser(
                f"Aucun avis d'expédition pour « {numero} ». Le code tiers est "
                "reconnu, mais la création automatique sans DESADV est "
                "désactivée (BL_SCAN_AUTO_SANS_DESADV).", CONFIANCE_CODE)
        if not champs["date_lue"]:
            return _refuser(
                "Aucun avis d'expédition et aucune date lisible sur le scan : "
                "la date de réception doit être confirmée.", CONFIANCE_CODE)
        return Decision(True, CONFIANCE_CODE, "", champs)

    # --- Voie 3 : tout le reste -> validation humaine ---------------------
    if tiers_reconnu:
        return _refuser(
            f"Tiers rapproché sur la raison sociale seule (« {tiers_reconnu} ») "
            "et aucun avis d'expédition : à confirmer.", CONFIANCE_NOM)
    detecte = " / ".join(x for x in (infos.get("code_tiers"),
                                     infos.get("tiers")) if x)
    return _refuser(
        f"Tiers non identifié au référentiel{f' (lu : « {detecte} »)' if detecte else ''}.",
        CONFIANCE_AUCUNE)


def _quai(tiers: str, quai_du_tiers: Callable[[str], Optional[str]],
          quai_defaut: str) -> str:
    """Quai du protocole logistique du tiers, quai par défaut sinon.
    Même règle que la saisie manuelle du projet d'origine."""
    if tiers:
        try:
            return quai_du_tiers(tiers) or quai_defaut
        except Exception:                    # table PLA absente / non accordée
            return quai_defaut
    return quai_defaut
