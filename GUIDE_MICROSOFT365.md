# Guide 1 — Microsoft 365 : abonnement, Entra ID, boîte de scan

Objectif : disposer d'une **boîte aux lettres** qui reçoit les PDF du copieur,
et d'un **service principal** que le job Databricks utilise pour la lire. Tout
se fait sur un tenant où **vous êtes administrateur** — donc sur un abonnement
personnel pour les essais, avant portage sur le tenant eMotors.

> ⚠️ **Prix et libellés d'interface** : les tarifs Microsoft changent et varient
> selon le pays et l'engagement. Les montants ci-dessous sont des ordres de
> grandeur au moment de la rédaction, **à vérifier sur la page tarifaire** avant
> de souscrire. Les noms de menus peuvent aussi évoluer légèrement.

---

# 1. Quel abonnement souscrire

## Ce dont vous avez réellement besoin

| Besoin | Fourni par |
|---|---|
| Un tenant Entra ID où vous êtes admin | **gratuit**, créé avec l'abonnement |
| Inscrire une application (service principal) | **gratuit** (Entra ID de base) |
| Donner un consentement administrateur à une permission Graph | **gratuit** |
| Une **boîte aux lettres** qui reçoit des mails | ⚠️ **payant** — Exchange Online |
| Exchange Online PowerShell (pour cantonner les droits) | inclus avec Exchange |

Le seul élément payant est la boîte aux lettres. Tout le reste — Entra ID,
Graph, les services principaux — est inclus dans n'importe quel tenant.

## Les options, de la moins chère à la plus complète

| Offre | Ordre de prix | Ce que ça donne | Verdict |
|---|---|---|---|
| **Exchange Online Plan 1** | ~4 €/utilisateur/mois | boîte 50 Go, Entra ID, Graph | ✅ **le minimum suffisant** |
| **Microsoft 365 Business Basic** | ~6 €/utilisateur/mois | + Teams, SharePoint, Office web | ✅ à prendre si vous voulez aussi tester Teams et *Scan to SharePoint* |
| Microsoft 365 Business Standard | ~12 €/utilisateur/mois | + Office pour poste de travail | ❌ inutile ici |
| Microsoft 365 Developer Program | gratuit | tenant complet préprovisionné | ⚠️ l'accès est devenu conditionnel (abonnement Visual Studio actif) — **à vérifier**, ne pas bâtir dessus |

**Ma recommandation : Microsoft 365 Business Basic, une seule licence.**

Le surcoût par rapport à Exchange Plan 1 est de l'ordre de 2 €/mois, et il
achète deux choses qui comptent pour votre feuille de route :

- **Teams** dans le tenant de test — vous pourrez valider le flux Power
  Automate de notification sans toucher au tenant eMotors ;
- **SharePoint** — indispensable si vous décidez plus tard de tester
  *Scan to SharePoint*, qui supprimerait l'e-mail de la chaîne.

Une seule licence suffit : elle est pour **vous** (le compte admin). Les boîtes
partagées, elles, ne consomment pas de licence.

## Souscrire

1. Aller sur `microsoft.com/microsoft-365/business` ▸ **Microsoft 365 Business
   Basic** ▸ *Essayer gratuitement* (1 mois d'essai, puis payant).
2. Créer un nouveau compte professionnel — **surtout pas** un compte Microsoft
   personnel existant : il faut un **nouveau tenant**.
3. Choisir un domaine `.onmicrosoft.com`, par exemple
   `blscanessai.onmicrosoft.com`. **Aucun domaine personnalisé n'est
   nécessaire** pour les essais.
4. Noter le compte admin créé, par exemple
   `admin@blscanessai.onmicrosoft.com`.

---

# 2. La boîte de scan

## Option simple pour les essais : votre propre boîte

Pour valider la chaîne, **la boîte du compte admin suffit**. Elle existe déjà
et elle est licenciée. Passez directement à l'étape 3 en utilisant
`admin@blscanessai.onmicrosoft.com` comme boîte de scan.

## Option propre, à retenir en production : une boîte partagée

1. **Centre d'administration Microsoft 365** ▸ *Teams et groupes* ▸ **Boîtes
   aux lettres partagées** ▸ **Ajouter une boîte aux lettres partagée**.
   - Nom : `Scans BL`
   - Adresse : `scans-bl@blscanessai.onmicrosoft.com`
2. **Ajouter les deux alias** — c'est ce qui détermine le sens du flux sans
   rien demander au réceptionniste :
   - la boîte ▸ *Gérer les types d'adresses e-mail* ▸ **Ajouter** :
     - `scans-bl-reception@blscanessai.onmicrosoft.com`
     - `scans-bl-expedition@blscanessai.onmicrosoft.com`

> 🔴 **Exchange masque l'alias dans le destinataire.** Un mail envoyé à
> `scans-bl-reception@…` arrive bien dans la boîte, mais Exchange Online
> **résout l'alias vers l'adresse principale** : Graph ne voit alors que
> `scans-bl@…` comme destinataire, et le sens serait perdu. Le job contourne ce
> comportement en cherchant aussi le fragment `reception` / `expedition` dans le
> **sujet** et dans les **en-têtes de routage** du mail (la clause `for <…>` des
> lignes `Received`, où le destinataire d'enveloppe survit). Aucune action de
> votre part : envoyez simplement à l'alias.
>
> Si, sur votre tenant, même les en-têtes ne portent pas l'alias (rare, mais
> possible), **configurez le copieur pour mettre `RECEPTION` ou `EXPEDITION`
> dans le sujet** du scan : le job le reconnaît de la même façon. Le log de rejet
> indique précisément ce qui a été vu (destinataires, sujet, en-têtes), de quoi
> trancher.
3. **Restreindre la réception aux expéditeurs internes** — une boîte ouverte au
   monde entier est une porte d'entrée pour des PDF non sollicités :
   la boîte ▸ *Gestion de la livraison* ▸ décocher « Autoriser les expéditeurs
   externes ».

> 💡 Pour les essais depuis votre messagerie personnelle, laissez d'abord les
> expéditeurs externes **autorisés** — sinon vos propres mails de test seront
> rejetés. Refermez avant la mise en service.

---

# 3. Le service principal (application Entra ID)

C'est l'identité que le job Databricks utilisera. Elle n'a **aucun mot de passe
d'utilisateur** et **aucune boîte** : juste le droit de lire celle du scan.

## 3.1 Inscrire l'application

1. **portal.azure.com** (ou `entra.microsoft.com`) ▸ **Microsoft Entra ID** ▸
   **Inscriptions d'applications** ▸ **Nouvelle inscription**.
2. Nom : `BLDEMAT-Ingestion-Scans`.
3. Types de comptes pris en charge : **Comptes de cet annuaire uniquement**
   (mono-locataire).
4. URI de redirection : **laisser vide** — il n'y a aucune connexion
   interactive.
5. **Inscrire**, puis noter sur la page *Vue d'ensemble* :

   | À noter | Où le retrouver | Servira comme |
   |---|---|---|
   | **ID d'application (client)** | Vue d'ensemble | `graph_client_id` |
   | **ID de l'annuaire (locataire)** | Vue d'ensemble | `graph_tenant_id` |

## 3.2 Créer le secret client

1. L'application ▸ **Certificats et secrets** ▸ *Secrets client* ▸ **Nouveau
   secret client**.
2. Description : `Databricks ingestion`. Expiration : **6 ou 12 mois**
   (24 mois maximum ; noter la date, voir §6).
3. 🔴 **Copiez la colonne « Valeur », PAS la colonne « ID secret ».** Le
   portail affiche les deux côte à côte, et l'ID ressemble tout autant à un
   identifiant technique — c'est **l'erreur de raccordement la plus fréquente**.

   | Colonne | À quoi ça ressemble | À utiliser ? |
   |---|---|---|
   | **Valeur** | ~40 caractères mêlant majuscules, minuscules, chiffres, souvent un `~` ou un `.` — ex. `abC8Q~x1...` | ✅ **oui** |
   | ID secret | un GUID, groupes de 8-4-4-4-12 caractères séparés par des tirets | ❌ non |

   Si vous vous trompez, le job échoue avec
   `AADSTS7000215: Invalid client secret provided` — Microsoft le dit
   explicitement : *« ensure the secret being sent is the client secret value,
   not the client secret ID »*.

4. **Copiez la valeur immédiatement** — elle ne sera **plus jamais** affichée,
   même à vous, même en revenant sur la page. Si vous la perdez, il n'y a rien
   à récupérer : supprimez le secret et créez-en un nouveau.
5. Ce secret va dans le *secret scope* Databricks et **nulle part ailleurs** —
   jamais dans un `app.yaml`, jamais dans un paramètre de job.

> ⚠️ **Attention au collage.** Sans argument de valeur,
> `databricks secrets put-secret <scope> <clé>` ouvre un éditeur, où un retour
> à la ligne ou une espace en fin de valeur produisent la **même erreur
> 7000215** — de quoi chercher très loin un simple problème de copier-coller.
> Le code retire ces caractères par précaution, mais le plus sûr est de ne pas
> passer par l'éditeur :
>
> ```bash
> databricks secrets put-secret bldemat_scan graph_client_secret \
>   --string-value 'LA_VALEUR_DU_SECRET'
> ```
>
> La valeur reste alors dans l'historique du shell : pensez à l'en retirer
> (`history -d`), ou préfixez la commande d'une espace si votre shell est
> configuré pour ignorer ces lignes.

## 3.3 Accorder les permissions Graph

1. L'application ▸ **API autorisations** ▸ **Ajouter une autorisation** ▸
   **Microsoft Graph** ▸ **Autorisations d'application** (surtout pas
   « déléguées » : il n'y a pas d'utilisateur connecté).
2. Cocher :
   - **`Mail.Read`** — lire les messages et leurs pièces jointes ;
   - **`Mail.ReadWrite`** — marquer lu et ranger dans `Traites` / `Rejetes`.
3. **Accorder le consentement administrateur** pour le tenant (bouton en haut
   de la liste). Sans ce clic, rien ne fonctionnera : c'est l'oubli le plus
   fréquent.
4. Vérifier que la colonne *État* affiche bien « Accordé pour … ».

## 3.4 ⚠️ Cantonner l'accès à la seule boîte de scan

**C'est l'étape de sécurité la plus importante de tout ce guide.**

`Mail.Read` en autorisation d'application donne, par défaut, accès à **toutes
les boîtes du tenant**. Sur un tenant d'essai, l'enjeu est faible ; sur le
tenant eMotors, ce serait un lecteur universel de la messagerie d'entreprise.
Il faut donc restreindre explicitement.

Depuis **Exchange Online PowerShell** (`Connect-ExchangeOnline`), en
remplaçant l'ID d'application et l'adresse :

```powershell
# 1. Un groupe de distribution mail qui ne contient QUE la boîte de scan.
New-DistributionGroup -Name "Boites-Scan-BL" `
  -Members "scans-bl@blscanessai.onmicrosoft.com" `
  -Type Distribution

# 2. La politique : cette application ne peut voir QUE ce groupe.
New-ApplicationAccessPolicy `
  -AppId "<ID d'application (client)>" `
  -PolicyScopeGroupId "Boites-Scan-BL@blscanessai.onmicrosoft.com" `
  -AccessRight RestrictAccess `
  -Description "BLDEMAT : lecture de la boite de scan uniquement"

# 3. Contrôler : doit répondre « Granted » pour la boîte de scan…
Test-ApplicationAccessPolicy -Identity "scans-bl@blscanessai.onmicrosoft.com" `
  -AppId "<ID d'application (client)>"

# …et « Denied » pour n'importe quelle autre boîte.
Test-ApplicationAccessPolicy -Identity "admin@blscanessai.onmicrosoft.com" `
  -AppId "<ID d'application (client)>"
```

> La politique peut prendre **jusqu'à une heure** à se propager. Si le premier
> test renvoie encore « Granted » partout, attendez avant de conclure à une
> erreur.

## 3.5 Créer les dossiers de rangement

Dans la boîte de scan (via Outlook Web), créer deux dossiers de premier
niveau : **`Traites`** et **`Rejetes`**. Le job les crée automatiquement s'ils
manquent, mais les créer à la main évite une ambiguïté de nommage (accents).

---

# 4. Databricks accède-t-il vraiment à une boîte d'un autre tenant ?

Oui, et c'est plus simple qu'il n'y paraît. **Aucune relation d'approbation
entre tenants n'est nécessaire.**

Le job fait deux appels HTTPS ordinaires :

```
1) POST https://login.microsoftonline.com/<VOTRE_TENANT>/oauth2/v2.0/token
      client_id=…  client_secret=…  grant_type=client_credentials
      scope=https://graph.microsoft.com/.default
   -> un jeton d'accès

2) GET  https://graph.microsoft.com/v1.0/users/<boite>/mailFolders/inbox/messages
      Authorization: Bearer <jeton>
```

L'identité du workspace Databricks n'intervient **pas** : c'est le
`client_id` / `client_secret` de *votre* tenant qui authentifie l'appel. C'est
exactement ce qui vous permet de valider toute la chaîne sur l'abonnement
personnel, puis de basculer sur eMotors en changeant **trois paramètres** :
`graph_tenant_id`, `graph_client_id` et le secret.

**Le seul prérequis réseau** : le workspace Databricks doit pouvoir joindre
`login.microsoftonline.com` et `graph.microsoft.com` en sortie. Si votre
environnement applique une politique réseau restrictive, faites-la ouvrir avant
de commencer — c'est le genre de détail qui se découvre en fin de recette.

---

# 5. Configurer le copieur

Créez **deux boutons** sur l'écran d'accueil du multifonction, l'un par flux.

| Réglage | Valeur | Pourquoi |
|---|---|---|
| Destination bouton 1 | `scans-bl-reception@…` | détermine le sens ACHAT |
| Destination bouton 2 | `scans-bl-expedition@…` | détermine le sens VENTE |
| Format | **PDF** | pas d'image seule : le nombre de pages doit être lisible |
| Résolution | **300 dpi** | en dessous, la lecture des numéros se dégrade |
| Couleur | **Niveaux de gris** | 3 à 4 fois plus léger, sans perte utile |
| Suppression des pages blanches | **activée** | supprime les versos vides du duplex |
| Redressement + recadrage auto | **activés** | le modèle lit bien mieux une page droite |
| Compression | **standard** — jamais « haute compression » | 🔴 voir l'avertissement ci-dessous |
| Objet du mail | préfixe fixe, ex. `BL-RECEPTION` | facilite le diagnostic |
| **Un fichier par travail** | **activé** | un scan = un BL, pas un fichier par page |

## 🔴 Avertissement : la compression peut altérer les chiffres

Les modes « haute compression » de certains multifonctions utilisent une
compression **JBIG2 avec correspondance de motifs** : le scanner reconnaît des
formes similaires et les remplace par un motif de référence commun. Sur du
texte imprimé, cela a produit des **substitutions silencieuses de chiffres** —
un incident documenté et de grande ampleur sur certains parcs.

Pour des numéros de BL, ce serait catastrophique : le document est visuellement
parfait, mais `8402002398` est devenu `8402002396`. Ni le modèle ni le
gestionnaire ne le verront, et le rapprochement DESADV échouera sans qu'on
comprenne pourquoi.

**Test à faire avant la mise en service** : scannez une page portant une série
de numéros connus, une fois en haute compression, une fois en standard, et
comparez **caractère par caractère**.

---

# 6. Points de vigilance à retenir

| Sujet | À faire |
|---|---|
| **Expiration du secret client** | Noter la date dans l'agenda. À expiration, le job échoue avec `AADSTS7000222` — sans préavis. Renouveler dans Entra ID puis mettre à jour le secret scope. |
| **Valeur vs ID du secret** | Voir §3.2 : c'est la **Valeur** qui va dans le secret scope. `AADSTS7000215` = mauvaise chaîne (ou espace parasite en fin de valeur). |
| **Cantonnement Graph** | Ne pas mettre en service sur le tenant eMotors sans `ApplicationAccessPolicy` vérifiée par `Test-ApplicationAccessPolicy`. |
| **Boîte ouverte à l'externe** | À refermer après les essais. |
| **Taille des pièces jointes** | Un BL de 25 pages en gris à 300 dpi reste modeste, mais mesurez un cas réel : Exchange plafonne souvent à 25–35 Mo. |
| **Rétention des mails** | Les dossiers `Traites` / `Rejetes` grossissent. Prévoir une règle de rétention Exchange, ou une purge périodique. |
| **Portage vers eMotors** | Refaire les §3.1 à §3.5 sur le tenant d'entreprise, puis changer 3 paramètres de job. Le code ne change pas. |

---

# 7. Ce que vous devez avoir noté avant de passer au guide 2

```
graph_tenant_id  = ________________________________  (ID de l'annuaire)
graph_client_id  = ________________________________  (ID d'application)
client secret    = ________________________________  (à mettre dans Databricks)
boite_scan       = scans-bl@______________.onmicrosoft.com
alias achat      = reception        (fragment cherché dans le destinataire)
alias vente      = expedition
expéditeur du copieur = ____________________________  (liste blanche)
```

Suite dans **GUIDE.md** : Lakebase, application, jobs et paramètres.
