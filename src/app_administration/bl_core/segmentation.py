"""Découpage d'un scan en bordereaux : un lot de feuilles, plusieurs BL.

Le réceptionniste peut passer au copieur une pile entière : 3 pages pour un
premier BL, 2 pages et une lettre de voiture pour le deuxième, 5 pages pour le
troisième. Le scan devient alors un **lot**, que ce module découpe en groupes
de pages — un groupe par bordereau. Chaque groupe suit ensuite le circuit
habituel (extraction, règle de décision), inchangé.

Méthode
-------
1. Chaque page est lue **seule** et résumée en une `Fiche` : type de document,
   numéro de BL visible, pagination « 2/3 », début de document ou non. Le
   modèle ne voit jamais deux bordereaux à la fois : c'est ce qui dégradait la
   lecture quand plusieurs BL partaient dans le même appel.
2. `decouper` compare les fiches voisines et pose les coupes. C'est un
   programme classique, sans IA, sans accès base ni réseau — comme
   `decision.py` — donc testable cas par cas.

Principe directeur
------------------
**Une page rangée dans le mauvais BL ne se voit pas.** Une erreur de lecture
se remarque (le numéro ne correspond à rien) ; une erreur de découpage produit
deux bordereaux plausibles et faux. Tout doute est donc consigné comme une
*incertitude*, et un lot qui en porte une n'est jamais découpé sans qu'un
gestionnaire confirme.

Règles métier arrêtées
----------------------
* pas de feuille séparatrice : la coupe repose sur le contenu des pages ;
* une annexe (lettre de voiture, liste de colisage, certificat…) est
  rattachée au **BL qui la précède** ; elle ne crée jamais un bordereau ;
* une page blanche (verso d'un recto-verso) est écartée sans créer de coupe.
"""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

logger = logging.getLogger("bl.segmentation")

TYPE_BL = "bl"
TYPE_ANNEXE = "annexe"
TYPE_BLANCHE = "blanche"
TYPE_ILLISIBLE = "illisible"

# Types renvoyés par le modèle -> libellé affiché. Tout ce qui n'est pas un
# bordereau est une annexe : mieux vaut rattacher une page à un BL que créer
# un bordereau fantôme à partir d'une lettre de voiture.
NATURES_ANNEXE = {
    "lettre_voiture": "Lettre de voiture",
    "colisage": "Liste de colisage",
    "certificat": "Certificat",
    "facture": "Facture",
    "conditions": "Conditions générales",
    "autre": "Autre document",
}


# ---------------------------------------------------------------------------
# Fiche d'identité d'une page
# ---------------------------------------------------------------------------
@dataclass
class Fiche:
    """Ce qu'on sait d'UNE page, lue isolément."""
    index: int                 # position dans le lot, à partir de 0
    type: str = TYPE_BL        # bl | annexe | blanche | illisible
    nature: str = ""           # libellé de l'annexe (« Lettre de voiture »…)
    numero: str = ""           # numéro de BL lu, tel qu'écrit
    tiers: str = ""
    page_no: int = 0           # « 2/3 » -> 2 ; 0 si absent
    page_total: int = 0        # « 2/3 » -> 3 ; 0 si absent
    debut: bool = False        # la page ressemble à une première page

    def vers_json(self) -> dict:
        return {"index": self.index, "type": self.type, "nature": self.nature,
                "numero": self.numero, "tiers": self.tiers,
                "page_no": self.page_no, "page_total": self.page_total,
                "debut": self.debut}

    def libelle(self) -> str:
        """Résumé court, pour la vignette de l'écran de découpage."""
        if self.type == TYPE_BLANCHE:
            return "Page blanche"
        if self.type == TYPE_ILLISIBLE:
            return "Page non analysée"
        if self.type == TYPE_ANNEXE:
            return self.nature or "Annexe"
        morceaux = [f"BL {self.numero}" if self.numero else "BL sans numéro lisible"]
        if self.page_no:
            morceaux.append(f"{self.page_no}/{self.page_total}"
                            if self.page_total else f"p. {self.page_no}")
        return " · ".join(morceaux)


_PAGINATION = re.compile(r"(\d{1,3})\s*(?:/|sur|de|of)\s*(\d{1,3})", re.I)
_VRAI = {"true", "vrai", "oui", "yes", "1"}


def _pagination(valeur) -> tuple[int, int]:
    """« 2/3 », « Page 2 sur 3 » -> (2, 3). (0, 0) si rien d'exploitable."""
    trouve = _PAGINATION.search(str(valeur or ""))
    if not trouve:
        return 0, 0
    numero, total = int(trouve.group(1)), int(trouve.group(2))
    if numero < 1 or total < 1 or numero > total:
        return 0, 0                      # « 7/3 » : lecture fausse, on l'ignore
    return numero, total


def fiche_depuis_modele(index: int, brut: Optional[dict]) -> Fiche:
    """Normalise la réponse du modèle pour une page. Ne lève jamais : une
    réponse inexploitable donne une fiche « illisible », traitée comme une
    incertitude par le découpage."""
    if not isinstance(brut, dict):
        return Fiche(index=index, type=TYPE_ILLISIBLE)
    genre = str(brut.get("type") or "").strip().lower().replace(" ", "_")
    if genre == TYPE_BLANCHE:
        return Fiche(index=index, type=TYPE_BLANCHE)
    numero = " ".join(str(brut.get("numero_bl") or "").split())
    tiers = " ".join(str(brut.get("tiers") or "").split())
    page_no, page_total = _pagination(brut.get("page"))
    debut = brut.get("debut")
    debut = debut is True or str(debut).strip().lower() in _VRAI
    if genre in NATURES_ANNEXE:
        return Fiche(index=index, type=TYPE_ANNEXE, nature=NATURES_ANNEXE[genre],
                     tiers=tiers)
    if genre not in ("bl", "bordereau", "bon_de_livraison"):
        # Type inconnu : un numéro de BL lisible suffit à y voir un bordereau,
        # sinon c'est une annexe « autre ».
        if not numero:
            return Fiche(index=index, type=TYPE_ANNEXE,
                         nature=NATURES_ANNEXE["autre"], tiers=tiers)
    return Fiche(index=index, type=TYPE_BL, numero=numero, tiers=tiers,
                 page_no=page_no, page_total=page_total, debut=debut)


def fiches_depuis_json(donnees: Optional[list], nb_pages: int) -> list[Fiche]:
    """Relit les fiches stockées avec le lot. Complète par des fiches
    « illisibles » si le stockage est absent ou partiel : l'écran de
    découpage reste utilisable, l'humain décide alors seul."""
    par_index = {}
    for brut in donnees or []:
        try:
            fiche = Fiche(**{cle: brut[cle] for cle in (
                "index", "type", "nature", "numero", "tiers", "page_no",
                "page_total", "debut") if cle in brut})
        except Exception:
            continue
        par_index[int(fiche.index)] = fiche
    return [par_index.get(i) or Fiche(index=i, type=TYPE_ILLISIBLE)
            for i in range(nb_pages)]


def ficher_lot(pages: list[bytes], lire_page: Callable[[bytes], dict],
               est_blanche: Callable[[bytes], bool] = lambda page: False,
               travailleurs: int = 4) -> list[Fiche]:
    """Une fiche par page du lot.

    `lire_page(image)` interroge le modèle (injecté : ce module ne connaît ni
    l'endpoint ni le réseau). `est_blanche(image)` évite un appel pour les
    versos vides. Les pages sont lues en parallèle, chacune dans son propre
    appel. Une page dont la lecture échoue donne une fiche « illisible » —
    jamais une exception : un incident sur une page ne doit pas faire perdre
    le lot."""
    fiches: list[Optional[Fiche]] = [None] * len(pages)
    a_lire = []
    for index, page in enumerate(pages):
        try:
            blanche = bool(est_blanche(page))
        except Exception:
            blanche = False
        if blanche:
            fiches[index] = Fiche(index=index, type=TYPE_BLANCHE)
        else:
            a_lire.append(index)

    def _lire(index: int) -> Fiche:
        try:
            return fiche_depuis_modele(index, lire_page(pages[index]))
        except Exception as exc:
            logger.warning("Lecture de la page %d impossible : %s", index + 1, exc)
            return Fiche(index=index, type=TYPE_ILLISIBLE)

    if a_lire:
        with ThreadPoolExecutor(max_workers=max(1, min(travailleurs, len(a_lire))),
                                thread_name_prefix="bl-fiche") as executeur:
            for fiche in executeur.map(_lire, a_lire):
                fiches[fiche.index] = fiche
    return [f for f in fiches if f is not None]


# ---------------------------------------------------------------------------
# Découpage
# ---------------------------------------------------------------------------
@dataclass
class Segment:
    """Un groupe de pages = un bordereau (annexes comprises)."""
    pages: list[int] = field(default_factory=list)      # index dans le lot
    annexes: list[int] = field(default_factory=list)    # sous-ensemble de pages
    numero: str = ""
    tiers: str = ""
    avertissements: list[str] = field(default_factory=list)

    @property
    def page_debut(self) -> int:         # numérotation humaine, à partir de 1
        return self.pages[0] + 1

    @property
    def page_fin(self) -> int:
        return self.pages[-1] + 1

    def etendue(self) -> str:
        return (f"page {self.page_debut}" if len(self.pages) == 1
                else f"pages {self.page_debut} à {self.page_fin}")


@dataclass
class Decoupage:
    segments: list[Segment] = field(default_factory=list)
    ecartees: list[int] = field(default_factory=list)   # pages blanches
    incertitudes: list[str] = field(default_factory=list)

    @property
    def sur(self) -> bool:
        """Aucun doute : le lot peut être découpé sans intervention."""
        return not self.incertitudes

    @property
    def multi(self) -> bool:
        return len(self.segments) > 1

    @property
    def debuts(self) -> list[int]:
        """Index de la première page de chaque groupe (les coupes)."""
        return [segment.pages[0] for segment in self.segments]

    def vers_json(self, fiches: Iterable[Fiche]) -> dict:
        """Trace stockée avec le lot : de quoi rouvrir l'écran de découpage
        sur la proposition du pipeline, sans rappeler le modèle."""
        return {
            "fiches": [fiche.vers_json() for fiche in fiches],
            "debuts": self.debuts,
            "ecartees": list(self.ecartees),
            "incertitudes": list(self.incertitudes),
            "groupes": [{"pages": s.pages, "annexes": s.annexes,
                         "numero": s.numero, "tiers": s.tiers,
                         "avertissements": s.avertissements}
                        for s in self.segments],
        }


def cle_numero(numero: str) -> str:
    """Forme de comparaison d'un numéro de BL : lettres et chiffres seuls."""
    return re.sub(r"[^A-Z0-9]", "", (numero or "").upper())


def _proches(a: str, b: str) -> bool:
    """Deux numéros de même longueur qui ne diffèrent que d'un caractère.

    Cas piégeux : c'est aussi bien une erreur de lecture (« 0 » lu « O ») que
    deux BL consécutifs du même fournisseur (…398 puis …399). Le numéro seul
    ne permet pas de trancher ; la pagination, si."""
    if a == b or len(a) != len(b) or len(a) < 4:
        return False
    return sum(1 for x, y in zip(a, b) if x != y) == 1


def _pagination_continue(fiche: Fiche, precedente_no: int, total: int) -> bool:
    return bool(fiche.page_no and precedente_no
                and fiche.page_no == precedente_no + 1
                and fiche.page_total and fiche.page_total == total)


def decouper(fiches: list[Fiche], max_pages_bl: int = 0) -> Decoupage:
    """Pose les coupes entre bordereaux et consigne les doutes.

    `max_pages_bl` : au-delà de ce nombre de pages, un groupe est jugé
    anormalement long dans un lot de plusieurs BL (coupe probablement
    manquée) — 0 désactive ce contrôle.
    """
    resultat = Decoupage()
    courant: Optional[Segment] = None
    cle_courante = ""
    dernier_no = 0          # pagination de la dernière page BL du groupe
    total_courant = 0

    def _ouvrir(index: int) -> Segment:
        nonlocal courant, cle_courante, dernier_no, total_courant
        courant = Segment(pages=[])
        resultat.segments.append(courant)
        cle_courante, dernier_no, total_courant = "", 0, 0
        return courant

    def _numero_suivant(depuis: int) -> str:
        """Clé du prochain numéro de BL lisible après la page `depuis`."""
        for suivante in fiches[depuis + 1:]:
            if suivante.type == TYPE_BL and suivante.numero:
                return cle_numero(suivante.numero)
        return ""

    for fiche in fiches:
        i, humain = fiche.index, fiche.index + 1

        if fiche.type == TYPE_BLANCHE:
            resultat.ecartees.append(i)
            continue

        if fiche.type == TYPE_ANNEXE:
            if courant is None:
                # Annexe en tête de pile : aucun BL avant elle. Elle rejoint
                # le premier bordereau, qui s'ouvrira sur la page suivante.
                _ouvrir(i)
                courant.avertissements.append(
                    f"{fiche.nature or 'Annexe'} en tête de lot (page {humain}) : "
                    "rattachée au premier bordereau.")
            courant.pages.append(i)
            courant.annexes.append(i)
            continue

        if fiche.type == TYPE_ILLISIBLE:
            if courant is None:
                _ouvrir(i)
            courant.pages.append(i)
            resultat.incertitudes.append(
                f"Page {humain} : non analysée par le modèle, rattachée au "
                "groupe précédent par défaut.")
            continue

        # ----- page de bordereau ------------------------------------------
        cle = cle_numero(fiche.numero)
        seulement_annexes = (courant is not None
                             and len(courant.annexes) == len(courant.pages))
        couper = False

        if courant is None:
            couper = True
        elif seulement_annexes:
            pass                                   # premier BL après annexes de tête
        elif not cle:
            if _pagination_continue(fiche, dernier_no, total_courant):
                pass                               # « 3/5 » après « 2/5 »
            elif cle_courante and _numero_suivant(i) == cle_courante and not fiche.debut:
                pass                               # encadrée par le même numéro
            elif fiche.debut or fiche.page_no == 1:
                couper = True
                resultat.incertitudes.append(
                    f"Page {humain} : début de document sans numéro de BL "
                    "lisible. Nouveau bordereau, ou suite du précédent ?")
            else:
                courant.avertissements.append(
                    f"Page {humain} sans numéro lisible : rattachée par "
                    "continuité.")
        elif not cle_courante:
            # Le groupe n'avait pas encore de numéro (première page mal lue).
            if fiche.debut or fiche.page_no == 1:
                couper = True
        elif cle == cle_courante:
            if fiche.page_no == 1 and dernier_no >= 1:
                courant.avertissements.append(
                    f"Le numéro {fiche.numero} repart en page 1 (page {humain}) : "
                    "exemplaire en double probable.")
        elif _proches(cle, cle_courante):
            if _pagination_continue(fiche, dernier_no, total_courant):
                courant.avertissements.append(
                    f"Page {humain} : numéro lu « {fiche.numero} », pagination "
                    f"continue — considéré comme une erreur de lecture de "
                    f"« {courant.numero} ».")
                cle = cle_courante
            else:
                couper = True
                if not (fiche.debut or fiche.page_no == 1):
                    resultat.incertitudes.append(
                        f"Pages {humain - 1} et {humain} : numéros presque "
                        f"identiques (« {courant.numero} » / « {fiche.numero} »). "
                        "Deux BL consécutifs, ou erreur de lecture ?")
        else:
            couper = True

        if couper:
            _ouvrir(i)
        courant.pages.append(i)
        if cle and not cle_courante:
            cle_courante, courant.numero = cle, fiche.numero
        if fiche.tiers and not courant.tiers:
            courant.tiers = fiche.tiers
        if fiche.page_no:
            dernier_no, total_courant = fiche.page_no, fiche.page_total

    _controler(resultat, fiches, max_pages_bl)
    return resultat


def _controler(resultat: Decoupage, fiches: list[Fiche], max_pages_bl: int) -> None:
    """Contrôles d'ensemble, une fois les coupes posées."""
    par_index = {fiche.index: fiche for fiche in fiches}
    vus: dict[str, Segment] = {}
    for segment in resultat.segments:
        cle = cle_numero(segment.numero)
        pages_bl = [p for p in segment.pages if p not in segment.annexes]

        if resultat.multi:
            if not pages_bl:
                resultat.incertitudes.append(
                    f"Groupe sans bordereau ({segment.etendue()}) : à "
                    "rattacher à un BL voisin.")
            elif not cle:
                resultat.incertitudes.append(
                    f"Groupe sans numéro de BL lisible ({segment.etendue()}).")
            if max_pages_bl and len(segment.pages) > max_pages_bl:
                resultat.incertitudes.append(
                    f"Groupe de {len(segment.pages)} pages ({segment.etendue()}), "
                    f"au-delà des {max_pages_bl} pages habituelles d'un "
                    "bordereau : une coupe a peut-être été manquée.")
        if cle:
            if cle in vus:
                resultat.incertitudes.append(
                    f"Le numéro {segment.numero} apparaît dans deux groupes "
                    f"({vus[cle].etendue()} et {segment.etendue()}) : pile "
                    "mélangée ou exemplaire en double.")
            else:
                vus[cle] = segment

        # Pagination annoncée contre pages présentes : information, pas blocage
        # (une page manquante ne se corrige qu'en rescannant).
        total = max((par_index[p].page_total for p in pages_bl
                     if p in par_index), default=0)
        if total and len(pages_bl) < total:
            segment.avertissements.append(
                f"Pagination incomplète : {len(pages_bl)} page(s) sur {total} "
                "annoncée(s).")


def recomposer(fiches: list[Fiche], debuts: Iterable[int],
               ecartees: Iterable[int] = ()) -> Decoupage:
    """Découpage issu des coupes choisies par un gestionnaire.

    Sert l'écran de découpage : l'humain déplace les coupes, on lui renvoie
    les groupes obtenus et les alertes. Ici les `incertitudes` sont des
    **alertes** : le gestionnaire reste l'autorité, il peut confirmer malgré
    elles (le modèle a pu mal lire un numéro)."""
    ecartees = set(ecartees)
    debuts = set(debuts)
    resultat = Decoupage(ecartees=sorted(ecartees))
    courant: Optional[Segment] = None
    coupe_reportee = False
    for fiche in fiches:
        if fiche.index in ecartees:
            # Écarter la première page d'un bordereau ne doit pas faire
            # disparaître la coupe : elle se reporte sur la page suivante,
            # sinon le reste du BL rejoindrait en silence son voisin.
            coupe_reportee = coupe_reportee or fiche.index in debuts
            continue
        if courant is None or fiche.index in debuts or coupe_reportee:
            courant = Segment()
            resultat.segments.append(courant)
            coupe_reportee = False
        courant.pages.append(fiche.index)
        if fiche.type == TYPE_ANNEXE:
            courant.annexes.append(fiche.index)
        elif fiche.type == TYPE_BL:
            if fiche.numero and not courant.numero:
                courant.numero = fiche.numero
            elif (fiche.numero and cle_numero(fiche.numero) != cle_numero(courant.numero)
                  and not any("numéros de BL différents" in a
                              for a in courant.avertissements)):
                courant.avertissements.append(
                    f"Deux numéros de BL différents lus dans ce groupe "
                    f"(« {courant.numero} » et « {fiche.numero} »).")
            if fiche.tiers and not courant.tiers:
                courant.tiers = fiche.tiers

    vus: dict[str, Segment] = {}
    for segment in resultat.segments:
        if len(segment.annexes) == len(segment.pages):
            resultat.incertitudes.append(
                f"Groupe sans bordereau ({segment.etendue()}) : il ne contient "
                "que des annexes.")
        resultat.incertitudes.extend(
            f"{a} ({segment.etendue()})" for a in segment.avertissements
            if "numéros de BL différents" in a)
        cle = cle_numero(segment.numero)
        if cle and cle in vus:
            resultat.incertitudes.append(
                f"Le numéro {segment.numero} apparaît dans deux groupes "
                f"({vus[cle].etendue()} et {segment.etendue()}).")
        elif cle:
            vus[cle] = segment
    return resultat


# ---------------------------------------------------------------------------
# Du découpage aux scans « fils »
# ---------------------------------------------------------------------------
def charges_segments(decoupage: Decoupage,
                     extraire: Callable[[list[int]], bytes]) -> list[dict]:
    """Prépare un scan fils par groupe : son PDF, ses pages, ses annexes.

    `extraire(index_pages)` fabrique le PDF du groupe (injecté : PyMuPDF
    n'entre pas dans ce module). Chaque fils porte dans `decoupage` de quoi
    retrouver ses annexes — pour ne pas les faire lire comme des pages de BL —
    et les avertissements à montrer au gestionnaire."""
    total = len(decoupage.segments)
    charges = []
    for rang, segment in enumerate(decoupage.segments, start=1):
        charges.append({
            "page_debut": segment.page_debut,
            "page_fin": segment.page_fin,
            "nb_pages": len(segment.pages),
            "contenu": extraire(list(segment.pages)),
            "decoupage": {
                "rang": rang, "total": total,
                "pages": [p + 1 for p in segment.pages],
                # Position des annexes DANS le PDF du groupe (à partir de 0).
                "annexes": [segment.pages.index(p) for p in segment.annexes],
                "numero_lu": segment.numero,
                "avertissements": list(segment.avertissements),
            },
        })
    return charges


def pages_a_analyser(pages: list, meta: Optional[dict]) -> list:
    """Pages à soumettre à l'extraction du BL : toutes sauf les annexes.

    Une lettre de voiture porte son propre numéro ; lue avec les pages du
    bordereau, elle pourrait le supplanter. Les annexes restent attachées au
    BL (images conservées), elles ne sont simplement pas lues comme des pages
    de bordereau. Si le filtre ne laisse rien, on rend tout : mieux vaut une
    lecture imparfaite qu'aucune."""
    annexes = set((meta or {}).get("annexes") or [])
    if not annexes:
        return list(pages)
    retenues = [page for position, page in enumerate(pages)
                if position not in annexes]
    return retenues or list(pages)
