"""Opérations PDF des lots : extraire un groupe de pages, repérer un verso vide."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
os.environ.setdefault("BL_ENVIRONMENT", "local")

pymupdf = pytest.importorskip("pymupdf")
from bl_core import scan_pdf  # noqa: E402


def _pdf(textes):
    doc = pymupdf.open()
    for texte in textes:
        page = doc.new_page(width=595, height=842)
        if texte:
            page.insert_text((60, 90), texte, fontsize=16)
            for ligne in range(12):
                page.insert_text((60, 140 + 22 * ligne),
                                 f"Ligne {ligne} - article et quantite", fontsize=11)
    return doc.tobytes()


def _textes(pdf):
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    return [page.get_text().split("\n")[0] for page in doc]


def test_extraire_pages_fabrique_le_pdf_du_groupe():
    lot = _pdf(["BL-A p1", "BL-A p2", "BL-B p1", "CMR", "BL-C p1"])
    groupe = scan_pdf.extraire_pages(lot, [2, 3])
    assert groupe.startswith(b"%PDF-")
    assert _textes(groupe) == ["BL-B p1", "CMR"]
    assert scan_pdf.compter_pages(lot) == 5              # le lot est intact


def test_extraire_pages_saute_une_page_ecartee():
    """Un verso blanc au milieu d'un BL n'entre pas dans son PDF."""
    lot = _pdf(["BL-A p1", "", "BL-A p2"])
    assert _textes(scan_pdf.extraire_pages(lot, [0, 2])) == ["BL-A p1", "BL-A p2"]


def test_extraire_pages_refuse_une_page_hors_du_lot():
    with pytest.raises(ValueError, match="hors du PDF"):
        scan_pdf.extraire_pages(_pdf(["a", "b"]), [1, 5])
    with pytest.raises(ValueError, match="Aucune page"):
        scan_pdf.extraire_pages(_pdf(["a"]), [])


def test_page_blanche_reconnue_et_page_ecrite_conservee():
    images = scan_pdf.rasteriser(_pdf(["BL-A p1", ""]))
    assert scan_pdf.est_blanche(images[0]) is False
    assert scan_pdf.est_blanche(images[1]) is True


def test_page_presque_vide_nest_pas_ecartee():
    """Une page qui ne porte qu'une ligne (signature, tampon) n'est PAS
    blanche : l'écarter à tort ferait perdre une information."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text(
        (60, 400), "Recu le 25/11 - 2 palettes abimees", fontsize=14)
    image, = scan_pdf.rasteriser(doc.tobytes())
    assert scan_pdf.est_blanche(image) is False


def test_image_illisible_reputee_non_blanche():
    """Dans le doute, on garde la page : le modèle tranchera."""
    assert scan_pdf.est_blanche(b"pas une image") is False


def test_plafond_de_pages_par_scan(monkeypatch):
    from bl_core import config
    monkeypatch.setenv("BL_SCAN_MAX_PAGES_LOT", "30")
    config.get_settings.cache_clear()
    try:
        assert len(scan_pdf.rasteriser(_pdf(["x"] * 30), dpi=40)) == 30
        with pytest.raises(ValueError, match="limite est de 30 pages par scan"):
            scan_pdf.rasteriser(_pdf(["x"] * 31), dpi=40)
        # Plafond explicite, prioritaire.
        with pytest.raises(ValueError, match="limite est de 2"):
            scan_pdf.rasteriser(_pdf(["x"] * 3), dpi=40, max_pages=2)
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


def test_plafond_lot_jamais_inferieur_au_plafond_bl(monkeypatch):
    from bl_core import config
    monkeypatch.setenv("BL_SCAN_MAX_PAGES", "25")
    monkeypatch.setenv("BL_SCAN_MAX_PAGES_LOT", "10")
    config.get_settings.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="BL_SCAN_MAX_PAGES_LOT"):
            config.get_settings()
    finally:
        monkeypatch.undo()
        config.get_settings.cache_clear()


def _jpeg(image):
    import io
    tampon = io.BytesIO()
    image.convert("RGB").save(tampon, "JPEG", quality=72)
    return tampon.getvalue()


@pytest.mark.parametrize("mot,taille", [("OK", 12), ("NOK", 9)])
def test_un_mot_isole_suffit_a_garder_la_page(mot, taille):
    """Une page qui ne porte que « NOK » pèse moins de noir que trente
    poussières : un pourcentage ne les distingue pas, le regroupement si.
    Écarter cette page ferait perdre l'état EDI du bordereau."""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((60, 400), mot, fontsize=taille)
    image, = scan_pdf.rasteriser(doc.tobytes())
    assert scan_pdf.est_blanche(image) is False


def test_poussieres_et_transparence_ne_font_pas_une_page_ecrite():
    """Verso réel d'un scan : points épars, texte du recto en gris clair,
    ombre de reliure dans la marge. Rien de tout cela n'est de l'encre."""
    import random
    from PIL import Image

    random.seed(1)
    page = Image.new("L", (1240, 1754), 240)
    for _ in range(60):                                   # poussières
        x, y = random.randint(100, 1100), random.randint(150, 1600)
        page.paste(40, (x, y, x + 2, y + 2))
    for y in range(300, 1500, 40):                        # transparence
        page.paste(195, (150, y, 1050, y + 8))
    page.paste(20, (0, 0, 40, 1754))                      # reliure
    assert scan_pdf.est_blanche(_jpeg(page)) is True
