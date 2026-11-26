-- ===========================================================================
-- V002 — Scans contenant plusieurs bordereaux (lots)
--
-- Un scan peut désormais être une PILE de bordereaux passée d'un seul coup au
-- copieur. Il devient alors un « lot », découpé en groupes de pages — un par
-- BL. Chaque groupe est une ligne FILLE de scans_recus, qui suit le circuit
-- habituel (analyse, création automatique ou validation).
--
-- À exécuter après V001, dans l'éditeur SQL Lakebase. Idempotent : peut être
-- rejoué sans risque. Sans effet sur les scans déjà traités.
-- ===========================================================================

ALTER TABLE bl_scan.scans_recus
  -- Scan d'origine d'un groupe. NULL pour un scan reçu du copieur.
  ADD COLUMN IF NOT EXISTS id_parent  BIGINT
      REFERENCES bl_scan.scans_recus (id) ON DELETE CASCADE,
  -- Première et dernière page du groupe DANS le lot (à partir de 1).
  ADD COLUMN IF NOT EXISTS page_debut INTEGER,
  ADD COLUMN IF NOT EXISTS page_fin   INTEGER,
  -- Sur un lot  : fiches des pages, coupes proposées, incertitudes.
  -- Sur un fils : ses pages, ses annexes, ses avertissements.
  ADD COLUMN IF NOT EXISTS decoupage  JSONB;

-- Deux statuts de plus pour un lot :
--   A_DECOUPER : découpage incertain, un gestionnaire doit le confirmer ;
--   DECOUPE    : lot réparti en lignes filles, plus rien à faire dessus.
-- La contrainte d'origine est anonyme : on retire toute contrainte CHECK qui
-- porte sur la colonne statut, quel que soit le nom que Postgres lui a donné.
DO $$
DECLARE
  contrainte RECORD;
BEGIN
  FOR contrainte IN
    SELECT c.conname
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE n.nspname = 'bl_scan' AND t.relname = 'scans_recus'
       AND c.contype = 'c'
       AND pg_get_constraintdef(c.oid) LIKE '%TRAITE_AUTO%'
  LOOP
    EXECUTE format('ALTER TABLE bl_scan.scans_recus DROP CONSTRAINT %I',
                   contrainte.conname);
  END LOOP;
END $$;

ALTER TABLE bl_scan.scans_recus
  ADD CONSTRAINT scans_recus_statut_check CHECK (statut IN (
    'RECU',          -- en attente d'analyse
    'EN_ANALYSE',    -- verrou du job de traitement
    'TRAITE_AUTO',   -- BL créé automatiquement
    'A_VALIDER',     -- rapprochement insuffisant
    'VALIDE',        -- BL créé après validation humaine
    'REJETE',        -- écarté (pas un BL, doublon…)
    'ERREUR',        -- échec technique, à rejouer
    'A_DECOUPER',    -- lot : découpage à confirmer par un gestionnaire
    'DECOUPE'));     -- lot : réparti en lignes filles

-- Un groupe porte ses pages, et seulement lui.
ALTER TABLE bl_scan.scans_recus
  DROP CONSTRAINT IF EXISTS scans_recus_pages_lot_check;
ALTER TABLE bl_scan.scans_recus
  ADD CONSTRAINT scans_recus_pages_lot_check CHECK (
    (id_parent IS NULL AND page_debut IS NULL AND page_fin IS NULL)
    OR (id_parent IS NOT NULL AND page_debut >= 1 AND page_fin >= page_debut));

CREATE INDEX IF NOT EXISTS idx_scans_parent
  ON bl_scan.scans_recus (id_parent) WHERE id_parent IS NOT NULL;

-- La file de travail inclut désormais les lots à découper.
DROP INDEX IF EXISTS bl_scan.idx_scans_a_traiter;
CREATE INDEX IF NOT EXISTS idx_scans_a_traiter
  ON bl_scan.scans_recus (statut, recu_le)
  WHERE statut IN ('RECU', 'A_VALIDER', 'A_DECOUPER', 'ERREUR');
