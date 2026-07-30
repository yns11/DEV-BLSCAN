"""Relève de la boîte de scan : mails du copieur → file d'attente Lakebase.

Le multifonction envoie **un PDF par bordereau** à une adresse dédiée. Ce job
relève la boîte via Microsoft Graph, contrôle chaque pièce jointe et la dépose
dans `scans_recus`. Il ne fait AUCUNE analyse : c'est `traitement_scans.py`
qui appelle le modèle, ce qui permet de rejouer l'un sans l'autre.

Planification conseillée : **toutes les 10 minutes**. Avec 10 à 30 scans par
jour et une tolérance de 20 à 30 minutes entre le scan et le traitement, c'est
largement suffisant — et 6 relèves par heure restent négligeables côté Graph.

Idempotence
-----------
`scans_recus.message_id` est UNIQUE et porte l'`Internet-Message-Id` du mail.
Un mail livré deux fois, une relève interrompue avant le rangement du message,
ou un job relancé ne produisent qu'une seule ligne. Le mail n'est marqué lu et
rangé qu'**après** l'insertion réussie : en cas de plantage entre les deux, la
relève suivante le retrouve et l'idempotence absorbe le doublon.

Sécurité
--------
* liste blanche d'expéditeurs (`BL_SCAN_EXPEDITEURS`) — vide = tout expéditeur
  interne accepté, à réserver aux essais ;
* vérification des **octets d'en-tête** du fichier (`%PDF-`), pas de son
  extension ;
* plafonds de taille et de nombre de pages ;
* le secret du service principal vient d'un **secret scope Databricks**.
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import types

from common import (
    configure_logging,
    identite_connexion,
    job_dbutils,
    json_metrics,
    lire_parametres,
    resoudre_endpoint,
)
from databricks.sdk import WorkspaceClient

logger = logging.getLogger("bl.jobs.ingestion")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

PARAMETRES = [
    ("pg_host", ""),
    ("pg_database", "databricks_postgres"),
    ("pg_schema", "bl_scan"),
    ("lakebase_endpoint", ""),
    ("pg_user", ""),
    # --- Microsoft Graph ---
    ("graph_tenant_id", ""),
    ("graph_client_id", ""),
    ("secret_scope", "bldemat_scan"),
    ("secret_cle_client", "graph_client_secret"),
    ("boite_scan", ""),                  # adresse SMTP de la boîte relevée
    ("dossier_traites", "Traites"),
    ("dossier_rejetes", "Rejetes"),
    # --- Règles ---
    ("alias_achat", "reception"),
    ("alias_vente", "expedition"),
    ("expediteurs_autorises", ""),       # CSV ; vide = pas de filtre
    ("max_messages", "50"),
    ("max_octets_pdf", "41943040"),      # 40 Mo
    ("max_pages", "25"),
]


def parametres(dbutils):
    valeurs = lire_parametres(dbutils, PARAMETRES)
    if not IDENTIFIER.fullmatch(valeurs["pg_schema"]):
        raise ValueError(f"pg_schema invalide ({valeurs['pg_schema']!r}).")
    for nom in ("pg_host", "graph_tenant_id", "graph_client_id", "boite_scan"):
        if not valeurs[nom]:
            raise ValueError(f"Le paramètre {nom} est obligatoire.")
    return types.SimpleNamespace(
        **{cle: valeurs[cle] for cle, _ in PARAMETRES},
        expediteurs=tuple(a.strip().lower()
                          for a in valeurs["expediteurs_autorises"].split(",")
                          if a.strip()),
    )


def _secret(dbutils, scope: str, cle: str) -> str:
    """Secret du service principal Entra ID. Message explicite si le scope ou
    la clé manque : c'est l'erreur de déploiement la plus fréquente."""
    try:
        return dbutils.secrets.get(scope=scope, key=cle)
    except Exception as exc:
        raise RuntimeError(
            f"Secret « {cle} » introuvable dans le scope « {scope} ». "
            "Créez-le avec : databricks secrets create-scope "
            f"{scope} && databricks secrets put-secret {scope} {cle}"
        ) from exc


def preparer_environnement(args, workspace: WorkspaceClient) -> None:
    """Variables lues par bl_core (connexion Lakebase et règles du pipeline)."""
    endpoint = resoudre_endpoint(workspace, args.pg_host, args.lakebase_endpoint)
    os.environ.update({
        "LAKEBASE_ENDPOINT": endpoint,
        "PGHOST": args.pg_host,
        "PGPORT": "5432",
        "PGDATABASE": args.pg_database,
        "PGUSER": args.pg_user or identite_connexion(workspace),
        "PGSSLMODE": "require",
        "PGAPPNAME": "bldemat-ingestion",
        "BL_PG_SCHEMA": args.pg_schema,
        "BL_ENVIRONMENT": "prod",
        "BL_RBAC_MODE": "strict",
        "BL_SCAN_ALIAS_ACHAT": args.alias_achat,
        "BL_SCAN_ALIAS_VENTE": args.alias_vente,
        "BL_SCAN_MAX_PAGES": args.max_pages,
        # Ce job n'appelle jamais le modèle : l'analyse est faite par
        # traitement_scans.py.
        "BL_LLM_ENDPOINT": "",
    })


def main() -> None:
    configure_logging()
    dbutils = job_dbutils()
    args = parametres(dbutils)
    secret = _secret(dbutils, args.secret_scope, args.secret_cle_client)
    workspace = WorkspaceClient()
    preparer_environnement(args, workspace)

    # Import APRÈS la préparation des variables : bl_core lit et met en cache
    # sa configuration au premier accès.
    from bl_core import decision, graph_mail, repository, scan_pdf

    boite = graph_mail.BoiteMail(
        tenant_id=args.graph_tenant_id, client_id=args.graph_client_id,
        client_secret=secret, boite=args.boite_scan)

    max_octets = int(args.max_octets_pdf)
    max_pages = int(args.max_pages)
    metriques = {"messages": 0, "scans": 0, "doublons": 0, "rejetes": 0,
                 "sans_piece_jointe": 0}
    motifs_rejet: list[str] = []

    for message in boite.messages_non_lus(limite=int(args.max_messages)):
        metriques["messages"] += 1
        rejets = []

        # --- Contrôle de l'expéditeur ------------------------------------
        if args.expediteurs and message.expediteur not in args.expediteurs:
            rejets.append(f"expéditeur non autorisé ({message.expediteur})")

        # --- Sens du flux -------------------------------------------------
        sens = decision.sens_du_destinataire(
            message.destinataires, args.alias_achat, args.alias_vente)
        if sens is None:
            rejets.append(
                f"aucun alias reconnu dans {message.destinataires} "
                f"(attendus : « {args.alias_achat} » / « {args.alias_vente} »)")

        if rejets:
            metriques["rejetes"] += 1
            motifs_rejet.append(f"{message.objet or message.id} : "
                                + " ; ".join(rejets))
            logger.warning("Message rejeté — %s", " ; ".join(rejets))
            boite.marquer_lu(message.id)
            boite.deplacer(message.id, args.dossier_rejetes)
            continue

        # --- Pièces jointes PDF -------------------------------------------
        pieces = boite.pieces_jointes(message.id, extensions=(".pdf",),
                                      taille_max=max_octets)
        if not pieces:
            metriques["sans_piece_jointe"] += 1
            logger.warning("Message sans PDF exploitable : %s", message.objet)
            boite.marquer_lu(message.id)
            boite.deplacer(message.id, args.dossier_rejetes)
            continue

        recu_le = _horodatage(message.recu_le)
        insere = 0
        for index, piece in enumerate(pieces):
            # Un mail peut porter plusieurs PDF (copieur configuré en
            # « un fichier par page », ou envoi groupé) : chacun devient un
            # scan distinct, avec une clé d'idempotence dérivée.
            cle = (message.internet_message_id if len(pieces) == 1
                   else f"{message.internet_message_id}#{index}")

            if not piece.contenu.startswith(b"%PDF-"):
                metriques["rejetes"] += 1
                motifs_rejet.append(f"{piece.nom} : ce n'est pas un PDF "
                                    "(octets d'en-tête invalides)")
                continue
            try:
                nb_pages = scan_pdf.compter_pages(piece.contenu)
            except Exception as exc:
                metriques["rejetes"] += 1
                motifs_rejet.append(f"{piece.nom} : PDF illisible ({exc})")
                continue
            if nb_pages > max_pages:
                metriques["rejetes"] += 1
                motifs_rejet.append(
                    f"{piece.nom} : {nb_pages} pages pour un seul BL "
                    f"(limite {max_pages}) — le copieur a-t-il scanné "
                    "plusieurs bordereaux d'un coup ?")
                continue

            scan_id = repository.enregistrer_scan(
                message_id=cle, source="MAIL", expediteur=message.expediteur,
                destinataire=", ".join(message.destinataires),
                objet=message.objet, nom_fichier=piece.nom,
                contenu=piece.contenu, nb_pages=nb_pages, sens=sens,
                recu_le=recu_le)
            if scan_id is None:
                metriques["doublons"] += 1
                logger.info("Scan déjà ingéré (idempotence) : %s", cle)
            else:
                metriques["scans"] += 1
                insere += 1
                logger.info("Scan %d ingéré : %s, %d page(s), sens %s",
                            scan_id, piece.nom, nb_pages, sens)

        # Rangement APRÈS insertion : si le job meurt avant, la relève
        # suivante retrouve le message et l'idempotence évite le doublon.
        boite.marquer_lu(message.id)
        boite.deplacer(message.id,
                       args.dossier_traites if insere else args.dossier_rejetes)

    logger.info("Relève terminée : %s", json_metrics(**metriques))
    if motifs_rejet:
        # Les rejets sont journalisés mais ne font PAS échouer la tâche : un
        # scan mal formé ne doit pas masquer une relève par ailleurs réussie.
        logger.warning("Scans écartés (%d) : %s", len(motifs_rejet),
                       " | ".join(motifs_rejet[:20]))


def _horodatage(iso: str) -> datetime.datetime:
    """Horodatage Graph (ISO 8601 UTC) vers datetime conscient du fuseau."""
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc)


if __name__ == "__main__":
    main()
