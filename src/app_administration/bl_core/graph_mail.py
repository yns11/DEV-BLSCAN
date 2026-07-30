"""Lecture d'une boîte aux lettres Microsoft 365 via Microsoft Graph.

Sert l'ingestion des scans du copieur : le multifonction envoie un PDF par
bordereau à une adresse dédiée, un job Databricks relève cette boîte.

Choix d'implémentation
----------------------
* **Authentification applicative** (`client_credentials`) sur un *service
  principal* Entra ID : aucun utilisateur interactif, aucun jeton à
  renouveler à la main. Le secret vient d'un *secret scope* Databricks, jamais
  d'`app.yaml` — c'est le seul secret de toute la solution.
* **Aucune dépendance** en dehors de la bibliothèque standard : `urllib`
  suffit, ce qui évite d'embarquer `msal` ou `requests` dans l'environnement
  du job.
* Le tenant visé n'a **pas besoin d'être celui du workspace Databricks** :
  l'appel est un simple échange HTTPS avec le point de terminaison du tenant
  qui héberge la boîte. C'est ce qui permet de valider la chaîne sur un
  abonnement Microsoft 365 personnel avant de la porter sur le tenant
  d'entreprise.

⚠️ Sécurité : la permission applicative `Mail.Read` donne par défaut accès à
**toutes** les boîtes du tenant. Elle DOIT être cantonnée à la seule boîte de
scan par une `ApplicationAccessPolicy` Exchange (voir GUIDE_MICROSOFT365.md).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger("bl.graph")

AUTORITE = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"
PORTEE = "https://graph.microsoft.com/.default"

# Une relève ne traite jamais plus que ce nombre de messages : borne le temps
# d'exécution du job et la mémoire, même après un week-end d'accumulation.
MAX_MESSAGES_PAR_RELEVE = 50


class ErreurGraph(RuntimeError):
    """Échec d'appel à Graph, avec le code HTTP et le corps de la réponse."""


def _appeler(url: str, methode: str = "GET", jeton: str = "",
             corps: dict | None = None, binaire: bool = False,
             tentatives: int = 3, delai_base: float = 1.5):
    """Appel HTTP avec relances sur les erreurs transitoires.

    Graph répond 429 (throttling) ou 503 sans que rien ne soit cassé : une
    relance avec attente croissante suffit. Les erreurs 4xx autres que 429 ne
    sont PAS relancées — elles traduisent un problème de droits ou de requête,
    que réessayer ne corrigera pas."""
    donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
    entetes = {"Accept": "application/json"}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    if donnees is not None:
        entetes["Content-Type"] = "application/json"

    derniere = None
    for essai in range(tentatives):
        requete = urllib.request.Request(url, data=donnees, headers=entetes,
                                         method=methode)
        try:
            with urllib.request.urlopen(requete, timeout=60) as reponse:
                brut = reponse.read()
                if binaire:
                    return brut
                return json.loads(brut) if brut else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            derniere = ErreurGraph(f"HTTP {exc.code} sur {methode} {url} : {detail}")
            if exc.code not in (429, 500, 502, 503, 504):
                raise derniere from None
            attente = float(exc.headers.get("Retry-After") or
                            delai_base * (2 ** essai))
            logger.warning("Graph %s : relance dans %.1fs (essai %d/%d)",
                           exc.code, attente, essai + 1, tentatives)
            time.sleep(attente)
        except Exception as exc:                      # réseau, DNS, timeout
            derniere = ErreurGraph(f"{type(exc).__name__} sur {methode} {url} : {exc}")
            time.sleep(delai_base * (2 ** essai))
    raise derniere or ErreurGraph(f"Échec de {methode} {url}")


@dataclass
class PieceJointe:
    nom: str
    type_mime: str
    taille: int
    contenu: bytes


@dataclass
class Message:
    """Message pertinent pour l'ingestion, déjà nettoyé."""
    id: str
    internet_message_id: str
    objet: str
    expediteur: str
    destinataires: list[str]
    recu_le: str                       # ISO 8601 UTC, tel que renvoyé par Graph
    pieces_jointes: list[PieceJointe] = field(default_factory=list)


class BoiteMail:
    """Accès en lecture (et déplacement) à UNE boîte aux lettres.

    `boite` est l'adresse SMTP de la boîte relevée, pas celle du service
    principal : en authentification applicative, on précise explicitement
    l'utilisateur visé (`/users/{boite}/…`)."""

    def __init__(self, tenant_id: str, client_id: str, client_secret: str,
                 boite: str) -> None:
        if not all((tenant_id, client_id, client_secret, boite)):
            raise ValueError(
                "tenant_id, client_id, client_secret et boite sont obligatoires "
                "pour lire la boîte de scan.")
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._secret = client_secret
        self.boite = boite
        self._jeton = ""
        self._expire_a = 0.0

    # -- Authentification --------------------------------------------------
    def _obtenir_jeton(self) -> str:
        """Jeton applicatif, mis en cache jusqu'à 60 s avant son expiration."""
        if self._jeton and time.time() < self._expire_a - 60:
            return self._jeton
        donnees = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self._secret,
            "scope": PORTEE,
            "grant_type": "client_credentials",
        }).encode("utf-8")
        requete = urllib.request.Request(
            f"{AUTORITE}/{self.tenant_id}/oauth2/v2.0/token",
            data=donnees, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                charge = json.loads(reponse.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ErreurGraph(
                f"Authentification Entra ID refusée (HTTP {exc.code}) : {detail}"
            ) from None
        self._jeton = charge["access_token"]
        self._expire_a = time.time() + int(charge.get("expires_in", 3600))
        return self._jeton

    def verifier_acces(self) -> dict:
        """Diagnostic : confirme que le jeton et les droits permettent de lire
        la boîte. Renvoie quelques attributs de la boîte."""
        jeton = self._obtenir_jeton()
        return _appeler(
            f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
            "?$select=displayName,mail,userPrincipalName", jeton=jeton)

    # -- Lecture -----------------------------------------------------------
    def messages_non_lus(self, dossier: str = "inbox",
                         limite: int = MAX_MESSAGES_PAR_RELEVE) -> list[Message]:
        """Messages non lus du dossier, du plus ancien au plus récent.

        L'ordre chronologique importe : en cas d'interruption, les scans les
        plus anciens sont traités d'abord et rien ne « double la file »."""
        jeton = self._obtenir_jeton()
        parametres = urllib.parse.urlencode({
            "$filter": "isRead eq false and hasAttachments eq true",
            "$orderby": "receivedDateTime asc",
            "$top": min(limite, MAX_MESSAGES_PAR_RELEVE),
            "$select": ("id,internetMessageId,subject,from,toRecipients,"
                        "receivedDateTime"),
        })
        reponse = _appeler(
            f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
            f"/mailFolders/{dossier}/messages?{parametres}", jeton=jeton)
        messages = []
        for brut in reponse.get("value", []):
            adresse = (((brut.get("from") or {}).get("emailAddress") or {})
                       .get("address") or "")
            destinataires = [
                ((d.get("emailAddress") or {}).get("address") or "").lower()
                for d in brut.get("toRecipients") or []
            ]
            messages.append(Message(
                id=brut["id"],
                # Repli sur l'id Graph : un message forgé peut ne pas porter
                # d'Internet-Message-Id, et l'idempotence doit rester assurée.
                internet_message_id=(brut.get("internetMessageId")
                                     or f"graph:{brut['id']}"),
                objet=brut.get("subject") or "",
                expediteur=adresse.lower(),
                destinataires=destinataires,
                recu_le=brut.get("receivedDateTime") or "",
            ))
        logger.info("Boîte %s : %d message(s) non lu(s) avec pièce jointe",
                    self.boite, len(messages))
        return messages

    def pieces_jointes(self, message_id: str,
                       extensions: tuple[str, ...] = (".pdf",),
                       taille_max: int = 40 * 1024 * 1024) -> list[PieceJointe]:
        """Pièces jointes *fichier* du message, filtrées par extension.

        Les pièces jointes en ligne (signatures, logos) et les messages
        imbriqués sont ignorés : seul `#microsoft.graph.fileAttachment` est
        retenu. Une pièce jointe au-delà de `taille_max` est écartée avec un
        avertissement plutôt que de faire échouer la relève."""
        jeton = self._obtenir_jeton()
        reponse = _appeler(
            f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
            f"/messages/{message_id}/attachments", jeton=jeton)
        retenues = []
        for brut in reponse.get("value", []):
            if brut.get("@odata.type") != "#microsoft.graph.fileAttachment":
                continue
            nom = brut.get("name") or ""
            if extensions and not nom.lower().endswith(extensions):
                logger.info("Pièce jointe ignorée (extension) : %s", nom)
                continue
            taille = int(brut.get("size") or 0)
            if taille > taille_max:
                logger.warning("Pièce jointe ignorée (%d octets > %d) : %s",
                               taille, taille_max, nom)
                continue
            contenu = brut.get("contentBytes")
            if not contenu:
                continue
            import base64

            retenues.append(PieceJointe(
                nom=nom,
                type_mime=brut.get("contentType") or "application/octet-stream",
                taille=taille,
                contenu=base64.b64decode(contenu)))
        return retenues

    # -- Écriture ----------------------------------------------------------
    def marquer_lu(self, message_id: str) -> None:
        jeton = self._obtenir_jeton()
        _appeler(f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
                 f"/messages/{message_id}", methode="PATCH", jeton=jeton,
                 corps={"isRead": True})

    def deplacer(self, message_id: str, dossier: str) -> None:
        """Déplace le message dans un dossier de la boîte (par son nom
        d'affichage ou son id). Best effort : un échec de rangement ne doit
        pas empêcher le traitement du scan, l'idempotence reposant sur
        `message_id` en base et non sur la position du mail."""
        jeton = self._obtenir_jeton()
        try:
            identifiant = self._id_dossier(dossier, jeton)
            _appeler(f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
                     f"/messages/{message_id}/move", methode="POST",
                     jeton=jeton, corps={"destinationId": identifiant})
        except Exception as exc:
            logger.warning("Rangement du message dans « %s » impossible : %s",
                           dossier, exc)

    def _id_dossier(self, nom: str, jeton: str) -> str:
        """Id d'un dossier de premier niveau, créé s'il n'existe pas."""
        parametres = urllib.parse.urlencode({"$top": 100, "$select": "id,displayName"})
        reponse = _appeler(
            f"{GRAPH}/users/{urllib.parse.quote(self.boite)}"
            f"/mailFolders?{parametres}", jeton=jeton)
        for dossier in reponse.get("value", []):
            if (dossier.get("displayName") or "").lower() == nom.lower():
                return dossier["id"]
        cree = _appeler(f"{GRAPH}/users/{urllib.parse.quote(self.boite)}/mailFolders",
                        methode="POST", jeton=jeton, corps={"displayName": nom})
        return cree["id"]
