# Tests du pipeline de scan

```bash
python -m pytest bldemat-scan/tests -q       # depuis la racine du dépôt
```

66 tests, sans base de données ni appel réseau : les dépendances sont injectées
ou bouchonnées. `ROOT` est déduit de l'emplacement du fichier, donc les tests
tournent depuis n'importe quel clone.

| Fichier | Objet |
|---|---|
| `test_scan_decision.py` | La **règle de décision** : création automatique ou validation humaine. Le DESADV fait foi sur le tiers, le code tiers seul ne suffit pas par défaut, aucun BL créé si le contrôle d'unicité échoue. Plus le routage achat/vente, y compris la récupération de l'alias que Exchange masque. |
| `test_scan_graph.py` | Le **client Microsoft Graph** : jeton mis en cache, indices de diagnostic sur les codes AADSTS, dégradation en trois paliers devant `InefficientFilter`, tri chronologique côté client, relance sur 429 mais pas sur 403. |
| `test_scan_app.py` | Les **écrans** (Streamlit AppTest) : file de validation, pré-remplissage depuis l'extraction déjà faite, supervision du pipeline, alerte de pipeline muet. |
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
