# Expression du besoin — Dématérialisation des bordereaux de livraison (BL)

**Demandeur** : Younes El Hachi · **Périmètre** : flux Achat et Vente — logistique, appros, ADV

---

## 🟦 Partie 1 — Compréhension du besoin

### 1. 📌 Contexte actuel

Les BL accompagnant chaque réception fournisseur et chaque expédition client sont
traités **au format papier**. Aucun référentiel numérique n'existe : pas de
recherche, pas de consultation à distance, pas de piste d'audit.

- **Où** : quais de réception et d'expédition, à chaque mouvement de marchandise.
- **Qui** : réceptionnistes, approvisionneurs, ADV, comptabilité fournisseurs,
  qualité — **[À CONFIRMER : 15 à 30 personnes]**.
- **Aujourd'hui** : BL classé physiquement ; rapprochement avec l'avis
  d'expédition ERP (DESADV) et traitement des anomalies EDI **manuels**.
- **Volumétrie** : 10 à 30 BL/jour, **15 en moyenne** (~3 500/an), 1 à 25 pages
  par BL (5 en moyenne).

**Irritants** :

- l'approvisionneur n'apprend l'arrivée d'une réception que par appel ou mail ;
- retrouver un BL ancien demande une recherche physique, parfois infructueuse ;
- document **unique et non répliqué** : une perte est définitive ;
- écarts BL / DESADV non détectés systématiquement ;
- **aucun indicateur** : ni volume, ni délai, ni taux d'anomalie EDI ;
- information dépendante de la personne qui a réceptionné ;
- **aucune traçabilité** opposable en cas d'audit client ou qualité.

### 2. 🎯 Besoin exprimé

Un **référentiel numérique unique des BL, alimenté automatiquement**, sans saisie
au quai.

**Fonctionnement cible** :

1. le réceptionniste **scanne le BL sur le copieur** du site (bouton « BL
   Réception » ou « BL Expédition ») — aucun autre geste ;
2. le scan est relevé automatiquement, **lu par un modèle d'IA** et rapproché du
   référentiel tiers et des avis d'expédition de l'ERP ;
3. **rapprochement certain** → BL **créé automatiquement**, tous champs
   renseignés, appros du portefeuille **notifiés nommément dans Teams** ;
4. **sinon** → **validation contrôlée** : pages affichées en grand, champs
   **pré-remplis** par l'IA, motif du doute explicité ;
5. BL consultables, filtrables, exportables, avec **audit complet** et
   **rapports d'activité automatiques**.

**Critères de réussite** :

| Critère | Cible |
|---|---|
| BL créés sans intervention humaine | ≥ 70 % (taux mesuré en continu) |
| Délai scan → BL disponible | ≤ 30 min |
| Traitement d'un BL à valider | ≤ 1 min |
| Saisie manuelle au quai | supprimée |
| BL retrouvables par recherche / traçabilité | 100 % |

> **Un prototype fonctionnel est déjà en pré-production** (base, application,
> numérisation, extraction IA, notifications, rapports, tests). La faisabilité
> est démontrée : le besoin porte sur l'**industrialisation** encadrée par la
> DSIAI.

---

## 🟨 Partie 2 — Justification du besoin

### 3. 💡 Bénéfices attendus

- **Temps** : suppression de la saisie, du classement et surtout des recherches
  ultérieures — sur ~3 500 BL/an, à **[À CONFIRMER : temps constaté par BL]**.
- **Charge manuelle** : plus d'appels « le camion est-il arrivé ? » ; même les
  cas à valider sont pré-remplis, le gestionnaire **contrôle** au lieu de saisir.
- **Fiabilité** : le tiers vient de **l'ERP**, pas d'une lecture approximative ;
  unicité du numéro contrôlée ; écarts scan/ERP signalés.
- **Traçabilité** : auteur, horodatage, historique et image du document source
  conservés.
- **Risque et coût** : plus de BL perdu ; litiges et rapprochements de factures
  instruits pièce en main ; taux d'anomalie EDI par fournisseur mesuré, donc
  actionnable.
- **Robustesse** : habilitations par rôle, fermées par défaut ; **conception non
  bloquante** — IA ou messagerie indisponible, la chaîne continue en mode
  dégradé, un incident technique ne bloque jamais un quai.

### 4. 👥 Impact métier

- **Direct** : quais (le geste se simplifie), approvisionnements et ADV
  (validation, notifications, consultation).
- **Indirect** : comptabilité fournisseurs (rapprochement facture), qualité
  (traçabilité).
- **Utilisateurs** : **[À CONFIRMER : 15 à 30 nommés, dont 3 à 8 quotidiens]**.
- **Outils concernés** : copieurs du site, ERP (tiers, DESADV, statut EDI),
  Microsoft 365 / Teams, plateforme Databricks.
- **Nature** : **régulier et structurant** — tous les jours ouvrés. Non critique
  au sens d'un arrêt de ligne, mais critique pour la **capacité à justifier**
  (litiges, factures, audits).

### 5. ⚠️ Risque si le besoin n'est pas traité

- **Opérationnel** : le BL reste un document unique sans sauvegarde ; sa perte
  empêche d'instruire un litige ou de justifier une expédition.
- **Conformité** : aucune piste d'audit ; une demande de traçabilité se traite
  par recherche physique, sans garantie de délai ni de résultat — référentiel
  qualité applicable **[À CONFIRMER : type IATF 16949]**.
- **Financier** : un écart non documenté n'est pas opposable au fournisseur ; une
  facture peut être réglée sans justificatif vérifié.
- **Surcharge invisible** : la charge actuelle (classement, recherches, relances)
  ne figure dans aucun indicateur, donc ne peut pas être arbitrée.
- **Aggravation** : ~3 500 BL/an s'ajoutent au stock papier ; et le prototype
  resterait hors du cadre d'exploitation DSIAI — précisément la dette technique
  que ce besoin vise à supprimer.

### 6. ⏳ Échéance ou priorité

- **Souhaitée** : **[À CONFIRMER — proposition : production sur le flux Achat au
  prochain trimestre, Vente ensuite]**. Lien projet / audit / client :
  **[À CONFIRMER]**.
- **Chemin critique** : plus le développement, mais l'encadrement DSIAI. Une
  seule dépendance bloquante : **boîte aux lettres partagée avec deux alias** et
  **inscription d'application Entra ID** restreinte à cette boîte — hors des
  accès du demandeur.
- **Si reporté** : le bénéfice est retardé sans que la charge diminue ; ~300 BL
  de plus par mois au classement papier ; et le prototype se périme (secrets
  d'authentification à durée de vie limitée).

### 7. 🔴 Fonction critique ou contournement existant

Le BL est la **preuve de la livraison** : indispensable aux litiges, au
rapprochement des factures et à la traçabilité.

Deux contournements coexistent :

1. **le processus papier** — entièrement manuel, chronophage, risqué (document
   unique), non traçable ;
2. **le prototype interne** — fonctionnel, mais maintenu hors cadre DSIAI, sur un
   tenant Microsoft de test, et **dépendant d'une seule personne**. C'est cette
   situation qu'il faut régulariser.

**Charge** : côté métier, celle du papier, quotidienne et invisible. Côté DSIAI,
aucune aujourd'hui — et c'est le problème : une solution multi-métiers sans
supervision ni continuité de maintenance est un risque à traiter maintenant,
tant qu'il est peu coûteux.

### 8. 🧭 Alignement avec la stratégie Emotors

- **Transformation digitale** : suppression d'un processus papier en s'appuyant
  sur des moyens **déjà en place** — copieurs, Microsoft 365, Databricks. Aucun
  achat de matériel, de licence applicative ni de poste.
- **Performance opérationnelle** : automatisation de la majorité des cas,
  l'humain n'intervenant que sur les cas réellement douteux.
- **Qualité et sécurité** : traçabilité exhaustive, donnée fiabilisée par l'ERP,
  taux d'anomalie EDI par fournisseur, habilitations fermées par défaut, aucun
  mot de passe stocké.
- **Fiabilisation du SI** : le besoin **réduit** la surface technique — une seule
  base, aucun progiciel nouveau, référentiels ERP non dupliqués, solution
  documentée et couverte par des tests, donc reprenable par un tiers.
- **Réutilisable** : la chaîne (numérisation → IA → rapprochement → validation
  des seuls cas douteux) est transposable à d'autres documents entrants
  (accusés de réception, certificats matière, documents de transport).
