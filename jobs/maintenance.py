"""Maintenance quotidienne : brouillons, jobs orphelins et file de scans.

Trois nettoyages, dans une seule transaction :

* brouillons de BL interrompus depuis plus de `draft_hours` ;
* exécutions de job restées `STARTED` depuis plus de `stale_job_hours` ;
* **file de scans** — verrous de validation oubliés et analyses interrompues
  par un job tué en cours de route, plus purge du PDF source des scans
  terminés depuis `retention_pdf_jours`.
"""

from __future__ import annotations

import logging
import re
import types
import uuid
from datetime import datetime, timezone

from common import (
    configure_logging,
    entier_parametre,
    identite_connexion,
    job_dbutils,
    json_metrics,
    lakebase_connection,
    lire_parametres,
    resoudre_endpoint,
)
from databricks.sdk import WorkspaceClient

logger = logging.getLogger("bl.jobs.maintenance")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Job parameters (Key/Value dans l'onglet Parameters de la tâche).
PARAMETRES = [
    ("pg_host", ""),
    ("pg_database", "databricks_postgres"),
    ("pg_schema", "bl_scan"),
    ("lakebase_endpoint", ""),
    ("pg_user", ""),
    ("draft_hours", "24"),
    ("stale_job_hours", "6"),
    ("scan_verrou_heures", "4"),
    ("retention_pdf_jours", "180"),
]


def parametres():
    """Lit les job parameters via dbutils.widgets (plus d'argparse)."""
    valeurs = lire_parametres(job_dbutils(), PARAMETRES)
    if not IDENTIFIER.fullmatch(valeurs["pg_schema"]):
        raise ValueError(f"Le paramètre pg_schema contient un identifiant invalide "
                         f"({valeurs['pg_schema']!r}).")
    if not valeurs["pg_host"]:
        raise ValueError("Le paramètre pg_host est obligatoire.")
    # pg_user est facultatif : par défaut, l'identité qui exécute le job
    # (celle pour laquelle le jeton OAuth est frappé).
    # lakebase_endpoint est facultatif : retrouvé depuis pg_host si absent.
    return types.SimpleNamespace(
        pg_host=valeurs["pg_host"],
        pg_database=valeurs["pg_database"],
        pg_schema=valeurs["pg_schema"],
        lakebase_endpoint=valeurs["lakebase_endpoint"],
        pg_user=valeurs["pg_user"],
        draft_hours=entier_parametre(valeurs["draft_hours"], "draft_hours", 1, 168),
        stale_job_hours=entier_parametre(valeurs["stale_job_hours"], "stale_job_hours", 1, 48),
        scan_verrou_heures=entier_parametre(valeurs["scan_verrou_heures"],
                                            "scan_verrou_heures", 1, 72),
        retention_pdf_jours=entier_parametre(valeurs["retention_pdf_jours"],
                                             "retention_pdf_jours", 7, 3650),
    )


def main() -> None:
    configure_logging()
    args = parametres()
    workspace = WorkspaceClient()
    run_id = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    metrics = {"drafts_marked_error": 0, "stale_jobs_closed": 0,
               "scans_debloques": 0, "pdf_purges": 0}

    endpoint = resoudre_endpoint(workspace, args.pg_host, args.lakebase_endpoint)
    with lakebase_connection(
        workspace,
        endpoint=endpoint,
        host=args.pg_host,
        database=args.pg_database,
        user=args.pg_user or identite_connexion(workspace),
    ) as connection:
        try:
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {args.pg_schema}.job_executions "
                    "(job_name, run_id, statut, started_at) "
                    "VALUES ('maintenance', %s, 'STARTED', %s) RETURNING id",
                    (run_id, started_at),
                )
                execution_id = cursor.fetchone()[0]
                cursor.execute(
                    f"""
                    WITH marked AS (
                      UPDATE {args.pg_schema}.suivi_bl
                      SET document_statut = 'ERREUR',
                          modifie_par = 'job:maintenance',
                          modifie_le = now(),
                          version = version + 1
                      WHERE document_statut = 'BROUILLON'
                        AND saisie_le < now() - make_interval(hours => %s)
                      RETURNING id_bl
                    )
                    INSERT INTO {args.pg_schema}.audit_bl
                      (id_bl, evenement, valeur_apres, modifie_par)
                    SELECT id_bl, 'BROUILLON_EXPIRE', 'ERREUR', 'job:maintenance'
                    FROM marked
                    """,
                    (args.draft_hours,),
                )
                metrics["drafts_marked_error"] = cursor.rowcount
                cursor.execute(
                    f"UPDATE {args.pg_schema}.job_executions "
                    "SET statut = 'FAILED', finished_at = now(), "
                    "erreur = coalesce(erreur, 'Exécution déclarée orpheline par maintenance') "
                    "WHERE statut = 'STARTED' AND id <> %s "
                    "AND started_at < now() - make_interval(hours => %s)",
                    (execution_id, args.stale_job_hours),
                )
                metrics["stale_jobs_closed"] = cursor.rowcount

                # --- File de scans : verrous oubliés et analyses tuées ------
                cursor.execute(
                    f"UPDATE {args.pg_schema}.scans_recus "
                    "SET verrouille_par = NULL, verrouille_le = NULL "
                    "WHERE statut = 'A_VALIDER' AND verrouille_le IS NOT NULL "
                    "  AND verrouille_le < now() - make_interval(hours => %s)",
                    (args.scan_verrou_heures,))
                debloques = cursor.rowcount
                cursor.execute(
                    f"UPDATE {args.pg_schema}.scans_recus SET statut = 'RECU' "
                    "WHERE statut = 'EN_ANALYSE' "
                    "  AND analyse_le < now() - make_interval(hours => %s)",
                    (args.scan_verrou_heures,))
                metrics["scans_debloques"] = debloques + cursor.rowcount

                # --- Purge du PDF source (la trace, elle, est conservée) ----
                cursor.execute(
                    f"UPDATE {args.pg_schema}.scans_recus "
                    "SET contenu = ''::bytea, taille_octets = 1 "
                    "WHERE statut IN ('TRAITE_AUTO', 'VALIDE', 'REJETE') "
                    "  AND traite_le < now() - make_interval(days => %s) "
                    "  AND length(contenu) > 1",
                    (args.retention_pdf_jours,))
                metrics["pdf_purges"] = cursor.rowcount

                cursor.execute(
                    f"UPDATE {args.pg_schema}.job_executions "
                    "SET statut = 'SUCCEEDED', finished_at = now(), metrics = %s::jsonb "
                    "WHERE id = %s",
                    (json_metrics(**metrics), execution_id),
                )
        except Exception as exc:
            logger.exception("Maintenance en échec")
            with connection.transaction(), connection.cursor() as cursor:
                cursor.execute(
                    f"INSERT INTO {args.pg_schema}.job_executions "
                    "(job_name, run_id, statut, started_at, finished_at, erreur) "
                    "VALUES ('maintenance', %s, 'FAILED', %s, now(), %s)",
                    (run_id, started_at, str(exc)[:4000]),
                )
            raise
    logger.info("Maintenance terminée : %s", json_metrics(**metrics))


if __name__ == "__main__":
    main()
