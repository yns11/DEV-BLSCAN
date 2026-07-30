"""Configuration centralisée et validée de BLDEMAT.

Toute la configuration vient des variables d'environnement déclarées dans le
fichier `app.yaml` de chaque application : ni bundle, ni secret scope, ni
valeur en dur dans le code. Une configuration incohérente échoue au
démarrage, avant toute action métier.

Les variables PG* (PGHOST, PGDATABASE, PGUSER…) sont injectées
automatiquement par la ressource « Database » de l'app : elles ne figurent
donc pas dans app.yaml.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

_SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# « pre-prod » : iso-production (RBAC strict imposé), mais les traces
# techniques restent affichées pour la recette — voir `is_production`.
_ENVIRONMENTS = {"local", "dev", "rec", "pre-prod", "prod"}
_ENVIRONNEMENTS_SENSIBLES = {"pre-prod", "prod"}
_RBAC_MODES = {"strict", "disabled"}
# Comment les gestionnaires sont mentionnés dans les cartes Teams :
#   flow  : l'app pose le marqueur {{MENTIONS}} et envoie les e-mails ; le flux
#           Power Automate génère les jetons (action « Obtenir un jeton
#           @mention pour un utilisateur ») — seule méthode fiable ;
#   texte : noms en clair, aucune mention (repli si le flux est figé).
_MENTION_MODES = {"flow", "texte"}


def _int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} doit être un entier.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} doit être compris entre {minimum} et {maximum}.")
    return value


def _bool(name: str, default: bool) -> bool:
    valeur = os.environ.get(name, str(default)).strip().lower()
    if valeur in ("true", "1", "oui", "yes"):
        return True
    if valeur in ("false", "0", "non", "no"):
        return False
    raise RuntimeError(f"{name} doit valoir true ou false (reçu : {valeur!r}).")


def _csv(name: str) -> tuple[str, ...]:
    return tuple(
        value.strip().lower()
        for value in os.environ.get(name, "").split(",")
        if value.strip()
    )


@dataclass(frozen=True)
class Settings:
    environment: str
    pg_schema: str
    timezone: str
    page_size_default: int
    max_image_bytes: int
    max_total_bytes: int
    max_dimension_px: int
    max_pages: int
    scan_max_pages: int
    scan_pages_avance: int
    scan_auto_sans_desadv: bool
    scan_alias_achat: str
    scan_alias_vente: str
    scan_expediteurs_autorises: tuple[str, ...]
    scan_retention_pdf_jours: int
    scan_verrou_minutes: int
    rbac_mode: str
    bootstrap_admins: tuple[str, ...]
    database_pool_min: int
    database_pool_max: int
    database_pool_lifetime_s: int
    database_connect_timeout_s: int
    llm_endpoint: str
    llm_prompt_version: str
    teams_webhook_reception: str
    teams_webhook_edi: str
    teams_timeout_s: int
    teams_mention_mode: str

    @property
    def is_production(self) -> bool:
        """Masque les traces techniques à l'utilisateur. Volontairement FAUX en
        pre-prod : la recette a besoin du détail des erreurs."""
        return self.environment == "prod"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    environment = os.environ.get("BL_ENVIRONMENT", "local").strip().lower()
    if environment not in _ENVIRONMENTS:
        raise RuntimeError(
            f"BL_ENVIRONMENT invalide : {environment!r}. "
            f"Valeurs admises : {sorted(_ENVIRONMENTS)}."
        )

    schema = os.environ.get("BL_PG_SCHEMA", "bl_scan").strip()
    if not _SCHEMA_RE.fullmatch(schema):
        raise RuntimeError("BL_PG_SCHEMA doit être un identifiant PostgreSQL simple.")

    rbac_mode = os.environ.get("BL_RBAC_MODE", "strict").strip().lower()
    if rbac_mode not in _RBAC_MODES:
        raise RuntimeError(f"BL_RBAC_MODE doit être dans {sorted(_RBAC_MODES)}.")
    if environment in _ENVIRONNEMENTS_SENSIBLES and rbac_mode != "strict":
        raise RuntimeError(
            f"Le RBAC ne peut pas être désactivé en « {environment} » "
            "(environnement iso-production).")

    max_image_bytes = _int("BL_MAX_IMAGE_BYTES", 4 * 1024 * 1024, 256_000, 20 * 1024 * 1024)
    max_pages = _int("BL_MAX_PAGES", 20, 1, 100)
    max_total_bytes = _int(
        "BL_MAX_TOTAL_BYTES",
        min(50 * 1024 * 1024, max_image_bytes * max_pages),
        max_image_bytes,
        250 * 1024 * 1024,
    )

    # --- Pipeline de scan -------------------------------------------------
    # Un scan = UN bordereau, de 1 à 25 pages (5 en moyenne). Toutes ses pages
    # sont analysées ENSEMBLE, comme le fait la saisie unitaire d'un BL
    # multi-pages : c'est un seul document.
    scan_max_pages = _int("BL_SCAN_MAX_PAGES", 25, 1, 100)
    # Scans pré-analysés en arrière-plan dans l'écran de validation, pendant
    # que le gestionnaire traite le scan courant.
    scan_pages_avance = _int("BL_SCAN_PAGES_AVANCE", 1, 0, 4)
    # Création automatique quand le code tiers est reconnu mais qu'AUCUN avis
    # d'expédition ne confirme le numéro. Faux par défaut : le recoupement avec
    # l'ERP est le seul signal vraiment fort (voir bl_core/decision.py).
    scan_auto_sans_desadv = _bool("BL_SCAN_AUTO_SANS_DESADV", False)
    # Fragments d'adresse qui déterminent le sens du scan à partir du
    # destinataire du mail (un alias par flux).
    scan_alias_achat = os.environ.get("BL_SCAN_ALIAS_ACHAT", "reception").strip().lower()
    scan_alias_vente = os.environ.get("BL_SCAN_ALIAS_VENTE", "expedition").strip().lower()
    if not scan_alias_achat or not scan_alias_vente:
        raise RuntimeError("BL_SCAN_ALIAS_ACHAT et BL_SCAN_ALIAS_VENTE sont requis.")
    if scan_alias_achat == scan_alias_vente:
        raise RuntimeError(
            "BL_SCAN_ALIAS_ACHAT et BL_SCAN_ALIAS_VENTE doivent différer, "
            "sinon le sens du scan est indéterminable.")
    # Jours de conservation du PDF source après traitement (la ligne de trace
    # et les images du BL sont conservées indéfiniment).
    scan_retention = _int("BL_SCAN_RETENTION_PDF_JOURS", 180, 7, 3650)
    # Durée du verrou de prise en charge d'un scan à valider.
    scan_verrou = _int("BL_SCAN_VERROU_MINUTES", 60, 5, 480)

    # Notifications Teams : une URL de flux « Workflows » par type d'alerte
    # (la même URL peut servir aux deux). Vide = notification désactivée.
    webhook_reception = os.environ.get("BL_TEAMS_WEBHOOK_RECEPTION", "").strip()
    webhook_edi = os.environ.get("BL_TEAMS_WEBHOOK_EDI", "").strip()
    for nom, url in (("BL_TEAMS_WEBHOOK_RECEPTION", webhook_reception),
                     ("BL_TEAMS_WEBHOOK_EDI", webhook_edi)):
        if url and not url.startswith("https://"):
            raise RuntimeError(f"{nom} doit être une URL https.")

    mention_mode = os.environ.get("BL_TEAMS_MENTION_MODE", "flow").strip().lower()
    if mention_mode not in _MENTION_MODES:
        raise RuntimeError(
            f"BL_TEAMS_MENTION_MODE doit être dans {sorted(_MENTION_MODES)}.")

    return Settings(
        environment=environment,
        pg_schema=schema,
        timezone=os.environ.get("BL_TIMEZONE", "Europe/Paris"),
        page_size_default=_int("BL_PAGE_SIZE", 50, 10, 500),
        max_image_bytes=max_image_bytes,
        max_total_bytes=max_total_bytes,
        max_dimension_px=_int("BL_MAX_DIMENSION_PX", 3508, 1024, 10000),
        max_pages=max_pages,
        scan_max_pages=scan_max_pages,
        scan_pages_avance=scan_pages_avance,
        scan_auto_sans_desadv=scan_auto_sans_desadv,
        scan_alias_achat=scan_alias_achat,
        scan_alias_vente=scan_alias_vente,
        scan_expediteurs_autorises=_csv("BL_SCAN_EXPEDITEURS"),
        scan_retention_pdf_jours=scan_retention,
        scan_verrou_minutes=scan_verrou,
        rbac_mode=rbac_mode,
        bootstrap_admins=_csv("BL_BOOTSTRAP_ADMINS"),
        database_pool_min=_int("BL_DB_POOL_MIN", 1, 1, 10),
        database_pool_max=_int("BL_DB_POOL_MAX", 8, 1, 50),
        database_pool_lifetime_s=_int("BL_DB_POOL_LIFETIME_S", 2400, 300, 3300),
        database_connect_timeout_s=_int("BL_DB_CONNECT_TIMEOUT_S", 8, 2, 30),
        llm_endpoint=os.environ.get("BL_LLM_ENDPOINT", "").strip(),
        llm_prompt_version=os.environ.get("BL_LLM_PROMPT_VERSION", "2026-07-01").strip(),
        teams_webhook_reception=webhook_reception,
        teams_webhook_edi=webhook_edi,
        teams_timeout_s=_int("BL_TEAMS_TIMEOUT_S", 8, 2, 30),
        teams_mention_mode=mention_mode,
    )


def reset_settings_cache() -> None:
    """Réservé aux tests et aux outils de validation."""
    get_settings.cache_clear()
