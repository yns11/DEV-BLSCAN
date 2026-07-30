"""Tests du pipeline de scan : règle de décision, Graph, ingestion."""
import datetime
import json
import os
import sys
from pathlib import Path

import pytest

# Racine du projet, déduite de l'emplacement du test : les tests
# doivent tourner depuis n'importe quel clone, pas seulement le mien.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))

os.environ.setdefault("BL_ENVIRONMENT", "local")
os.environ.setdefault("BL_RBAC_MODE", "strict")

PLAGES = (["00h-06h"] + [f"{h:02d}h-{h + 2:02d}h" for h in range(6, 20, 2)]
          + ["20h-00h"])
TIERS = ["S-000803 : MOLEX FRANCE", "S-000178 : ROSENBERGER HOCHFREQUENZTECHNIK"]
RECU = datetime.datetime(2026, 7, 28, 9, 30)


def _infos(**valeurs):
    from bl_core import extraction
    base = {champ: "" for champ in extraction.CHAMPS_ATTENDUS}
    base.update(valeurs)
    return base


def _decider(infos, **surcharges):
    from bl_core import decision
    options = {
        "desadv_pour_numero": lambda num: None,
        "numero_disponible": lambda num: True,
        "quai_du_tiers": lambda tiers: None,
        "quai_defaut": "B15",
        "plages": PLAGES,
        "auto_sans_desadv": False,
    }
    options.update(surcharges)
    return decision.decider(infos, "ACHAT", "RECEPTION", TIERS, RECU, **options)


# ---------------------------------------------------------------------------
# Voie 1 : recoupement avec un avis d'expédition (confiance maximale)
# ---------------------------------------------------------------------------
def test_desadv_declenche_la_creation_automatique():
    from bl_core import decision
    verdict = _decider(
        _infos(numero_bl="8402002398", code_tiers="S-000803", tiers="MOLEX"),
        desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is True
    assert verdict.confiance == decision.CONFIANCE_DESADV
    assert verdict.statut_scan == "TRAITE_AUTO"
    assert verdict.champs["fournisseur"] == "S-000803 : MOLEX FRANCE"


def test_le_desadv_fait_foi_sur_le_tiers():
    """Si le scan et l'ERP divergent, c'est l'ERP qui gagne — mais l'écart est
    signalé au gestionnaire."""
    verdict = _decider(
        _infos(numero_bl="BL-1", code_tiers="S-000178", tiers="ROSENBERGER"),
        desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is True
    assert verdict.champs["fournisseur"] == "S-000803 : MOLEX FRANCE"
    assert "ROSENBERGER" in verdict.champs["ecart_tiers"]
    assert "MOLEX" in verdict.champs["ecart_tiers"]


def test_desadv_suffit_meme_si_le_tiers_est_illisible():
    """Le numéro seul, confirmé par l'ERP, suffit : c'est le cas d'un BL dont
    la raison sociale est mal imprimée ou masquée par un tampon."""
    verdict = _decider(_infos(numero_bl="8402002398"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is True
    assert verdict.champs["fournisseur"] == "S-000803 : MOLEX FRANCE"


# ---------------------------------------------------------------------------
# Voie 2 : code tiers reconnu, sans DESADV
# ---------------------------------------------------------------------------
def test_code_tiers_seul_ne_cree_rien_par_defaut():
    """Garde-fou par défaut : sans confirmation de l'ERP, on demande."""
    from bl_core import decision
    verdict = _decider(_infos(numero_bl="BL-NEUF", code_tiers="S-000803",
                              tiers="MOLEX", date="2026-07-28"))
    assert verdict.automatique is False
    assert verdict.confiance == decision.CONFIANCE_CODE
    assert "BL_SCAN_AUTO_SANS_DESADV" in verdict.motif


def test_code_tiers_cree_si_loption_activee():
    from bl_core import decision
    verdict = _decider(_infos(numero_bl="BL-NEUF", code_tiers="S-000803",
                              tiers="MOLEX", date="2026-07-28"),
                       auto_sans_desadv=True)
    assert verdict.automatique is True
    assert verdict.confiance == decision.CONFIANCE_CODE
    assert verdict.champs["date_reception"] == datetime.date(2026, 7, 28)


def test_code_tiers_sans_date_lisible_reste_a_valider():
    """Sans DESADV NI date lue, la date de réception serait devinée : on
    préfère la faire confirmer."""
    verdict = _decider(_infos(numero_bl="BL-NEUF", code_tiers="S-000803",
                              tiers="MOLEX"), auto_sans_desadv=True)
    assert verdict.automatique is False
    assert "date" in verdict.motif.lower()


# ---------------------------------------------------------------------------
# Voie 3 : tout le reste part en validation
# ---------------------------------------------------------------------------
def test_rapprochement_par_nom_seul_reste_a_valider():
    from bl_core import decision
    verdict = _decider(_infos(numero_bl="BL-NEUF", tiers="MOLEX FRANCE"),
                       auto_sans_desadv=True)
    assert verdict.automatique is False
    assert verdict.confiance == decision.CONFIANCE_NOM
    assert "raison sociale" in verdict.motif


def test_tiers_inconnu_reste_a_valider():
    from bl_core import decision
    verdict = _decider(_infos(numero_bl="BL-NEUF", tiers="SOCIETE FANTOME"),
                       auto_sans_desadv=True)
    assert verdict.automatique is False
    assert verdict.confiance == decision.CONFIANCE_AUCUNE
    assert "non identifié" in verdict.motif


def test_numero_illisible_bloque():
    verdict = _decider(_infos(code_tiers="S-000803", tiers="MOLEX"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is False
    assert "illisible" in verdict.motif


def test_numero_deja_pris_bloque():
    """Deuxième envoi du même scan, ou erreur de lecture : jamais de doublon
    créé en silence."""
    verdict = _decider(_infos(numero_bl="8402002398"),
                       numero_disponible=lambda num: False,
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is False
    assert "existe déjà" in verdict.motif


def test_base_indisponible_ne_cree_rien():
    """Si le contrôle d'unicité échoue, on ne crée RIEN à l'aveugle."""
    def boum(numero):
        raise ConnectionError("Lakebase injoignable")

    verdict = _decider(_infos(numero_bl="BL-1"), numero_disponible=boum,
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.automatique is False
    assert "ConnectionError" in verdict.motif


def test_desadv_indisponible_bascule_en_validation_sans_planter():
    """Une panne de lecture DESADV ne doit pas faire échouer le traitement :
    le scan part simplement en validation."""
    def boum(numero):
        raise TimeoutError("requête trop longue")

    verdict = _decider(_infos(numero_bl="BL-1", code_tiers="S-000803",
                              tiers="MOLEX"), desadv_pour_numero=boum)
    assert verdict.automatique is False


# ---------------------------------------------------------------------------
# Champs dérivés
# ---------------------------------------------------------------------------
def test_plage_horaire_deduite_de_lheure_du_scan():
    """Le réceptionniste scanne au moment de la réception : l'heure du mail
    est un bien meilleur estimateur qu'une valeur par défaut."""
    from bl_core import decision
    assert decision.plage_horaire_de(
        datetime.datetime(2026, 7, 28, 9, 30), PLAGES) == "08h-10h"
    assert decision.plage_horaire_de(
        datetime.datetime(2026, 7, 28, 15, 5), PLAGES) == "14h-16h"
    assert decision.plage_horaire_de(
        datetime.datetime(2026, 7, 28, 3, 0), PLAGES) == "00h-06h"
    assert decision.plage_horaire_de(
        datetime.datetime(2026, 7, 28, 22, 0), PLAGES) == "20h-00h"


def test_date_de_repli_sur_la_reception_du_scan():
    verdict = _decider(_infos(numero_bl="BL-1"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.champs["date_reception"] == RECU.date()
    assert verdict.champs["date_lue"] is False


def test_quai_du_protocole_logistique_prioritaire():
    verdict = _decider(_infos(numero_bl="BL-1"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE",
                       quai_du_tiers=lambda tiers: "B06EST")
    assert verdict.champs["quai"] == "B06EST"


def test_quai_par_defaut_sans_pla():
    verdict = _decider(_infos(numero_bl="BL-1"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.champs["quai"] == "B15"


def test_pla_en_erreur_retombe_sur_le_quai_par_defaut():
    def boum(tiers):
        raise RuntimeError("table PLA non accordée")

    verdict = _decider(_infos(numero_bl="BL-1"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE",
                       quai_du_tiers=boum)
    assert verdict.champs["quai"] == "B15"


def test_statut_edi_nok_detecte_a_la_main():
    verdict = _decider(_infos(numero_bl="BL-1", statut="NOK"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.champs["statut_bl"] == "0"      # 0 = EDI NOK


def test_statut_ok_par_defaut():
    verdict = _decider(_infos(numero_bl="BL-1"),
                       desadv_pour_numero=lambda num: "S-000803 : MOLEX FRANCE")
    assert verdict.champs["statut_bl"] == "1"


def test_les_champs_sont_toujours_prepares_meme_en_validation():
    """L'écran de validation doit pouvoir pré-remplir même quand la décision
    est « à valider »."""
    verdict = _decider(_infos(numero_bl="BL-1", tiers="MOLEX FRANCE",
                              commentaire="Palette abîmée"))
    assert verdict.automatique is False
    for champ in ("numero", "fournisseur", "date_reception", "plage_horaire",
                  "quai", "statut_bl", "commentaire"):
        assert champ in verdict.champs, champ
    assert verdict.champs["commentaire"] == "Palette abîmée"


# ---------------------------------------------------------------------------
# Sens du flux d'après l'alias destinataire
# ---------------------------------------------------------------------------
def test_sens_deduit_de_lalias():
    """Logique pure, dans bl_core : testable sans le SDK Databricks."""
    from bl_core.decision import sens_du_destinataire
    assert sens_du_destinataire(
        ["scans-bl-reception@emotors.com"], "reception", "expedition") == "ACHAT"
    assert sens_du_destinataire(
        ["scans-bl-expedition@emotors.com"], "reception", "expedition") == "VENTE"
    # Alias inconnu : None -> le scan est rejeté plutôt que rangé au hasard.
    assert sens_du_destinataire(
        ["autre@emotors.com"], "reception", "expedition") is None
    # Casse indifférente, et un destinataire en copie ne perturbe pas.
    assert sens_du_destinataire(
        ["Direction@emotors.com", "SCANS-BL-Reception@emotors.com"],
        "reception", "expedition") == "ACHAT"


def test_sens_recupere_dans_les_entetes_quand_exchange_reecrit():
    """Cas RÉEL : Exchange résout l'alias vers l'adresse principale dans le
    destinataire (« scans-bl@ »), mais le destinataire d'enveloppe survit dans
    la clause « for » d'une ligne Received. Le fragment doit y être retrouvé."""
    from bl_core.decision import sens_du_destinataire
    destinataire_reecrit = "scans-bl@themachineye.onmicrosoft.com"
    entete_received = ("from mail.example.com by outlook.office365.com "
                       "for <scans-bl-reception@themachineye.onmicrosoft.com>; "
                       "wed, 30 jul 2026 07:25:00 +0000")
    signaux = [destinataire_reecrit, "TR: LETS GO", entete_received]
    assert sens_du_destinataire(signaux, "reception", "expedition") == "ACHAT"


def test_sens_recupere_dans_le_sujet():
    """Repli copieur : le multifonction met « RECEPTION » dans le sujet."""
    from bl_core.decision import sens_du_destinataire
    signaux = ["scans-bl@x", "RECEPTION quai 12", ""]
    assert sens_du_destinataire(signaux, "reception", "expedition") == "ACHAT"


def test_sens_ambigu_rejete_plutot_que_devine():
    """Les deux fragments présents (chaîne de transfert) : on n'invente pas un
    sens, on écarte — une erreur de sens coûte cher à rattraper."""
    from bl_core.decision import sens_du_destinataire
    signaux = ["scans-bl@x", "Fwd: reception ET expedition", ""]
    assert sens_du_destinataire(signaux, "reception", "expedition") is None


def test_sens_none_si_rien_ne_ressort():
    from bl_core.decision import sens_du_destinataire
    assert sens_du_destinataire(
        ["scans-bl@x", "TR: LETS GO", ""], "reception", "expedition") is None
