# Expression du besoin — Dématérialisation des bordereaux de livraison (BL)

**Demandeur** : Younes El Hachi
**Périmètre** : flux Achat (réceptions) et Vente (expéditions) — logistique / appros / ADV
**Date** : juillet 2026

> Les mentions **[À CONFIRMER]** signalent les rares valeurs que le demandeur doit
> chiffrer lui-même (effectifs, temps constatés, échéances internes). Tout le
> reste est établi.

---

## 🟦 Partie 1 — Compréhension du besoin

### 1. 📌 Contexte actuel

**Le problème.** Les bordereaux de livraison qui accompagnent chaque réception
fournisseur et chaque expédition client sont aujourd'hui traités **au format
papier**. Il n'existe aucun référentiel numérique des BL : ni recherche, ni
consultation à distance, ni rapprochement automatisé avec l'ERP, ni piste
d'audit.

**Où le sujet apparaît.** Au quai de réception et d'expédition, à chaque
mouvement de marchandise. Le BL est le document qui atteste physiquement de la
livraison ; il est ensuite nécessaire aux approvisionneurs (litiges,
réclamations, écarts de quantité), à l'ADV (preuve d'expédition), à la
comptabilité fournisseurs (rapprochement facture) et à la qualité (traçabilité
d'un lot).

**Qui est concerné.**

| Population | Rôle dans le processus actuel |
|---|---|
| Réceptionnistes / opérateurs de quai | reçoivent le BL papier, le classent |
| Approvisionneurs (appros) | instruisent les écarts et litiges fournisseurs |
| ADV | justifient les expéditions auprès des clients |
| Comptabilité / finance | rapprochent BL et factures |
| Qualité | recherchent un BL pour tracer un lot |

Effectif total concerné : **[À CONFIRMER — ordre de grandeur : 15 à 30
personnes]**.

**Comment c'est traité aujourd'hui.** Le BL papier est réceptionné au quai, puis
classé physiquement. Toute recherche ultérieure suppose de retrouver le document
dans un classement. Le rapprochement avec l'avis d'expédition électronique
(DESADV) de l'ERP, ainsi que le traitement des anomalies EDI (statut « NOK »),
sont effectués **manuellement**, au cas par cas.

**Limites et irritants constatés.**

- **Aucune information avant d'avoir le papier en main** : l'approvisionneur
  n'apprend l'arrivée d'une réception que par un appel, un mail ou un passage au
  quai.
- **Recherche longue et incertaine** : retrouver un BL de plusieurs mois demande
  une recherche physique, parfois infructueuse.
- **Document unique et non répliqué** : un BL égaré, taché ou détruit n'est pas
  récupérable — il n'existe qu'en un exemplaire.
- **Rapprochement BL / DESADV manuel** : les écarts entre ce qui a été annoncé
  par le fournisseur et ce qui a été livré ne sont pas détectés
  systématiquement.
- **Aucune mesure du processus** : ni volume par fournisseur, ni délai, ni taux
  d'anomalie EDI, ni charge par plage horaire ou par quai. Le pilotage repose
  sur du ressenti.
- **Dépendance aux personnes** : l'information vit dans le classement et la
  mémoire de celui qui a réceptionné. Une absence crée une rupture.
- **Aucune piste d'audit** : impossible de démontrer qui a enregistré quoi et
  quand, ni de produire un historique en cas de contrôle client ou qualité.

Volumétrie actuelle : **10 à 30 BL par jour, 15 en moyenne**, soit de l'ordre de
**3 500 BL par an**. Chaque BL compte **1 à 25 pages, 5 en moyenne**.

---

### 2. 🎯 Besoin exprimé

**Ce qui est attendu.** Disposer d'un **référentiel numérique unique des BL**,
alimenté **automatiquement**, sans saisie manuelle au quai, et exploitable par
tous les métiers concernés.

**Fonctionnement cible.**

1. Le réceptionniste **scanne le BL sur le copieur professionnel** déjà présent
   sur site, en appuyant sur un bouton dédié — « BL Réception » ou « BL
   Expédition ». Aucun autre geste, aucune saisie.
2. Le copieur envoie le scan par mail à une boîte dédiée. Un traitement
   automatisé relève cette boîte et dépose le document dans une file.
3. Un modèle d'intelligence artificielle **lit le bordereau** (numéro, tiers,
   date, anomalie éventuelle) et **rapproche les valeurs lues du référentiel**
   fournisseurs/clients et des avis d'expédition (DESADV) de l'ERP.
4. **Si le rapprochement est certain** — le numéro de BL correspond à un avis
   d'expédition actif de l'ERP — le BL est **créé automatiquement**, tous champs
   renseignés (numéro, tiers, quai, date, plage horaire, statut EDI), et les
   approvisionneurs du portefeuille concerné sont **notifiés dans Teams**,
   nommément.
5. **Sinon**, le scan est présenté à l'approvisionneur ou à l'ADV dans un écran
   de **validation contrôlée** : les pages s'affichent en grand, les champs sont
   **déjà pré-remplis** par l'IA, et le motif du doute est explicité. La
   validation se fait en quelques secondes.
6. Les BL sont ensuite **consultables, filtrables et exportables** (tableau de
   bord, recherche, PDF), avec **piste d'audit complète** et **rapports
   d'activité** automatiques (journalier, hebdomadaire, mensuel, trimestriel,
   annuel).

**Résultat concret attendu.**

- Zéro saisie manuelle au quai ; le geste du réceptionniste se limite au scan.
- Un BL numérisé, indexé et recherchable **en moins de 30 minutes** après son
  arrivée physique.
- Les approvisionneurs informés **sans avoir à demander**.
- Le rapprochement BL / DESADV et la détection des anomalies EDI **outillés** au
  lieu d'être laissés à l'initiative de chacun.
- Des indicateurs de pilotage produits automatiquement, sans collecte.

**Critères de réussite.**

| Critère | Cible |
|---|---|
| Part des BL créés **sans intervention humaine** | ≥ 70 % à terme (taux mesuré en continu par la solution) |
| Délai entre le scan et la disponibilité du BL | ≤ 30 min |
| Temps de traitement d'un BL nécessitant validation | ≤ 1 min |
| BL numérisés retrouvables par recherche | 100 % |
| Saisie manuelle au quai | supprimée |
| Traçabilité (auteur, horodatage, historique) | exhaustive |

**Point d'avancement.** Un **prototype fonctionnel a déjà été réalisé en
interne** et est déployé en pré-production : base de données, application de
pilotage, chaîne de numérisation, extraction IA, notifications Teams, rapports
et tests automatisés. La faisabilité technique est donc **démontrée** — le
présent besoin porte sur l'**industrialisation** et la mise en production
encadrée de cette solution (voir §7).

---

## 🟨 Partie 2 — Éléments de justification du besoin

### 3. 💡 Bénéfices attendus

**Gain de temps.** La saisie et le classement disparaissent du poste de quai.
Sur ~3 500 BL par an, à **[À CONFIRMER — temps constaté par BL aujourd'hui,
saisie + classement + recherches ultérieures]**, le gain annuel se calcule
directement. Le gain le plus important n'est probablement pas la saisie
elle-même mais les **recherches évitées** : retrouver un BL devient instantané.

**Réduction de charge manuelle.**

- suppression de la saisie et du classement physique ;
- suppression des appels et mails « le camion est-il arrivé ? » : la
  notification Teams est automatique et nominative ;
- pré-remplissage IA même dans les cas nécessitant une validation : le
  gestionnaire contrôle au lieu de saisir.

**Qualité, fiabilité, traçabilité.**

- le tiers retenu vient de **l'ERP** (avis d'expédition) et non d'une lecture
  approximative : la donnée est plus fiable que la saisie humaine, qui se trompe
  sur les raisons sociales proches ;
- **aucun doublon possible** : l'unicité du numéro de BL est contrôlée à
  l'enregistrement ;
- **piste d'audit exhaustive** : qui, quoi, quand, avec conservation de l'image
  du document source ;
- les écarts entre scan et ERP sont **signalés** au gestionnaire au lieu de
  passer inaperçus.

**Réduction de risque et de coût.**

- un BL ne peut plus être perdu : il est stocké et sauvegardé ;
- les litiges fournisseurs et les rapprochements de factures s'instruisent
  **pièce en main**, ce qui réduit le risque d'accepter une facture non
  justifiée ou de ne pas défendre un écart ;
- la mesure du taux d'anomalie EDI par fournisseur permet d'**agir sur les
  causes** plutôt que de subir.

**Disponibilité, robustesse, sécurité.**

- information accessible à tous les métiers, en même temps, sans dépendre d'une
  personne ni d'un classeur ;
- **habilitations par rôle** (appros, ADV, finance, administrateur), fermées par
  défaut ;
- **conception non bloquante** : si l'IA ou la messagerie est indisponible, la
  chaîne continue de fonctionner en mode dégradé — les BL partent simplement en
  validation manuelle. Un incident technique ne bloque jamais un quai.

---

### 4. 👥 Impact métier

**Métiers et équipes impactés.**

| Périmètre | Nature de l'impact |
|---|---|
| Logistique / quais (réception et expédition) | **Direct** : le geste change, il se simplifie (scan au lieu de classement) |
| Approvisionnements | **Direct** : validation des cas douteux, notifications, consultation |
| ADV | **Direct** : idem sur le flux Vente |
| Comptabilité fournisseurs / finance | **Indirect** : consultation des BL en appui du rapprochement facture |
| Qualité | **Indirect** : recherche et traçabilité documentaire |
| DSIAI | Exploitation de la solution (base, application, traitements planifiés) |

**Nombre d'utilisateurs** : **[À CONFIRMER — ordre de grandeur : 15 à 30
utilisateurs nommés, dont 3 à 8 en usage quotidien]**.

**Outils et processus concernés** : processus de réception et d'expédition ;
copieurs multifonctions du site ; ERP (référentiel tiers et avis d'expédition
DESADV, statut EDI) ; Microsoft 365 / Teams ; plateforme de données Databricks.

**Nature de l'impact** : **régulier et structurant**. Le processus se déroule
tous les jours ouvrés, à chaque mouvement de marchandise. Il n'est pas critique
au sens d'un arrêt de ligne — une réception peut se faire sans la solution — mais
il l'est pour la **capacité à justifier et à instruire** : litiges, factures,
audits, traçabilité.

---

### 5. ⚠️ Risque si le besoin n'est pas traité

**Risque opérationnel.** Le BL reste un **document unique, non répliqué**. Sa
perte est définitive et empêche d'instruire un litige fournisseur ou de
justifier une expédition auprès d'un client. Chaque BL papier est un point de
défaillance sans sauvegarde.

**Risque qualité et conformité.** Aucune piste d'audit n'est produite
aujourd'hui : impossible de démontrer par qui et quand une réception a été
enregistrée, ni de restituer rapidement l'historique documentaire d'un lot lors
d'un audit client ou d'un contrôle selon le référentiel qualité automobile
**[À CONFIRMER — référentiel applicable, type IATF 16949]**. Une demande de
traçabilité se traite aujourd'hui par une recherche physique, avec un résultat
et un délai non garantis.

**Risque financier.** Sans BL retrouvable, un écart de quantité ou de
qualité n'est pas opposable au fournisseur, et une facture peut être réglée sans
justificatif vérifié. Le rapprochement BL / DESADV étant manuel, les écarts
échappent au contrôle de façon non mesurée — donc non chiffrable aujourd'hui,
ce qui est en soi un problème.

**Surcharge de travail.** La charge actuelle est **subie et invisible** : temps
de classement, recherches, relances téléphoniques, reconstitutions de dossiers.
Elle ne figure dans aucun indicateur, ce qui la rend impossible à arbitrer.

**Aggravation avec le temps.**

- le volume d'archives papier croît d'environ **3 500 BL par an**, dégradant
  mécaniquement les délais de recherche ;
- les documents anciens se dégradent physiquement ;
- **un prototype fonctionnel existe déjà** : ne pas l'industrialiser laisserait
  en place une solution non encadrée par la DSIAI, ce qui constituerait
  précisément la dette technique et le risque de dépendance à une personne que
  ce besoin vise à supprimer.

---

### 6. ⏳ Échéance ou priorité demandeur

**Échéance souhaitée** : **[À CONFIRMER — proposition : mise en production sur
le flux Achat au cours du prochain trimestre, extension au flux Vente ensuite]**.

**Éléments de calendrier.**

- Le **prototype est déjà en pré-production** et validé techniquement : le
  chemin critique n'est plus le développement, mais l'**encadrement DSIAI**
  (habilitations, comptes de service, boîte de messagerie dédiée sur le tenant
  eMotors, exploitation des traitements planifiés).
- Une seule dépendance externe : la création d'une **boîte aux lettres partagée
  avec deux alias** et d'une **inscription d'application Entra ID** avec accès
  restreint à cette seule boîte. Ce point relève d'une administration à laquelle
  le demandeur n'a pas accès, et conditionne toute la chaîne.
- Lien projet / audit / client : **[À CONFIRMER]**.

**Pourquoi c'est prioritaire.** L'effort de conception est déjà consenti. Le
report ne réduit pas la charge, il retarde seulement le bénéfice tout en
laissant s'accumuler le stock papier et en maintenant la solution hors du cadre
d'exploitation de la DSIAI.

**Si le sujet est traité plus tard** : le processus papier continue, avec ses
irritants ; environ **300 BL supplémentaires par mois** rejoignent un classement
non indexé ; et le prototype se périme (dépendances techniques, secrets
d'authentification à durée de vie limitée).

---

### 7. 🔴 Fonction critique ou contournement existant

**La fonction est-elle nécessaire à l'activité ?** Oui. Le BL est la **preuve de
la livraison**. Il est indispensable aux litiges fournisseurs, au rapprochement
des factures, à la justification des expéditions et à la traçabilité. Ce n'est
pas un confort : c'est la pièce justificative du mouvement de marchandise.

**Contournement existant.** Deux, de natures différentes :

1. **Le processus papier lui-même** — c'est le contournement historique. Il est
   **entièrement manuel**, **chronophage** (classement puis recherches),
   **risqué** (document unique, perte définitive) et **non traçable**. Il
   fonctionne, mais sans aucune garantie de délai ni de résultat sur une
   recherche.

2. **Un prototype développé en interne**, déployé en pré-production, qui couvre
   déjà le besoin de bout en bout : base de données, application de pilotage,
   numérisation, extraction IA, notifications, rapports, habilitations et tests
   automatisés. Il est **fonctionnel mais temporaire par nature** : conçu et
   maintenu hors du cadre d'exploitation de la DSIAI, il repose sur une seule
   personne et sur un tenant Microsoft de test. **C'est cette situation qu'il
   faut régulariser.**

**Charge générée par le contournement.**

- **Côté métier** : la charge du papier, quotidienne et invisible dans les
  indicateurs.
- **Côté DSIAI** : aucune aujourd'hui — et c'est précisément le problème. Une
  solution utilisée par plusieurs métiers, sans intégration au cadre
  d'exploitation, sans supervision ni continuité de maintenance, constitue un
  risque à traiter maintenant, tant qu'il est peu coûteux de le faire.

---

### 8. 🧭 Alignement avec la stratégie Emotors

**Transformation digitale.** Le besoin supprime un processus papier au profit
d'un référentiel numérique, en s'appuyant sur des moyens **déjà en place** :
copieurs du site, Microsoft 365 / Teams, plateforme de données Databricks. Aucun
matériel à acquérir, aucun poste supplémentaire, aucune application tierce à
acheter.

**Performance opérationnelle.** Suppression d'une saisie, d'un classement et des
recherches associées ; information poussée aux bons interlocuteurs au bon
moment ; automatisation de la majorité des cas, l'humain n'intervenant que sur
les cas réellement douteux.

**Qualité, production, traçabilité, sécurité.**

- piste d'audit exhaustive et conservation de l'image du document source, à
  l'appui des exigences de traçabilité automobile ;
- fiabilisation de la donnée : le tiers vient de l'ERP, le numéro est contrôlé
  en unicité ;
- mesure du **taux d'anomalie EDI par fournisseur**, orientée vers l'action
  corrective plutôt que le constat ;
- habilitations par rôle, fermées par défaut ; aucun mot de passe stocké
  (authentification par identité de service) ; un unique secret, géré dans un
  coffre.

**Fiabilisation et standardisation du SI.** Le besoin **réduit** la surface
technique au lieu de l'étendre :

- une seule base (PostgreSQL managé) porte données, images et audits ;
- pas de nouveau progiciel, pas de nouvelle brique d'infrastructure ;
- le rapprochement avec l'ERP s'appuie sur les référentiels existants, sans les
  dupliquer ;
- la solution est **documentée et couverte par des tests automatisés**, ce qui
  la rend reprenable par un tiers — condition d'une exploitation pérenne par la
  DSIAI.

**Valeur réutilisable.** La chaîne mise en place — numérisation, extraction IA,
rapprochement à un référentiel, validation humaine des seuls cas douteux — est
**transposable à d'autres documents entrants** (accusés de réception, certificats
matière, documents de transport). Le BL est un premier cas d'usage, pas un
développement isolé.
