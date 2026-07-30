# Jobs Lakeflow — BLDEMAT-SCAN

Six tâches, à créer dans l'interface Databricks (Lakeflow Jobs ▸ Create job,
tâche « Python script » sur compute **serverless**). Il n'y a **ni bundle, ni
job d'envoi de notifications** : les cartes Teams sont publiées directement au
moment de l'événement.

Les deux premières forment le **pipeline de scan** ; les autres sont reprises
sans changement de la solution d'origine.

| Script | Rôle | Planification conseillée |
|---|---|---|
| `ingestion_scans.py` | Relève la boîte de scan via **Microsoft Graph**, contrôle chaque pièce jointe (expéditeur, alias → sens, octets `%PDF-`, taille, nombre de pages) et la dépose dans `scans_recus`. Aucune analyse. | `*/10 * * * *` |
| `traitement_scans.py` | Rasterise le PDF, appelle le modèle vision sur **toutes les pages ensemble**, applique la règle de décision : création automatique du BL + notification Teams, ou mise en validation humaine avec le motif. | `5-59/10 * * * *` |
| `sync_referentiels_erp.py` | Historise le staging Delta, synchronise tiers et DESADV achat par lots (avec `statut_edi` issu de `messagestate`), gère renommages/inactivations et recalcule le rapprochement BL ⇄ DESADV dans les deux sens. | quotidienne, 05h30 |
| `rapports_activite.py` | Produit les rapports d'activité PDF **échus** (journalier de la veille, plus hebdo/mensuel/trimestriel/annuel dès qu'ils se clôturent), analyse rédigée par le modèle comprise. | quotidienne, 03h30 |
| `maintenance.py` | Brouillons interrompus, exécutions de job restées `STARTED`, **verrous de validation oubliés**, **analyses interrompues** (`EN_ANALYSE` → `RECU`) et **purge du PDF source** des scans terminés. | quotidienne, 04h00 |
| `simulation_donnees.py` | Génère ou supprime un jeu de données de simulation identifié par préfixe. **Ne pas planifier** : à lancer à la demande. | à la demande |

Le pipeline est volontairement **coupé en deux** : on peut relancer l'analyse
d'un scan sans retoucher à la boîte mail, et une panne de l'endpoint de modèle
n'empêche pas les scans d'arriver. Le décalage de 5 minutes évite que les deux
tâches se disputent la même transaction au démarrage.

Avec 10 à 30 scans par jour et une tolérance de 20 à 30 minutes entre le scan et
le traitement, ce rythme laisse une large marge : **15 minutes au pire** entre le
scan et le BL.

`common.py` est partagé par tous les scripts (connexion Lakebase, lecture des
job parameters, résolution de l'endpoint, journal d'exécution, métriques). Il
doit être déposé **dans le même dossier** que les scripts dans l'espace de
travail.

## Paramètres (job parameters)

Les scripts lisent les **job parameters** de la tâche (onglet *Parameters*,
Key/Value), via `dbutils.widgets` — pas d'`argparse`.

Ces clés sont communes à tous les scripts :

| Key | Exemple / défaut |
|---|---|
| `pg_host` | *(PGHOST du projet Lakebase — **obligatoire**)* |
| `pg_database` | `databricks_postgres` |
| `pg_schema` | `bl_scan` |
| `lakebase_endpoint` | *(facultatif — déduit de `pg_host` si vide)* |
| `pg_user` | *(facultatif — par défaut l'identité **Run as** du job)* |

**`ingestion_scans`**

| Key | Exemple / défaut |
|---|---|
| `graph_tenant_id` | **obligatoire** — *Directory (tenant) ID* Entra ID |
| `graph_client_id` | **obligatoire** — *Application (client) ID* |
| `secret_scope` | `bldemat_scan` |
| `secret_cle_client` | `graph_client_secret` |
| `boite_scan` | **obligatoire** — adresse SMTP de la boîte relevée |
| `dossier_traites` | `Traites` — créé automatiquement s'il n'existe pas |
| `dossier_rejetes` | `Rejetes` |
| `alias_achat` | `reception` — fragment d'adresse qui signe une **réception** |
| `alias_vente` | `expedition` — … une **expédition** |
| `expediteurs_autorises` | *(vide = pas de filtre)* ; CSV d'adresses |
| `max_messages` | `50` messages par relève |
| `max_octets_pdf` | `41943040` (40 Mo) |
| `max_pages` | `25` pages pour un seul BL |

Le secret du service principal Entra ID est lu dans un **secret scope
Databricks** : c'est le seul secret de la solution. Le *Run as* du job doit
avoir l'ACL `READ` sur le scope
(`databricks secrets put-acl bldemat_scan <RUN_AS> READ`). Si le scope ou la clé
manque, le message d'erreur porte la commande exacte à lancer.

**`traitement_scans`**

| Key | Exemple / défaut |
|---|---|
| `llm_endpoint` | `databricks-claude-opus-4-8` — **vide = tous les scans partent en validation, sans pré-remplissage** |
| `teams_webhook_reception` | URL du flux Teams *(facultatif)* |
| `auto_sans_desadv` | `false` — création automatique sur code tiers reconnu **sans** avis d'expédition. Laisser `false` jusqu'à avoir mesuré la précision réelle sur votre parc. |
| `max_scans` | `40` (1–500) scans par exécution |
| `dpi` | `150` (72–400) pour la rasterisation |

`auto_sans_desadv` doit rester **cohérent** avec `BL_SCAN_AUTO_SANS_DESADV` de
l'`app.yaml`, sinon l'écran de validation explique un garde-fou qui n'est plus
actif.

**`sync_referentiels_erp`**

| Key | Exemple / défaut |
|---|---|
| `catalogue_erp` | `emotors_data_platform` |
| `schema_erp` | `bronze_erp` |
| `catalogue_staging` | `emotors_data_champions` |
| `schema_staging` | `bl_scan_staging` |
| `sales_desadv_enabled` | `false` (ou `true` pour le DESADV vente) |

Ce job est le **carburant de l'automatisation** : sans DESADV à jour, la voie de
confiance maximale ne se déclenche jamais et tous les scans partent en
validation.

**`rapports_activite`**

| Key | Exemple / défaut |
|---|---|
| `llm_endpoint` | `databricks-claude-opus-4-8` — **vide = rapports sans analyse rédigée** |
| `date_reference` | *(vide = aujourd'hui)* ; `AAAA-MM-JJ` pour rattraper une nuit manquée |
| `periodicites` | *(vide = les périodes échues)* ; ex. `MENSUEL,ANNUEL` pour forcer |

Un rapport déjà présent pour une période est **remplacé** : la tâche est
réexécutable sans produire de doublon. Un rapport en échec n'interrompt pas les
autres ; la tâche échoue à la fin en listant les périodes concernées.

**`maintenance`**

| Key | Exemple / défaut |
|---|---|
| `draft_hours` | `24` (1–168) |
| `stale_job_hours` | `6` (1–48) |
| `scan_verrou_heures` | `4` (1–72) — libère les prises en charge oubliées |
| `retention_pdf_jours` | `180` (7–3650) — purge du PDF source des scans terminés |

La purge ne touche **que** la colonne `contenu` des scans terminés : la ligne de
`scans_recus` et les images du BL sont conservées. La traçabilité reste
intacte, seul l'octet lourd disparaît.

**`simulation_donnees`**

| Key | Exemple / défaut |
|---|---|
| `action` | `generer` ou `supprimer` |
| `nb_bl` | `5000` (1–200 000) |
| `date_min` / `date_max` | *(vide = 18 mois → hier)*, format `AAAA-MM-JJ` |
| `graine` | `20260727` — même graine = jeu identique |
| `prefixe` | `SIM-` — marqueur des lignes factices (majuscules, terminé par `-`) |
| `pages_par_bl` | `1` (0–5) ; `0` = aucune image, génération bien plus rapide |
| `remplacer` | `false` ; `true` = supprimer le jeu existant avant de régénérer |

Le job refuse de générer si des lignes préfixées existent déjà (sans
`remplacer=true`). La suppression est aussi disponible en SQL pur :
`sql/simulation/supprimer_donnees_simulation.sql`.

Chaque clé a une valeur par défaut ; la valeur saisie dans l'interface la
remplace. Une valeur manquante ou invalide fait échouer la tâche avec un
message explicite.

**`lakebase_endpoint` est facultatif** : laissé vide, il est retrouvé
automatiquement à partir de `pg_host` (parcours des projets/branches/endpoints
du workspace, comparaison du nom d'hôte). Le renseigner explicitement évite ces
appels de découverte. Le job s'authentifie auprès de Lakebase avec
`generate_database_credential` : aucun mot de passe n'est stocké.

**`pg_user` est facultatif** : laissé vide, le job prend l'identité qui
l'exécute — son **Run as** (`current_user.me()`). C'est **cette même identité**
qui frappe le jeton OAuth ; les deux coïncident donc toujours. Si `user` et
jeton diffèrent, Lakebase refuse la connexion avec
`OAuth: User is not authorized`. Prérequis : le *Run as* doit avoir **Can
connect** sur l'instance Lakebase et les GRANT correspondants (voir
[GUIDE.md](../GUIDE.md) étape 6.2). Ne renseigner `pg_user` que pour forcer un
autre rôle, lui aussi frappable par ce *Run as*.

## Dépendances

Déclarer dans l'environnement de la tâche serverless :

```
psycopg[binary]==3.2.3
databricks-sdk>=0.81.0
```

Pour les tâches qui réutilisent `bl_core` — `ingestion_scans`,
`traitement_scans`, `rapports_activite` — ajouter :

```
psycopg-pool==3.2.3
pymupdf==1.24.14        # ingestion_scans, traitement_scans
pillow                  # traitement_scans
pandas==2.2.3           # rapports_activite
fpdf2==2.8.7            # rapports_activite
```

Déposer alors `shared/bl_core/` **à côté** du script dans l'espace de travail
(ou l'ajouter au `sys.path` de la tâche). Aucune logique métier n'est dupliquée
dans les jobs : c'est le **même** `decision.py` et le même `extraction.py` que
l'application. `cache.py` bascule automatiquement sur `functools.lru_cache` hors
application.

⚠️ **Streamlit n'est PAS installé dans un environnement de tâche.** Les modules
métier de `bl_core` s'importent sans lui, mais deux modules en dépendent et sont
réservés à l'application : **`ui.py`** et **`identity.py`**. Un job qui en
importe un échoue au démarrage sur `ModuleNotFoundError: No module named
'streamlit'`. Les libellés dont un job a besoin vivent donc ailleurs — ainsi
`repository.libelle_statut`, que `ui` réexporte pour l'application.

**Aucune dépendance Microsoft n'est requise** : `bl_core/graph_mail.py`
n'utilise que `urllib` de la bibliothèque standard (pas de `msal`, pas de
`requests`). Le seul accès réseau sortant nécessaire est vers
`login.microsoftonline.com` et `graph.microsoft.com`.

Pour `simulation_donnees`, `pillow` est utilisé si disponible pour produire des
pages lisibles ; à défaut, un JPEG minimal est employé (aucune erreur).
