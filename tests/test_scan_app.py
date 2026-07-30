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
    r.dernier_scan_recu = lambda: MAINTENANT
    r.verrouiller_scan = lambda i, u, m=60: True
    r.liberer_scan = lambda i: None


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
    assert labels.get("Reçus") == "17", labels          # 12+1+4+0
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


if __name__ == "__main__":
    test_demarrage_et_navigation()
    test_file_des_scans_a_valider()
    test_ecran_de_validation_prerempli()
    test_supervision_du_pipeline()
    test_alerte_pipeline_muet()
    print("\nTOUS LES TESTS APP SCAN OK")
