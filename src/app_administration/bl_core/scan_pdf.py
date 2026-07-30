"""Lecture du PDF d'un scan et pré-analyse anticipée des scans à valider.

Ici, **un PDF = UN bordereau**, de 1 à 25 pages : le réceptionniste scanne un
BL à la fois. Toutes les pages d'un même scan sont donc analysées ENSEMBLE par
le modèle, exactement comme un BL photographié en plusieurs prises dans les
versions précédentes.

Deux responsabilités, sans aucune dépendance à Streamlit :

* `rasteriser` — transforme le PDF en une image JPEG par page, calibrée pour
  l'affichage et pour le modèle de vision ;
* `Prechargeur` — dans l'écran de validation, analyse **les scans suivants en
  avance** pendant que le gestionnaire traite le scan courant, de sorte que le
  passage au suivant n'attende pratiquement jamais le modèle.

Le pré-chargement s'appuie sur un `ThreadPoolExecutor` conservé dans l'état de
session. Les threads ne touchent JAMAIS `st.session_state` : ils se contentent
de remplir les `Future` détenus par le préchargeur, que le thread principal
consomme au moment voulu.
"""

from __future__ import annotations

import io
import logging
from concurrent.futures import Future, ThreadPoolExecutor

from . import extraction
from .config import get_settings

logger = logging.getLogger("bl.lot")


# ---------------------------------------------------------------------------
# PDF -> images
# ---------------------------------------------------------------------------
def _ouvrir(pdf: bytes):
    try:
        import pymupdf
    except ImportError as exc:                    # dépendance non installée
        raise RuntimeError(
            "La lecture des PDF nécessite le paquet « pymupdf » "
            "(voir requirements.txt de l'app Création)."
        ) from exc
    try:
        return pymupdf.open(stream=pdf, filetype="pdf")
    except Exception as exc:
        raise ValueError(f"Fichier PDF illisible ou endommagé : {exc}") from exc


def compter_pages(pdf: bytes) -> int:
    document = _ouvrir(pdf)
    try:
        return document.page_count
    finally:
        document.close()


def rasteriser(pdf: bytes, dpi: int = 150, qualite: int = 72,
               progression=None) -> list[bytes]:
    """Une image JPEG par page du PDF, dans l'ordre du document.

    `dpi` 150 est le compromis retenu : suffisant pour qu'un modèle de vision
    lise un numéro de BL imprimé, assez léger pour un document de 25 pages.
    Les images sont ensuite bornées par `BL_MAX_DIMENSION_PX` et recompressées
    tant qu'elles dépassent `BL_MAX_IMAGE_BYTES`.

    `progression(index, total)` est appelé après chaque page (barre d'avancement).
    """
    from PIL import Image

    parametres = get_settings()
    document = _ouvrir(pdf)
    try:
        total = document.page_count
        if total == 0:
            raise ValueError("Ce PDF ne contient aucune page.")
        if total > parametres.scan_max_pages:
            raise ValueError(
                f"Ce PDF contient {total} pages ; la limite est de "
                f"{parametres.scan_max_pages} pages pour un bordereau. "
                "Un scan doit contenir UN seul BL.")
        pages = []
        for index in range(total):
            pixmap = document[index].get_pixmap(dpi=dpi)
            image = Image.frombytes("RGB", (pixmap.width, pixmap.height),
                                    pixmap.samples)
            image.thumbnail((parametres.max_dimension_px,
                             parametres.max_dimension_px))
            pages.append(_compresser(image, qualite, parametres.max_image_bytes))
            if progression:
                progression(index + 1, total)
        logger.info("PDF rasterisé : %d page(s), %.1f Mo",
                    len(pages), sum(map(len, pages)) / 1024 / 1024)
        return pages
    finally:
        document.close()


def _compresser(image, qualite: int, taille_max: int) -> bytes:
    """JPEG sous la taille maximale, en abaissant la qualité par paliers."""
    for essai in (qualite, 60, 45, 32):
        tampon = io.BytesIO()
        image.save(tampon, format="JPEG", quality=essai, optimize=True)
        donnees = tampon.getvalue()
        if len(donnees) <= taille_max:
            return donnees
    return donnees                                 # dernier essai, au plus léger


# ---------------------------------------------------------------------------
# Pré-analyse anticipée
# ---------------------------------------------------------------------------
class Prechargeur:
    """Analyse les scans **en avance** sur le gestionnaire qui les valide.

    Un scan est un document : toutes ses pages partent au modèle en un appel,
    via `extraction.extraire_infos_bl` — la procédure complète, avec ses
    passes de raffinement sur le référentiel (liste des tiers si le tiers
    n'est pas reconnu, puis numéros de BL connus du tiers reconnu).

    Sert l'écran de validation : pendant que le gestionnaire traite le scan N,
    les scans suivants sont déjà analysés. En régime normal les scans arrivent
    déjà analysés par le job ; ce préchargeur couvre les cas où l'analyse doit
    être refaite (correction du référentiel, relance manuelle).

    Utilisation ::

        p = Prechargeur(scans, "fournisseur", referentiel)
        p.amorcer(0)
        infos = p.resultat(0)

    Aucune exception n'est propagée : un scan dont l'analyse a échoué revient
    avec des champs vides et le motif dans `erreurs`, le gestionnaire saisit
    alors manuellement."""

    def __init__(self, scans: list[list[bytes]], tiers_libelle: str,
                 referentiel=None, scans_d_avance: int | None = None) -> None:
        parametres = get_settings()
        self.scans = scans                 # une liste de pages par scan
        self.tiers_libelle = tiers_libelle
        self.referentiel = referentiel
        self.scans_d_avance = (parametres.scan_pages_avance
                               if scans_d_avance is None else max(0, scans_d_avance))
        self.erreurs: dict[int, str] = {}
        self._resultats: dict[int, dict] = {}
        self._futures: dict[int, Future] = {}
        # 2 threads : au-delà, on sature l'endpoint sans gagner en fluidité,
        # le gestionnaire ne validant qu'un scan à la fois.
        self._executeur = ThreadPoolExecutor(max_workers=2,
                                             thread_name_prefix="bl-scan")
        self.actif = bool(extraction.endpoint_configure())

    def _analyser(self, index: int) -> dict:
        return extraction.extraire_infos_bl(
            self.scans[index], self.tiers_libelle, referentiel=self.referentiel)

    def amorcer(self, index: int) -> None:
        """Soumet le scan `index` et ceux d'avance, s'ils ne le sont pas déjà."""
        if not self.actif:
            return
        for scan in range(index, min(index + self.scans_d_avance + 1,
                                     len(self.scans))):
            if scan not in self._futures:
                self._futures[scan] = self._executeur.submit(self._analyser, scan)

    def en_cours(self, index: int) -> bool:
        """Vrai si l'analyse de ce scan n'est pas encore disponible."""
        if not self.actif or index in self._resultats:
            return False
        future = self._futures.get(index)
        return future is not None and not future.done()

    def resultat(self, index: int, delai: float = 180.0) -> dict:
        """Champs détectés pour le scan `index` ; attend son analyse au besoin."""
        if index in self._resultats:
            return self._resultats[index]
        if not self.actif or index >= len(self.scans):
            return {}
        self.amorcer(index)
        try:
            self._resultats[index] = self._futures[index].result(timeout=delai)
        except Exception as exc:
            logger.warning("Analyse du scan %d en échec : %s", index, exc,
                           exc_info=True)
            self._resultats[index] = {}
            self.erreurs[index] = f"{type(exc).__name__} : {exc}"
        return self._resultats[index]

    def avancement(self) -> tuple[int, int]:
        """(scans analysés, scans totaux) — pour informer l'utilisateur."""
        return len(self._resultats), len(self.scans)

    def fermer(self) -> None:
        """Libère les threads. Idempotent."""
        self._executeur.shutdown(wait=False, cancel_futures=True)
        self._futures.clear()


# ---------------------------------------------------------------------------
# Contrôles avant création
# ---------------------------------------------------------------------------
def controler_bl(numero: str, fournisseur: str, numero_disponible) -> str:
    """Anomalie bloquante d'un BL prêt à être créé, ou chaîne vide.

    `numero_disponible(numero)` est injecté pour rester testable sans base.
    En cas d'indisponibilité de la base, on renvoie une anomalie plutôt que de
    laisser passer : mieux vaut refuser que créer un doublon."""
    numero = (numero or "").strip()
    if not numero:
        return "Numéro de BL manquant"
    if not (fournisseur or "").strip():
        return "Tiers non renseigné"
    try:
        if not numero_disponible(numero):
            return f"Le numéro « {numero} » est déjà enregistré en base"
    except Exception as exc:
        return f"Vérification d'unicité impossible ({type(exc).__name__})"
    return ""
