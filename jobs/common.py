"""Utilitaires communs aux tâches Lakeflow BLDEMAT."""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from typing import Iterator

import psycopg
from databricks.sdk import WorkspaceClient

logger = logging.getLogger("bl.jobs.common")


def configure_logging() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=logging.INFO,
        format='{"ts":"%(asctime)s","level":"%(levelname)s",'
        '"logger":"%(name)s","message":"%(message)s"}',
    )


def job_dbutils():
    """Retourne l'objet ``dbutils`` du job Databricks.

    Fonctionne dans une tâche notebook (``dbutils`` injecté dans le namespace
    interactif) comme dans une tâche script Python (via ``pyspark.dbutils``).
    Sert à lire les *job parameters* passés en Key/Value dans l'interface.
    """
    try:                                   # tâche notebook : dbutils déjà injecté
        import IPython

        shell = IPython.get_ipython()
        if shell is not None and "dbutils" in shell.user_ns:
            return shell.user_ns["dbutils"]
    except Exception:
        pass
    from pyspark.dbutils import DBUtils   # tâche script : reconstruit depuis Spark
    from pyspark.sql import SparkSession

    return DBUtils(SparkSession.builder.getOrCreate())


def lire_parametres(dbutils, specs) -> dict:
    """Déclare et lit des *job parameters* Databricks (widgets).

    ``specs`` : liste de tuples ``(nom, valeur_par_defaut)``. Chaque widget est
    déclaré (idempotent) puis lu ; la valeur saisie dans les Parameters du job
    remplace la valeur par défaut. Renvoie ``{nom: valeur}`` (chaînes nettoyées).
    """
    valeurs = {}
    for nom, defaut in specs:
        try:
            dbutils.widgets.text(nom, defaut)
        except Exception:                  # widget déjà déclaré : sans effet
            pass
        valeurs[nom] = (dbutils.widgets.get(nom) or "").strip()
    return valeurs


def entier_parametre(valeur: str, nom: str, minimum: int, maximum: int) -> int:
    try:
        entier = int(valeur)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Le paramètre {nom} doit être un entier.") from exc
    if not minimum <= entier <= maximum:
        raise ValueError(f"Le paramètre {nom} doit être compris entre {minimum} et {maximum}.")
    return entier


def endpoint_token(workspace: WorkspaceClient, endpoint: str) -> str:
    return workspace.postgres.generate_database_credential(endpoint=endpoint).token


def identite_connexion(workspace: WorkspaceClient) -> str:
    """Nom de rôle Postgres = identité pour laquelle le jeton OAuth est frappé.

    Lakebase refuse la connexion (« OAuth: User is not authorized ») si le
    ``user`` passé à psycopg n'est pas l'identité qui a généré le jeton. Cette
    identité est celle qui exécute le job (le *run-as*) : pour un utilisateur
    c'est son e-mail, pour un *service principal* son ID d'application. La lire
    ici garantit que ``user`` et ``password`` (le jeton) désignent la même
    personne, sans dépendre d'un paramètre saisi à la main.
    """
    identite = workspace.current_user.me().user_name
    logger.info("Identité de connexion Lakebase : %s", identite)
    return identite


def resoudre_endpoint(workspace: WorkspaceClient, host: str, endpoint: str = "") -> str:
    """Chemin de ressource de l'endpoint Lakebase
    (``projects/<id>/branches/<b>/endpoints/<ep>``).

    Si le paramètre ``lakebase_endpoint`` est renseigné, il est utilisé tel
    quel. Sinon, l'endpoint est retrouvé automatiquement en parcourant les
    projets/branches/endpoints du workspace et en comparant leur nom d'hôte
    au ``pg_host`` : plus besoin d'aller chercher ce chemin dans l'interface.
    """
    if endpoint:
        return endpoint
    if not host:
        raise ValueError("pg_host est requis pour retrouver l'endpoint Lakebase.")
    for projet in workspace.postgres.list_projects():
        for branche in workspace.postgres.list_branches(parent=projet.name):
            for ep in workspace.postgres.list_endpoints(parent=branche.name):
                hosts = getattr(ep.status, "hosts", None) if ep.status else None
                if hosts and hosts.host == host:
                    logger.info("Endpoint Lakebase résolu depuis pg_host : %s", ep.name)
                    return ep.name
    raise ValueError(
        f"Aucun endpoint Lakebase trouvé pour l'hôte « {host} ». "
        "Renseignez le paramètre lakebase_endpoint "
        "(projects/<id>/branches/<b>/endpoints/<ep>)."
    )


@contextmanager
def lakebase_connection(
    workspace: WorkspaceClient,
    *,
    endpoint: str,
    host: str,
    database: str,
    user: str,
) -> Iterator[psycopg.Connection]:
    connection = psycopg.connect(
        host=host,
        port=5432,
        dbname=database,
        user=user,
        password=endpoint_token(workspace, endpoint),
        sslmode="require",
        application_name="bldemat-jobs",
        connect_timeout=20,
    )
    try:
        yield connection
    finally:
        connection.close()


def json_metrics(**values) -> str:
    return json.dumps(values, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Cohérence de la copie de bl_core déposée à côté des scripts
# ---------------------------------------------------------------------------
# Les jobs lisent un `bl_core/` copié à la main dans l'espace de travail. Une
# mise à jour partielle — quelques fichiers remplacés, les autres restés d'une
# version antérieure — produit des `AttributeError` isolés, découverts un par
# un, très loin de leur cause. Ce contrôle transforme ce cas en UNE erreur
# explicite au démarrage, avant tout traitement.
SYMBOLES_REQUIS = {
    "decision": ("decider", "sens_du_destinataire", "plage_horaire_de"),
    "extraction": ("Referentiel", "extraire_infos_bl", "CHAMPS_ATTENDUS",
                   "rapprocher_tiers", "statut_est_nok", "endpoint_configure"),
    "graph_mail": ("BoiteMail", "ErreurGraph"),
    "notifications": ("notifier_nouvelle_reception",),
    "rapports": ("generer_rapport",),
    "repository": ("enregistrer_scan", "prendre_scans_a_analyser",
                   "cloturer_scan", "creer_bl_depuis_scan", "libelle_statut"),
    "scan_pdf": ("rasteriser", "compter_pages"),
}


def verifier_bl_core(*modules: str) -> None:
    """Échoue vite si la copie de bl_core est incomplète ou dépareillée.

    Appelé par chaque job juste après l'import de ses modules. Le message
    nomme le fichier fautif et le symbole manquant : c'est ce qui manquait
    quand `extraction.py` d'une version antérieure a fait échouer le
    traitement sur `Referentiel`."""
    import importlib

    manquants = []
    for nom in modules:
        attendus = SYMBOLES_REQUIS.get(nom)
        if not attendus:
            continue
        try:
            module = importlib.import_module(f"bl_core.{nom}")
        except Exception as exc:
            manquants.append(f"bl_core/{nom}.py : import impossible ({exc})")
            continue
        absents = [s for s in attendus if not hasattr(module, s)]
        if absents:
            manquants.append(f"bl_core/{nom}.py : {', '.join(absents)}")
    if manquants:
        raise RuntimeError(
            "La copie de bl_core de ce dossier est incomplète ou obsolète — "
            "des symboles attendus sont absents :\n  - "
            + "\n  - ".join(manquants)
            + "\nRedéployez le dossier bl_core/ ENTIER (les 18 fichiers de "
              "shared/bl_core/), et non quelques fichiers isolés : mélanger "
              "deux versions produit des erreurs très éloignées de leur cause."
        )
