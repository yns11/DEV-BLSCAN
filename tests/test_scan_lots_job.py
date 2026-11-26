"""Le job de traitement face à un scan de plusieurs bordereaux.

Le dépôt et le modèle sont remplacés par des doublures ; le découpage et la
règle de décision sont les vrais. On vérifie le SORT de chaque scan : un seul
BL traité comme avant, un lot réparti en scans fils, un découpage douteux
envoyé à un gestionnaire."""
import datetime
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECU = datetime.datetime(2026, 11, 25, 9, 31)
PLAGES = (["00h-06h"] + [f"{h:02d}h-{h + 2:02d}h" for h in range(6, 20, 2)]
          + ["20h-00h"])


@pytest.fixture(scope="module")
def job():
    """Charge jobs/traitement_scans.py en bouchonnant le SDK Databricks."""
    for nom, attrs in (("databricks", {}),
                       ("databricks.sdk", {"WorkspaceClient": object})):
        if nom not in sys.modules:
            module = types.ModuleType(nom)
            for cle, val in attrs.items():
                setattr(module, cle, val)
            sys.modules[nom] = module
    sys.path.insert(0, str(ROOT / "jobs"))
    sys.path.insert(0, str(ROOT / "shared"))
    os.environ.setdefault("BL_ENVIRONMENT", "local")
    os.environ["BL_SCAN_AUTO_SANS_DESADV"] = "false"
    import traitement_scans
    return traitement_scans


class Depot:
    """Doublure de `repository` : retient ce que le job lui demande."""
    STATUT_SCAN_AUTO, STATUT_SCAN_A_VALIDER = "TRAITE_AUTO", "A_VALIDER"
    STATUT_SCAN_A_DECOUPER, STATUT_SCAN_DECOUPE = "A_DECOUPER", "DECOUPE"
    STATUT_SCAN_ERREUR = "ERREUR"
    SENS_VENTE, TYPE_EXPEDITION, TYPE_RECEPTION = "VENTE", "EXPEDITION", "RECEPTION"
    TIERS_CLIENT, TIERS_FOURNISSEUR = "CLIENT", "FOURNISSEUR"
    QUAI_DEFAUT, PLAGES_HORAIRES = "B15", PLAGES

    def __init__(self, desadv=None, file=None):
        self.desadv = desadv or {}
        self.file = list(file or [])
        self.segments, self.decoupages, self.clotures, self.bl = [], [], [], []

    def lister_tiers(self, type_tiers):
        return ["S-000803 : MOLEX FRANCE", "S-000178 : ROSENBERGER"]

    def adresses_par_tiers(self):
        return {}

    def bls_desadv_pour_tiers(self, nom, sens):
        return []

    def fournisseur_pour_bl(self, numero, sens):
        return self.desadv.get(numero)

    def numero_bl_disponible(self, numero, type_operation):
        return True

    def quai_pla(self, tiers):
        return None

    def libelle_statut(self, statut):
        return "OK"

    def enregistrer_decoupage(self, scan_id, trace, **options):
        self.decoupages.append((scan_id, trace, options))

    def creer_segments(self, parent_id, charges, utilisateur, trace=None):
        self.segments.append((parent_id, charges))
        for rang, charge in enumerate(charges):
            self.file.append({"id": 100 + rang, "id_parent": parent_id,
                              "contenu": charge["contenu"], "sens": "ACHAT",
                              "recu_le": RECU, "nom_fichier": "fils.pdf",
                              "decoupage": charge["decoupage"]})
        return list(range(100, 100 + len(charges)))

    def prendre_scans_a_analyser(self, limite=20):
        pris, self.file = self.file[:limite], self.file[limite:]
        return pris

    def cloturer_scan(self, scan_id, statut, **options):
        self.clotures.append((scan_id, statut, options))

    def creer_bl_depuis_scan(self, **champs):
        self.bl.append(champs)


def modules(job, depot, lot, lu=None, modele_actif=True):
    """`lot` : une fiche brute par page ; le « PDF » est la liste des index."""
    from bl_core import decision, segmentation
    lectures = []

    def ficher_page(image, client=None):
        fiche = lot[image]
        if isinstance(fiche, Exception):
            raise fiche
        return fiche

    def extraire_infos_bl(pages, tiers_libelle, referentiel):
        lectures.append(list(pages))
        premier = next((lot[p] for p in pages if isinstance(lot[p], dict)
                        and lot[p].get("numero_bl")), {})
        return {"numero_bl": (lu or {}).get("numero_bl", premier.get("numero_bl", "")),
                "code_tiers": "", "tiers": "", "adresse": "", "statut": "",
                "date": "", "commentaire": ""}

    extraction = types.SimpleNamespace(
        ficher_page=ficher_page, extraire_infos_bl=extraire_infos_bl,
        endpoint_configure=lambda: "endpoint" if modele_actif else None,
        Referentiel=lambda **k: None,
        CHAMPS_ATTENDUS=["numero_bl", "code_tiers", "tiers", "adresse", "statut",
                         "date", "commentaire"],
        rapprocher_tiers=lambda code, nom, connus: (None, ""),
        statut_est_nok=lambda statut: False)
    # decision appelle extraction.rapprocher_tiers / statut_est_nok du vrai
    # module : on y branche la doublure le temps du test.
    decision.extraction = extraction
    scan_pdf = types.SimpleNamespace(
        rasteriser=lambda contenu, dpi=150: list(contenu),
        est_blanche=lambda image: lot[image] == "blanche",
        extraire_pages=lambda contenu, index: bytes(contenu[i] for i in index))
    notifications = types.SimpleNamespace(
        notifier_nouvelle_reception=lambda **k: (True, ""))
    return (decision, extraction, notifications, depot, scan_pdf,
            segmentation), lectures


def bl(numero, page, debut=None):
    return {"type": "bl", "numero_bl": numero, "page": page,
            "debut": page.startswith("1/") if debut is None else debut}


CMR = {"type": "lettre_voiture", "numero_bl": "CMR-1"}
LOT = [bl("8402002398", "1/3"), bl("8402002398", "2/3"), bl("8402002398", "3/3"),
       bl("RB-774512", "1/2"), bl("RB-774512", "2/2"), CMR,
       bl("8402002415", "1/2"), bl("8402002415", "2/2")]
OPTIONS = types.SimpleNamespace(multi_bl=True, multi_bl_auto=True, max_pages_bl=25)


def scan(nb_pages, **extra):
    return {"id": 7, "contenu": bytes(range(nb_pages)), "sens": "ACHAT",
            "recu_le": RECU, "nom_fichier": "lot.pdf", "id_parent": None,
            "decoupage": {}, **extra}


@pytest.fixture(autouse=True)
def _restaurer_decision():
    yield
    if "bl_core.decision" in sys.modules:
        from bl_core import decision, extraction
        decision.extraction = extraction


# ---------------------------------------------------------------------------
def test_lot_sur_reparti_en_scans_fils(job):
    depot = Depot()
    mods, _ = modules(job, depot, LOT)
    statut = job.traiter_un_scan(scan(8), mods, 150, OPTIONS)
    assert statut == "DECOUPE"
    (parent, charges), = depot.segments
    assert parent == 7
    assert [(c["page_debut"], c["page_fin"]) for c in charges] == [(1, 3), (4, 6), (7, 8)]
    assert charges[1]["contenu"] == bytes([3, 4, 5])        # PDF du 2e groupe
    assert not depot.bl                                     # rien créé à ce stade


def test_les_fils_sont_traites_dans_la_meme_execution(job):
    """Les trois BL d'un lot ne doivent pas attendre le passage suivant du job.
    Deux ont un avis d'expédition : créés seuls. Le troisième part en validation."""
    depot = Depot(desadv={"8402002398": "S-000803 : MOLEX FRANCE",
                          "RB-774512": "S-000178 : ROSENBERGER"}, file=[scan(8)])
    mods, lectures = modules(job, depot, LOT)
    os.environ.update({"BL_SCAN_MULTI_BL": "true", "BL_SCAN_MULTI_BL_AUTO": "true"})
    metriques = job.traiter_la_file(depot, mods, 150, max_scans=40)
    assert metriques == {"scans": 4, "crees_auto": 2, "a_valider": 1,
                         "lots_decoupes": 1, "lots_a_decouper": 0, "erreurs": 0}
    assert [b["numero_bl"] for b in depot.bl] == ["8402002398", "RB-774512"]
    assert [b["origine"] for b in depot.bl] == ["SCAN_AUTO", "SCAN_AUTO"]
    # La lettre de voiture reste attachée au 2e BL (3 images)…
    assert len(depot.bl[1]["pages"]) == 3
    # …mais n'a pas été lue avec ses pages (2 pages soumises à l'extraction).
    assert [len(pages) for pages in lectures] == [3, 2, 2]


def test_decoupage_douteux_envoye_a_un_gestionnaire(job):
    """Un début de document sans numéro lisible : aucun fils, aucun BL."""
    lot = [bl("A-100", "", True), bl("", "", True), bl("B-200", "", True)]
    depot = Depot(desadv={"A-100": "S-000803 : MOLEX FRANCE"})
    mods, _ = modules(job, depot, lot)
    statut = job.traiter_un_scan(scan(3), mods, 150, OPTIONS)
    assert statut == "A_DECOUPER"
    assert not depot.segments and not depot.bl
    (_, trace, options), = depot.decoupages
    assert options["statut"] == "A_DECOUPER"
    assert "sans numéro de BL" in options["motif"]
    assert trace["debuts"] == [0, 1, 2]              # proposition conservée


def test_automatisme_des_lots_desactivable(job):
    """multi_bl_auto=false : même un lot sans le moindre doute est confirmé
    par un humain."""
    depot = Depot()
    mods, _ = modules(job, depot, LOT)
    options = types.SimpleNamespace(multi_bl=True, multi_bl_auto=False,
                                    max_pages_bl=25)
    assert job.traiter_un_scan(scan(8), mods, 150, options) == "A_DECOUPER"
    assert not depot.segments
    assert "confirmation demandée" in depot.decoupages[0][2]["motif"]


def test_scan_dun_seul_bl_traite_comme_avant(job):
    lot = [bl("8402002398", "1/2"), bl("8402002398", "2/2")]
    depot = Depot(desadv={"8402002398": "S-000803 : MOLEX FRANCE"})
    mods, lectures = modules(job, depot, lot)
    assert job.traiter_un_scan(scan(2), mods, 150, OPTIONS) == "TRAITE_AUTO"
    assert not depot.segments
    assert len(depot.bl) == 1 and len(depot.bl[0]["pages"]) == 2
    assert [len(p) for p in lectures] == [2]


def test_bl_unique_avec_lettre_de_voiture(job):
    """Un seul BL suivi de sa lettre de voiture : pas de lot, l'annexe est
    gardée dans le BL et écartée de la lecture."""
    lot = [bl("8402002398", "1/1"), CMR]
    depot = Depot(desadv={"8402002398": "S-000803 : MOLEX FRANCE"})
    mods, lectures = modules(job, depot, lot)
    assert job.traiter_un_scan(scan(2), mods, 150, OPTIONS) == "TRAITE_AUTO"
    assert len(depot.bl[0]["pages"]) == 2
    assert [len(p) for p in lectures] == [1]


def test_scan_fils_jamais_redecoupe(job):
    """Un groupe issu d'un lot est déjà un bordereau : pas de second découpage,
    même si une de ses pages porte un autre numéro."""
    lot = [bl("A-100", "1/1"), bl("B-200", "1/1")]
    depot = Depot()
    mods, _ = modules(job, depot, lot)
    fils = scan(2, id_parent=3, decoupage={"annexes": [], "avertissements": []})
    assert job.traiter_un_scan(fils, mods, 150, OPTIONS) == "A_VALIDER"
    assert not depot.segments and not depot.decoupages


def test_avertissements_du_decoupage_montres_au_gestionnaire(job):
    lot = [bl("A-100", "1/1")]
    depot = Depot()
    mods, _ = modules(job, depot, lot)
    fils = scan(1, id_parent=3, decoupage={
        "annexes": [], "avertissements": ["Pagination incomplète : 1 page(s) sur 3 annoncée(s)."]})
    job.traiter_un_scan(fils, mods, 150, OPTIONS)
    (_, statut, options), = depot.clotures
    assert statut == "A_VALIDER"
    assert "Pagination incomplète" in options["motif"]


def test_mode_multi_bl_coupe(job):
    """multi_bl=false : comportement d'origine, aucune lecture page par page."""
    depot = Depot()
    mods, lectures = modules(job, depot, LOT)
    options = types.SimpleNamespace(multi_bl=False, multi_bl_auto=True,
                                    max_pages_bl=25)
    assert job.traiter_un_scan(scan(8), mods, 150, options) == "A_VALIDER"
    assert not depot.segments and not depot.decoupages
    assert [len(p) for p in lectures] == [8]


def test_modele_en_panne_sur_toutes_les_pages_repli_sur_un_seul_bl(job):
    """Si aucune page n'a pu être lue, on ne sait rien du lot : le scan suit
    le circuit d'origine au lieu d'encombrer la file de découpage."""
    lot = [TimeoutError("saturé")] * 3
    depot = Depot()
    mods, _ = modules(job, depot, lot)
    assert job.traiter_un_scan(scan(3), mods, 150, OPTIONS) == "A_VALIDER"
    assert not depot.decoupages and not depot.segments


def test_sans_endpoint_aucun_decoupage(job):
    depot = Depot()
    mods, _ = modules(job, depot, LOT, modele_actif=False)
    assert job.traiter_un_scan(scan(8), mods, 150, OPTIONS) == "A_VALIDER"
    assert not depot.segments and not depot.decoupages


def test_page_blanche_ni_lue_ni_decoupante(job):
    lot = [bl("A-100", "1/2"), "blanche", bl("A-100", "2/2")]
    depot = Depot(desadv={"A-100": "S-000803 : MOLEX FRANCE"})
    mods, lectures = modules(job, depot, lot)
    assert job.traiter_un_scan(scan(3), mods, 150, OPTIONS) == "TRAITE_AUTO"
    assert [len(p) for p in lectures] == [2]          # le verso n'est pas lu
    assert len(depot.bl[0]["pages"]) == 3             # mais reste dans le BL
