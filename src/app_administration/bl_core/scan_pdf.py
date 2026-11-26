"""Lecture du PDF d'un scan et pré-analyse anticipée des scans à valider.

Un PDF est soit **un bordereau** (1 à 25 pages), soit **un lot** de plusieurs
bordereaux passés ensemble au copieur. Le découpage d'un lot est décidé par
`segmentation.py` ; ce module fournit les opérations sur le PDF lui-même.

Sans aucune dépendance à Streamlit :

* `rasteriser` — transforme le PDF en une image JPEG par page, calibrée pour
  l'affichage et pour le modèle de vision ;
* `extraire_pages` — fabrique le PDF d'un groupe de pages (un BL d'un lot) ;
* `est_blanche` — repère un verso vide sans interroger le modèle ;
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
               progression=None, max_pages: int | None = None) -> list[bytes]:
    """Une image JPEG par page du PDF, dans l'ordre du document.

    `dpi` 150 est le compromis retenu : suffisant pour qu'un modèle de vision
    lise un numéro de BL imprimé, assez léger pour un document de 25 pages.
    Les images sont ensuite bornées par `BL_MAX_DIMENSION_PX` et recompressées
    tant qu'elles dépassent `BL_MAX_IMAGE_BYTES`.

    `progression(index, total)` est appelé après chaque page (barre d'avancement).
    `max_pages` : plafond de pages ; par défaut celui d'un lot
    (`BL_SCAN_MAX_PAGES_LOT`), un scan pouvant contenir plusieurs bordereaux.
    """
    from PIL import Image

    parametres = get_settings()
    document = _ouvrir(pdf)
    try:
        total = document.page_count
        if total == 0:
            raise ValueError("Ce PDF ne contient aucune page.")
        limite = max_pages or parametres.scan_max_pages_lot
        if total > limite:
            raise ValueError(
                f"Ce PDF contient {total} pages ; la limite est de "
                f"{limite} pages par scan.")
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


def extraire_pages(pdf: bytes, pages: list[int]) -> bytes:
    """PDF réduit aux pages demandées (index à partir de 0), dans cet ordre.

    Sert à découper un lot : chaque bordereau devient un PDF autonome, que le
    reste du pipeline traite comme n'importe quel scan d'un seul BL."""
    if not pages:
        raise ValueError("Aucune page à extraire.")
    document = _ouvrir(pdf)
    try:
        total = document.page_count
        hors_bornes = [p for p in pages if p < 0 or p >= total]
        if hors_bornes:
            raise ValueError(
                f"Page(s) {[p + 1 for p in hors_bornes]} hors du PDF "
                f"({total} page(s)).")
        document.select(list(pages))
        return document.tobytes(garbage=3, deflate=True)
    finally:
        document.close()


def est_blanche(image: bytes) -> bool:
    """Vrai si la page est vide (verso d'un recto-verso, intercalaire).

    Le critère n'est pas la QUANTITÉ de pixels sombres mais leur
    REGROUPEMENT : la page est quadrillée en petites zones, et une seule zone
    contenant de l'encre groupée suffit à la dire écrite. Un mot isolé
    (« NOK », une signature, une réserve manuscrite) forme un amas ; des
    poussières de scanner restent des points épars. Un simple pourcentage de
    noir ne sait pas les distinguer — deux lettres pèsent moins que trente
    poussières.

    Les marges sont exclues (ombre de reliure, trace d'agrafe). En cas de
    doute ou d'erreur, la page est réputée NON blanche — le modèle tranchera :
    écarter à tort une page écrite serait pire que payer un appel de trop."""
    try:
        from PIL import Image

        vue = Image.open(io.BytesIO(image)).convert("L")
        vue.thumbnail((1000, 1000))
        largeur, hauteur = vue.size
        marge_x, marge_y = int(largeur * 0.06), int(hauteur * 0.06)
        coeur = vue.crop((marge_x, marge_y, largeur - marge_x, hauteur - marge_y))
        if coeur.size[0] < _ZONE or coeur.size[1] < _ZONE:
            return False
        # Masque des pixels sombres, puis moyenne par zone : chaque pixel de
        # la grille vaut 255 x (part de pixels sombres de sa zone).
        masque = coeur.point(lambda valeur: 255 if valeur < 128 else 0)
        grille = masque.resize((coeur.size[0] // _ZONE, coeur.size[1] // _ZONE),
                               Image.BOX)
        return max(grille.getdata()) < _ENCRE_MIN
    except Exception:
        return False


_ZONE = 40          # côté d'une zone, en pixels (page ramenée à 1000 px)
_ENCRE_MIN = 2      # ~12 pixels sombres dans une zone de 1600 : de l'encre


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
