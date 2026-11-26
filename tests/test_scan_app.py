"""AppTest de la variante scan : navigation, validation, supervision."""
import datetime
import os
import sys
from pathlib import Path

import pandas as pd

# Racine du projet, déduite de l'emplacement du test : les tests
# doivent tourner depuis n'importe quel clone, pas seulement le mien.
ROOT = Path(__file__).resolve().parent.parent
AJD = datetime.date(2026, 7, 28)
MAINTENANT = datetime.datetime(2026, 7, 28, 10, 0)

os.environ.update({
    "BL_ENVIRONMENT": "local", "BL_RBAC_MODE": "disabled",
    "BL_PG_SCHEMA": "bl_scan", "BL_LOCAL_USER": "appro@emotors.com",
    "BL_TEAMS_WEBHOOK_RECEPTION": "", "BL_TEAMS_WEBHOOK_EDI": "",
    "BL_LLM_ENDPOINT": "",
})

from streamlit.testing.v1 import AppTest  # noqa: E402

sys.path.insert(0, str(ROOT / "src/app_administration"))


def _pdf(nb_pages=3):
    import pymupdf
    doc = pymupdf.open()
    for i in range(nb_pages):
        doc.new_page(width=595, height=842).insert_text(
            (60, 90), f"BL-2026-{i:04d}", fontsize=14)
    return doc.tobytes()


SCAN = {
    "id": 7, "message_id": "<m1@copieur>", "source": "MAIL",
    "expediteur": "copieur@emotors.com", "destinataire": "scans-bl-reception@x",
    "objet": "BL-RECEPTION", "nom_fichier": "SKM_C450i.pdf",
    "taille_octets": 12345, "nb_pages": 3, "sens": "ACHAT",
    "recu_le": MAINTENANT, "ingere_le": MAINTENANT, "statut": "A_VALIDER",
    "confiance": "nom", "motif_validation": "Tiers rapproché sur la raison "
                                            "sociale seule : à confirmer.",
    "id_bl": None, "verrouille_par": None, "verrouille_le": None,
    "analyse_le": MAINTENANT, "traite_le": None, "traite_par": None,
    "tentatives": 1, "erreur": None,
    "extraction": {"numero_bl": "BL-2026-0001", "tiers": "FRN1",
                   "code_tiers": "", "date": "2026-07-28", "statut": "",
                   "commentaire": "", "adresse": "",
                   "plage_horaire": "08h-10h", "quai": "B15"},
}


def _stub(repository):
    r = repository
    r.maintenant_local = lambda: MAINTENANT
    r.lister_gestionnaires = lambda: ["appro 1"]
    r.gestionnaires_pour_fournisseur = lambda frs: []
    r.lister_tiers = lambda t: ["S-001 : FRN1"]
    r.lister_tous_tiers = lambda: ["S-001 : FRN1"]
    r.lister_adresses = lambda: ["1 rue des Docks"]
    r.lister_quais = lambda: ["B15", "B06EST"]
    r.quai_pla = lambda tiers: "B15"
    r.adresses_par_tiers = lambda: {}
    r.numero_bl_disponible = lambda n, t=None: True
    r.fournisseur_pour_bl = lambda n, s: None
    r.bls_desadv_pour_tiers = lambda nom, sens: []
    r.lister_ecrans = lambda u, v: pd.DataFrame(columns=["nom", "est_defaut", "etat"])
    r.lire_referentiel = lambda nom, filtres=None: pd.DataFrame({"code_quai": ["B15"]})
    r.lire_bl_pour_dashboard = lambda dmin=None, dmax=None: pd.DataFrame({
        "type_operation": ["RECEPTION"], "statut_bl": ["0"],
        "nom_fournisseur": ["S-001 : FRN1"], "date_reception": [AJD],
        "plage_horaire": ["08h-10h"]})
    r.lire_desadv = (lambda **k: pd.DataFrame(columns=[
        "numero_bl", "nom_fournisseur", "issuedatetime", "integrationdate",
        "statut_edi"]))
    r.lister_scans = lambda **k: pd.DataFrame([SCAN])
    r.telecharger_scan = lambda i: {**SCAN, "contenu": _pdf(3)}
    r.stats_scans = lambda depuis: {"TRAITE_AUTO": 12, "A_VALIDER": 1,
                                    "VALIDE": 4, "ERREUR": 0}
    r.stats_lots = lambda depuis: {"scans": 17, "lots": 0, "issus": 0}
    r.dernier_scan_recu = lambda: MAINTENANT
    r.verrouiller_scan = lambda i, u, m=60: True
    r.liberer_scan = lambda i: None
    r.rouvrir_decoupage = lambda i, u: ""


def _app(vue, **etat):
    from bl_core import repository
    _stub(repository)
    at = AppTest.from_file(str(ROOT / "src/app_administration/app.py"),
                           default_timeout=30)
    at.session_state["nav_vue"] = vue
    for cle, valeur in etat.items():
        at.session_state[cle] = valeur
    at.run()
    assert not at.exception, at.exception
    return at


def test_demarrage_et_navigation():
    at = _app("Tableau de bord")
    labels = [b.label for b in at.button]
    # Les vues du pipeline ont remplacé l'archivage par lots.
    assert any("Scans à valider" in (l or "") for l in labels), labels
    assert any("Scans reçus" in (l or "") for l in labels), labels
    assert not any("Archivage" in (l or "") for l in labels), labels
    print("OK : navigation du pipeline")


def test_file_des_scans_a_valider():
    at = _app("Scans à valider (achat)")
    labels = {m.label: m.value for m in at.metric}
    assert labels.get("Scans à valider") == "1", labels
    # Sans sélection, l'écran guide l'utilisateur plutôt que d'afficher des
    # actions inertes (même convention que les vues BL).
    captions = " ".join(c.value or "" for c in at.caption)
    assert "Sélectionnez une ligne" in captions, captions
    assert not any("Prendre en charge" in (b.label or "") for b in at.button)
    print("OK : file d'attente des scans")


def test_ecran_de_validation_prerempli():
    """Les champs viennent de la colonne `extraction` : aucun appel modèle."""
    at = _app("Scans à valider (achat)", scan_ouvert=7)
    valeurs = [w.value for w in at.text_input]
    assert "BL-2026-0001" in valeurs, valeurs
    labels = [b.label for b in at.button]
    for attendu in ("Créer le BL", "Écarter", "Rendre à la file",
                    "Relancer l'analyse"):
        assert any(attendu in (l or "") for l in labels), (attendu, labels)
    # Le motif de la validation est expliqué au gestionnaire.
    textes = " ".join(w.value for w in at.warning)
    assert "raison sociale" in textes
    print("OK : écran de validation pré-rempli")


def test_supervision_du_pipeline():
    at = _app("Scans reçus")
    labels = {m.label: m.value for m in at.metric}
    assert labels.get("Scans reçus") == "17", labels
    assert labels.get("BL créés automatiquement") == "12", labels
    captions = " ".join(c.value or "" for c in at.caption)
    assert "Taux d'automatisation" in captions
    print("OK : supervision du pipeline")


def test_alerte_pipeline_muet():
    """Un pipeline silencieux est la panne la plus dangereuse."""
    from bl_core import repository
    _stub(repository)
    repository.dernier_scan_recu = lambda: MAINTENANT - datetime.timedelta(hours=30)
    at = AppTest.from_file(str(ROOT / "src/app_administration/app.py"),
                           default_timeout=30)
    at.session_state["nav_vue"] = "Scans reçus"
    at.run()
    assert not at.exception, at.exception
    alertes = " ".join(w.value for w in at.warning)
    assert "Aucun scan reçu depuis" in alertes, alertes
    print("OK : alerte pipeline muet")


# ---------------------------------------------------------------------------
# Lots de plusieurs bordereaux
# ---------------------------------------------------------------------------
def _trace_lot():
    """Proposition du pipeline pour un lot de 6 pages : 2 + 2 (dont une lettre
    de voiture) + 2, avec un doute sur le dernier groupe."""
    from bl_core import segmentation as seg
    bruts = [
        {"type": "bl", "numero_bl": "8402002398", "page": "1/2", "debut": True},
        {"type": "bl", "numero_bl": "8402002398", "page": "2/2"},
        {"type": "bl", "numero_bl": "RB-774512", "page": "1/1", "debut": True},
        {"type": "lettre_voiture"},
        {"type": "bl", "numero_bl": "", "page": "", "debut": True},
        {"type": "bl", "numero_bl": "8402002415", "page": "", "debut": False},
    ]
    fiches = [seg.fiche_depuis_modele(i, b) for i, b in enumerate(bruts)]
    decoupage = seg.decouper(fiches)
    return decoupage.vers_json(fiches), " ".join(decoupage.incertitudes)


def _lot():
    trace, motif = _trace_lot()
    return {**SCAN, "id": 9, "nom_fichier": "pile_0931.pdf", "nb_pages": 6,
            "statut": "A_DECOUPER", "confiance": None, "motif_validation": motif,
            "extraction": {}, "decoupage": trace, "id_parent": None,
            "page_debut": None, "page_fin": None}


def _app_lot(creations=None, **etat):
    from bl_core import repository
    _stub(repository)
    lot = _lot()
    repository.lister_scans = lambda **k: pd.DataFrame([
        {**SCAN, "id_parent": None, "page_debut": None, "page_fin": None}, lot])
    repository.telecharger_scan = lambda i: {**lot, "contenu": _pdf(6)}
    repository.creer_segments = (
        lambda parent, charges, utilisateur, trace=None:
        (creations if creations is not None else []).append(
            (parent, charges, utilisateur, trace)) or [101, 102, 103])
    at = AppTest.from_file(str(ROOT / "src/app_administration/app.py"),
                           default_timeout=30)
    at.session_state["nav_vue"] = "Scans à valider (achat)"
    for cle, valeur in etat.items():
        at.session_state[cle] = valeur
    at.run()
    assert not at.exception, at.exception
    return at


def _bouton(at, fragment):
    return next(b for b in at.button if fragment in (b.label or ""))


def test_un_lot_a_decouper_figure_dans_la_file():
    at = _app_lot()
    labels = {m.label: m.value for m in at.metric}
    assert labels.get("Scans à valider") == "2", labels
    grille = at.dataframe[0].value
    assert list(grille["nature"]) == ["📄 BL à valider", "✂️ Lot à découper"]


def test_ecran_de_decoupage_part_de_la_proposition_du_pipeline():
    """Aucun appel au modèle : fiches et coupes viennent de la colonne
    `decoupage`. Le motif du doute est affiché."""
    at = _app_lot(scan_ouvert=9)
    debuts = [c for c in at.checkbox if "Nouveau BL ici" in c.label]
    assert len(debuts) == 6
    assert [c.value for c in debuts] == [True, False, True, False, True, False]
    assert debuts[0].disabled                    # la première page ouvre toujours un BL
    assert "(3 BL)" in _bouton(at, "Confirmer le découpage").label
    assert "sans numéro de BL" in " ".join(w.value for w in at.warning)
    recap = at.dataframe[0].value
    assert list(recap["Numéro lu"]) == ["8402002398", "RB-774512", "8402002415"]
    assert list(recap["Annexes"]) == [0, 1, 0]   # la lettre de voiture suit le 2e BL
    legendes = " ".join(c.value or "" for c in at.caption)
    assert "Lettre de voiture" in legendes


def test_reunir_deux_bl_declenche_une_alerte_et_exige_une_confirmation():
    at = _app_lot(scan_ouvert=9)
    coupe = [c for c in at.checkbox if "Nouveau BL ici" in c.label][2]
    coupe.uncheck().run()                        # réunit les BL 1 et 2
    assert not at.exception, at.exception
    bouton = _bouton(at, "Confirmer le découpage")
    assert "(2 BL)" in bouton.label
    assert "numéros de BL différents" in " ".join(w.value for w in at.warning)
    assert bouton.disabled                       # tant que l'alerte n'est pas assumée
    next(c for c in at.checkbox if "malgré les alertes" in c.label).check().run()
    assert not _bouton(at, "Confirmer le découpage").disabled


def test_revenir_a_la_proposition():
    at = _app_lot(scan_ouvert=9)
    [c for c in at.checkbox if "Nouveau BL ici" in c.label][2].uncheck().run()
    _bouton(at, "Revenir à la proposition").click().run()
    assert not at.exception, at.exception
    assert "(3 BL)" in _bouton(at, "Confirmer le découpage").label


def test_confirmer_cree_un_scan_par_bordereau():
    """Chaque groupe devient un PDF autonome ; la page écartée n'y entre pas."""
    import pymupdf
    creations = []
    at = _app_lot(creations, scan_ouvert=9)
    # Le gestionnaire écarte la page 5 (début de document illisible).
    [c for c in at.checkbox if "Écarter la page" in c.label][4].check().run()
    assert "(3 BL)" in _bouton(at, "Confirmer le découpage").label
    _bouton(at, "Confirmer le découpage").click().run()
    assert not at.exception, at.exception
    (parent, charges, auteur, trace), = creations
    assert parent == 9 and auteur == "appro@emotors.com"
    assert [(c["page_debut"], c["page_fin"]) for c in charges] == [(1, 2), (3, 4), (6, 6)]
    tailles = [pymupdf.open(stream=c["contenu"], filetype="pdf").page_count
               for c in charges]
    assert tailles == [2, 2, 1]
    assert charges[1]["decoupage"]["annexes"] == [1]
    assert trace["ecartees"] == [4] and trace["confirme_par"] == auteur


def test_bordereau_issu_dun_lot_peut_rouvrir_le_decoupage():
    from bl_core import repository
    _stub(repository)
    fils = {**SCAN, "id": 101, "id_parent": 9, "page_debut": 3, "page_fin": 4,
            "nom_fichier": "pile_0931 [p. 3-4].pdf",
            "decoupage": {"rang": 2, "total": 3, "annexes": [1],
                          "avertissements": []}}
    repository.lister_scans = lambda **k: pd.DataFrame([fils])
    repository.telecharger_scan = lambda i: {**fils, "contenu": _pdf(2)}
    rouverts = []
    repository.rouvrir_decoupage = lambda i, u: rouverts.append((i, u)) or ""
    at = AppTest.from_file(str(ROOT / "src/app_administration/app.py"),
                           default_timeout=30)
    at.session_state["nav_vue"] = "Scans à valider (achat)"
    at.session_state["scan_ouvert"] = 101
    at.run()
    assert not at.exception, at.exception
    assert "Bordereau 2 sur 3" in " ".join(c.value or "" for c in at.caption)
    _bouton(at, "Revoir le découpage du lot").click().run()
    assert rouverts == [(9, "appro@emotors.com")]
    assert at.session_state["scan_ouvert"] == 9         # on bascule sur le lot


def test_supervision_distingue_scans_et_bordereaux():
    """Un lot découpé n'est pas un bordereau : le taux d'automatisation se
    compte par BL, pas par PDF reçu."""
    from bl_core import repository
    _stub(repository)
    repository.stats_scans = lambda depuis: {"TRAITE_AUTO": 6, "A_VALIDER": 1,
                                             "A_DECOUPER": 1, "DECOUPE": 2}
    repository.stats_lots = lambda depuis: {"scans": 5, "lots": 3, "issus": 5}
    at = AppTest.from_file(str(ROOT / "src/app_administration/app.py"),
                           default_timeout=30)
    at.session_state["nav_vue"] = "Scans reçus"
    at.run()
    assert not at.exception, at.exception
    labels = {m.label: m.value for m in at.metric}
    assert labels.get("Scans reçus") == "5" and labels.get("À valider") == "2"
    captions = " ".join(c.value or "" for c in at.caption)
    assert "75 %" in captions            # 6 sur 8 bordereaux (lots découpés exclus)
    assert "3 scan(s) contenaient plusieurs bordereaux" in captions


if __name__ == "__main__":
    test_demarrage_et_navigation()
    test_file_des_scans_a_valider()
    test_ecran_de_validation_prerempli()
    test_supervision_du_pipeline()
    test_alerte_pipeline_muet()
    print("\nTOUS LES TESTS APP SCAN OK")
