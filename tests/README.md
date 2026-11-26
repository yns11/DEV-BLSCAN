# Tests du pipeline de scan

```bash
python -m pytest tests -q       # depuis la racine du dépôt
```

130 tests, sans base de données ni appel réseau : les dépendances sont injectées
ou bouchonnées. Un 131ᵉ joue un lot de bout en bout sur une **vraie base
PostgreSQL** ; il est ignoré tant que `BL_TEST_DSN` n'est pas défini (voir
plus bas). `ROOT` est déduit de l'emplacement du fichier, donc les tests
tournent depuis n'importe quel clone.

| Fichier | Objet |
|---|---|
| `test_scan_decision.py` | La **règle de décision** : création automatique ou validation humaine. Le DESADV fait foi sur le tiers, le code tiers seul ne suffit pas par défaut, aucun BL créé si le contrôle d'unicité échoue. Plus le routage achat/vente, y compris la récupération de l'alias que Exchange masque. |
| `test_scan_graph.py` | Le **client Microsoft Graph** : jeton mis en cache, indices de diagnostic sur les codes AADSTS, dégradation en trois paliers devant `InefficientFilter`, tri chronologique côté client, relance sur 429 mais pas sur 403. |
| `test_scan_app.py` | Les **écrans** (Streamlit AppTest) : file de validation, pré-remplissage depuis l'extraction déjà faite, supervision du pipeline, alerte de pipeline muet. |
| `test_scan_segmentation.py` | Le **découpage d'un scan en bordereaux**, cas par cas : lot de référence (3 pages, 2 pages + lettre de voiture, 5 pages), pages sans numéro, BL consécutifs à un caractère près, pile mélangée, exemplaires en double, annexe en tête, corrections d'un gestionnaire. |
| `test_scan_lots_job.py` | Le **sort d'un scan** dans le job : un seul BL traité comme avant, lot sûr réparti en scans fils analysés dans la même exécution, lot douteux envoyé à un gestionnaire, annexes exclues de la lecture. |
| `test_scan_lots_pdf.py` | Les **opérations PDF** : extraire les pages d'un groupe, reconnaître un verso vide — sans jamais écarter une page qui ne porte qu'un mot manuscrit. |
| `test_scan_lots_base.py` | Un lot **sur une vraie base** : migrations, contraintes, clés d'idempotence, création des BL et de leurs pages. Seul le modèle est simulé. |
| `test_scan_jobs_sans_streamlit.py` | Garde-fou : **aucun job ne doit importer un module qui exige Streamlit**, absent des environnements de tâche. Les imports sont relus dans les sources et rejoués Streamlit neutralisé. |
| `test_scan_manifeste.py` | Garde-fou : le **contrôle de cohérence de `bl_core`** au démarrage des jobs, et la validité du manifeste lui-même. |

## Deux tests qui en surveillent d'autres

`test_scan_jobs_sans_streamlit.py` et `test_scan_manifeste.py` protègent contre
des pannes survenues en exploitation réelle, invisibles aux tests classiques :
un import qui n'existe pas dans l'environnement cible, et une copie de
`bl_core` mélangeant deux versions. Tous deux comportent un **témoin positif**
qui vérifie que le test mesure bien quelque chose — sans quoi un test vert ne
prouverait rien.

## Les tests de la solution d'origine

Les 84 tests de `v5/` ne sont pas ici. Les deux projets embarquent un paquet
nommé `bl_core` : les lancer dans la **même** session pytest ferait gagner un
`sys.path` sur l'autre. Deux invocations séparées, donc.

## Le test sur base réelle

Les doublures ne prouvent rien sur le SQL. `test_scan_lots_base.py` joue le vrai
`repository` contre PostgreSQL, sur une base **jetable** dont le nom contient
« test », migrations V001 et V002 exécutées :

```bash
createdb bltest
psql -d bltest -f sql/migrations/V001__baseline_scan.sql
psql -d bltest -f sql/migrations/V002__scans_multi_bl.sql
BL_TEST_DSN="host=127.0.0.1 dbname=bltest user=postgres" python -m pytest tests -q
```

Il ne touche que les lignes qu'il crée (préfixe `TSTLOT-`) et les supprime
ensuite. Il se met de lui-même en « ignoré » si la file de la base contient
déjà des scans en attente : le job traite tout ce qui attend, et créerait des
BL à partir de données étrangères au test.
