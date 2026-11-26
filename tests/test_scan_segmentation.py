"""Découpage d'un scan en bordereaux : les règles, cas par cas.

`segmentation.decouper` ne touche ni la base ni le réseau : chaque situation
de quai s'écrit comme une liste de fiches. Le risque couvert ici n'est pas une
erreur de lecture — elle se remarque — mais une page rangée dans le mauvais
BL, qui produit deux bordereaux plausibles et faux."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
os.environ.setdefault("BL_ENVIRONMENT", "local")

from bl_core import segmentation as seg  # noqa: E402


def fiches(*descriptions):
    """Construit un lot. Chaque page : ("bl", numéro, "2/3", début?) ou
    "cmr" / "blanche" / "illisible" / "suite" (page de BL sans rien de lisible)."""
    resultat = []
    for index, d in enumerate(descriptions):
        if d == "cmr":
            brut = {"type": "lettre_voiture", "numero_bl": "CMR-99887"}
        elif d == "blanche":
            brut = {"type": "blanche"}
        elif d == "illisible":
            brut = None
        elif d == "suite":
            brut = {"type": "bl", "numero_bl": "", "page": "", "debut": False}
        else:
            _, numero, page, *reste = d
            brut = {"type": "bl", "numero_bl": numero, "page": page,
                    "debut": reste[0] if reste else page.startswith("1/")}
        resultat.append(seg.fiche_depuis_modele(index, brut))
    return resultat


def groupes(decoupage):
    return [[p + 1 for p in s.pages] for s in decoupage.segments]


# ---------------------------------------------------------------------------
# Le cas de référence : 3 pages, 2 pages + lettre de voiture, 5 pages
# ---------------------------------------------------------------------------
LOT_REFERENCE = [
    ("bl", "8402002398", "1/3"), ("bl", "8402002398", "2/3"), ("bl", "8402002398", "3/3"),
    ("bl", "RB-774512", "1/2"), ("bl", "RB-774512", "2/2"), "cmr",
    ("bl", "8402002415", "1/5"), ("bl", "8402002415", "2/5"), "suite",
    ("bl", "8402002415", "4/5"), ("bl", "8402002415", "5/5"),
]


def test_lot_de_reference_decoupe_en_trois_bordereaux():
    d = seg.decouper(fiches(*LOT_REFERENCE))
    assert groupes(d) == [[1, 2, 3], [4, 5, 6], [7, 8, 9, 10, 11]]
    assert [s.numero for s in d.segments] == ["8402002398", "RB-774512", "8402002415"]
    assert d.sur and d.multi


def test_la_lettre_de_voiture_est_rattachee_au_bl_precedent():
    """Décision métier : l'annexe suit le bordereau qui la précède, et ne
    crée jamais de bordereau — son numéro n'est pas un numéro de BL."""
    d = seg.decouper(fiches(*LOT_REFERENCE))
    deuxieme = d.segments[1]
    assert deuxieme.annexes == [5]                     # page 6
    assert deuxieme.numero == "RB-774512"
    assert "CMR-99887" not in [s.numero for s in d.segments]


def test_page_sans_numero_encadree_par_le_meme_bl_reste_sure():
    """La page 9 n'a rien de lisible, mais les pages 8 et 10 portent le même
    numéro : aucun doute sur son rattachement."""
    d = seg.decouper(fiches(*LOT_REFERENCE))
    assert 8 in d.segments[2].pages
    assert d.sur


def test_un_seul_bordereau_reste_un_seul_groupe():
    """Non-régression : un scan d'un BL se comporte comme avant."""
    d = seg.decouper(fiches(("bl", "BL-1", "1/3"), ("bl", "BL-1", "2/3"),
                            ("bl", "BL-1", "3/3")))
    assert groupes(d) == [[1, 2, 3]]
    assert d.sur and not d.multi


def test_bl_sans_pagination_decoupe_sur_le_changement_de_numero():
    d = seg.decouper(fiches(("bl", "A-100", "", True), ("bl", "A-100", "", False),
                            ("bl", "B-200", "", True)))
    assert groupes(d) == [[1, 2], [3]]
    assert d.sur


# ---------------------------------------------------------------------------
# Pages sans identité
# ---------------------------------------------------------------------------
def test_page_blanche_ecartee_sans_creer_de_coupe():
    """Verso vide d'un recto-verso, au milieu d'un BL."""
    d = seg.decouper(fiches(("bl", "A-100", "1/2"), "blanche", ("bl", "A-100", "2/2")))
    assert groupes(d) == [[1, 3]]
    assert d.ecartees == [1]
    assert d.sur


def test_page_de_suite_sans_numero_rattachee_par_continuite():
    d = seg.decouper(fiches(("bl", "A-100", "", True), "suite",
                            ("bl", "B-200", "", True)))
    assert groupes(d) == [[1, 2], [3]]
    assert d.sur
    assert any("continuité" in a for a in d.segments[0].avertissements)


def test_debut_de_document_sans_numero_rend_le_lot_incertain():
    """Le vrai danger : un BL au numéro illisible avalé par son voisin. Une
    page qui ressemble à une première page, sans numéro, ne doit JAMAIS être
    rattachée en silence."""
    pages = fiches(("bl", "A-100", "", True),
                   ("bl", "", "", True),            # nouveau document, illisible
                   ("bl", "B-200", "", True))
    d = seg.decouper(pages)
    assert groupes(d) == [[1], [2], [3]]
    assert not d.sur
    assert any("sans numéro de BL" in i for i in d.incertitudes)


def test_page_non_analysee_rend_le_lot_incertain():
    d = seg.decouper(fiches(("bl", "A-100", "1/2"), "illisible",
                            ("bl", "B-200", "1/1")))
    assert not d.sur
    assert any("non analysée" in i for i in d.incertitudes)


def test_pagination_qui_continue_rattache_une_page_sans_numero():
    pages = fiches(("bl", "A-100", "1/3"), ("bl", "", "2/3", False),
                   ("bl", "", "3/3", False))
    d = seg.decouper(pages)
    assert groupes(d) == [[1, 2, 3]]
    assert d.sur


# ---------------------------------------------------------------------------
# Numéros voisins : deux BL consécutifs, ou une erreur de lecture ?
# ---------------------------------------------------------------------------
def test_bl_consecutifs_du_meme_fournisseur_sont_separes():
    """…398 puis …399 : un seul caractère d'écart, deux vrais bordereaux. La
    pagination qui repart à 1 lève le doute."""
    d = seg.decouper(fiches(("bl", "8402002398", "1/2"), ("bl", "8402002398", "2/2"),
                            ("bl", "8402002399", "1/2"), ("bl", "8402002399", "2/2")))
    assert groupes(d) == [[1, 2], [3, 4]]
    assert d.sur


def test_erreur_de_lecture_dun_caractere_absorbee_par_la_pagination():
    """« 0 » lu « O » en page 2 : la pagination continue, c'est le même BL."""
    d = seg.decouper(fiches(("bl", "8402002398", "1/3"), ("bl", "84O2002398", "2/3"),
                            ("bl", "8402002398", "3/3")))
    assert groupes(d) == [[1, 2, 3]]
    assert d.sur


def test_numeros_presque_identiques_sans_indice_demandent_un_humain():
    """Ni pagination ni en-tête : impossible de trancher entre deux BL
    consécutifs et une erreur de lecture. On coupe, et on demande."""
    d = seg.decouper(fiches(("bl", "8402002398", "", True),
                            ("bl", "8402002399", "", False)))
    assert groupes(d) == [[1], [2]]
    assert not d.sur
    assert any("presque identiques" in i for i in d.incertitudes)


# ---------------------------------------------------------------------------
# Pile en désordre, doublons, annexes mal placées
# ---------------------------------------------------------------------------
def test_pile_melangee_detectee():
    """Le numéro A revient après B : le programme le voit, il ne le répare pas."""
    d = seg.decouper(fiches(("bl", "A-100", "1/2"), ("bl", "B-200", "1/1"),
                            ("bl", "A-100", "2/2", False)))
    assert not d.sur
    assert any("deux groupes" in i for i in d.incertitudes)


def test_exemplaire_en_double_reste_dans_le_meme_groupe():
    """Deux exemplaires du même BL à la suite : un seul bordereau, signalé."""
    d = seg.decouper(fiches(("bl", "A-100", "1/1"), ("bl", "A-100", "1/1")))
    assert groupes(d) == [[1, 2]]
    assert d.sur
    assert any("double" in a for a in d.segments[0].avertissements)


def test_annexe_en_tete_de_lot_rattachee_au_premier_bl():
    d = seg.decouper(fiches("cmr", ("bl", "A-100", "1/1"), ("bl", "B-200", "1/1")))
    assert groupes(d) == [[1, 2], [3]]
    assert d.segments[0].numero == "A-100"
    assert d.segments[0].annexes == [0]
    assert any("tête de lot" in a for a in d.segments[0].avertissements)
    assert d.sur


def test_groupe_anormalement_long_signale():
    pages = [("bl", "A-100", "", True)] + ["suite"] * 6 + [("bl", "B-200", "", True)]
    d = seg.decouper(fiches(*pages), max_pages_bl=5)
    assert not d.sur
    assert any("coupe a peut-être été manquée" in i for i in d.incertitudes)


def test_pagination_incomplete_signalee_sans_bloquer():
    """Une page manquante ne se corrige qu'en rescannant : information."""
    d = seg.decouper(fiches(("bl", "A-100", "1/3"), ("bl", "A-100", "2/3"),
                            ("bl", "B-200", "1/1")))
    assert d.sur
    assert any("incomplète" in a for a in d.segments[0].avertissements)


# ---------------------------------------------------------------------------
# Normalisation des fiches
# ---------------------------------------------------------------------------
def test_reponse_du_modele_inexploitable_donne_une_fiche_illisible():
    assert seg.fiche_depuis_modele(0, None).type == seg.TYPE_ILLISIBLE
    assert seg.fiche_depuis_modele(0, "texte").type == seg.TYPE_ILLISIBLE


def test_pagination_absurde_ignoree():
    assert seg.fiche_depuis_modele(0, {"type": "bl", "page": "7/3"}).page_no == 0
    fiche = seg.fiche_depuis_modele(0, {"type": "bl", "page": "Page 2 sur 3"})
    assert (fiche.page_no, fiche.page_total) == (2, 3)


def test_type_inconnu_sans_numero_devient_une_annexe():
    """Dans le doute, une page n'ouvre pas un bordereau."""
    assert seg.fiche_depuis_modele(0, {"type": "bon de transport"}).type == seg.TYPE_ANNEXE
    avec_numero = seg.fiche_depuis_modele(0, {"type": "???", "numero_bl": "A-1"})
    assert avec_numero.type == seg.TYPE_BL


def test_numero_dune_annexe_jamais_retenu():
    fiche = seg.fiche_depuis_modele(0, {"type": "lettre_voiture",
                                        "numero_bl": "CMR-1"})
    assert fiche.type == seg.TYPE_ANNEXE and fiche.numero == ""


# ---------------------------------------------------------------------------
# Lecture des pages : blanches évitées, pannes isolées
# ---------------------------------------------------------------------------
def test_ficher_lot_nappelle_pas_le_modele_pour_une_page_blanche():
    appels = []

    def lire(page):
        appels.append(page)
        return {"type": "bl", "numero_bl": "A-1", "page": "1/1"}

    resultat = seg.ficher_lot([b"p1", b"vide", b"p3"], lire,
                              est_blanche=lambda page: page == b"vide")
    assert [f.type for f in resultat] == ["bl", "blanche", "bl"]
    assert sorted(appels) == [b"p1", b"p3"]


def test_ficher_lot_isole_la_panne_dune_page():
    """Un échec du modèle sur une page ne fait pas perdre le lot."""
    def lire(page):
        if page == b"p2":
            raise TimeoutError("endpoint saturé")
        return {"type": "bl", "numero_bl": "A-1"}

    resultat = seg.ficher_lot([b"p1", b"p2", b"p3"], lire)
    assert [f.type for f in resultat] == ["bl", "illisible", "bl"]
    assert [f.index for f in resultat] == [0, 1, 2]        # ordre conservé


# ---------------------------------------------------------------------------
# Découpage corrigé par un gestionnaire
# ---------------------------------------------------------------------------
def test_recomposer_suit_les_coupes_choisies():
    pages = fiches(*LOT_REFERENCE)
    d = seg.recomposer(pages, debuts={0, 3, 6})
    assert groupes(d) == [[1, 2, 3], [4, 5, 6], [7, 8, 9, 10, 11]]
    assert not d.incertitudes


def test_recomposer_alerte_si_un_bl_est_coupe_en_deux():
    d = seg.recomposer(fiches(*LOT_REFERENCE), debuts={0, 1, 3, 6})
    assert any("deux groupes" in a for a in d.incertitudes)


def test_recomposer_alerte_si_deux_bl_sont_reunis():
    d = seg.recomposer(fiches(*LOT_REFERENCE), debuts={0, 6})
    assert any("numéros de BL différents" in a for a in d.incertitudes)


def test_recomposer_alerte_sur_un_groupe_dannexes():
    d = seg.recomposer(fiches(*LOT_REFERENCE), debuts={0, 3, 5, 6})
    assert any("que des annexes" in a for a in d.incertitudes)


def test_recomposer_ecarte_les_pages_demandees():
    d = seg.recomposer(fiches(*LOT_REFERENCE), debuts={0, 3, 6}, ecartees={5})
    assert groupes(d)[1] == [4, 5]


def test_ecarter_la_premiere_page_dun_bl_conserve_la_coupe():
    """Sans report de la coupe, le reste du bordereau rejoindrait en silence
    le BL voisin — exactement l'erreur que l'écran doit empêcher."""
    d = seg.recomposer(fiches(*LOT_REFERENCE), debuts={0, 3, 6}, ecartees={6})
    assert groupes(d) == [[1, 2, 3], [4, 5, 6], [8, 9, 10, 11]]


# ---------------------------------------------------------------------------
# Du découpage aux scans fils
# ---------------------------------------------------------------------------
def test_charges_segments_un_pdf_par_groupe():
    d = seg.decouper(fiches(*LOT_REFERENCE))
    demandes = []
    charges = seg.charges_segments(d, lambda index: demandes.append(index) or b"%PDF")
    assert demandes == [[0, 1, 2], [3, 4, 5], [6, 7, 8, 9, 10]]
    assert [(c["page_debut"], c["page_fin"]) for c in charges] == [(1, 3), (4, 6), (7, 11)]
    # L'annexe du 2e groupe est sa 3e page : position 2 dans SON pdf.
    assert charges[1]["decoupage"]["annexes"] == [2]
    assert charges[1]["decoupage"]["rang"] == 2 and charges[1]["decoupage"]["total"] == 3


def test_annexes_exclues_de_la_lecture_du_bl():
    """La lettre de voiture reste attachée au BL, mais n'est pas lue avec ses
    pages : son numéro pourrait supplanter celui du bordereau."""
    assert seg.pages_a_analyser(["p1", "p2", "cmr"], {"annexes": [2]}) == ["p1", "p2"]
    assert seg.pages_a_analyser(["p1", "p2"], {}) == ["p1", "p2"]
    # Filtre qui ne laisserait rien : on rend tout plutôt que rien.
    assert seg.pages_a_analyser(["cmr"], {"annexes": [0]}) == ["cmr"]


def test_trace_json_relisible():
    """La trace stockée avec le lot suffit à rouvrir l'écran de découpage."""
    pages = fiches(*LOT_REFERENCE)
    trace = seg.decouper(pages).vers_json(pages)
    assert trace["debuts"] == [0, 3, 6]
    relues = seg.fiches_depuis_json(trace["fiches"], len(pages))
    assert [f.vers_json() for f in relues] == [f.vers_json() for f in pages]


def test_trace_absente_ne_bloque_pas_lecran():
    relues = seg.fiches_depuis_json(None, 3)
    assert [f.type for f in relues] == [seg.TYPE_ILLISIBLE] * 3
