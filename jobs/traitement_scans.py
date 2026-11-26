"""Analyse des scans reçus : extraction IA, rapprochement, création ou renvoi.

Pour chaque scan en attente :

0. **découpage** — un scan peut être une pile de plusieurs bordereaux. Chaque
   page est lue seule (type de document, numéro de BL, pagination), puis
   `segmentation.decouper` pose les coupes. Un lot de plusieurs BL est réparti
   en scans fils, qui repassent ici un par un ; un découpage douteux part en
   confirmation humaine. Un scan d'un seul BL continue directement ;
1. rasterisation du PDF ;
2. `extraction.extraire_infos_bl` sur **toutes les pages du bordereau
   ensemble**, annexes exclues (une lettre de voiture porte son propre
   numéro) ;
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
    # --- Scans contenant plusieurs bordereaux ---
    ("multi_bl", "true"),          # false = un scan est toujours UN bordereau
    ("multi_bl_auto", "true"),     # false = tout lot est confirmé par un humain
    ("max_pages_bl", "25"),        # au-delà : groupe jugé anormalement long
    ("max_pages_lot", "60"),       # plafond de pages d'un scan
]


def parametres(dbutils):
    valeurs = lire_parametres(dbutils, PARAMETRES)
    if not IDENTIFIER.fullmatch(valeurs["pg_schema"]):
        raise ValueError(f"pg_schema invalide ({valeurs['pg_schema']!r}).")
    if not valeurs["pg_host"]:
        raise ValueError("Le paramètre pg_host est obligatoire.")
    for booleen in ("auto_sans_desadv", "multi_bl", "multi_bl_auto"):
        if valeurs[booleen].strip().lower() not in ("true", "false"):
            raise ValueError(f"{booleen} doit valoir true ou false.")
    pages_bl = entier_parametre(valeurs["max_pages_bl"], "max_pages_bl", 1, 100)
    pages_lot = entier_parametre(valeurs["max_pages_lot"], "max_pages_lot", 1, 200)
    if pages_lot < pages_bl:
        raise ValueError("max_pages_lot ne peut pas être inférieur à max_pages_bl.")
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
        "BL_SCAN_MULTI_BL": args.multi_bl.strip().lower(),
        "BL_SCAN_MULTI_BL_AUTO": args.multi_bl_auto.strip().lower(),
        "BL_SCAN_MAX_PAGES": args.max_pages_bl.strip(),
        "BL_SCAN_MAX_PAGES_LOT": args.max_pages_lot.strip(),
    })


def options_decoupage() -> types.SimpleNamespace:
    """Réglages du découpage, lus dans l'environnement préparé par le job."""
    return types.SimpleNamespace(
        multi_bl=os.environ.get("BL_SCAN_MULTI_BL", "true") == "true",
        multi_bl_auto=os.environ.get("BL_SCAN_MULTI_BL_AUTO", "true") == "true",
        max_pages_bl=int(os.environ.get("BL_SCAN_MAX_PAGES", "25")))


def decouper_lot(scan: dict, pages: list, modules, options):
    """Découpe un scan reçu du copieur. Renvoie `(statut, meta)`.

    * `statut` non vide : le sort du scan est réglé ici — lot réparti en
      scans fils (DECOUPE) ou envoyé en confirmation humaine (A_DECOUPER) ;
    * `statut` vide : c'est UN bordereau, le traitement continue comme avant.
      `meta` indique alors les pages à ne pas lire comme des pages de BL
      (annexes, versos blancs) et les avertissements éventuels.
    """
    _decision, extraction, _notifications, repository, scan_pdf, segmentation = modules
    scan_id = int(scan["id"])
    fiches = segmentation.ficher_lot(
        pages, lire_page=extraction.ficher_page, est_blanche=scan_pdf.est_blanche)
    lisibles = [f for f in fiches if f.type != segmentation.TYPE_BLANCHE]
    if lisibles and all(f.type == segmentation.TYPE_ILLISIBLE for f in lisibles):
        # Modèle indisponible sur toutes les pages : on ne sait rien du lot.
        # Repli sur le comportement d'origine plutôt que d'envoyer chaque scan
        # en confirmation de découpage.
        logger.warning("Scan %d : aucune page analysée, découpage ignoré.",
                       scan_id)
        return "", {}

    decoupage = segmentation.decouper(fiches, max_pages_bl=options.max_pages_bl)
    trace = decoupage.vers_json(fiches)

    if decoupage.sur and not decoupage.multi:
        repository.enregistrer_decoupage(scan_id, trace, nb_pages=len(pages))
        segment = decoupage.segments[0] if decoupage.segments else None
        return "", {
            "annexes": sorted(set(decoupage.ecartees)
                              | set(segment.annexes if segment else [])),
            "avertissements": list(segment.avertissements) if segment else [],
        }

    if decoupage.sur and options.multi_bl_auto:
        charges = segmentation.charges_segments(
            decoupage, lambda index: scan_pdf.extraire_pages(scan["contenu"], index))
        repository.creer_segments(scan_id, charges, ACTEUR, trace)
        logger.info("Scan %d : lot de %d pages découpé en %d bordereaux (%s)",
                    scan_id, len(pages), len(charges),
                    ", ".join(s.etendue() for s in decoupage.segments))
        return repository.STATUT_SCAN_DECOUPE, {}

    motif = (" ".join(decoupage.incertitudes) if decoupage.incertitudes else
             f"Lot de {len(decoupage.segments)} bordereaux : confirmation "
             "demandée (découpage automatique des lots désactivé).")
    repository.enregistrer_decoupage(
        scan_id, trace, statut=repository.STATUT_SCAN_A_DECOUPER, motif=motif,
        nb_pages=len(pages))
    logger.info("Scan %d : découpage à confirmer — %s", scan_id, motif)
    return repository.STATUT_SCAN_A_DECOUPER, {}


def traiter_un_scan(scan: dict, modules, dpi: int, options=None) -> str:
    """Analyse et conclut un scan. Renvoie le statut appliqué.

    Chaque scan est traité indépendamment : une exception ici ne concerne que
    ce scan, qui repart en ERREUR et pourra être rejoué."""
    decision, extraction, notifications, repository, scan_pdf, segmentation = modules
    options = options or options_decoupage()
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

    # --- 1 bis. Un bordereau, ou une pile de bordereaux ? -----------------
    # Un scan fils est déjà un groupe : il ne se redécoupe pas. Sans modèle,
    # aucune page ne peut être lue : le scan est traité comme un seul BL.
    est_groupe = scan.get("id_parent") is not None
    meta = dict(scan.get("decoupage") or {}) if est_groupe else {}
    if (options.multi_bl and not est_groupe and len(pages) > 1
            and extraction.endpoint_configure()):
        statut_lot, meta = decouper_lot(scan, pages, modules, options)
        if statut_lot:
            return statut_lot
    # Les annexes restent attachées au BL, mais ne sont pas lues comme des
    # pages de bordereau.
    pages_bl = segmentation.pages_a_analyser(pages, meta)
    avertissements = [str(a) for a in meta.get("avertissements") or []]

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
        infos = extraction.extraire_infos_bl(pages_bl, tiers_libelle, referentiel)
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
    if avertissements:
        trace["avertissements_decoupage"] = avertissements

    if not verdict.automatique:
        motif = " ".join([verdict.motif, *avertissements]).strip()
        repository.cloturer_scan(
            scan_id, repository.STATUT_SCAN_A_VALIDER,
            confiance=verdict.confiance, motif=motif,
            extraction_json=trace, nb_pages=len(pages))
        logger.info("Scan %d en validation humaine : %s", scan_id, motif)
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


def traiter_la_file(repository, modules, dpi: int, max_scans: int,
                    taille_lot: int = 10) -> dict:
    """Vide la file par petits paquets, dans la limite de `max_scans`.

    La file est relue après chaque paquet : les bordereaux issus d'un lot
    découpé à l'instant y entrent à l'état RECU et sont donc analysés dans la
    MÊME exécution, sans attendre le passage suivant du job."""
    metriques = {"scans": 0, "crees_auto": 0, "a_valider": 0,
                 "lots_decoupes": 0, "lots_a_decouper": 0, "erreurs": 0}
    compteur = {repository.STATUT_SCAN_AUTO: "crees_auto",
                repository.STATUT_SCAN_A_VALIDER: "a_valider",
                repository.STATUT_SCAN_DECOUPE: "lots_decoupes",
                repository.STATUT_SCAN_A_DECOUPER: "lots_a_decouper"}
    restant = max_scans
    while restant > 0:
        scans = repository.prendre_scans_a_analyser(limite=min(restant, taille_lot))
        if not scans:
            break
        restant -= len(scans)
        for scan in scans:
            metriques["scans"] += 1
            try:
                statut = traiter_un_scan(scan, modules, dpi)
                metriques[compteur.get(statut, "a_valider")] += 1
            except Exception as exc:
                # Le scan repart en ERREUR : visible dans la vue Scans reçus,
                # et rejouable après correction (référentiel, endpoint, PDF).
                metriques["erreurs"] += 1
                logger.exception("Traitement du scan %s en échec", scan.get("id"))
                try:
                    repository.cloturer_scan(
                        int(scan["id"]), repository.STATUT_SCAN_ERREUR,
                        erreur=f"{type(exc).__name__} : {exc}")
                except Exception:
                    logger.exception("Impossible de tracer l'échec du scan %s",
                                     scan.get("id"))
    return metriques


def main() -> None:
    configure_logging()
    dbutils = job_dbutils()
    args = parametres(dbutils)
    workspace = WorkspaceClient()
    preparer_environnement(args, workspace)

    # `ui` n'est volontairement PAS importé : il dépend de Streamlit, absent
    # de l'environnement d'une tâche Lakeflow.
    from bl_core import (decision, extraction, notifications, repository,
                         scan_pdf, segmentation)

    verifier_bl_core("decision", "extraction", "notifications",
                     "repository", "scan_pdf", "segmentation")
    repository.verifier_schema_multi_bl()
    modules = (decision, extraction, notifications, repository, scan_pdf,
               segmentation)
    metriques = traiter_la_file(repository, modules, args.dpi_n, args.max_scans_n)
    if not metriques["scans"]:
        logger.info("Aucun scan en attente.")
        return

    logger.info("Traitement terminé : %s", json_metrics(**metriques))
    # La tâche échoue si TOUS les scans ont échoué : signe d'un problème
    # systémique (endpoint, droits) et non d'un scan mal formé isolé.
    if metriques["erreurs"] and metriques["erreurs"] == metriques["scans"]:
        raise RuntimeError(
            f"Les {metriques['erreurs']} scans de cette exécution ont échoué — vérifiez "
            "l'endpoint de modèle et les droits Lakebase.")


if __name__ == "__main__":
    main()
