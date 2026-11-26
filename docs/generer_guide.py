"""Génère le guide rapide PDF de l'application BL dématérialisés."""
import sys

from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

SORTIE = sys.argv[1]
POLICES = "/usr/share/fonts/truetype/dejavu/"
pdfmetrics.registerFont(TTFont("Sans", POLICES + "DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("Sans-Bold", POLICES + "DejaVuSans-Bold.ttf"))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold")

MARINE = colors.HexColor("#1F3A5F")
SARCELLE = colors.HexColor("#2A8C82")
AMBRE = colors.HexColor("#C98A1B")
GRIS = colors.HexColor("#5B6573")
FOND = colors.HexColor("#F2F5F8")
FILET = colors.HexColor("#D5DCE4")

LARGEUR, HAUTEUR = A4
MARGE = 18 * mm
UTILE = LARGEUR - 2 * MARGE

S = {
    "corps": ParagraphStyle("corps", fontName="Sans", fontSize=9.5, leading=13.5,
                            textColor=colors.HexColor("#1E2630"), alignment=TA_LEFT),
    "intro": ParagraphStyle("intro", fontName="Sans", fontSize=10.5, leading=15,
                            textColor=colors.HexColor("#1E2630")),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=13, leading=17,
                         textColor=MARINE, spaceBefore=10, spaceAfter=4),
    "ou": ParagraphStyle("ou", fontName="Sans", fontSize=8.5, leading=11,
                         textColor=GRIS, spaceAfter=6),
    "cellule": ParagraphStyle("cellule", fontName="Sans", fontSize=9, leading=12),
    "cellule_g": ParagraphStyle("cellule_g", fontName="Sans-Bold", fontSize=9,
                                leading=12, textColor=MARINE),
    "num": ParagraphStyle("num", fontName="Sans-Bold", fontSize=10, leading=12,
                          textColor=colors.white, alignment=1),
    "puce": ParagraphStyle("puce", fontName="Sans", fontSize=9.5, leading=13.5,
                           leftIndent=11, bulletIndent=0, spaceAfter=2.5),
    "encadre": ParagraphStyle("encadre", fontName="Sans", fontSize=9.2, leading=13,
                              textColor=colors.HexColor("#1E2630")),
}


def titre(texte, ou=None):
    blocs = [Paragraph(texte, S["h2"])]
    if ou:
        blocs.append(Paragraph(ou, S["ou"]))
    return blocs


def puces(lignes):
    return [Paragraph(l, S["puce"], bulletText="•") for l in lignes]


def encadre(texte, couleur=SARCELLE):
    t = Table([[Paragraph(texte, S["encadre"])]], colWidths=[UTILE])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), FOND),
        ("LINEBEFORE", (0, 0), (0, -1), 3, couleur),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return t


def etapes(lignes):
    rangs = [[Paragraph(str(i), S["num"]), Paragraph(l, S["corps"])]
             for i, l in enumerate(lignes, 1)]
    t = Table(rangs, colWidths=[8 * mm, UTILE - 8 * mm])
    style = [("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (1, 0), (1, -1), 8),
             ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for i in range(len(rangs)):
        style.append(("BACKGROUND", (0, i), (0, i), MARINE))
        # Filet blanc entre les numéros : chaque étape reste une pastille.
        style.append(("LINEBELOW", (0, i), (0, i), 3, colors.white))
    t.setStyle(TableStyle(style))
    return t


def tableau(rangs, largeur_1=38 * mm):
    donnees = [[Paragraph(a, S["cellule_g"]), Paragraph(b, S["cellule"])]
               for a, b in rangs]
    t = Table(donnees, colWidths=[largeur_1, UTILE - largeur_1])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.5, FILET),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def schema():
    """Le circuit d'un BL : scan -> analyse -> automatique OU à valider -> BL."""
    l, h = UTILE, 44 * mm
    d = Drawing(l, h)
    bw, bh = 34 * mm, 15 * mm
    xs = [0, 43 * mm, 89 * mm, l - bw]
    ym = (h - bh) / 2
    yh, yb = h - bh - 1 * mm, 1 * mm

    def boite(x, y, lignes, fond, texte=colors.white):
        d.add(Rect(x, y, bw, bh, rx=2.5 * mm, ry=2.5 * mm, fillColor=fond,
                   strokeColor=None))
        n = len(lignes)
        for i, ligne in enumerate(lignes):
            gras = i == 0
            d.add(String(x + bw / 2, y + bh / 2 + (n - 1) * 4.6 - i * 9.2 - 3,
                         ligne, fontName="Sans-Bold" if gras else "Sans",
                         fontSize=8.2 if gras else 7.4, fillColor=texte,
                         textAnchor="middle"))

    def fleche(x1, y1, x2, y2, couleur=GRIS):
        d.add(Line(x1, y1, x2, y2, strokeColor=couleur, strokeWidth=1.2))
        import math
        a = math.atan2(y2 - y1, x2 - x1)
        p = 2.6 * mm
        d.add(Polygon([x2, y2,
                       x2 - p * math.cos(a - 0.45), y2 - p * math.sin(a - 0.45),
                       x2 - p * math.cos(a + 0.45), y2 - p * math.sin(a + 0.45)],
                      fillColor=couleur, strokeColor=None))

    boite(xs[0], ym, ["Scan au copieur", "un bordereau", "ou une pile"], MARINE)
    boite(xs[1], ym, ["Découpage, lecture", "et rapprochement", "avec l'ERP"], MARINE)
    boite(xs[2], yh, ["BL créé seul", "+ notification Teams"], SARCELLE)
    boite(xs[2], yb, ["À valider", "champs déjà remplis"], AMBRE)
    boite(xs[3], ym, ["BL consultable", "recherche, PDF,", "historique"], MARINE)

    fleche(xs[0] + bw + 1, ym + bh / 2, xs[1] - 1, ym + bh / 2)
    fleche(xs[1] + bw + 1, ym + bh / 2 + 3, xs[2] - 1, yh + bh / 2, SARCELLE)
    fleche(xs[1] + bw + 1, ym + bh / 2 - 3, xs[2] - 1, yb + bh / 2, AMBRE)
    fleche(xs[2] + bw + 1, yh + bh / 2, xs[3] - 1, ym + bh / 2 + 3, SARCELLE)
    fleche(xs[2] + bw + 1, yb + bh / 2, xs[3] - 1, ym + bh / 2 - 3, AMBRE)
    d.add(String(xs[2] + bw / 2, yh - 4.6 * mm, "certitude", fontName="Sans",
                 fontSize=7, fillColor=SARCELLE, textAnchor="middle"))
    d.add(String(xs[2] + bw / 2, yb + bh + 2.2 * mm, "doute", fontName="Sans",
                 fontSize=7, fillColor=AMBRE, textAnchor="middle"))
    return d


def libre(elements):
    """Bloc autorisé à se répartir sur deux pages (évite une page à moitié vide)."""
    return elements


def decor(canvas, doc):
    canvas.saveState()
    if doc.page == 1:
        canvas.setFillColor(MARINE)
        canvas.rect(0, HAUTEUR - 34 * mm, LARGEUR, 34 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Sans-Bold", 21)
        canvas.drawString(MARGE, HAUTEUR - 18 * mm, "BL dématérialisés")
        canvas.setFont("Sans", 11.5)
        canvas.drawString(MARGE, HAUTEUR - 26 * mm,
                          "Guide rapide de l'application")
    canvas.setStrokeColor(FILET)
    canvas.line(MARGE, 13 * mm, LARGEUR - MARGE, 13 * mm)
    canvas.setFillColor(GRIS)
    canvas.setFont("Sans", 7.5)
    canvas.drawString(MARGE, 9 * mm, "BL dématérialisés — guide rapide")
    canvas.drawRightString(LARGEUR - MARGE, 9 * mm, f"{doc.page}")
    canvas.restoreState()


histoire = [
    Spacer(1, 22 * mm),
    Paragraph(
        "L'application crée les bordereaux de livraison (BL) à partir des scans "
        "du copieur. Quand elle reconnaît un bordereau avec certitude, le BL est "
        "créé seul ; sinon, il vous est présenté pour vérification, avec les "
        "champs déjà remplis. Vous n'avez plus rien à saisir ni à classer.",
        S["intro"]),
    Spacer(1, 4 * mm),

    *titre("Le circuit d'un BL"),
    schema(),
    Spacer(1, 3 * mm),
    encadre(
        "<b>Quand le BL est-il créé seul ?</b> Quand son numéro correspond à un "
        "avis d'expédition (DESADV) de l'ERP : le fournisseur est alors repris de "
        "l'ERP, pas de la lecture du scan. Dans tous les autres cas, le BL attend "
        "une validation — l'application préfère demander plutôt que se tromper."),

    *titre("Scanner un bordereau", "Au copieur"),
    *puces([
        "Choisir le bouton <b>« BL Réception »</b> pour une réception, "
        "<b>« BL Expédition »</b> pour une expédition. Le bouton détermine le sens "
        "du flux : ne pas les confondre.",
        "<b>Une pile de plusieurs bordereaux peut passer en une fois</b> "
        "(60 pages au plus) : l'application repère où commence chaque BL. Garder "
        "les pages dans l'ordre, et chaque lettre de voiture <b>juste après</b> "
        "son bordereau — elle lui sera rattachée.",
        "Ne pas mélanger réceptions et expéditions dans une même pile.",
        "Le BL apparaît dans l'application en <b>15 minutes environ</b>, 30 au plus.",
    ]),

    *libre([
        *titre("Valider un scan",
               "Achat ▸ Scans à valider (achat) · Vente ▸ Scans à valider (vente)"),
        etapes([
            "Sélectionner une ligne de la file, puis <b>« Prendre en charge »</b>. "
            "Le scan vous est réservé : un collègue ne peut pas le traiter en même "
            "temps.",
            "Lire le <b>motif</b> affiché en haut : il indique pourquoi "
            "l'application n'a pas conclu seule (fournisseur non reconnu, numéro "
            "illisible, pas d'avis d'expédition…).",
            "Vérifier les champs à gauche en les comparant aux pages affichées à "
            "droite. Zoom et rotation sont disponibles sur l'image.",
            "Cliquer <b>« Créer le BL »</b>. Un numéro déjà existant est signalé "
            "avant l'enregistrement : aucun doublon ne peut être créé.",
        ]),
        Spacer(1, 2 * mm),
        tableau([
            ("Relancer l'analyse IA", "Nouvelle lecture du scan, par exemple après "
                                      "l'ajout d'un fournisseur au référentiel."),
            ("Écarter", "Scan inutilisable (page blanche, mauvais document). "
                        "Le motif est obligatoire."),
            ("Rendre à la file", "Libérer le scan sans décision, pour qu'un "
                                 "collègue le reprenne."),
        ], largeur_1=44 * mm),
    ]),

    KeepTogether([
        *titre("Découper un lot",
               "Scans à valider ▸ ligne « Lot à découper » ▸ Découper le lot"),
        Paragraph(
            "Quand une pile est claire, elle est découpée seule. Quand "
            "l'application hésite sur une coupe (numéro masqué par un tampon, "
            "pile en désordre), le lot vous est présenté, avec la raison du "
            "doute et une vignette par page.", S["corps"]),
        Spacer(1, 2 * mm),
        tableau([
            ("Nouveau BL ici", "À cocher sur la première page de chaque bordereau. "
                               "Décocher réunit la page au BL précédent."),
            ("Écarter la page", "Page blanche ou document étranger : elle "
                                "n'entrera dans aucun BL."),
            ("Confirmer le découpage", "Chaque bordereau part ensuite dans le "
                                       "circuit habituel (10 minutes environ). "
                                       "Une alerte doit être cochée pour être "
                                       "passée outre."),
            ("Revoir le découpage du lot", "Depuis un bordereau issu d'un lot, "
                                           "tant qu'aucun BL n'en a été créé."),
        ], largeur_1=50 * mm),
    ]),

    KeepTogether([
        *titre("Retrouver et consulter un BL",
               "Achat ▸ BL réception · Vente ▸ BL expédition"),
        *puces([
            "Filtrer par période, fournisseur ou client, état, quai… Le choix "
            "<b>« La période porte sur »</b> permet de chercher par date d'opération "
            "ou par <b>date de saisie</b> — utile pour retrouver un BL ancien "
            "enregistré récemment.",
            "<b>« Enregistrer l'écran actuel »</b> mémorise vos filtres pour la "
            "prochaine visite.",
            "Cliquer sur une ligne fait apparaître les actions :",
        ]),
        tableau([
            ("Voir les images", "Les pages scannées du bordereau."),
            ("PDF", "Export du BL, prêt à transmettre."),
            ("Historique", "Toutes les modifications : quoi, par qui, quand."),
            ("Modifier", "Corriger un champ du BL."),
            ("Passer à OK", "Lever une anomalie EDI, avec un commentaire facultatif "
                            "publié dans Teams."),
            ("Supprimer / Restaurer", "Une suppression est réversible ; le filtre "
                                      "« Supprimés inclus » les fait réapparaître."),
        ]),
    ]),

    KeepTogether([
        *titre("Suivre l'activité"),
        tableau([
            ("Tableau de bord", "Volumes, anomalies EDI, répartition par jour, "
                                "plage horaire et quai, fournisseurs les plus "
                                "concernés."),
            ("Rapports d'activité", "PDF produits automatiquement — journaliers, "
                                    "hebdomadaires, mensuels, trimestriels et "
                                    "annuels — avec une analyse rédigée."),
            ("Rapprochement", "BL et avis d'expédition face à face : ce qui a été "
                              "annoncé sans être reçu, et l'inverse."),
            ("Scans reçus", "Tout ce qui est arrivé du copieur et ce qu'il est "
                            "devenu, avec le taux de création automatique."),
        ], largeur_1=40 * mm),
        Spacer(1, 3 * mm),
        encadre(
            "<b>Alerte « aucun scan depuis plus de 8 h ».</b> Si elle apparaît "
            "dans Scans reçus en pleine journée d'activité, la chaîne de "
            "numérisation est probablement interrompue : prévenir "
            "l'administrateur de l'application.", AMBRE),
    ]),

    KeepTogether([
        *titre("Bon à savoir"),
        *puces([
            "<b>Notifications Teams</b> : à chaque nouvelle réception, les "
            "gestionnaires du portefeuille du fournisseur sont mentionnés "
            "nommément dans le canal.",
            "Les vues et les actions proposées <b>dépendent de votre rôle</b> "
            "(approvisionnement, ADV, finance, administration). Une vue absente "
            "de votre menu n'est pas une panne.",
            "<b>Copieur indisponible ?</b> Les administrateurs peuvent déposer un "
            "PDF via <b>« Dépôt manuel »</b> dans Scans reçus : il suit exactement "
            "le même traitement.",
            "Rien ne se perd : chaque BL conserve ses images et l'historique "
            "complet de ses modifications.",
        ]),
    ]),
]

doc = SimpleDocTemplate(SORTIE, pagesize=A4, leftMargin=MARGE, rightMargin=MARGE,
                        topMargin=16 * mm, bottomMargin=18 * mm,
                        title="BL dématérialisés — Guide rapide",
                        author="BL dématérialisés",
                        subject="Principales fonctionnalités de l'application")
doc.build(histoire, onFirstPage=decor, onLaterPages=decor)
print("OK", SORTIE)
