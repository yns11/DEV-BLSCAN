-- BLDEMAT-SCAN — modèle PostgreSQL de référence (schéma bl_scan).
--
-- Baseline CONSOLIDÉE : ce projet part d'un schéma neuf, il n'y a donc pas de
-- suite de migrations à rejouer. À exécuter tel quel dans l'éditeur SQL du
-- projet Lakebase. Idempotent.
--
-- Pour un autre nom de schéma : remplacer « bl_scan » partout et aligner
-- BL_PG_SCHEMA dans app.yaml et dans les paramètres des jobs.
--
-- ⚠️ Ce schéma est DISTINCT de celui du projet BLDEMAT d'origine (bl_demat) :
-- les deux solutions peuvent cohabiter dans la même instance Lakebase sans se
-- perturber.

CREATE SCHEMA IF NOT EXISTS bl_scan;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ===========================================================================
-- 1. RÉFÉRENTIELS
-- ===========================================================================
CREATE TABLE IF NOT EXISTS bl_scan.base_tiers (
  name                TEXT PRIMARY KEY,
  type_tiers          TEXT NOT NULL CHECK (type_tiers IN ('FOURNISSEUR', 'CLIENT')),
  source_donnee       TEXT NOT NULL DEFAULT 'MANUEL' CHECK (source_donnee IN ('ERP', 'MANUEL')),
  source_key          TEXT,
  actif               BOOLEAN NOT NULL DEFAULT true,
  last_seen_at        TIMESTAMPTZ,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now(),
  modifie_le          TIMESTAMPTZ NOT NULL DEFAULT now(),
  version             INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_base_tiers_source
  ON bl_scan.base_tiers (type_tiers, source_key)
  WHERE source_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS bl_scan.gestionnaires (
  code_gestionnaire   TEXT PRIMARY KEY,
  email               TEXT,   -- UPN Microsoft 365 : sert à la @mention Teams
  nom_affichage       TEXT,   -- nom affiché dans la mention
  actif               BOOLEAN NOT NULL DEFAULT true,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bl_scan.quais (
  code_quai           TEXT PRIMARY KEY,
  actif               BOOLEAN NOT NULL DEFAULT true,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bl_scan.adresses (
  adresse             TEXT PRIMARY KEY,
  actif               BOOLEAN NOT NULL DEFAULT true,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bl_scan.portefeuilles (
  code_gestionnaire   TEXT NOT NULL
    REFERENCES bl_scan.gestionnaires (code_gestionnaire) ON UPDATE CASCADE,
  nom_fournisseur     TEXT NOT NULL
    REFERENCES bl_scan.base_tiers (name) ON UPDATE CASCADE,
  PRIMARY KEY (code_gestionnaire, nom_fournisseur)
);

CREATE TABLE IF NOT EXISTS bl_scan.sites_logistiques (
  entite              TEXT NOT NULL
    REFERENCES bl_scan.base_tiers (name) ON UPDATE CASCADE,
  adresse             TEXT NOT NULL
    REFERENCES bl_scan.adresses (adresse) ON UPDATE CASCADE,
  PRIMARY KEY (entite, adresse)
);

CREATE TABLE IF NOT EXISTS bl_scan.pla (
  nom_fournisseur     TEXT PRIMARY KEY
    REFERENCES bl_scan.base_tiers (name) ON UPDATE CASCADE,
  code_quai           TEXT NOT NULL
    REFERENCES bl_scan.quais (code_quai) ON UPDATE CASCADE,
  jours_livraison     TEXT,
  frequence_livraison TEXT,
  version             INTEGER NOT NULL DEFAULT 1 CHECK (version > 0)
);

CREATE TABLE IF NOT EXISTS bl_scan.roles_utilisateurs (
  utilisateur         TEXT NOT NULL CHECK (utilisateur = lower(utilisateur)),
  role                TEXT NOT NULL CHECK (
    role IN ('LOG', 'APPROS', 'ADV', 'FINANCE', 'ADMIN_METIER')
  ),
  attribue_par        TEXT,
  attribue_le         TIMESTAMPTZ NOT NULL DEFAULT now(),
  expire_le           TIMESTAMPTZ,
  PRIMARY KEY (utilisateur, role),
  CHECK (expire_le IS NULL OR expire_le > attribue_le)
);

-- ===========================================================================
-- 2. FLUX EDI (avis d'expédition venus de l'ERP)
-- ===========================================================================
CREATE TABLE IF NOT EXISTS bl_scan.base_desadv (
  numero_bl           TEXT NOT NULL,
  nom_fournisseur     TEXT NOT NULL
    REFERENCES bl_scan.base_tiers (name) ON UPDATE CASCADE,
  sens                TEXT NOT NULL CHECK (sens IN ('ACHAT', 'VENTE')),
  issuedatetime       TIMESTAMPTZ,
  integrationdate     DATE,
  statut_edi          TEXT CHECK (statut_edi IS NULL OR statut_edi IN ('OK', 'EDI NOK')),
  source_donnee       TEXT NOT NULL DEFAULT 'MANUEL' CHECK (source_donnee IN ('ERP', 'MANUEL')),
  source_key          TEXT,
  actif               BOOLEAN NOT NULL DEFAULT true,
  last_seen_at        TIMESTAMPTZ,
  payload_hash        TEXT,
  version             INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  PRIMARY KEY (numero_bl, sens)
);
CREATE INDEX IF NOT EXISTS idx_desadv_tiers
  ON bl_scan.base_desadv (sens, nom_fournisseur, integrationdate DESC);

-- ===========================================================================
-- 3. BORDEREAUX
-- ===========================================================================
CREATE TABLE IF NOT EXISTS bl_scan.suivi_bl (
  id_bl               TEXT PRIMARY KEY,
  numero_bl           TEXT NOT NULL CHECK (length(btrim(numero_bl)) BETWEEN 1 AND 80),
  date_reception      DATE,
  plage_horaire       TEXT,
  nom_fournisseur     TEXT
    REFERENCES bl_scan.base_tiers (name) ON UPDATE CASCADE,
  quai_reception      TEXT
    REFERENCES bl_scan.quais (code_quai) ON UPDATE CASCADE,
  statut_bl           TEXT CHECK (statut_bl IS NULL OR statut_bl IN ('0', '1')),
  comment_bl          TEXT CHECK (comment_bl IS NULL OR length(comment_bl) <= 2000),
  saisie_par          TEXT NOT NULL,
  saisie_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
  modifie_par         TEXT,
  modifie_le          TIMESTAMPTZ,
  type_operation      TEXT NOT NULL CHECK (
    type_operation IN ('RECEPTION', 'EXPEDITION',
                       'ARCHIVAGE_RECEPTION', 'ARCHIVAGE_EXPEDITION')
  ),
  sens                TEXT GENERATED ALWAYS AS (
    CASE
      WHEN type_operation IN ('RECEPTION', 'ARCHIVAGE_RECEPTION') THEN 'ACHAT'
      ELSE 'VENTE'
    END
  ) STORED,
  source_donnee       TEXT NOT NULL DEFAULT 'MANUEL' CHECK (source_donnee IN ('ERP', 'MANUEL')),
  document_statut     TEXT NOT NULL DEFAULT 'BROUILLON'
    CHECK (document_statut IN ('BROUILLON', 'COMPLET', 'ERREUR')),
  -- Comment le BL est entré dans l'outil : automatiquement par le pipeline de
  -- scan, après validation humaine, ou par saisie manuelle de secours.
  origine             TEXT NOT NULL DEFAULT 'MANUEL'
    CHECK (origine IN ('SCAN_AUTO', 'SCAN_VALIDE', 'MANUEL')),
  est_supprime        BOOLEAN NOT NULL DEFAULT false,
  supprime_par        TEXT,
  supprime_le         TIMESTAMPTZ,
  desadv_rapproche    BOOLEAN NOT NULL DEFAULT false,
  desadv_rapproche_le TIMESTAMPTZ,
  version             INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  CHECK (
    (est_supprime = false AND supprime_par IS NULL AND supprime_le IS NULL)
    OR
    (est_supprime = true AND supprime_par IS NOT NULL AND supprime_le IS NOT NULL)
  )
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_suivi_bl_numero_sens
  ON bl_scan.suivi_bl (upper(numero_bl), sens);
CREATE INDEX IF NOT EXISTS idx_suivi_bl_saisie
  ON bl_scan.suivi_bl (saisie_le DESC);
CREATE INDEX IF NOT EXISTS idx_suivi_bl_date
  ON bl_scan.suivi_bl (sens, date_reception DESC);
CREATE INDEX IF NOT EXISTS idx_suivi_bl_tiers
  ON bl_scan.suivi_bl (nom_fournisseur, date_reception DESC);
CREATE INDEX IF NOT EXISTS idx_suivi_bl_origine
  ON bl_scan.suivi_bl (origine, saisie_le DESC);

CREATE TABLE IF NOT EXISTS bl_scan.pieces_jointes_bl (
  id_photo            TEXT PRIMARY KEY,
  id_bl               TEXT NOT NULL
    REFERENCES bl_scan.suivi_bl (id_bl) ON DELETE RESTRICT,
  contenu             BYTEA,
  storage_uri         TEXT,
  sha256              TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
  taille_octets       BIGINT NOT NULL CHECK (taille_octets > 0),
  content_type        TEXT NOT NULL DEFAULT 'image/jpeg',
  index_page          INTEGER NOT NULL CHECK (index_page >= 0),
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (id_bl, index_page),
  CHECK ((contenu IS NOT NULL) <> (storage_uri IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_pieces_id_bl
  ON bl_scan.pieces_jointes_bl (id_bl, index_page);

-- ===========================================================================
-- 4. SCANS REÇUS — file d'attente alimentée par le copieur
-- ===========================================================================
-- Un scan = UN bordereau, de 1 à 25 pages. Le PDF source est conservé tel
-- quel : c'est la pièce probante en cas de litige, et il permet de rejouer
-- l'analyse sans redemander un scan.
CREATE TABLE IF NOT EXISTS bl_scan.scans_recus (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  -- Internet-Message-Id du mail : SEULE clé d'idempotence fiable. Les
  -- copieurs réutilisent leurs noms de fichiers, un mail peut être livré
  -- deux fois, et un job peut être relancé.
  message_id          TEXT NOT NULL UNIQUE,
  source              TEXT NOT NULL DEFAULT 'MAIL'
    CHECK (source IN ('MAIL', 'MANUEL')),
  expediteur          TEXT,
  destinataire        TEXT,          -- l'alias reçu : détermine le sens
  objet               TEXT,
  nom_fichier         TEXT NOT NULL,
  contenu             BYTEA NOT NULL,
  taille_octets       BIGINT NOT NULL CHECK (taille_octets > 0),
  nb_pages            INTEGER CHECK (nb_pages IS NULL OR nb_pages > 0),
  sens                TEXT NOT NULL CHECK (sens IN ('ACHAT', 'VENTE')),
  recu_le             TIMESTAMPTZ NOT NULL,      -- horodatage du mail
  ingere_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
  statut              TEXT NOT NULL DEFAULT 'RECU' CHECK (statut IN (
                        'RECU',          -- en attente d'analyse
                        'EN_ANALYSE',    -- verrou du job de traitement
                        'TRAITE_AUTO',   -- BL créé automatiquement
                        'A_VALIDER',     -- rapprochement insuffisant
                        'VALIDE',        -- BL créé après validation humaine
                        'REJETE',        -- écarté (pas un BL, doublon…)
                        'ERREUR')),      -- échec technique, à rejouer
  extraction          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- champs détectés
  confiance           TEXT,          -- code | desadv | nom | aucun
  motif_validation    TEXT,          -- pourquoi pas de création automatique
  id_bl               TEXT REFERENCES bl_scan.suivi_bl (id_bl),
  -- Verrou de prise en charge : évite que deux gestionnaires valident le même
  -- scan. Libéré par le job de maintenance après expiration.
  verrouille_par      TEXT,
  verrouille_le       TIMESTAMPTZ,
  analyse_le          TIMESTAMPTZ,
  traite_le           TIMESTAMPTZ,
  traite_par          TEXT,
  tentatives          INTEGER NOT NULL DEFAULT 0 CHECK (tentatives >= 0),
  erreur              TEXT
);
CREATE INDEX IF NOT EXISTS idx_scans_a_traiter
  ON bl_scan.scans_recus (statut, recu_le)
  WHERE statut IN ('RECU', 'A_VALIDER', 'ERREUR');
CREATE INDEX IF NOT EXISTS idx_scans_recents
  ON bl_scan.scans_recus (recu_le DESC);

-- ===========================================================================
-- 5. AUDIT, QUALITÉ, ÉCRANS, NOTIFICATIONS, JOBS, RAPPORTS
-- ===========================================================================
CREATE TABLE IF NOT EXISTS bl_scan.audit_bl (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  id_bl               TEXT NOT NULL,
  evenement           TEXT NOT NULL,
  champ               TEXT,
  valeur_avant        TEXT,
  valeur_apres        TEXT,
  modifie_par         TEXT NOT NULL,
  modifie_le          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_bl_id
  ON bl_scan.audit_bl (id_bl, modifie_le DESC);

CREATE TABLE IF NOT EXISTS bl_scan.audit_evenements (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  categorie           TEXT NOT NULL,
  action              TEXT NOT NULL,
  cible               TEXT,
  acteur              TEXT NOT NULL,
  details             JSONB NOT NULL DEFAULT '{}'::jsonb,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_evenements
  ON bl_scan.audit_evenements (cree_le DESC, categorie);

CREATE TABLE IF NOT EXISTS bl_scan.qualite_extraction (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now(),
  utilisateur         TEXT,
  numero_bl           TEXT,
  champ               TEXT NOT NULL,
  valeur_ia           TEXT,
  valeur_validee      TEXT,
  identique           BOOLEAN NOT NULL,
  modele_endpoint     TEXT,
  prompt_version      TEXT,
  score_confiance     NUMERIC(5,4)
    CHECK (score_confiance IS NULL OR score_confiance BETWEEN 0 AND 1)
);
CREATE INDEX IF NOT EXISTS idx_qualite_champ
  ON bl_scan.qualite_extraction (champ, cree_le DESC);

CREATE TABLE IF NOT EXISTS bl_scan.ecrans_utilisateur (
  utilisateur         TEXT NOT NULL,
  vue                 TEXT NOT NULL,
  nom                 TEXT NOT NULL,
  est_defaut          BOOLEAN NOT NULL DEFAULT false,
  etat                TEXT NOT NULL,
  modifie_le          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (utilisateur, vue, nom)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ecran_defaut
  ON bl_scan.ecrans_utilisateur (lower(utilisateur), vue)
  WHERE est_defaut = true;

CREATE TABLE IF NOT EXISTS bl_scan.notifications (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_key           TEXT NOT NULL UNIQUE,
  type_notif          TEXT NOT NULL,   -- NOUVELLE_RECEPTION | EDI_NOK_OK
  numero_bl           TEXT,
  message             TEXT NOT NULL,
  commentaire         TEXT,
  destinataires       TEXT,
  envoyee             BOOLEAN NOT NULL DEFAULT false,
  envoyee_le          TIMESTAMPTZ,
  erreur_envoi        TEXT,
  cree_le             TIMESTAMPTZ NOT NULL DEFAULT now(),
  cree_par            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_cree_le
  ON bl_scan.notifications (cree_le DESC);

CREATE TABLE IF NOT EXISTS bl_scan.job_executions (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_name            TEXT NOT NULL,
  run_id              TEXT,
  statut              TEXT NOT NULL CHECK (statut IN ('STARTED', 'SUCCEEDED', 'FAILED')),
  started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at         TIMESTAMPTZ,
  metrics             JSONB NOT NULL DEFAULT '{}'::jsonb,
  erreur              TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_executions
  ON bl_scan.job_executions (job_name, started_at DESC);

CREATE TABLE IF NOT EXISTS bl_scan.rapports_activite (
  id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  periodicite         TEXT NOT NULL CHECK (periodicite IN (
                        'QUOTIDIEN', 'HEBDOMADAIRE', 'MENSUEL',
                        'TRIMESTRIEL', 'ANNUEL')),
  periode_debut       DATE NOT NULL,
  periode_fin         DATE NOT NULL,
  libelle             TEXT NOT NULL,
  contenu             BYTEA NOT NULL,
  taille_octets       BIGINT NOT NULL CHECK (taille_octets > 0),
  synthese            TEXT,
  analyse_ia          BOOLEAN NOT NULL DEFAULT false,
  metriques           JSONB NOT NULL DEFAULT '{}'::jsonb,
  genere_le           TIMESTAMPTZ NOT NULL DEFAULT now(),
  genere_par          TEXT NOT NULL,
  CHECK (periode_fin >= periode_debut)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_rapport_periode
  ON bl_scan.rapports_activite (periodicite, periode_debut);
CREATE INDEX IF NOT EXISTS idx_rapport_recent
  ON bl_scan.rapports_activite (periode_debut DESC, periodicite);

-- ===========================================================================
-- 6. VUES
-- ===========================================================================
CREATE OR REPLACE VIEW bl_scan.v_rapprochement_bl_desadv AS
SELECT
  b.id_bl,
  b.numero_bl,
  b.sens,
  b.nom_fournisseur AS tiers_bl,
  d.nom_fournisseur AS tiers_desadv,
  b.date_reception,
  d.integrationdate,
  (d.numero_bl IS NOT NULL) AS rapproche,
  (d.numero_bl IS NOT NULL AND d.nom_fournisseur IS DISTINCT FROM b.nom_fournisseur)
    AS tiers_different
FROM bl_scan.suivi_bl b
LEFT JOIN bl_scan.base_desadv d
  ON upper(d.numero_bl) = upper(b.numero_bl)
 AND d.sens = b.sens
 AND d.actif = true
WHERE b.est_supprime = false
  AND b.document_statut = 'COMPLET';

-- ===========================================================================
-- 7. AMORÇAGE
-- ===========================================================================
INSERT INTO bl_scan.quais (code_quai)
VALUES ('B15'), ('B06EST'), ('B06NORD'), ('B02NORD'), ('AUTRE')
ON CONFLICT DO NOTHING;
