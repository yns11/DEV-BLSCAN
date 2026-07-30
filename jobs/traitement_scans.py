"""Analyse des scans reçus : extraction IA, rapprochement, création ou renvoi.

Pour chaque scan en attente :

1. rasterisation du PDF (1 à 25 pages) ;
2. `extraction.extraire_infos_bl` sur **toutes les pages ensemble** — un scan
   est UN bordereau, exactement comme un BL photographié en plusieurs prises ;
3. `decision.decider` tranche : création automatique, ou renvoi en validation
   humaine avec le motif ;
4. si automatique : BL créé **en une transaction** (métadonnées + pages), puis
   notification Teams des gestionnaires du portefeuille pour une réception.

Séparé de l'ingestion à dessein : on peut relancer l'analyse d'un scan sans
retoucher à la boîte mail, et une panne de l'endpoint de modèle n'empêche pas
les scans d'arriver.

Planification conseillée : **toutes les 10 minutes**, décalée de 5 minutes par
rapport à l'ingestion.
"""

from __future__ import annotations

import logging
import os
import re
import types
import uuid

from common import (
    configure_logging,
    entier_parametre,
    identite_connexion,
    job_dbutils,
    json_metrics,
    lire_parametres,
    resoudre_endpoint,
    verifier_bl_core,
)
from databricks.sdk import WorkspaceClient

logger = logging.getLogger("bl.jobs.traitement")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Acteur inscrit dans saisie_par et dans l'audit pour les créations
# automatiques : distingue sans ambiguïté ce qui vient du pipeline.
ACTEUR = "pipeline:scan"

PARAMETRES = [
    ("pg_host", ""),
    ("pg_database", "databricks_postgres"),
    ("pg_schema", "bl_scan"),
    ("lakebase_endpoint", ""),
    ("pg_user", ""),
    ("llm_endpoint", "databricks-claude-opus-4-8"),
    ("teams_webhook_reception", ""),
    ("auto_sans_desadv", "false"),
    ("max_scans", "40"),
    ("dpi", "150"),
]


def parametres(dbutils):
    valeurs = lire_parametres(dbutils, PARAMETRES)
    if not IDENTIFIER.fullmatch(valeurs["pg_schema"]):
        raise ValueError(f"pg_schema invalide ({valeurs['pg_schema']!r}).")
    if not valeurs["pg_host"]:
        raise ValueError("Le paramètre pg_host est obligatoire.")
    if valeurs["auto_sans_desadv"].strip().lower() not in ("true", "false"):
        raise ValueError("auto_sans_desadv doit valoir true ou false.")
    return types.SimpleNamespace(
        **{cle: valeurs[cle] for cle, _ in PARAMETRES},
        max_scans_n=entier_parametre(valeurs["max_scans"], "max_scans", 1, 500),
        dpi_n=entier_parametre(valeurs["dpi"], "dpi", 72, 400),
    )


def preparer_environnement(args, workspace: WorkspaceClient) -> None:
    endpoint = resoudre_endpoint(workspace, args.pg_host, args.lakebase_endpoint)
    os.environ.update({
        "LAKEBASE_ENDPOINT": endpoint,
        "PGHOST": args.pg_host,
        "PGPORT": "5432",
        "PGDATABASE": args.pg_database,
        "PGUSER": args.pg_user or identite_connexion(workspace),
        "PGSSLMODE": "require",
        "PGAPPNAME": "bldemat-traitement",
        "BL_PG_SCHEMA": args.pg_schema,
        "BL_ENVIRONMENT": "prod",
        "BL_RBAC_MODE": "strict",
        "BL_LLM_ENDPOINT": args.llm_endpoint.strip(),
        "BL_TEAMS_WEBHOOK_RECEPTION": args.teams_webhook_reception.strip(),
        "BL_SCAN_AUTO_SANS_DESADV": args.auto_sans_desadv.strip().lower(),
    })


def traiter_un_scan(scan: dict, modules, dpi: int) -> str:
    """Analyse et conclut un scan. Renvoie le statut appliqué.

    Chaque scan est traité indépendamment : une exception ici ne concerne que
    ce scan, qui repart en ERREUR et pourra être rejoué."""
    decision, extraction, notifications, repository, scan_pdf = modules
    scan_id = int(scan["id"])
    sens = scan["sens"]
    est_vente = sens == repository.SENS_VENTE
    type_operation = (repository.TYPE_EXPEDITION if est_vente
                      else repository.TYPE_RECEPTION)
    type_tiers = (repository.TIERS_CLIENT if est_vente
                  else repository.TIERS_FOURNISSEUR)
    tiers_libelle = "client" if est_vente else "fournisseur"

    # --- 1. PDF -> images ------------------------------------------------
    pages = scan_pdf.rasteriser(scan["contenu"], dpi=dpi)

    # --- 2. Extraction, avec le même référentiel que la saisie manuelle ---
    tiers_connus = repository.lister_tiers(type_tiers)
    try:
        adresses = repository.adresses_par_tiers()
    except Exception:
        adresses = {}
    referentiel = extraction.Referentiel(
        tiers=tiers_connus,
        bls_pour_tiers=lambda nom: repository.bls_desadv_pour_tiers(nom, sens),
        adresses=adresses)

    if extraction.endpoint_configure():
        infos = extraction.extraire_infos_bl(pages, tiers_libelle, referentiel)
    else:
        # Sans modèle, tout part en validation humaine : le pipeline reste
        # utilisable, il ne pré-remplit simplement rien.
        infos = {champ: "" for champ in extraction.CHAMPS_ATTENDUS}
        logger.warning("Aucun endpoint LLM : scan %d envoyé en validation "
                       "sans pré-remplissage.", scan_id)

    # --- 3. Décision ------------------------------------------------------
    verdict = decision.decider(
        infos, sens, type_operation, tiers_connus, scan["recu_le"],
        desadv_pour_numero=lambda num: repository.fournisseur_pour_bl(num, sens),
        numero_disponible=lambda num: repository.numero_bl_disponible(
            num, type_operation),
        quai_du_tiers=repository.quai_pla,
        quai_defaut=repository.QUAI_DEFAUT,
        plages=repository.PLAGES_HORAIRES,
        auto_sans_desadv=os.environ["BL_SCAN_AUTO_SANS_DESADV"] == "true")

    trace = {**infos, **{cle: str(val) for cle, val in verdict.champs.items()}}

    if not verdict.automatique:
        repository.cloturer_scan(
            scan_id, repository.STATUT_SCAN_A_VALIDER,
            confiance=verdict.confiance, motif=verdict.motif,
            extraction_json=trace, nb_pages=len(pages))
        logger.info("Scan %d en validation humaine : %s", scan_id, verdict.motif)
        return repository.STATUT_SCAN_A_VALIDER

    # --- 4. Création automatique ------------------------------------------
    champs = verdict.champs
    id_bl = str(uuid.uuid4())
    repository.creer_bl_depuis_scan(
        id_bl=id_bl, numero_bl=champs["numero"],
        nom_fournisseur=champs["fournisseur"], statut_bl=champs["statut_bl"],
        type_operation=type_operation, utilisateur=ACTEUR, pages=pages,
        origine="SCAN_AUTO", date_reception=champs["date_reception"],
        quai_reception=champs["quai"], comment_bl=champs["commentaire"],
        plage_horaire=champs["plage_horaire"])
    repository.cloturer_scan(
        scan_id, repository.STATUT_SCAN_AUTO, confiance=verdict.confiance,
        extraction_json=trace, id_bl=id_bl, nb_pages=len(pages),
        traite_par=ACTEUR)
    logger.info("Scan %d : BL %s créé automatiquement (confiance %s)",
                scan_id, champs["numero"], verdict.confiance)

    # --- 5. Notification Teams (best effort, jamais bloquante) ------------
    if type_operation == repository.TYPE_RECEPTION:
        succes, detail = notifications.notifier_nouvelle_reception(
            numero_bl=champs["numero"], fournisseur=champs["fournisseur"],
            quai=champs["quai"], date_reception=champs["date_reception"],
            plage_horaire=champs["plage_horaire"],
            statut_libelle=repository.libelle_statut(champs["statut_bl"]),
            nb_pages=len(pages), utilisateur=ACTEUR)
        if not succes:
            logger.warning("Notification du BL %s non publiée : %s",
                           champs["numero"], detail)
    return repository.STATUT_SCAN_AUTO


def main() -> None:
    configure_logging()
    dbutils = job_dbutils()
    args = parametres(dbutils)
    workspace = WorkspaceClient()
    preparer_environnement(args, workspace)

    # `ui` n'est volontairement PAS importé : il dépend de Streamlit, absent
    # de l'environnement d'une tâche Lakeflow.
    from bl_core import (decision, extraction, notifications, repository,
                         scan_pdf)

    verifier_bl_core("decision", "extraction", "notifications",
                     "repository", "scan_pdf")
    modules = (decision, extraction, notifications, repository, scan_pdf)
    scans = repository.prendre_scans_a_analyser(limite=args.max_scans_n)
    if not scans:
        logger.info("Aucun scan en attente.")
        return

    metriques = {"scans": len(scans), "crees_auto": 0, "a_valider": 0,
                 "erreurs": 0}
    for scan in scans:
        try:
            statut = traiter_un_scan(scan, modules, args.dpi_n)
            if statut == repository.STATUT_SCAN_AUTO:
                metriques["crees_auto"] += 1
            else:
                metriques["a_valider"] += 1
        except Exception as exc:
            # Le scan repart en ERREUR : visible dans la vue Scans reçus, et
            # rejouable après correction (référentiel, endpoint, PDF).
            metriques["erreurs"] += 1
            logger.exception("Traitement du scan %s en échec", scan.get("id"))
            try:
                repository.cloturer_scan(
                    int(scan["id"]), repository.STATUT_SCAN_ERREUR,
                    erreur=f"{type(exc).__name__} : {exc}")
            except Exception:
                logger.exception("Impossible de tracer l'échec du scan %s",
                                 scan.get("id"))

    logger.info("Traitement terminé : %s", json_metrics(**metriques))
    # La tâche échoue si TOUS les scans ont échoué : signe d'un problème
    # systémique (endpoint, droits) et non d'un scan mal formé isolé.
    if metriques["erreurs"] and metriques["erreurs"] == metriques["scans"]:
        raise RuntimeError(
            f"Les {metriques['erreurs']} scans du lot ont échoué — vérifiez "
            "l'endpoint de modèle et les droits Lakebase.")


if __name__ == "__main__":
    main()
