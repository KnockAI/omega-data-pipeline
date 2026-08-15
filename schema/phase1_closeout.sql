-- Additive Phase 1 observability, precision, and normalized activity contracts.
CREATE TABLE IF NOT EXISTS omega_ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    source_key TEXT NOT NULL,
    dataset_key TEXT NOT NULL,
    geography TEXT NOT NULL DEFAULT 'unknown',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','complete','partial','failed','rate_limited','blocked')),
    records_fetched BIGINT NOT NULL DEFAULT 0,
    records_inserted BIGINT NOT NULL DEFAULT 0,
    records_updated BIGINT NOT NULL DEFAULT 0,
    records_rejected BIGINT NOT NULL DEFAULT 0,
    source_vintage TEXT,
    source_url TEXT,
    coverage TEXT,
    freshness TEXT,
    adapter_version TEXT,
    error_summary TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_source ON omega_ingestion_runs(source_key, dataset_key, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingestion_runs_status ON omega_ingestion_runs(status);

CREATE TABLE IF NOT EXISTS omega_zip5_h3 (
    zip5 CHAR(5) PRIMARY KEY,
    state CHAR(2),
    centroid_lat DOUBLE PRECISION,
    centroid_lng DOUBLE PRECISION,
    h3_cell TEXT NOT NULL,
    h3_resolution SMALLINT NOT NULL CHECK (h3_resolution BETWEEN 0 AND 15),
    mapping_method TEXT NOT NULL,
    precision_class TEXT NOT NULL CHECK (precision_class IN ('zip_centroid','zcta_approximation','validated_coordinate')),
    source TEXT NOT NULL,
    source_vintage TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_zip5_h3_state ON omega_zip5_h3(state);
CREATE INDEX IF NOT EXISTS idx_zip5_h3_cell ON omega_zip5_h3(h3_cell);

CREATE TABLE IF NOT EXISTS omega_permits_normalized (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    permit_id TEXT NOT NULL,
    permit_type TEXT,
    status TEXT,
    issued_at DATE,
    value NUMERIC(14,2),
    address TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    h3_cell TEXT,
    jurisdiction TEXT,
    source_precision TEXT NOT NULL,
    source_vintage TEXT,
    raw_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source, permit_id)
);

CREATE OR REPLACE VIEW omega_data_health AS
SELECT source_key, dataset_key, geography, status, coverage, freshness,
       source_vintage, records_fetched, records_inserted, records_rejected,
       started_at, completed_at, error_summary
FROM (
  SELECT r.*, ROW_NUMBER() OVER (PARTITION BY source_key, dataset_key, geography ORDER BY started_at DESC) AS rn
  FROM omega_ingestion_runs r
) latest WHERE rn = 1;

INSERT INTO world_source_registry (source_id, source_name, publisher, category, access_method, endpoint, license, coverage, geography, update_frequency, active)
VALUES
 ('acs_demographics','Census ACS 5-year demographics','US Census Bureau','demographics','api','https://api.census.gov/data/','Public Domain','national','US','annual',true),
 ('cbp_business','Census County Business Patterns','US Census Bureau','economic','api','https://api.census.gov/data/','Public Domain','national','US','annual',true),
 ('bls_laus','BLS Local Area Unemployment Statistics','US BLS','economic','api','https://api.bls.gov/publicAPI/v2/timeseries/data/','Public Domain','national','US','monthly',true),
 ('fec_contributions','FEC individual contributions','US FEC','political','bulk_download','https://www.fec.gov/data/browse-data/?tab=bulk-data','Public Domain','national','US','quarterly',true),
 ('census_bps','Census Building Permits Survey','US Census Bureau','permits','bulk_download','https://www.census.gov/construction/bps/','Public Domain','national','US','monthly',true),
 ('municipal_socrata_permits','Municipal Socrata permit enrichment','Municipal open data','permits','api','https://data.seattle.gov/','Varies','partial','US','weekly',true)
ON CONFLICT (source_id) DO NOTHING;
