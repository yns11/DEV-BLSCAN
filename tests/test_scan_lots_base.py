"""Un lot de bout en bout sur une VRAIE base PostgreSQL.

Les autres tests remplacent le dépôt par une doublure : ils ne prouvent rien
sur le SQL (contraintes, clés d'idempotence, verrous, transaction). Celui-ci
joue la migration réelle et le vrai `repository`. Seul le modèle est simulé.

Ignoré par défaut. Pour le lancer, sur une base JETABLE dont le nom contient
« test », migrations V001 et V002 jouées :

    BL_TEST_DSN="host=127.0.0.1 port=5432 dbname=bltest user=postgres" \\
        python -m pytest tests/test_scan_lots_base.py
"""
import datetime
import os
import sys
import types
from pathlib import Path

import pytest

DSN = os.environ.get("BL_TEST_DSN", "")
pytestmark = pytest.mark.skipif(
    not DSN or "test" not in DSN,
    reason="BL_TEST_DSN absent (ou base dont le nom ne contient pas « test »)")

ROOT = Path(__file__).resolve().parent.parent
PREFIXE = "TSTLOT-"
RECU = datetime.datetime(2026, 11, 25, 9, 31, tzinfo=datetime.timezone.utc)


@pytest.fixture()
def env(monkeypatch):
    for nom, attrs in (("databricks", {}),
                       ("databricks.sdk", {"WorkspaceClient": object})):
        if nom not in sys.modules:
            module = types.ModuleType(nom)
            for cle, val in attrs.items():
                setattr(module, cle, val)
            sys.modules[nom] = module
    sys.path.insert(0, str(ROOT / "jobs"))
    sys.path.insert(0, str(ROOT / "shared"))
    monkeypatch.setenv("BL_ENVIRONMENT", "local")
    monkeypatch.setenv("BL_PG_SCHEMA", "bl_scan")
    monkeypatch.setenv("BL_DATABASE_DSN", DSN)
    monkeypatch.setenv("BL_SCAN_AUTO_SANS_DESADV", "false")
    monkeypatch.setenv("BL_SCAN_MULTI_BL", "true")
    monkeypatch.setenv("BL_SCAN_MULTI_BL_AUTO", "true")
    monkeypatch.setenv("BL_LLM_ENDPOINT", "simule")
    import importlib
    from bl_core import config, database, repository
    config.get_settings.cache_clear()
    database.close_pool()
    # Les tests d'écran remplacent des fonctions du dépôt par des doublures
    # sans les restaurer : on recharge le vrai module, quel que soit l'ordre
    # d'exécution des fichiers de test.
    importlib.reload(repository)
    import psycopg

    def nettoyer():
        with psycopg.connect(DSN, autocommit=True) as cx:
            cx.execute("DELETE FROM bl_scan.scans_recus WHERE message_id LIKE 'tstlot:%'")
            # Tables qui référencent un BL (pages, audit…) : d'abord elles.
            dependantes = cx.execute(
                "SELECT c.conrelid::regclass::text, a.attname "
                "FROM pg_constraint c JOIN pg_attribute a "
                "  ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                "WHERE c.contype = 'f' "
                "  AND c.confrelid = 'bl_scan.suivi_bl'::regclass "
                "  AND c.conrelid <> 'bl_scan.scans_recus'::regclass").fetchall()
            for table, colonne in dependantes:
                cx.execute(
                    f"DELETE FROM {table} WHERE {colonne} IN (SELECT id_bl FROM "
                    "bl_scan.suivi_bl WHERE numero_bl LIKE %s)", (PREFIXE + "%",))
            cx.execute("DELETE FROM bl_scan.suivi_bl WHERE numero_bl LIKE %s",
                       (PREFIXE + "%",))
            cx.execute("DELETE FROM bl_scan.base_desadv WHERE numero_bl LIKE %s",
                       (PREFIXE + "%",))
            cx.execute("DELETE FROM bl_scan.base_tiers WHERE name LIKE 'S-TST%'")

    nettoyer()
    with psycopg.connect(DSN, autocommit=True) as cx:
        # Le job traite TOUT ce qui attend dans la file : sur une base qui
        # porte d'autres scans, il créerait des BL à partir de données qui ne
        # sont pas celles du test. On refuse plutôt que de polluer.
        en_attente, = cx.execute(
            "SELECT count(*) FROM bl_scan.scans_recus "
            "WHERE statut IN ('RECU', 'EN_ANALYSE')").fetchone()
        if en_attente:
            pytest.skip(f"{en_attente} scan(s) en attente dans cette base : "
                        "le test exige une file vide (base jetable).")
        cx.execute("INSERT INTO bl_scan.base_tiers (name, type_tiers, source_donnee) "
                   "VALUES ('S-TST001 : FOURNISSEUR ESSAI', 'FOURNISSEUR', 'MANUEL')")
        for numero in ("A1", "B2"):                 # C3 n'a PAS d'avis d'expédition
            cx.execute(
                "INSERT INTO bl_scan.base_desadv (numero_bl, nom_fournisseur, sens, "
                "source_donnee) VALUES (%s, 'S-TST001 : FOURNISSEUR ESSAI', 'ACHAT', "
                "'MANUEL')", (PREFIXE + numero,))
    yield psycopg
    nettoyer()
    database.close_pool()
    config.get_settings.cache_clear()


def _pdf(textes):
    import pymupdf
    doc = pymupdf.open()
    for texte in textes:
        page = doc.new_page(width=595, height=842)
        page.insert_text((60, 90), texte, fontsize=16)
        for ligne in range(10):
            page.insert_text((60, 140 + 22 * ligne), f"article {ligne}", fontsize=11)
    return doc.tobytes()


def test_un_lot_devient_trois_bordereaux_en_base(env):
    psycopg = env
    import traitement_scans as job
    from bl_core import decision, extraction, repository, scan_pdf, segmentation

    A, B, C = PREFIXE + "A1", PREFIXE + "B2", PREFIXE + "C3"
    plan = [("bl", A, "1/2"), ("bl", A, "2/2"), ("bl", B, "1/1"),
            ("cmr", "", ""), ("bl", C, "1/2"), ("bl", C, "2/2")]
    lot = _pdf([f"{genre} {numero} {page}" for genre, numero, page in plan])
    fiches_par_image = {}
    for image, (genre, numero, page) in zip(scan_pdf.rasteriser(lot), plan):
        fiches_par_image[image] = (
            {"type": "lettre_voiture", "numero_bl": "CMR-1"} if genre == "cmr" else
            {"type": "bl", "numero_bl": numero, "page": page,
             "debut": page.startswith("1/")})
    numeros_lus = iter([A, B, C])
    lectures = []
    faux_modele = types.SimpleNamespace(
        ficher_page=lambda image, client=None: fiches_par_image[image],
        extraire_infos_bl=lambda pages, tiers, ref: (
            lectures.append(len(pages)) or
            {**{c: "" for c in extraction.CHAMPS_ATTENDUS},
             "numero_bl": next(numeros_lus)}),
        endpoint_configure=lambda: "simule", Referentiel=extraction.Referentiel,
        CHAMPS_ATTENDUS=extraction.CHAMPS_ATTENDUS)
    notifications = types.SimpleNamespace(
        notifier_nouvelle_reception=lambda **k: (True, ""))
    modules = (decision, faux_modele, notifications, repository, scan_pdf,
               segmentation)

    repository.verifier_schema_multi_bl()
    parent = repository.enregistrer_scan(
        "tstlot:pile-1", "MAIL", "copieur@essai", "scans-bl-reception@essai",
        "Scan", "pile.pdf", lot, 6, "ACHAT", RECU)
    metriques = job.traiter_la_file(repository, modules, 150, max_scans=20)

    assert metriques == {"scans": 4, "crees_auto": 2, "a_valider": 1,
                         "lots_decoupes": 1, "lots_a_decouper": 0, "erreurs": 0}
    assert lectures == [2, 1, 2]        # la lettre de voiture n'est pas lue comme un BL

    with psycopg.connect(DSN) as cx:
        fils = cx.execute(
            "SELECT page_debut, page_fin, statut, nb_pages, id_bl IS NOT NULL "
            "FROM bl_scan.scans_recus WHERE id_parent = %s ORDER BY page_debut",
            (parent,)).fetchall()
        assert fils == [(1, 2, "TRAITE_AUTO", 2, True),
                        (3, 4, "TRAITE_AUTO", 2, True),
                        (5, 6, "A_VALIDER", 2, False)]
        assert cx.execute("SELECT statut FROM bl_scan.scans_recus WHERE id = %s",
                          (parent,)).fetchone() == ("DECOUPE",)
        bls = cx.execute(
            "SELECT b.numero_bl, b.nom_fournisseur, b.origine, "
            "       (SELECT count(*) FROM bl_scan.pieces_jointes_bl p "
            "         WHERE p.id_bl = b.id_bl) "
            "FROM bl_scan.suivi_bl b WHERE b.numero_bl LIKE %s ORDER BY 1",
            (PREFIXE + "%",)).fetchall()
        # Deux BL créés seuls, tiers repris de l'avis d'expédition ; la lettre
        # de voiture est restée attachée au 2e (2 pages).
        assert bls == [(A, "S-TST001 : FOURNISSEUR ESSAI", "SCAN_AUTO", 2),
                       (B, "S-TST001 : FOURNISSEUR ESSAI", "SCAN_AUTO", 2)]

    # Rejouer le job ne crée rien de plus.
    assert job.traiter_la_file(repository, modules, 150, max_scans=20)["scans"] == 0

    # Un BL existe déjà : le découpage ne peut plus être défait.
    assert "déjà été créé" in repository.rouvrir_decoupage(parent, "appro@essai")
    # Le PDF de chaque fils ne contient que ses pages.
    restant = repository.lister_scans(statuts=["A_VALIDER"])
    restant = restant[restant["id_parent"] == parent]
    assert scan_pdf.compter_pages(
        repository.telecharger_scan(int(restant["id"].iloc[0]))["contenu"]) == 2
