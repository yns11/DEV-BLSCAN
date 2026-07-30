"""Les tâches Lakeflow tournent SANS Streamlit installé.

L'environnement d'une tâche serverless n'embarque que psycopg, le SDK, pymupdf,
pillow — pas Streamlit. Tout module de `bl_core` importé par un job doit donc
être importable sans lui. `traitement_scans` importait `ui` pour un libellé
d'une ligne : `ModuleNotFoundError: No module named 'streamlit'` au démarrage.

Le contrôle est fait dans un **sous-processus** avec un blocage de l'import :
neutraliser Streamlit dans la session pytest casserait les tests d'interface.
"""
import ast
import pathlib
import subprocess
import sys
from pathlib import Path
import textwrap

import pytest

# Racine du projet, déduite de l'emplacement du test.
ROOT = str(Path(__file__).resolve().parent.parent)

def _imports_bl_core(source: pathlib.Path) -> list[str]:
    """Modules de bl_core importés par un job, LUS DANS SON CODE.

    Volontairement déduit de la source et non écrit à la main : une liste
    maintenue à la main documente l'état passé, elle ne protège pas d'un
    `from bl_core import ui` ajouté demain."""
    arbre = ast.parse(source.read_text())
    modules = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.ImportFrom) and noeud.module == "bl_core":
            modules.update(alias.name for alias in noeud.names)
    return sorted(modules)


IMPORTS_DES_JOBS = {
    chemin.stem: _imports_bl_core(chemin)
    for chemin in sorted(pathlib.Path(f"{ROOT}/jobs").glob("*.py"))
    if chemin.stem != "common"
}

BLOCAGE = '''
import importlib.abc, importlib.machinery, sys

class SansStreamlit(importlib.abc.MetaPathFinder):
    """Fait comme si Streamlit n'était pas installé."""
    def find_spec(self, nom, chemin=None, cible=None):
        if nom == "streamlit" or nom.startswith("streamlit."):
            raise ModuleNotFoundError("No module named 'streamlit'")
        return None

sys.meta_path.insert(0, SansStreamlit())
sys.path.insert(0, "{racine}/shared")
import os
os.environ.setdefault("BL_ENVIRONMENT", "local")
{corps}
print("OK")
'''


def _sans_streamlit(corps: str):
    code = BLOCAGE.format(racine=ROOT, corps=textwrap.dedent(corps))
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=120)


def test_le_blocage_fonctionne():
    """Vérifie l'outil de mesure avant de s'y fier : sans ce contrôle, un test
    qui passe ne prouverait rien."""
    resultat = _sans_streamlit("import streamlit")
    assert resultat.returncode != 0
    assert "No module named 'streamlit'" in resultat.stderr


@pytest.mark.parametrize("job,modules", sorted(IMPORTS_DES_JOBS.items()))
def test_les_modules_du_job_simportent_sans_streamlit(job, modules):
    if not modules:
        pytest.skip(f"{job} n'importe rien de bl_core")
    resultat = _sans_streamlit(f"from bl_core import {', '.join(modules)}")
    assert resultat.returncode == 0, (
        f"{job} : import impossible sans Streamlit\n{resultat.stderr}")


def test_le_libelle_de_statut_est_accessible_sans_streamlit():
    """La fonction dont l'absence a cassé le job : elle doit vivre hors de
    `ui`, tout en restant réexportée pour l'application."""
    resultat = _sans_streamlit("""
        from bl_core import repository
        assert repository.libelle_statut("1") == "✅ OK"
        assert repository.libelle_statut("0") == "🟥 EDI NOK"
    """)
    assert resultat.returncode == 0, resultat.stderr


def test_ui_reste_le_point_dentree_de_lapplication():
    """Le réexport ne doit pas être cassé : l'app appelle ui.libelle_statut."""
    sys.path.insert(0, f"{ROOT}/shared")
    from bl_core import repository, ui
    assert ui.libelle_statut is repository.libelle_statut


def test_ui_est_bien_le_module_a_ne_jamais_importer_dans_un_job():
    """Témoin positif : `ui` DOIT échouer sans Streamlit. Sans ce contrôle, le
    test paramétré ci-dessus pourrait passer parce qu'il ne mesure rien."""
    resultat = _sans_streamlit("from bl_core import ui")
    assert resultat.returncode != 0
    assert "No module named 'streamlit'" in resultat.stderr
    assert "ui" not in IMPORTS_DES_JOBS.get("traitement_scans", [])
