"""Tests du client Microsoft Graph : jeton, filtrage, relances, idempotence."""
import base64
import io
import json
import os
import sys
import urllib.error
from pathlib import Path

import pytest

# Racine du projet, déduite de l'emplacement du test : les tests
# doivent tourner depuis n'importe quel clone, pas seulement le mien.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared"))
os.environ.setdefault("BL_ENVIRONMENT", "local")


class _Reponse:
    def __init__(self, charge, binaire=False):
        self._corps = charge if binaire else json.dumps(charge).encode()

    def read(self):
        return self._corps

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http(monkeypatch, reponses):
    """Remplace urlopen par une file de réponses ; capture les requêtes."""
    from bl_core import graph_mail
    appels = []

    def faux_urlopen(requete, timeout=None):
        appels.append(requete)
        reponse = reponses[min(len(appels) - 1, len(reponses) - 1)]
        if isinstance(reponse, Exception):
            raise reponse
        return _Reponse(reponse)

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux_urlopen)
    return appels


JETON = {"access_token": "jeton-test", "expires_in": 3600}


def _boite():
    from bl_core import graph_mail
    return graph_mail.BoiteMail("tenant-1", "client-1", "secret-1",
                                "scans@exemple.invalid")


def test_parametres_obligatoires():
    from bl_core import graph_mail
    with pytest.raises(ValueError, match="obligatoires"):
        graph_mail.BoiteMail("", "c", "s", "b")


def test_jeton_mis_en_cache(monkeypatch):
    """Un seul aller-retour d'authentification pour plusieurs appels."""
    appels = _http(monkeypatch, [JETON, {"value": []}, {"value": []}])
    boite = _boite()
    boite.messages_non_lus()
    boite.messages_non_lus()
    urls = [a.full_url for a in appels]
    assert sum("oauth2/v2.0/token" in u for u in urls) == 1


def test_authentification_refusee_message_explicite(monkeypatch):
    from bl_core import graph_mail

    def refus(requete, timeout=None):
        raise urllib.error.HTTPError(
            requete.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"error":"invalid_client"}'))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", refus)
    with pytest.raises(graph_mail.ErreurGraph, match="Entra ID refusée"):
        _boite().messages_non_lus()


def test_messages_non_lus_filtre_et_ordonne(monkeypatch):
    appels = _http(monkeypatch, [JETON, {"value": [{
        "id": "AAA", "internetMessageId": "<m1@copieur>",
        "subject": "BL-RECEPTION", "receivedDateTime": "2026-07-28T09:30:00Z",
        "from": {"emailAddress": {"address": "Copieur@Emotors.com"}},
        "toRecipients": [{"emailAddress": {"address": "scans-bl-reception@x"}}],
    }]}])
    messages = _boite().messages_non_lus()
    requete = appels[-1].full_url
    assert "isRead+eq+false" in requete or "isRead%20eq%20false" in requete
    assert "hasAttachments+eq+true" in requete or "hasAttachments%20eq%20true" in requete
    # PAS de $orderby : Exchange rejette le tri par date associé à un filtre
    # sur d'autres propriétés (« InefficientFilter »). Le tri est fait ensuite,
    # côté client.
    assert "orderby" not in requete
    assert len(messages) == 1
    assert messages[0].internet_message_id == "<m1@copieur>"
    # Adresses normalisées en minuscules : la comparaison à la liste blanche
    # et aux alias est ainsi insensible à la casse.
    assert messages[0].expediteur == "copieur@emotors.com"
    assert messages[0].destinataires == ["scans-bl-reception@x"]


def test_message_sans_internet_message_id(monkeypatch):
    """Repli sur l'id Graph : l'idempotence doit rester assurée."""
    _http(monkeypatch, [JETON, {"value": [{
        "id": "AAA", "subject": "", "receivedDateTime": "2026-07-28T09:30:00Z",
        "from": {}, "toRecipients": []}]}])
    messages = _boite().messages_non_lus()
    assert messages[0].internet_message_id == "graph:AAA"


def test_pieces_jointes_ne_retient_que_les_pdf(monkeypatch):
    contenu = base64.b64encode(b"%PDF-1.7 ...").decode()
    _http(monkeypatch, [JETON, {"value": [
        {"@odata.type": "#microsoft.graph.fileAttachment", "name": "bl.pdf",
         "contentType": "application/pdf", "size": 11, "contentBytes": contenu},
        {"@odata.type": "#microsoft.graph.fileAttachment", "name": "logo.png",
         "contentType": "image/png", "size": 9, "contentBytes": contenu},
        {"@odata.type": "#microsoft.graph.itemAttachment", "name": "reponse.msg",
         "size": 5},
    ]}])
    pieces = _boite().pieces_jointes("AAA")
    assert [p.nom for p in pieces] == ["bl.pdf"]
    assert pieces[0].contenu.startswith(b"%PDF-")


def test_piece_jointe_trop_lourde_ecartee(monkeypatch):
    contenu = base64.b64encode(b"%PDF-").decode()
    _http(monkeypatch, [JETON, {"value": [
        {"@odata.type": "#microsoft.graph.fileAttachment", "name": "gros.pdf",
         "contentType": "application/pdf", "size": 99_000_000,
         "contentBytes": contenu}]}])
    # Écartée sans exception : une relève ne doit pas échouer pour un scan.
    assert _boite().pieces_jointes("AAA", taille_max=1_000_000) == []


def test_relance_sur_throttling(monkeypatch):
    """429 est transitoire : relance avec attente, pas d'échec."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        if len(appels) == 2:
            raise urllib.error.HTTPError(requete.full_url, 429, "Too Many",
                                         {"Retry-After": "0"}, io.BytesIO(b"{}"))
        return _Reponse({"value": []})

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    monkeypatch.setattr(graph_mail.time, "sleep", lambda s: None)
    assert _boite().messages_non_lus() == []
    assert len(appels) == 3


def test_pas_de_relance_sur_403(monkeypatch):
    """403 = droits manquants : réessayer ne corrigera rien, on échoue vite
    avec le corps de la réponse pour diagnostiquer."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        raise urllib.error.HTTPError(
            requete.full_url, 403, "Forbidden", {},
            io.BytesIO(b'{"error":{"message":"Access denied"}}'))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph, match="403"):
        _boite().messages_non_lus()
    assert len(appels) == 2          # aucune relance


def test_deplacement_best_effort(monkeypatch):
    """Un échec de rangement du mail ne doit pas casser l'ingestion :
    l'idempotence repose sur la base, pas sur la position du message."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        raise urllib.error.HTTPError(requete.full_url, 404, "Not Found", {},
                                     io.BytesIO(b"{}"))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    _boite().deplacer("AAA", "Traites")        # ne lève pas


# ---------------------------------------------------------------------------
# Raccordement Entra ID : les erreurs de configuration doivent se diagnostiquer
# depuis le seul log du job.
# ---------------------------------------------------------------------------
def test_secret_nettoye_des_espaces_parasites():
    """Un secret collé dans un secret scope emporte souvent un saut de ligne.
    Entra ID le rejette alors comme invalide, ce qui envoie chercher un
    problème de valeur là où il n'y a qu'un problème de collage."""
    from bl_core import graph_mail
    boite = graph_mail.BoiteMail(" tenant-1\n", "client-1 ", "secret-1\n",
                                 " scans@exemple.invalid ")
    assert boite.tenant_id == "tenant-1"
    assert boite.client_id == "client-1"
    assert boite._secret == "secret-1"
    assert boite.boite == "scans@exemple.invalid"


def test_secret_reduit_a_un_saut_de_ligne_est_signale_manquant():
    """Ne PAS envoyer une chaîne vide à Entra ID pour récolter un 401 obscur."""
    from bl_core import graph_mail
    with pytest.raises(ValueError, match="obligatoires"):
        graph_mail.BoiteMail("tenant-1", "client-1", "\n  ",
                             "scans@exemple.invalid")


def test_indice_sur_valeur_confondue_avec_id_du_secret(monkeypatch):
    """AADSTS7000215 : l'erreur la plus fréquente du raccordement. Le message
    doit dire quoi corriger, pas seulement relayer le texte de Microsoft."""
    from bl_core import graph_mail
    corps = (b'{"error":"invalid_client","error_description":"AADSTS7000215: '
             b'Invalid client secret provided."}')

    def faux(requete, timeout=None):
        raise urllib.error.HTTPError(requete.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(corps))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph) as capture:
        _boite().verifier_acces()
    message = str(capture.value)
    assert "AADSTS7000215" in message
    assert "Valeur" in message and "ID secret" in message   # la distinction
    assert "GUID" in message                                # comment trancher


def test_indice_sur_secret_expire(monkeypatch):
    from bl_core import graph_mail
    corps = b'{"error_description":"AADSTS7000222: The provided secret expired"}'

    def faux(requete, timeout=None):
        raise urllib.error.HTTPError(requete.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(corps))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph, match="EXPIRÉ"):
        _boite().verifier_acces()


def test_aucun_indice_inventé_sur_un_code_inconnu(monkeypatch):
    """Pas de conseil hors sujet quand le code n'est pas reconnu : le texte de
    Microsoft passe tel quel."""
    from bl_core import graph_mail
    corps = b'{"error_description":"AADSTS50000: quelque chose d\'autre"}'

    def faux(requete, timeout=None):
        raise urllib.error.HTTPError(requete.full_url, 401, "Unauthorized", {},
                                     io.BytesIO(corps))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph) as capture:
        _boite().verifier_acces()
    assert "→" not in str(capture.value)


# ---------------------------------------------------------------------------
# InefficientFilter : Exchange refuse certaines associations filtre + tri, et
# sa tolérance varie selon la boîte. La relève doit tenir dans tous les cas.
# ---------------------------------------------------------------------------
def _msg(mid, date, **extra):
    return {"id": mid, "internetMessageId": f"<{mid}@copieur>", "subject": mid,
            "receivedDateTime": date, "from": {}, "toRecipients": [], **extra}


def _refus_filtre(url):
    return urllib.error.HTTPError(
        url, 400, "Bad Request", {},
        io.BytesIO(b'{"error":{"code":"InefficientFilter","message":'
                   b'"The restriction or sort order is too complex."}}'))


def test_tri_chronologique_fait_cote_client(monkeypatch):
    """Le tri ne peut plus être demandé au serveur : il doit être garanti
    ici, sinon un scan ancien resterait derrière un scan récent."""
    _http(monkeypatch, [JETON, {"value": [
        _msg("RECENT", "2026-07-28T17:00:00Z"),
        _msg("ANCIEN", "2026-07-28T08:15:00Z"),
        _msg("MIDI", "2026-07-28T12:30:00Z"),
    ]}])
    messages = _boite().messages_non_lus()
    assert [m.objet for m in messages] == ["ANCIEN", "MIDI", "RECENT"]


def test_degradation_quand_le_filtre_complet_est_refuse(monkeypatch):
    """Premier niveau refusé -> on retire le filtre sur les pièces jointes et
    on filtre soi-même. Aucun message éligible ne doit être perdu."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        if "hasAttachments+eq+true" in requete.full_url:
            raise _refus_filtre(requete.full_url)
        return _Reponse({"value": [
            _msg("AVEC-PJ", "2026-07-28T09:00:00Z", hasAttachments=True,
                 isRead=False),
            _msg("SANS-PJ", "2026-07-28T10:00:00Z", hasAttachments=False,
                 isRead=False),
        ]})

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    messages = _boite().messages_non_lus()
    assert [m.objet for m in messages] == ["AVEC-PJ"]
    # La requête de repli garde le filtre sur les non-lus et élargit la fenêtre.
    filtre = appels[-1].split("%24filter=")[1].split("&")[0]
    assert filtre == "isRead+eq+false"          # plus de clause pièce jointe
    assert "%24top=200" in appels[-1]


def test_degradation_ultime_sans_aucun_filtre(monkeypatch):
    """Les deux niveaux filtrés refusés -> tri par date seul, puis filtrage
    intégral côté client (non-lus ET avec pièce jointe)."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        if "%24filter" in requete.full_url:
            raise _refus_filtre(requete.full_url)
        return _Reponse({"value": [
            _msg("DEJA-LU", "2026-07-28T08:00:00Z", hasAttachments=True,
                 isRead=True),
            _msg("A-TRAITER", "2026-07-28T09:00:00Z", hasAttachments=True,
                 isRead=False),
        ]})

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    messages = _boite().messages_non_lus()
    assert [m.objet for m in messages] == ["A-TRAITER"]
    assert "%24filter" not in appels[-1]
    assert len(appels) == 4          # jeton + 3 tentatives


def test_erreur_400_autre_quinefficient_filter_remonte(monkeypatch):
    """Une requête réellement invalide ne doit pas être masquée par trois
    tentatives : elle remonte immédiatement."""
    from bl_core import graph_mail
    appels = []

    def faux(requete, timeout=None):
        appels.append(requete.full_url)
        if len(appels) == 1:
            return _Reponse(JETON)
        raise urllib.error.HTTPError(
            requete.full_url, 400, "Bad Request", {},
            io.BytesIO(b'{"error":{"code":"ErrorInvalidProperty"}}'))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph, match="ErrorInvalidProperty"):
        _boite().messages_non_lus()
    assert len(appels) == 2          # aucune dégradation inutile


def test_refus_de_toutes_les_formes_donne_une_erreur_explicite(monkeypatch):
    from bl_core import graph_mail

    def faux(requete, timeout=None):
        if "oauth2" in requete.full_url:
            return _Reponse(JETON)
        raise _refus_filtre(requete.full_url)

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    with pytest.raises(graph_mail.ErreurGraph,
                       match="toutes les formes de requête"):
        _boite().messages_non_lus()


def test_limite_respectee_apres_filtrage_client(monkeypatch):
    """La fenêtre élargie des modes dégradés ne doit pas faire traiter plus de
    messages que la limite demandée."""
    _http(monkeypatch, [JETON, {"value": [
        _msg(f"M{i:02d}", f"2026-07-28T{i:02d}:00:00Z") for i in range(10)]}])
    assert len(_boite().messages_non_lus(limite=3)) == 3


def test_plafond_de_releve_respecte(monkeypatch):
    """Une limite excessive ne doit pas déplafonner la relève : le job garde
    une durée d'exécution bornée."""
    from bl_core import graph_mail
    appels = _http(monkeypatch, [JETON, {"value": []}])
    _boite().messages_non_lus(limite=5000)
    assert f"%24top={graph_mail.MAX_MESSAGES_PAR_RELEVE}" in appels[-1].full_url


# ---------------------------------------------------------------------------
# Récupération de l'alias dans les en-têtes : Exchange réécrit le destinataire.
# ---------------------------------------------------------------------------
def test_texte_routage_extrait_les_entetes_pertinents(monkeypatch):
    from bl_core import graph_mail
    reponse = {"internetMessageHeaders": [
        {"name": "Subject", "value": "TR: LETS GO"},          # ignoré
        {"name": "Received",
         "value": "from x by y for <scans-bl-reception@themachineye.com>;"},
        {"name": "X-Envelope-To", "value": "scans-bl-reception@themachineye.com"},
        {"name": "Content-Type", "value": "multipart/mixed"},  # ignoré
    ]}
    _http(monkeypatch, [JETON, reponse])
    blob = _boite().texte_routage("AAA")
    assert "reception" in blob
    assert "multipart" not in blob          # en-tête non pertinent écarté
    assert blob == blob.lower()             # normalisé pour la recherche


def test_texte_routage_best_effort_si_illisible(monkeypatch):
    """Un échec de lecture des en-têtes ne doit pas casser la relève : le
    routage se rabat alors sur les seules adresses résolues."""
    from bl_core import graph_mail

    def faux(requete, timeout=None):
        if "oauth2" in requete.full_url:
            return _Reponse(JETON)
        raise urllib.error.HTTPError(requete.full_url, 500, "KO", {},
                                     io.BytesIO(b"{}"))

    monkeypatch.setattr(graph_mail.urllib.request, "urlopen", faux)
    assert _boite().texte_routage("AAA") == ""
