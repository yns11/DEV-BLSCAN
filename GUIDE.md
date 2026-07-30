# BLDEMAT-SCAN — Guide de déploiement et de fonctionnement

Dématérialisation des bordereaux de livraison (BL) **sans photo et sans saisie
au quai** : le réceptionniste scanne le bordereau sur le copieur professionnel,
qui l'envoie par mail ; un job Databricks relève la boîte, un second analyse le
scan et **crée le BL automatiquement** quand le rapprochement est sûr. Sinon, le
scan part en **validation contrôlée** dans l'app Administration.

Il n'y a **qu'une seule application** : Administration. L'app Création de BL
n'existe plus — c'est le pipeline qui crée les BL.

> **Le raccordement Microsoft 365 (abonnement, boîte de scan, Entra ID, Graph,
> copieur) fait l'objet d'un guide dédié : [GUIDE_MICROSOFT365.md](GUIDE_MICROSOFT365.md).**
> Faites-le **d'abord** : les jobs ci-dessous ont besoin du *tenant id*, du
> *client id*, du *client secret* et de l'adresse de la boîte.

---

# 1. Vue d'ensemble

```
   Copieur professionnel
   (scan → mail, 1 PDF = 1 BL)
        │
        │  scans-bl-reception@…  → ACHAT
        │  scans-bl-expedition@… → VENTE
        ▼
   Boîte Microsoft 365
        │
        │  Microsoft Graph (client_credentials, Mail.ReadWrite)
        ▼
┌──────────────────────────┐        ┌─────────────────────────────┐
│ JOB ingestion_scans      │───────▶│         LAKEBASE            │
│ toutes les 10 min        │        │   (PostgreSQL managé)       │
└──────────────────────────┘        │      schéma bl_scan         │
                                    │                             │
┌──────────────────────────┐        │  scans_recus  (file)        │
│ JOB traitement_scans     │◀──────▶│  suivi_bl     (BL créés)    │
│ toutes les 10 min (+5)   │        │  pieces_jointes_bl (pages)  │
└───────┬──────────────────┘        └──────────┬──────────────────┘
        │ IA vision                            │
        ▼                                      │
┌──────────────────────┐                       │
│ Endpoint model       │      ┌────────────────┴────────────┐
│ serving (Claude)     │◀─────┤   App ADMINISTRATION DES BL │
└──────────────────────┘      │  • Scans à valider          │
                              │  • Scans reçus (pilotage)   │
        carte Teams ◀─────────┤  • BL, DESADV, rapports…    │
                              └──────────┬──────────────────┘
                                         ▲ jobs quotidiens
                              ┌──────────┴──────────────────┐
                              │ sync_referentiels_erp       │
                              │ rapports_activite           │
                              │ maintenance                 │
                              └─────────────────────────────┘
```

| Composant | Rôle |
|---|---|
| **Copieur** | Scanne le BL (1 à 25 pages) et l'envoie en PDF à l'alias correspondant au sens du flux. |
| **Job `ingestion_scans`** | Relève la boîte via Graph, contrôle (expéditeur, alias, `%PDF-`, taille, pages) et dépose dans `scans_recus`. **Aucune analyse.** |
| **Job `traitement_scans`** | Rasterise, appelle le modèle sur **toutes les pages ensemble**, applique la règle de décision : création automatique **ou** mise en validation. |
| **App Administration** | Validation contrôlée des scans douteux, pilotage du pipeline, tableau de bord, rapports PDF, vues BL/DESADV/Rapprochement, référentiels, rôles. |
| **Lakebase** | File de scans (PDF en BYTEA), BL, pages, audit, rapports — schéma `bl_scan`. |
| **Model serving** | Modèle vision : lecture des scans **et** analyse rédigée des rapports. |

## Ce qui change par rapport à la solution d'origine (`bl_demat`)

| | Solution d'origine | Cette solution |
|---|---|---|
| Numérisation | photo smartphone, app Création | **scan copieur → mail** |
| Saisie | opérateur au quai, 4 étapes | **automatique**, ou validation d'un cas douteux |
| Applications | Création + Administration | **Administration seule** |
| Schéma Lakebase | `bl_demat` | **`bl_scan`** (les deux cohabitent sans conflit) |
| Secret | aucun | **1** : le client secret Entra ID, en *secret scope* |
| Vues BL | tous les BL, y compris saisis | **uniquement des BL déjà traités** (auto ou validés) |
| Traçabilité | `saisie_par` | `saisie_par` **+ `suivi_bl.origine`** (`SCAN_AUTO` / `SCAN_VALIDE` / `MANUEL`) |

Le schéma étant distinct, vous pouvez faire tourner **les deux solutions en
parallèle** sur le même projet Lakebase pendant la période d'essai.

---

# 2. Déploiement pas à pas

Prérequis : le [guide Microsoft 365](GUIDE_MICROSOFT365.md) est terminé et vous
avez sous la main les 4 valeurs de sa fiche de relevé (§7) — *tenant id*,
*client id*, *client secret*, *adresse de la boîte*.

## Étape 1 — Créer le projet Lakebase

Databricks ▸ **Compute / Database (Lakebase)** ▸ *Create project*, par exemple
`demat-bl`. Noter le **PGHOST** (onglet *Connection details*) : il servira aux
quatre jobs.

> Si vous exploitez déjà la solution d'origine, **réutilisez le même projet** :
> le schéma `bl_scan` s'ajoute à côté de `bl_demat`.

## Étape 2 — Créer le schéma et les tables

Ouvrir l'**éditeur SQL du projet Lakebase** (⚠️ pas l'éditeur SQL Spark) sur la
branche `production`, puis exécuter **un seul fichier** :

```
sql/migrations/V001__baseline_scan.sql
```

C'est une baseline consolidée (les `V002` à `V004` de la solution d'origine y
sont intégrées). Elle est **idempotente** : ré-exécutable sans risque.

Elle crée le schéma `bl_scan` et, en plus des tables métier habituelles :

| Objet | Rôle |
|---|---|
| `scans_recus` | File d'attente des scans : PDF source, métadonnées du mail, statut, extraction IA, motif de validation, verrou. |
| `suivi_bl.origine` | `SCAN_AUTO` (créé par le pipeline), `SCAN_VALIDE` (validé par un gestionnaire), `MANUEL` (repli). |

Pour un autre nom de schéma : rechercher/remplacer `bl_scan` dans le fichier et
aligner `BL_PG_SCHEMA` dans `app.yaml` **et** le paramètre `pg_schema` des
quatre jobs.

## Étape 3 — Créer le secret scope

Le client secret Entra ID est le **seul** secret de la solution. Il n'a rien à
faire dans un `app.yaml` (l'app ne lit jamais la boîte mail : seuls les jobs le
font).

```bash
databricks secrets create-scope bldemat_scan
databricks secrets put-secret bldemat_scan graph_client_secret
# … coller la valeur du secret Entra ID quand l'éditeur s'ouvre
```

Vérifier :

```bash
databricks secrets list-secrets bldemat_scan
```

> Notez dès maintenant la **date d'expiration** du secret (24 mois maximum côté
> Entra ID) dans votre agenda : à l'expiration, l'ingestion s'arrête
> silencieusement côté Graph. C'est le principal point de fragilité de la
> chaîne — la vue **Scans reçus** affiche une alerte quand aucun scan n'est
> arrivé depuis plus de 8 heures.

## Étape 4 — Créer l'application

Compute ▸ **Apps** ▸ *Create app* (app personnalisée), une seule fois :
`bl-administration-scan`.

Onglet **Edit ▸ Resources ▸ + Add resource** :

| Ressource | Paramètres |
|---|---|
| **Database** | projet Lakebase, branche `production`, base `databricks_postgres`, permission **Can connect and create**, clé **`postgres`** |
| **Serving endpoint** | le modèle vision (ex. `databricks-claude-opus-4-8`), permission **Can query** |

> Les variables `PGHOST`, `PGDATABASE`, `PGUSER`… sont alors injectées
> automatiquement : **ne pas** les écrire dans `app.yaml`.

L'endpoint de model serving est nécessaire **à l'app aussi** : l'écran de
validation propose « 🔄 Relancer l'analyse IA », et les rapports d'activité
comportent une analyse rédigée.

## Étape 5 — Déployer le code

Déployer le dossier `src/app_administration` (il est autonome : il embarque sa
copie de `bl_core`).

> Après toute modification de `shared/bl_core`, resynchroniser la copie :
> `cp shared/bl_core/*.py src/app_administration/bl_core/`

## Étape 6 — Accorder les droits SQL

### 6.1 Service principal de l'app

Récupérer le **client ID du service principal** de l'app (page de l'app ▸ onglet
*Authorization*), puis dans l'éditeur SQL Lakebase :

```sql
GRANT USAGE ON SCHEMA bl_scan TO "<SP_APP_ADMINISTRATION>";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA bl_scan
  TO "<SP_APP_ADMINISTRATION>";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bl_scan
  TO "<SP_APP_ADMINISTRATION>";
```

### 6.2 Identité qui exécute les jobs

Les jobs se connectent à Lakebase avec l'**identité qui exécute le job** — son
*Run as* (Job ▸ ⋯ ▸ *Edit permissions* / *Run as*). C'est **pour cette identité**
que le jeton OAuth est frappé ; c'est donc **elle**, et non un service principal
saisi à la main, qui doit posséder un rôle Postgres et des droits.

1. **Autoriser la connexion** : Compute ▸ **Database instances** ▸ votre
   instance ▸ *Permissions* ▸ ajouter l'identité *Run as* avec **Can connect**.
   Sans cela, aucun rôle Postgres n'est provisionné et la connexion échoue avec
   *`OAuth: User is not authorized`*.
2. **Retrouver le nom du rôle** — l'**e-mail** pour un utilisateur, l'**ID
   d'application** pour un service principal :

   ```sql
   SELECT rolname FROM pg_roles ORDER BY rolname;   -- après la 1re connexion
   ```

3. **Accorder les droits** au rôle `<RUN_AS>` :

   ```sql
   GRANT USAGE ON SCHEMA bl_scan TO "<RUN_AS>";

   -- Pipeline de scan : les deux jobs écrivent dans la file et créent des BL.
   GRANT SELECT, INSERT, UPDATE ON bl_scan.scans_recus, bl_scan.suivi_bl,
     bl_scan.pieces_jointes_bl, bl_scan.audit_bl, bl_scan.notifications,
     bl_scan.qualite_extraction TO "<RUN_AS>";

   -- Référentiels lus par la décision (DESADV, tiers, PLA, portefeuilles).
   GRANT SELECT ON bl_scan.base_tiers, bl_scan.base_desadv, bl_scan.pla,
     bl_scan.quais, bl_scan.adresses, bl_scan.sites_logistiques,
     bl_scan.portefeuilles, bl_scan.gestionnaires TO "<RUN_AS>";

   -- Sync ERP, rapports, maintenance.
   GRANT SELECT, INSERT, UPDATE ON bl_scan.base_tiers, bl_scan.base_desadv,
     bl_scan.job_executions, bl_scan.rapports_activite TO "<RUN_AS>";

   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA bl_scan TO "<RUN_AS>";
   ```

4. **Autoriser la lecture du secret scope** par le *Run as* :

   ```bash
   databricks secrets put-acl bldemat_scan <RUN_AS> READ
   ```

> Le paramètre `pg_user` des jobs est **facultatif** : laissé vide, il prend
> automatiquement l'identité *Run as* (le job appelle `current_user.me()`), ce
> qui garantit que le nom d'utilisateur et le jeton désignent la même personne.

## Étape 7 — Déposer le code des jobs

Dans l'espace de travail, un dossier par exemple `/Workspace/Shared/bldemat-scan/`
contenant :

```
common.py                 ← obligatoire, à côté des scripts
ingestion_scans.py
traitement_scans.py
sync_referentiels_erp.py
rapports_activite.py
maintenance.py
simulation_donnees.py
bl_core/                  ← copie de shared/bl_core (tous les jobs sauf sync)
```

`cache.py` bascule automatiquement sur `functools.lru_cache` hors application.
Aucune logique métier n'est dupliquée entre l'app et les jobs — c'est **le
même** `decision.py` et le même `extraction.py`.

> 🔴 **Redéployez toujours `bl_core/` en ENTIER.** Ne remplacez jamais quelques
> fichiers isolés : les modules se supposent mutuellement à la même version, et
> un mélange produit des `AttributeError` déroutants, très loin de leur cause
> (un `extraction.py` d'une version antérieure fait échouer `traitement_scans`
> sur `Referentiel`, sans que rien ne désigne le déploiement).
>
> Les jobs contrôlent d'ailleurs cette cohérence au démarrage
> (`common.verifier_bl_core`) : une copie dépareillée échoue immédiatement avec
> un message qui nomme le fichier et le symbole manquants, avant tout
> traitement.

## Étape 8 — Créer les jobs

Lakeflow Jobs ▸ *Create job* ▸ tâche **Python script** sur **serverless** :

| Script | Planification | Rôle |
|---|---|---|
| `jobs/ingestion_scans.py` | **`*/10 * * * *`** (toutes les 10 min) | Relève la boîte de scan → `scans_recus` |
| `jobs/traitement_scans.py` | **`5-59/10 * * * *`** (décalé de 5 min) | Analyse IA + décision + création |
| `jobs/sync_referentiels_erp.py` | quotidien 05h30 | Tiers et DESADV depuis l'ERP |
| `jobs/rapports_activite.py` | quotidien 03h30 | Rapports PDF échus |
| `jobs/maintenance.py` | quotidien 04h00 | Verrous, analyses interrompues, purge PDF |

Avec 15 scans/jour en moyenne et une tolérance de 20 à 30 minutes, ce rythme
laisse une très large marge : un scan est traité en **15 minutes au pire**
(10 min d'attente d'ingestion + 5 min de décalage).

### Paramètres de `ingestion_scans`

| Key | Valeur |
|---|---|
| `pg_host` | *(PGHOST du projet Lakebase — **obligatoire**)* |
| `pg_database` | `databricks_postgres` |
| `pg_schema` | `bl_scan` |
| `lakebase_endpoint` | *(facultatif — déduit de `pg_host`)* |
| `pg_user` | *(facultatif — le Run as par défaut)* |
| `graph_tenant_id` | **obligatoire** — *Directory (tenant) ID* |
| `graph_client_id` | **obligatoire** — *Application (client) ID* |
| `secret_scope` | `bldemat_scan` |
| `secret_cle_client` | `graph_client_secret` |
| `boite_scan` | **obligatoire** — ex. `scans-bl@votredomaine.onmicrosoft.com` |
| `dossier_traites` | `Traites` (créé automatiquement s'il manque) |
| `dossier_rejetes` | `Rejetes` |
| `alias_achat` | `reception` |
| `alias_vente` | `expedition` |
| `expediteurs_autorises` | *(vide = pas de filtre)* ; CSV, ex. `copieur-hall@emotors.com` |
| `max_messages` | `50` par relève |
| `max_octets_pdf` | `41943040` (40 Mo) |
| `max_pages` | `25` |

### Paramètres de `traitement_scans`

| Key | Valeur |
|---|---|
| `pg_host`, `pg_database`, `pg_schema`, `lakebase_endpoint`, `pg_user` | *(comme ci-dessus)* |
| `llm_endpoint` | `databricks-claude-opus-4-8` — **vide = tout part en validation** |
| `teams_webhook_reception` | URL du flux Teams (facultatif) |
| `auto_sans_desadv` | `false` — voir §4 |
| `max_scans` | `40` scans par exécution |
| `dpi` | `150` (72–400) |

### Paramètres de `maintenance`

| Key | Valeur |
|---|---|
| `draft_hours` | `24` |
| `stale_job_hours` | `6` |
| `scan_verrou_heures` | `4` — libère les prises en charge oubliées |
| `retention_pdf_jours` | `180` — purge du PDF source des scans terminés |

Les autres jobs sont inchangés par rapport à la solution d'origine :
détails dans [`jobs/README.md`](jobs/README.md).

### Dépendances des tâches serverless

```
psycopg[binary]==3.2.3
psycopg-pool==3.2.3
databricks-sdk>=0.81.0
pymupdf==1.24.14        # ingestion_scans, traitement_scans
pillow                  # traitement_scans
pandas==2.2.3           # rapports_activite
fpdf2==2.8.7            # rapports_activite
```

Aucune dépendance Microsoft n'est nécessaire : `bl_core/graph_mail.py`
n'utilise que `urllib` de la bibliothèque standard (pas de `msal`, pas de
`requests`).

## Étape 9 — Renseigner l'`app.yaml`

| Variable | Valeur |
|---|---|
| `BL_ENVIRONMENT` | `local`, `dev`, `rec`, `pre-prod` ou `prod` |
| `BL_RBAC_MODE` | `strict` |
| `BL_BOOTSTRAP_ADMINS` | votre e-mail (secours au premier démarrage) |
| `BL_PG_SCHEMA` | `bl_scan` |
| `BL_LLM_ENDPOINT` | nom de l'endpoint, ou vide pour désactiver l'IA |
| `BL_SCAN_MAX_PAGES` | `25` |
| `BL_SCAN_PAGES_AVANCE` | `1` |
| `BL_SCAN_AUTO_SANS_DESADV` | `false` |
| `BL_SCAN_ALIAS_ACHAT` / `_VENTE` | `reception` / `expedition` — **doivent différer** |
| `BL_SCAN_VERROU_MINUTES` | `60` |
| `BL_SCAN_RETENTION_PDF_JOURS` | `180` |
| `BL_TEAMS_WEBHOOK_RECEPTION` | URL du flux Teams |
| `BL_TEAMS_WEBHOOK_EDI` | URL du flux Teams (la même convient) |

Les valeurs `BL_SCAN_*` de l'app doivent rester **cohérentes** avec les
paramètres des jobs (`alias_achat`/`alias_vente`, `max_pages`,
`auto_sans_desadv`) : l'app les utilise pour l'affichage et la validation, les
jobs pour la décision. Puis **Deploy**.

## Étape 10 — Paramétrer les accès et les référentiels

1. Page de l'app ▸ **Permissions** ▸ ajouter les utilisateurs/groupes
   (**Can use**). C'est le 1ᵉʳ niveau : qui peut *ouvrir* l'app.
2. Ouvrir l'app (vous êtes admin via `BL_BOOTSTRAP_ADMINS`) ▸ **Gestion ▸
   Rôles** : attribuer les rôles (§6). **S'attribuer ADMIN_METIER en premier**,
   puis vider `BL_BOOTSTRAP_ADMINS` et redéployer.
3. **Gestion ▸ Gestionnaires** : nom affiché + **e-mail Microsoft 365** (UPN),
   indispensable aux mentions Teams.
4. **Gestion ▸ Portefeuilles** : associer chaque gestionnaire à ses
   fournisseurs — c'est ce lien qui détermine **qui est mentionné**.
5. **Gestion ▸ PLA** : quai par fournisseur — le pipeline s'en sert pour
   renseigner le quai **automatiquement** (§4).
6. Lancer une première fois `sync_referentiels_erp` : sans référentiel tiers ni
   DESADV, **aucun** scan ne pourra être créé automatiquement.

## Étape 11 — Le flux Teams (notifications)

Identique à la solution d'origine. En résumé :

1. Teams ▸ canal cible ▸ **+** ▸ **Workflows** ▸ modèle « Envoyez des alertes
   webhook à un canal » ▸ copier l'**URL de webhook**.
2. La coller dans `BL_TEAMS_WEBHOOK_RECEPTION` (app) **et** dans le paramètre
   `teams_webhook_reception` du job `traitement_scans` — le job notifie les
   créations automatiques, l'app notifie les créations après validation.
3. **Ajouter au canal tous les gestionnaires** susceptibles d'être mentionnés :
   une mention ne notifie que si la personne est membre.

### Rendre les mentions cliquables

**Point clé** : écrire soi-même `msteams.entities` dans une carte envoyée par
Power Automate **ne fonctionne pas** — le Flow bot rejette la carte avec « One
or more mention entity could not be found in card text ». La seule méthode
fiable est l'action Teams **« Obtenir un jeton @mention pour un utilisateur »**.

Cette action **échoue si la personne n'est pas membre de l'équipe**, et son
échec fait échouer tout le flux — donc aussi la notification. Le flux ci-dessous
évite ce piège : il **liste les membres réels de l'équipe** et ne demande un
jeton que pour ceux qui figurent dans les e-mails envoyés par l'application. Un
gestionnaire absent du canal est simplement ignoré ; la carte part quand même.

L'application et le job envoient, à la racine de la charge utile, un tableau
`mentions` contenant les e-mails **en minuscules**, et placent le marqueur
`{{MENTIONS}}` dans la carte :

```json
{
  "type": "message",
  "mentions": ["marie.durand@emotors.com", "paul.martin@emotors.com"],
  "attachments": [{ "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": { "...": "… Gestionnaire(s) : {{MENTIONS}} …" } }]
}
```

`mentions` est **toujours** présent — vide pour la MessageCard « EDI NOK → OK »
ou pour une réception sans gestionnaire.

#### Convention préalable : renommer les actions

Les expressions référencent les actions par leur nom, apostrophes doublées et
espaces remplacés par `_` : `body('Répertorier_les_membres_de_l''équipe')` est
illisible et source d'erreurs. **Renommez chaque action ajoutée** (⋯ ▸
*Renommer*) avec les noms ASCII utilisés ci-dessous.

#### Les 6 actions à ajouter

Power Automate ▸ Mes flux ▸ *Envoyer des alertes webhook à …* ▸ **Modifier**.
Les actions 1 à 5 se placent **après le déclencheur et avant** « Publier une
carte dans un chat ou un canal ».

**1. Initialiser une variable** — nom `JetonsMentions`, type **Chaîne**, valeur
vide.

**2. Teams ▸ « Répertorier les membres de l'équipe »** — renommer `Membres`,
équipe = celle qui contient le canal.

> Sortie utile : `body('Membres')?['value']`, tableau d'objets contenant
> `displayName`, `userPrincipalName`, `email`, `userId`. Faites un premier
> **Test** et regardez la sortie brute pour confirmer les noms de champs de
> votre tenant — les expressions ci-dessous utilisent un `coalesce` qui accepte
> `userPrincipalName` **ou** `email`.

**3. « Filtrer un tableau »** — renommer `Gestionnaires`, *De* =
`body('Membres')?['value']`. Condition en **mode avancé** :

```
@contains(
  coalesce(triggerBody()?['mentions'], createArray()),
  toLower(coalesce(item()?['userPrincipalName'], item()?['email'], ''))
)
```

> `toLower` est indispensable : `contains` est sensible à la casse et Teams
> renvoie souvent l'UPN avec des majuscules. C'est pour cela que l'application
> envoie les e-mails déjà en minuscules. Le `coalesce` sur
> `triggerBody()?['mentions']` évite l'erreur *« expression … is of type
> 'Null' »* sur les charges sans mentions.

**4. « Appliquer à chacun »** — renommer `PourChaqueGestionnaire`, sortie =
`body('Gestionnaires')`.

> La sortie d'un *Filtrer un tableau* **est** le tableau : pas de `?['value']`
> ici. Un tableau vide fait simplement zéro itération.

**5a. Dans la boucle — Teams ▸ « Obtenir un jeton @mention pour un
utilisateur »** — renommer `Jeton`, utilisateur =
`coalesce(item()?['userPrincipalName'], item()?['email'])`.

**5b. Toujours dans la boucle — « Ajouter à la variable chaîne »** —
`JetonsMentions`, valeur `concat(outputs('Jeton')?['body/atMention'], ' ')`.

**6. Dans « Publier une carte »**, remplacer le *Corps du message* par :

```
json(replace(string(item()?['content']), '{{MENTIONS}}', trim(variables('JetonsMentions'))))
```

> Dans la branche « si les pièces jointes sont nulles » (MessageCard EDI) :
> `json(replace(string(variables('Body')), '{{MENTIONS}}', trim(variables('JetonsMentions'))))`
> — cette carte ne contient pas le marqueur, le `replace` est donc sans effet.

#### Ordre final du flux

```
Déclencheur : requête webhook Teams
├─ Initialiser JetonsMentions (chaîne, vide)
├─ Membres            → Répertorier les membres de l'équipe
├─ Gestionnaires      → Filtrer un tableau (membres ∩ mentions)
├─ PourChaqueGestionnaire (sur body('Gestionnaires'))
│   ├─ Jeton          → Obtenir un jeton @mention
│   └─ Ajouter à JetonsMentions : concat(jeton, ' ')
└─ Publier une carte  → {{MENTIONS}} remplacé par JetonsMentions
```

Si vous ne pouvez pas modifier le flux : passer `BL_TEAMS_MENTION_MODE` à
`texte` — les gestionnaires sont cités en clair, sans notification personnelle.

## Étape 12 — Premier essai de bout en bout

Dans cet ordre — chaque étape se vérifie avant de passer à la suivante :

1. **Accès Graph.** Lancer `ingestion_scans` **à la main** sur une boîte vide.
   Le log doit afficher `Relève terminée : {"messages": 0, …}`. Une erreur ici
   est un problème de secret, de permission Graph ou d'`ApplicationAccessPolicy`
   (voir GUIDE_MICROSOFT365 §3).
2. **Un mail de test.** S'envoyer à soi-même, depuis n'importe quelle boîte, un
   mail vers `…-reception@…` avec un PDF de BL en pièce jointe. Relancer
   `ingestion_scans`. Attendu : `{"messages": 1, "scans": 1}`, le mail passe en
   *lu* et arrive dans le dossier `Traites`.
3. **La file.** App ▸ **Pipeline ▸ Scans reçus** : une ligne au statut `RECU`.
4. **L'analyse.** Lancer `traitement_scans` à la main. Le log indique soit
   `BL … créé automatiquement (confiance desadv)`, soit `en validation
   humaine : <motif>`.
5. **La décision.** Selon le cas : le BL apparaît dans **Achat ▸ BL réception**
   (origine `SCAN_AUTO`), **ou** le scan apparaît dans **Achat ▸ Scans à
   valider (achat)**.
6. **La validation.** Prendre le scan en charge, vérifier que les champs sont
   pré-remplis, valider. Le BL est créé avec l'origine `SCAN_VALIDE`.
7. **La notification.** Vérifier la carte dans le canal Teams et le caractère
   cliquable de la mention.

Ensuite seulement : activer les planifications, et faire scanner un vrai BL
depuis le copieur.

---

# 3. Le pipeline en détail

## 3.1 Ingestion (`ingestion_scans.py`)

```
Graph : messages non lus AVEC pièce jointe, du plus ancien au plus récent
   │
   ├─ expéditeur dans la liste blanche ?        sinon → Rejetes
   ├─ alias destinataire reconnu → sens ACHAT/VENTE ?  sinon → Rejetes
   ├─ pièce jointe .pdf de moins de 40 Mo ?     sinon → Rejetes
   ├─ octets d'en-tête = « %PDF- » ?            sinon → écarté
   ├─ nombre de pages ≤ 25 ?                    sinon → écarté
   │
   ├─▶ INSERT dans scans_recus (statut RECU)
   └─▶ ensuite seulement : marquer lu + déplacer le mail
```

**Idempotence.** `scans_recus.message_id` est UNIQUE et porte
l'`Internet-Message-Id` du mail. Un mail livré deux fois, une relève interrompue
avant le rangement, ou un job relancé ne produisent qu'**une seule** ligne. Le
mail n'est rangé qu'**après** l'insertion : en cas de plantage entre les deux, la
relève suivante le retrouve et l'idempotence absorbe le doublon.

Un mail portant **plusieurs** PDF donne plusieurs scans, avec des clés dérivées
(`<message-id>#0`, `#1`…).

**Un scan écarté ne fait jamais échouer la tâche** : les motifs sont journalisés
en `WARNING`. Une relève par ailleurs réussie ne doit pas être masquée par un
PDF mal formé.

## 3.2 Analyse et décision (`traitement_scans.py`)

```
prendre_scans_a_analyser()   ← FOR UPDATE SKIP LOCKED, statut RECU → EN_ANALYSE
   │
   ├─ 1. rasterisation du PDF (1 à 25 pages), 150 dpi
   ├─ 2. extraction IA sur TOUTES les pages ensemble
   ├─ 3. decision.decider(…)
   │       ├─ automatique → BL créé en UNE transaction + notification Teams
   │       └─ à valider   → statut A_VALIDER + motif + extraction conservée
   └─ 4. cloturer_scan(…)
```

Un scan est **un** bordereau : toutes ses pages sont envoyées au modèle dans le
même appel, exactement comme un BL photographié en plusieurs prises dans la
solution d'origine. C'est `extraction.extraire_infos_bl`, **la même fonction**,
avec ses passes de raffinement (réinterrogation avec la liste des tiers si le
tiers n'est pas reconnu, puis avec les BL du tiers reconnu).

**Isolement des erreurs.** Chaque scan est traité indépendamment : une
exception le fait passer en `ERREUR` (visible et rejouable) sans toucher aux
autres. La tâche n'échoue que si **tous** les scans du lot ont échoué — signe
d'un problème systémique (endpoint indisponible, droits) plutôt que d'un scan
mal formé isolé.

`FOR UPDATE SKIP LOCKED` garantit que deux exécutions simultanées ne traitent
jamais le même scan.

## 3.3 La règle de décision

**Principe directeur : un BL créé à tort coûte plus cher qu'un BL à valider.**
Une création erronée pollue le rapprochement DESADV, fausse les indicateurs et
se corrige à la main ; une validation de trop coûte trente secondes. La règle
est donc volontairement **conservatrice** : en cas de doute, on demande.

Contrôles bloquants d'abord — dans ces trois cas, **jamais** de création :

| Situation | Pourquoi |
|---|---|
| Numéro de BL illisible | rien à rapprocher |
| Numéro déjà présent en base | doublon probable, ou erreur de lecture |
| Contrôle d'unicité impossible (base injoignable) | on ne crée rien à l'aveugle |

Puis, par ordre de confiance décroissante :

| Confiance | Condition | Création automatique |
|---|---|---|
| **`desadv`** | le numéro lu correspond à un **avis d'expédition actif** de l'ERP pour ce sens | ✅ **oui** |
| **`code`** | pas de DESADV, mais un **code tiers** (`S-000000` / `C-000000`) reconnu au référentiel | ⚠️ seulement si `BL_SCAN_AUTO_SANS_DESADV=true` **et** date lisible sur le scan |
| **`nom`** | rapprochement sur la **raison sociale** seule | ❌ non |
| **`aucun`** | tiers non identifié au référentiel | ❌ non |

Le niveau `desadv` est le seul signal réellement fort : c'est un recoupement
avec un **système tiers**. Dans ce cas le tiers est pris **dans le DESADV**, pas
dans la lecture du modèle — l'ERP se trompe moins que l'OCR. Si les deux
divergent, l'ERP gagne mais l'écart est **enregistré et affiché** au
gestionnaire (`Le scan indique « X », l'avis d'expédition « Y »`).

Un code tiers est bien plus robuste qu'une raison sociale — peu de caractères,
format contraint — mais ne prouve rien sur le numéro. D'où le garde-fou
`BL_SCAN_AUTO_SANS_DESADV`, **désactivé par défaut** : activez-le seulement
après avoir mesuré, sur vos propres scans, le taux de créations correctes de la
voie `code` (la vue Scans reçus et la table `qualite_extraction` donnent la
matière).

Toute la règle est dans `shared/bl_core/decision.py`. Ce module ne fait **aucun
accès base ni appel réseau** : les dépendances (recherche DESADV, disponibilité
du numéro, quai PLA) sont **injectées**, ce qui le rend testable de bout en bout
— et testé.

## 3.4 Champs déduits automatiquement

Un BL créé par le pipeline a **tous** ses champs, sans intervention :

| Champ | Source |
|---|---|
| `numero_bl` | lu par le modèle |
| `nom_fournisseur` / client | **DESADV** si trouvé, sinon rapprochement au référentiel |
| `type_operation` | l'**alias destinataire** du mail (`reception` → RECEPTION, `expedition` → EXPEDITION) |
| `date_reception` | date lue sur le BL ; à défaut, **date d'arrivée du mail** |
| `plage_horaire` | **heure d'arrivée du mail** — le réceptionniste scanne au moment de la réception, c'est un bien meilleur estimateur qu'une valeur par défaut |
| `quai_reception` | **PLA du tiers** (Gestion ▸ PLA), sinon quai par défaut |
| `statut_bl` | `0` (EDI NOK) si le modèle a lu une mention d'anomalie, `1` sinon |
| `comment_bl` | commentaire lu sur le BL |
| `origine` | `SCAN_AUTO` |
| `saisie_par` | `pipeline:scan` |
| pages | images rasterisées du PDF, une par page |

L'acteur `pipeline:scan` distingue sans ambiguïté, dans l'audit et dans les
rapports, ce qui vient du pipeline de ce qui vient d'un humain.

## 3.5 Cycle de vie d'un scan

```
RECU ──▶ EN_ANALYSE ──┬──▶ TRAITE_AUTO  (BL créé, id_bl renseigné)
  ▲                   │
  │                   ├──▶ A_VALIDER ──┬──▶ VALIDE  (BL créé après contrôle)
  │                   │                └──▶ REJETE  (motif obligatoire)
  └───────────────────┴──▶ ERREUR  (rejouable)
     (maintenance relance
      les analyses interrompues)
```

Le PDF source est conservé `BL_SCAN_RETENTION_PDF_JOURS` jours (180 par défaut)
puis purgé par la maintenance. La **ligne** de `scans_recus` reste, ainsi que
les images du BL : la traçabilité est intacte, seul l'octet lourd disparaît.

---

# 4. Les écrans de l'application

## 4.1 Scans à valider (achat / vente)

Le cœur de la validation contrôlée. Deux vues distinctes, une par sens et par
rôle :

| Vue | Objet | Rôles |
|---|---|---|
| **Achat ▸ Scans à valider (achat)** | réceptions | APPROS, ADMIN_METIER |
| **Vente ▸ Scans à valider (vente)** | expéditions | ADV, ADMIN_METIER |

Sur ces deux vues, le fil d'Ariane cède la place à un titre compact : la hauteur
d'écran gagnée va à l'image, qui occupe **70 %** de la largeur utile.

**File d'attente.** Une grille des scans `A_VALIDER` : fichier, expéditeur,
nombre de pages, date de réception, confiance, motif. Le bouton **« Prendre en
charge »** pose un **verrou** (`BL_SCAN_VERROU_MINUTES`, 60 min) : deux
gestionnaires ne travaillent jamais sur le même scan. Quitter la vue libère le
verrou ; un verrou oublié est libéré par la maintenance après
`scan_verrou_heures`.

**Écran de validation.** À droite, les pages du scan dans une visionneuse
offrant **zoom, ajustement et rotation**, tous **côté navigateur** — zoomer ne
relance pas le script et ne fait donc rien perdre de la saisie en cours. À
gauche, les champs **déjà pré-remplis** avec l'extraction faite par le job : le
modèle n'est pas rappelé à l'ouverture, l'écran s'affiche instantanément. Le
motif de la mise en validation est rappelé en haut.

Quatre actions :

| Action | Effet |
|---|---|
| **✅ Valider et créer le BL** | BL créé avec l'origine `SCAN_VALIDE` + notification |
| **🔄 Relancer l'analyse IA** | rappelle le modèle (utile après une mise à jour du référentiel) |
| **🚫 Écarter ce scan** | statut `REJETE`, **motif obligatoire** |
| **↩️ Libérer** | rend le scan à la file sans décision |

Le numéro saisi est contrôlé **en direct** contre la base : un doublon est
signalé avant l'enregistrement, pas après.

## 4.2 Pipeline ▸ Scans reçus

Le tableau de bord du pipeline, accessible en lecture à tous les rôles métier :

- **KPI** : scans reçus, créés automatiquement, à valider, validés, rejetés, en
  erreur ;
- **taux d'automatisation** — la mesure qui décide si `BL_SCAN_AUTO_SANS_DESADV`
  mérite d'être activé ;
- **« Dernier scan »**, en heures. **Au-delà de 8 h, une alerte s'affiche.**

> Ce dernier indicateur mérite l'attention : le mode de défaillance le plus
> dangereux de cette architecture est le **pipeline muet**. Un secret expiré,
> une règle de boîte mal placée, un copieur reconfiguré — et plus rien
> n'arrive, sans aucune erreur nulle part. Personne ne s'en aperçoit tant qu'un
> BL n'est pas réclamé. Regardez cette valeur chaque matin.

## 4.3 Dépôt manuel d'un PDF

Bouton **« 📎 Déposer un scan »** dans la vue Scans reçus. Le repli quand la
chaîne mail est indisponible — copieur en panne, boîte saturée, secret expiré —
ou pour un BL arrivé par un autre canal (mail direct d'un fournisseur, PDF
retrouvé).

Le PDF déposé entre dans **exactement la même file** que les scans du copieur :
même analyse, même règle de décision, même traçabilité. La clé d'idempotence
est le **`sha256` du contenu** : déposer deux fois le même fichier ne crée
qu'un scan.

## 4.4 Les autres vues

Inchangées par rapport à la solution d'origine : tableau de bord, rapports
d'activité PDF (journalier, hebdo, mensuel, trimestriel, annuel), vues BL achat
et vente, DESADV, rapprochement BL/DESADV, référentiels, audit, qualité de
l'extraction, gestion des rôles, gestionnaires, portefeuilles, PLA,
notifications.

**Une différence importante** : les vues BL ne contiennent **que des BL déjà
traités** — créés automatiquement ou validés. Il n'y a plus de saisie manuelle
de BL, donc plus de brouillon en cours de saisie.

---

# 5. Notifications Teams

## 5.1 Nouvelle réception

```
BL de réception créé (par le job OU après validation)
        │
        ├─▶ 1. BL + pages enregistrés dans Lakebase       (transaction)
        ├─▶ 2. Ligne écrite dans « notifications »        (trace, fait foi)
        ├─▶ 3. Gestionnaires du portefeuille du fournisseur → e-mails
        └─▶ 4. POST de la carte adaptative au flux Teams  (best effort)
                 └─ succès → envoyee = true
                 └─ échec  → erreur_envoi renseignée, BL conservé
```

La carte affiche : numéro de BL, fournisseur, quai, date + plage horaire, état,
nombre de pages, auteur (`pipeline:scan` ou l'e-mail du valideur), et
**@mentionne** les gestionnaires concernés.

**Règle importante** : une indisponibilité de Teams **n'annule jamais** la
création du BL. La trace reste consultable dans **Gestion ▸ Notifications**
(colonne « Erreur »).

Seules les **réceptions** déclenchent cette carte, pas les expéditions.

## 5.2 Passage EDI NOK → OK

Depuis la fiche BL (bouton ✏️ Modifier) ou par action de masse (✅ Passer à OK).
Un champ « Commentaire pour la notification Teams (facultatif) » apparaît dans
la fenêtre de confirmation ; son contenu est ajouté à la carte.

## 5.3 Sans notification configurée

Si l'URL de webhook est vide, tout fonctionne normalement : les événements sont
journalisés en base, simplement pas publiés dans Teams.

---

# 6. Rôles et droits (RBAC)

| Rôle | Droits |
|---|---|
| **APPROS** | **Scans à valider (achat)** en modification ; BL réception (modification) ; DESADV achat, Rapprochement achat, Scans reçus, Notifications, Rapports d'activité (lecture) |
| **ADV** | **Scans à valider (vente)** en modification ; BL expédition (modification) ; DESADV vente, Rapprochement vente, Scans reçus, Notifications, Rapports (lecture) |
| **FINANCE** | BL, rapprochements, Scans reçus et rapports (lecture) |
| **ADMIN_METIER** | Toutes les vues, y compris le module Gestion et le dépôt manuel |

- Les vues sans droit sont **masquées** ; en lecture seule, les actions
  d'écriture disparaissent.
- La matrice est dans `shared/bl_core/rbac.py` (versionnée avec le code) ; les
  **affectations** sont en base (Gestion ▸ Rôles).
- `BL_RBAC_MODE=strict` : aucun rôle = aucun accès. Le mode `disabled` est
  refusé en `prod`.
- Le rôle **LOG** (opérateurs de quai) n'a plus d'objet : il n'y a plus d'app
  Création. Il reste dans le référentiel pour ne pas casser les affectations
  existantes, mais n'ouvre aucune vue.
- Les décisions d'autorisation ne sont **jamais mises en cache** : un rôle
  modifié dans Gestion ▸ Rôles s'applique dès la requête suivante.

---

# 7. Exploitation courante

| Situation | Où regarder / que faire |
|---|---|
| **Plus aucun scan n'arrive** | Vue **Scans reçus** ▸ « Dernier scan ». Dans l'ordre : (1) le client secret Entra ID a-t-il expiré ? (2) une règle de boîte détourne-t-elle les mails ? (3) le copieur envoie-t-il toujours au bon alias ? (4) le job `ingestion_scans` est-il en échec (log = message explicite) ? |
| Un scan reste en `RECU` | Le job `traitement_scans` ne tourne pas, ou son *Run as* n'a pas les droits. Voir son log. |
| Un scan est en `ERREUR` | Le log de `traitement_scans` porte l'exception. Causes fréquentes : endpoint de modèle indisponible, PDF corrompu, GRANT manquant. Corriger puis remettre le statut à `RECU` (`UPDATE bl_scan.scans_recus SET statut='RECU' WHERE id=…`). |
| Un scan reste en `EN_ANALYSE` | Job tué en cours de route. La **maintenance** le repasse en `RECU` automatiquement. |
| Trop de scans partent en validation | Regarder la colonne « Motif » de la file. Le plus souvent : **DESADV absent** (le job `sync_referentiels_erp` tourne-t-il ?), ou tiers absent du référentiel. Le taux d'automatisation de la vue Scans reçus mesure le progrès. |
| Un BL a été créé avec le mauvais tiers | La ligne `scans_recus` conserve l'extraction complète (`extraction` JSONB) et l'écart éventuel avec le DESADV : de quoi comprendre. Corriger le BL dans sa fiche ; l'audit garde la trace. |
| Le mauvais sens (achat/vente) | Le sens vient de l'alias visé, cherché dans le destinataire, le sujet **et** les en-têtes de routage (Exchange masquant l'alias dans le destinataire, cf. GUIDE_MICROSOFT365 §2). Vérifier le bouton du copieur puis `alias_achat`/`alias_vente`. Un mail sans alias reconnu est **rejeté**, jamais rangé au hasard. |
| `sens indéterminé` alors que le mail visait le bon alias | Exchange a réécrit le destinataire **et** l'alias n'apparaît pas non plus dans les en-têtes de ce tenant. Le log liste ce qui a été vu (destinataires, sujet, en-têtes). Solution robuste : régler le copieur pour écrire `RECEPTION` / `EXPEDITION` dans le **sujet** — le job le reconnaît aussi. |
| Un scan est bloqué « pris en charge » | La maintenance libère les verrous après `scan_verrou_heures` (4 h). Pour forcer : `UPDATE bl_scan.scans_recus SET verrouille_par=NULL, verrouille_le=NULL WHERE id=…`. |
| « … pages pour un seul BL (limite 25) » | Le copieur a scanné plusieurs bordereaux d'un coup. Rescanner séparément, ou relever `max_pages` **et** `BL_SCAN_MAX_PAGES`. |
| Des chiffres semblent faux sur le scan | ⚠️ **Compression JBIG2.** Voir GUIDE_MICROSOFT365 §5 : ce mode remplace des motifs visuellement proches, ce qui peut **substituer silencieusement des chiffres**. Configurer le copieur en JPEG/haute qualité. |
| Une notification n'est pas arrivée | Gestion ▸ **Notifications** : colonnes « Envoyée » et « Erreur ». |
| Une mention n'est pas cliquable | Le flux doit produire les jetons (§Étape 11), l'e-mail doit être l'UPN Microsoft 365, la personne doit être membre du canal. |
| Le flux échoue : *… is of type 'Null'* | Une expression boucle directement sur `triggerBody()?['mentions']`. L'envelopper dans `coalesce(…, createArray())`. |
| Le flux échoue sur *Obtenir un jeton @mention* | La personne n'est pas membre de l'équipe. Le filtre `Gestionnaires` doit s'intercaler avant la boucle. |
| L'IA ne pré-remplit plus | `BL_LLM_ENDPOINT` / `llm_endpoint` vide, ou ressource *Serving endpoint* absente / sans « Can query ». Sans modèle, le pipeline continue : tout part en validation, sans pré-remplissage. |
| « Ressource Lakebase absente » | La ressource Database n'est pas attachée à l'app (clé `postgres`). |
| Job : `OAuth: User is not authorized` | Le `user` de la connexion n'est pas l'identité qui frappe le jeton. Laisser `pg_user` **vide**, autoriser le *Run as* en **Can connect** sur l'instance Lakebase, et lui accorder les GRANT de l'étape 6.2. |
| Job : « Secret … introuvable dans le scope … » | Le message porte la commande exacte à lancer. Vérifier aussi l'ACL `READ` du *Run as* sur le scope. |
| Job : `AADSTS7000215: Invalid client secret` | Le secret scope contient l'**ID** du secret Entra ID au lieu de sa **Valeur** (le portail affiche les deux côte à côte), ou une espace/un saut de ligne s'est glissé au collage. Voir GUIDE_MICROSOFT365 §3.2 — l'erreur du job rappelle la distinction. |
| Job : `AADSTS7000222` | Le secret client a **expiré**. En créer un nouveau dans Entra ID, puis mettre à jour le secret scope. |
| Job : `InefficientFilter` | Exchange refuse la forme de la requête de listage. Le code **dégrade automatiquement** (filtre allégé, puis aucun filtre, avec filtrage côté client) : le log indique quelle stratégie a servi. Si les trois formes sont refusées, le message le dit — c'est alors à remonter, la boîte ayant un comportement inhabituel. |
| Job : `AADSTS700016` / `AADSTS900023` | `graph_client_id` et `graph_tenant_id` ne désignent pas le même annuaire, ou `graph_tenant_id` porte un ID d'application au lieu de l'ID d'annuaire. |
| Un utilisateur ne voit rien | Aucun rôle attribué (Gestion ▸ Rôles). |
| Un BL récent est introuvable | Les filtres de période portent par défaut sur la **date d'opération**. Basculer « La période porte sur » ▸ **Date de saisie**. |

**Sauvegarde** : tout est dans Lakebase (métadonnées, PDF, images). S'appuyer
sur les sauvegardes/branches du projet Lakebase.

**Volumétrie.** 15 scans/jour × 5 pages, PDF conservé 180 jours : de l'ordre de
quelques gigaoctets. La purge du PDF source par la maintenance suffit à
stabiliser la croissance ; les images du BL, plus légères, sont conservées.

---

# 8. Jeu de données de simulation

`jobs/simulation_donnees.py` génère un volume réaliste (5 000 BL par défaut) et
toutes les lignes liées, pour une démonstration ou une recette.

**Identification.** Aucune colonne technique n'a été ajoutée : toutes les lignes
générées portent le préfixe **`SIM-`** sur leur clé naturelle. La suppression
est donc un simple `LIKE` — voir `sql/simulation/supprimer_donnees_simulation.sql`
ou le job avec `action=supprimer`.

**Reproductibilité.** À graine, bornes de dates et volume identiques, le jeu
produit est exactement le même.

Paramètres détaillés : [`jobs/README.md`](jobs/README.md).

> Les BL de simulation portent l'origine `MANUEL` : ils n'altèrent donc pas le
> **taux d'automatisation** du pipeline.

---

# 9. Structure du dépôt

```
shared/bl_core/          code partagé (source de vérité)
  config.py              configuration validée (app.yaml + variables des jobs)
  database.py            pool PostgreSQL + transactions
  repository.py          accès aux données métier ET à la file de scans
  graph_mail.py          client Microsoft Graph (urllib seul, aucune dépendance)
  decision.py            règle de décision : création automatique ou validation
  scan_pdf.py            PDF -> images + préchargeur IA de la file de validation
  extraction.py          extraction IA d'un BL (toutes ses pages ensemble)
  llm.py                 appel mutualisé à l'endpoint de model serving
  teams.py               cartes Teams (adaptative + MessageCard)
  notifications.py       trace en base puis envoi (best effort)
  rbac.py                matrice des droits
  rapports.py            rapports d'activité : agrégats, analyse IA, rendu PDF
  cache.py               mémoïsation (Streamlit dans l'app, lru_cache en job)
  pdf_bl.py, ui.py, validation.py, identity.py
src/app_administration/  app + copie de bl_core + app.yaml + requirements.txt
sql/migrations/          V001__baseline_scan.sql (baseline consolidée)
sql/simulation/          suppression du jeu de données de simulation
jobs/                    ingestion, traitement, sync ERP, rapports,
                         maintenance, simulation + README
GUIDE_MICROSOFT365.md    abonnement, boîte, Entra ID, Graph, copieur
```

Python 3.11 — Streamlit 1.49.1 — Lakebase (PostgreSQL managé).
