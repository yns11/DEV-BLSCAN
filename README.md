# BLDEMAT-SCAN — BL dématérialisés depuis le copieur (eMotors)

Solution Databricks qui crée les bordereaux de livraison (BL) **à partir des
scans du copieur professionnel**, sans photo ni saisie au quai.

Le réceptionniste scanne le BL sur le multifonction, qui l'envoie par mail à un
alias dédié (un par sens de flux). Un job relève la boîte via Microsoft Graph,
un second analyse le scan avec un modèle vision, le rapproche du référentiel et
**crée le BL automatiquement** quand le recoupement est sûr. Sinon, le scan part
en **validation contrôlée** dans l'app Administration, champs déjà pré-remplis.

👉 **Raccordement Microsoft 365 (abonnement, boîte, Entra ID, Graph, copieur) :
[GUIDE_MICROSOFT365.md](GUIDE_MICROSOFT365.md).**
👉 **Déploiement Databricks et fonctionnement : [GUIDE.md](GUIDE.md).**

## Le flux

```
Copieur ──mail──▶ Boîte M365 ──Graph──▶ ingestion_scans ──▶ file « scans_recus »
                                                                    │
                                        traitement_scans ◀──────────┘
                                              │
                    ┌─────────────────────────┴──────────────────────┐
              recoupement sûr                                  doute
                    │                                             │
              BL créé + Teams                        Scans à valider (app)
              origine SCAN_AUTO                      → BL, origine SCAN_VALIDE
```

## Contenu

- `src/app_administration` : **l'unique application**. Validation contrôlée des
  scans douteux (pages du scan en grand avec zoom/rotation, champs pré-remplis
  par l'IA, contrôle d'unicité en direct, verrou de prise en charge),
  supervision du pipeline, dépôt manuel de PDF en repli, tableau de bord,
  rapports d'activité PDF (journalier → annuel), vues BL, DESADV, rapprochement
  BL/DESADV, référentiels, audit, qualité IA, rôles et notifications.
- `shared/bl_core` : cœur partagé par l'app **et** les jobs — configuration,
  RBAC, transactions, repository, client Microsoft Graph, **règle de décision**,
  rasterisation des scans, extraction IA, rapports, cartes Teams, PDF, design
  system.
- `jobs` : `ingestion_scans` (relève la boîte), `traitement_scans` (analyse et
  décide), plus la synchronisation ERP, les rapports, la maintenance et le
  générateur de données de simulation (`jobs/README.md`).
- `sql/migrations` : `V001__baseline_scan.sql`, baseline consolidée du schéma
  `bl_scan` ; `sql/simulation` : purge du jeu de test.

Il n'y a **plus d'app Création de BL** : c'est le pipeline qui crée les BL, et
les vues BL ne contiennent que des bordereaux **déjà traités** — créés
automatiquement ou validés par un gestionnaire.

## Principes d'architecture

- **Un BL créé à tort coûte plus cher qu'un BL à valider.** La règle de décision
  est volontairement conservatrice : la création automatique n'a lieu que sur un
  recoupement avec un **système tiers** (l'avis d'expédition de l'ERP). Le
  rapprochement par code tiers seul est possible mais **désactivé par défaut**
  (`BL_SCAN_AUTO_SANS_DESADV`).
- **`decision.py` ne fait aucun accès base ni appel réseau** : ses dépendances
  sont injectées. La règle métier la plus sensible de la solution est donc
  testable de bout en bout, et testée.
- **Idempotence de bout en bout** : `message_id` UNIQUE porté par
  l'`Internet-Message-Id`, mail rangé seulement après insertion,
  `FOR UPDATE SKIP LOCKED` sur la file, clé de contenu (`sha256`) pour les
  dépôts manuels. Rejouer un job ne crée jamais de doublon.
- **Isolement des erreurs** : un scan en échec passe en `ERREUR` (visible,
  rejouable) sans affecter les autres. Une tâche n'échoue que si *tout* le lot a
  échoué — signe d'un problème systémique et non d'un PDF mal formé.
- **Lakebase PostgreSQL** porte tout : file de scans (PDF en BYTEA),
  métadonnées, images, transactions et audits. Aucune dépendance à Unity
  Catalog. Le schéma `bl_scan` cohabite sans conflit avec le `bl_demat` de la
  solution d'origine.
- **Un seul secret** dans toute la solution — le client secret Entra ID, dans un
  *secret scope* Databricks, lu uniquement par les jobs. Tout le reste est dans
  `app.yaml` et les job parameters : ni bundle, ni valeur en dur.
- Les apps et jobs s'authentifient auprès de Lakebase avec des **credentials
  OAuth** renouvelés automatiquement ; aucun mot de passe n'est stocké.
- **RBAC strict et fermé par défaut** : un utilisateur sans rôle n'a aucun
  droit. La matrice est versionnée dans le code (`bl_core/rbac.py`), les
  affectations sont en base (Gestion ▸ Rôles), et les décisions d'autorisation
  ne sont **jamais mises en cache**.
- **L'IA n'est jamais bloquante** : endpoint absent ou en échec, le pipeline
  continue — tous les scans partent simplement en validation sans
  pré-remplissage, et les rapports sont produits sans l'analyse rédigée.
- **Notifications Teams *best effort*** : la trace en base est écrite d'abord et
  fait foi ; une indisponibilité de Teams n'annule jamais la création d'un BL.
- **Les modules métier de `bl_core` s'importent sans Streamlit**, ce qui permet
  aux tâches Lakeflow de réutiliser le même code que l'application
  (`cache.py` bascule sur `lru_cache` hors application). Deux exceptions
  assumées, réservées à l'application : `ui.py` (design system) et
  `identity.py` (identité SSO). **Un job ne doit jamais les importer** —
  l'environnement d'une tâche serverless n'embarque pas Streamlit. Un test
  relit les imports de chaque job et les rejoue Streamlit neutralisé.
- Le client Graph n'utilise que `urllib` — pas de `msal`, pas de `requests`.

## Surveillance

Le mode de défaillance le plus dangereux n'est pas l'erreur, c'est le **silence** :
secret expiré, règle de boîte mal placée, copieur reconfiguré — et plus rien
n'arrive, sans erreur nulle part. La vue **Pipeline ▸ Scans reçus** affiche pour
cela le délai depuis le dernier scan reçu, avec une **alerte au-delà de 8 h**,
à côté du **taux d'automatisation** du pipeline.

## Développement

`shared/bl_core` est la source de vérité. Après modification, resynchroniser la
copie embarquée par l'application :

```bash
cp shared/bl_core/*.py src/app_administration/bl_core/
```

Python 3.11 — Streamlit 1.49.1 — Lakebase (PostgreSQL managé).
