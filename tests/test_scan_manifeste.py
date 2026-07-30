"""Contrôle de cohérence de la copie de bl_core déposée pour les jobs.

Les jobs lisent un `bl_core/` copié à la main dans l'espace de travail. Une
mise à jour partielle (4 fichiers remplacés sur 18) a produit un
`AttributeError: module 'bl_core.extraction' has no attribute 'Referentiel'`
très loin de sa cause. `common.verifier_bl_core` transforme ce cas en une
erreur explicite au démarrage.

Ce manifeste est écrit à la main : il doit donc lui-même être vérifié. Un
symbole mal orthographié ferait échouer TOUS les déploiements, y compris les
bons — un garde-fou qui bloque le travail légitime est pire que pas de
garde-fou.
"""
import sys
import types
from pathlib import Path

import pytest

# Racine du projet, déduite de l'emplacement du test : les tests
# doivent tourner depuis n'importe quel clone, pas seulement le mien.
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def common():
    """Importe jobs/common.py en bouchonnant les dépendances Databricks,
    absentes de l'environnement de test."""
    # Seul le SDK Databricks est bouchonné : psycopg est réellement installé
    # et le remplacer casserait l'import de repository/notifications/rapports.
    for nom, attrs in (("databricks", {}),
                       ("databricks.sdk", {"WorkspaceClient": object})):
        if nom not in sys.modules:
            module = types.ModuleType(nom)
            for cle, val in attrs.items():
                setattr(module, cle, val)
            sys.modules[nom] = module
    sys.path.insert(0, str(ROOT / "jobs"))
    sys.path.insert(0, str(ROOT / "shared"))
    import common as module
    return module


def test_le_manifeste_ne_cite_que_des_symboles_existants(common):
    """Chaque symbole exigé doit exister pour de vrai dans bl_core."""
    import importlib

    introuvables = []
    for nom, symboles in common.SYMBOLES_REQUIS.items():
        module = importlib.import_module(f"bl_core.{nom}")
        introuvables += [f"{nom}.{s}" for s in symboles
                         if not hasattr(module, s)]
    assert not introuvables, (
        "Le manifeste exige des symboles absents de bl_core : "
        + ", ".join(introuvables))


def test_le_manifeste_couvre_ce_que_les_jobs_importent(common):
    """Tout module de bl_core importé par un job doit figurer au manifeste,
    sinon le contrôle laisserait passer une copie obsolète de ce fichier."""
    import ast

    importes = set()
    for chemin in (ROOT / "jobs").glob("*.py"):
        arbre = ast.parse(chemin.read_text())
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.ImportFrom) and noeud.module == "bl_core":
                importes.update(a.name for a in noeud.names)
    oublies = importes - set(common.SYMBOLES_REQUIS)
    assert not oublies, f"Modules importés mais non contrôlés : {oublies}"


def test_le_controle_passe_sur_une_copie_saine(common):
    common.verifier_bl_core(*common.SYMBOLES_REQUIS)      # ne lève pas


def test_le_controle_signale_un_fichier_obsolete(common, monkeypatch):
    """Reproduit la panne réelle : extraction.py d'une version antérieure,
    sans `Referentiel`."""
    from bl_core import extraction

    monkeypatch.delattr(extraction, "Referentiel")
    with pytest.raises(RuntimeError) as capture:
        common.verifier_bl_core("extraction")
    message = str(capture.value)
    assert "extraction.py" in message and "Referentiel" in message
    # Le message doit dire QUOI FAIRE, pas seulement ce qui manque.
    assert "ENTIER" in message


def test_le_controle_signale_un_module_absent(common):
    """Fichier carrément manquant dans la copie."""
    common.SYMBOLES_REQUIS["module_fantome"] = ("peu_importe",)
    try:
        with pytest.raises(RuntimeError, match="import impossible"):
            common.verifier_bl_core("module_fantome")
    finally:
        del common.SYMBOLES_REQUIS["module_fantome"]
