-- OMEGA World Model — Full Schema
-- Run once against the Knock AI prod database.
-- All tables are additive — safe to run on existing DB.

-- ─────────────────────────────────────────────
-- EXTENSIONS
-- ─────────────────────────────────────────────
-- CREATE EXTENSION IF NOT EXISTS postgis;  -- pre-created via system auth
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ─────────────────────────────────────────────
-- SOURCE REGISTRY
-- Every data source Omega knows how to ingest.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_source_registry (
    source_id           VARCHAR(64) PRIMARY KEY,
    source_name         TEXT NOT NULL,
    publisher           TEXT,
    category            TEXT,        -- boundaries, poi, buildings, land_cover, agricultural, soil, health, climate, etc.
    access_method       TEXT,        -- bulk_download, api, wfs, wms, object_storage
    endpoint            TEXT,
    license             TEXT,
    coverage            TEXT,        -- national, state, county, global
    geography           TEXT,        -- US, FL, MI, global
    temporal_coverage   TEXT,
    update_frequency    TEXT,        -- annual, monthly, daily, realtime, static
    auth_required       BOOLEAN DEFAULT false,
    last_successful_ingest TIMESTAMPTZ,
    last_modified       TIMESTAMPTZ,
    schema_version      INTEGER DEFAULT 1,
    quality_score       FLOAT,
    reliability_score   FLOAT,
    active              BOOLEAN DEFAULT true,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- INGESTION MANIFEST
-- One row per downloaded file. SHA-256 dedup.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_ingestion_manifest (
    manifest_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64) REFERENCES world_source_registry(source_id),
    source_version      TEXT,
    source_url          TEXT,
    retrieved_at        TIMESTAMPTZ,
    file_hash           VARCHAR(64),    -- SHA-256 hex
    file_size_bytes     BIGINT,
    format              TEXT,           -- geojson, shapefile, geoparquet, geotiff, csv, json
    compression         TEXT,           -- zstd, gzip, none
    object_location     TEXT,           -- local path or object storage URI
    license             TEXT,
    transformation_ver  TEXT,
    row_count           BIGINT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    status              TEXT DEFAULT 'pending',  -- pending, processing, complete, failed, quarantined
    error_message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_manifest_source ON world_ingestion_manifest(source_id);
CREATE INDEX IF NOT EXISTS idx_manifest_hash ON world_ingestion_manifest(file_hash);

-- ─────────────────────────────────────────────
-- WORLD BOUNDARIES
-- Census TIGER, Overture Divisions, admin areas.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_boundaries (
    boundary_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    source_entity_id    TEXT,
    gers_id             TEXT,           -- Overture Global Entity Reference System
    boundary_type       TEXT NOT NULL,  -- country, state, county, tract, block_group, precinct, zcta, place
    name                TEXT,
    name_alt            TEXT[],
    admin_level         INTEGER,        -- 2=country, 4=state, 6=county, 8=city, 10=tract
    fips_code           TEXT,
    geoid               TEXT,
    state_fips          CHAR(2),
    county_fips         CHAR(3),
    h3_r7               TEXT[],         -- H3 cells at r7 intersecting this boundary
    h3_r9               TEXT[],         -- H3 cells at r9 intersecting this boundary
    geometry            GEOMETRY(MultiPolygon, 4326),
    area_sqkm           FLOAT,
    effective_from      DATE,
    effective_to        DATE,
    source_version      TEXT,
    observed_at         TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_boundaries_geoid_type ON world_boundaries(geoid, boundary_type);
CREATE INDEX IF NOT EXISTS idx_boundaries_geom ON world_boundaries USING GIST(geometry);
CREATE INDEX IF NOT EXISTS idx_boundaries_type ON world_boundaries(boundary_type);
CREATE INDEX IF NOT EXISTS idx_boundaries_fips ON world_boundaries(fips_code);
CREATE INDEX IF NOT EXISTS idx_boundaries_state ON world_boundaries(state_fips);
CREATE INDEX IF NOT EXISTS idx_boundaries_geoid ON world_boundaries(geoid);

-- ─────────────────────────────────────────────
-- WORLD GEONAMES
-- Place name resolution layer.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_geonames (
    geoname_id          INTEGER PRIMARY KEY,
    name                TEXT NOT NULL,
    ascii_name          TEXT,
    alternate_names     TEXT[],
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    feature_class       CHAR(1),        -- A=admin, H=water, L=parks, P=populated place, R=road, S=spot, T=terrain, U=undersea, V=forest
    feature_code        TEXT,
    country_code        CHAR(2),
    admin1_code         TEXT,           -- state
    admin2_code         TEXT,           -- county
    admin3_code         TEXT,
    admin4_code         TEXT,
    population          BIGINT,
    elevation_m         INTEGER,
    timezone            TEXT,
    h3_r7               TEXT,
    h3_r9               TEXT,
    modification_date   DATE,
    ingested_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_geonames_h3_r9 ON world_geonames(h3_r9);
CREATE INDEX IF NOT EXISTS idx_geonames_country ON world_geonames(country_code);
CREATE INDEX IF NOT EXISTS idx_geonames_feature ON world_geonames(feature_class, feature_code);
CREATE INDEX IF NOT EXISTS idx_geonames_name_trgm ON world_geonames USING GIN(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_geonames_admin1 ON world_geonames(country_code, admin1_code);

-- ─────────────────────────────────────────────
-- WORLD BUILDINGS
-- Microsoft Building Footprints / Overture.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_buildings (
    building_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    source_entity_id    TEXT,
    gers_id             TEXT,
    building_type       TEXT,
    height_m            FLOAT,
    floors              INTEGER,
    area_sqm            FLOAT,
    h3_r9               TEXT NOT NULL,
    geometry            GEOMETRY(Polygon, 4326),
    confidence          FLOAT,
    state_fips          CHAR(2),
    county_fips         CHAR(3),
    source_version      TEXT,
    observed_at         TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_buildings_geom ON world_buildings USING GIST(geometry);
CREATE INDEX IF NOT EXISTS idx_buildings_h3 ON world_buildings(h3_r9);
CREATE INDEX IF NOT EXISTS idx_buildings_state ON world_buildings(state_fips);

-- ─────────────────────────────────────────────
-- WORLD LAND COVER
-- USGS NLCD — what is on the land by H3 cell.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_land_cover (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    h3_r9               TEXT NOT NULL,
    year                INTEGER NOT NULL,
    source_id           VARCHAR(64),
    source_version      TEXT,
    land_cover_class    INTEGER,        -- NLCD class code
    land_cover_name     TEXT,
    coverage_fraction   FLOAT DEFAULT 1.0,  -- fraction of H3 cell with this class
    confidence          FLOAT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (h3_r9, year, land_cover_class)
);
CREATE INDEX IF NOT EXISTS idx_land_cover_h3 ON world_land_cover(h3_r9);
CREATE INDEX IF NOT EXISTS idx_land_cover_year ON world_land_cover(year);
CREATE INDEX IF NOT EXISTS idx_land_cover_class ON world_land_cover(land_cover_class);

-- ─────────────────────────────────────────────
-- WORLD AGRICULTURAL
-- USDA NASS Cropland Data Layer (CDL).
-- Crop type by H3 cell per year.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_agricultural (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    h3_r9               TEXT NOT NULL,
    year                INTEGER NOT NULL,
    source_id           VARCHAR(64),
    source_version      TEXT,
    crop_code           INTEGER,
    crop_name           TEXT,
    coverage_fraction   FLOAT,          -- fraction of H3 cell with this crop
    confidence          FLOAT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (h3_r9, year, crop_code)
);
CREATE INDEX IF NOT EXISTS idx_agricultural_h3 ON world_agricultural(h3_r9);
CREATE INDEX IF NOT EXISTS idx_agricultural_year ON world_agricultural(year);
CREATE INDEX IF NOT EXISTS idx_agricultural_crop ON world_agricultural(crop_name);

-- ─────────────────────────────────────────────
-- WORLD SOIL
-- USDA SSURGO — soil properties by H3 cell.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_soil (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    h3_r9               TEXT NOT NULL,
    source_id           VARCHAR(64),
    mukey               TEXT,           -- SSURGO map unit key
    musym               TEXT,
    muname              TEXT,
    drainage_class      TEXT,
    hydric_rating       TEXT,
    slope_pct           FLOAT,
    texture             TEXT,
    organic_matter_pct  FLOAT,
    available_water_cap FLOAT,          -- cm/cm
    ph                  FLOAT,
    hydrologic_group    TEXT,           -- A/B/C/D
    source_version      TEXT,
    observed_at         DATE,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (h3_r9, mukey)
);
CREATE INDEX IF NOT EXISTS idx_soil_h3 ON world_soil(h3_r9);
CREATE INDEX IF NOT EXISTS idx_soil_drainage ON world_soil(drainage_class);

-- ─────────────────────────────────────────────
-- WORLD HEALTH
-- CDC PLACES — health outcomes by census tract.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_health (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    source_geography    TEXT,           -- county, place, census_tract, zcta
    source_geography_id TEXT,           -- FIPS / GEOID
    h3_r7               TEXT,
    year                INTEGER,
    measure             TEXT,           -- e.g. STROKE, DIABETES, OBESITY
    category            TEXT,           -- health_outcomes, prevention, health_status, disability
    data_value          FLOAT,
    data_value_type     TEXT,           -- crude_prevalence, age_adjusted_prevalence
    confidence_low      FLOAT,
    confidence_high     FLOAT,
    methodology         TEXT,
    source_version      TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (source_geography_id, year, measure)
);
CREATE INDEX IF NOT EXISTS idx_health_h3 ON world_health(h3_r7);
CREATE INDEX IF NOT EXISTS idx_health_measure ON world_health(measure);
CREATE INDEX IF NOT EXISTS idx_health_geoid ON world_health(source_geography_id);

-- ─────────────────────────────────────────────
-- WORLD VULNERABILITY
-- CDC/ATSDR Social Vulnerability Index.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_vulnerability (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    svi_version         TEXT,
    source_year         INTEGER,
    fips                TEXT NOT NULL,  -- 11-digit census tract FIPS
    state               TEXT,
    county              TEXT,
    location            TEXT,
    area_sqmi           FLOAT,
    total_pop           INTEGER,
    spl_themes          FLOAT,          -- overall SVI score (sum of flags)
    rpl_themes          FLOAT,          -- overall percentile ranking (0-1)
    rpl_theme1          FLOAT,          -- socioeconomic status percentile
    rpl_theme2          FLOAT,          -- household characteristics percentile
    rpl_theme3          FLOAT,          -- racial & ethnic minority status percentile
    rpl_theme4          FLOAT,          -- housing type & transportation percentile
    h3_r7               TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fips, svi_version)
);
CREATE INDEX IF NOT EXISTS idx_vulnerability_fips ON world_vulnerability(fips);
CREATE INDEX IF NOT EXISTS idx_vulnerability_h3 ON world_vulnerability(h3_r7);
CREATE INDEX IF NOT EXISTS idx_vulnerability_rpl ON world_vulnerability(rpl_themes);

-- ─────────────────────────────────────────────
-- WORLD FLOOD RISK
-- FEMA National Flood Hazard Layer.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_flood_risk (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    fld_zone            TEXT,           -- A, AE, AH, AO, AR, AV, V, VE, X (shaded/unshaded)
    fld_zone_subtype    TEXT,
    zone_description    TEXT,
    sfha                BOOLEAN,        -- Special Flood Hazard Area (100-yr)
    static_bfe          FLOAT,          -- Base Flood Elevation (feet)
    dfirm_id            TEXT,
    panel_id            TEXT,
    effective_date      DATE,
    study_type          TEXT,
    h3_r9               TEXT,
    geometry            GEOMETRY(MultiPolygon, 4326),
    source_version      TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_flood_geom ON world_flood_risk USING GIST(geometry);
CREATE INDEX IF NOT EXISTS idx_flood_h3 ON world_flood_risk(h3_r9);
CREATE INDEX IF NOT EXISTS idx_flood_zone ON world_flood_risk(fld_zone);
CREATE INDEX IF NOT EXISTS idx_flood_sfha ON world_flood_risk(sfha);

-- ─────────────────────────────────────────────
-- WORLD CLIMATE
-- NOAA CDO — weather station observations.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_climate (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id           VARCHAR(64),
    station_id          TEXT,
    station_name        TEXT,
    dataset             TEXT,           -- GHCND, GSOM, GSOY, NORMAL_DLY
    date                DATE NOT NULL,
    h3_r7               TEXT,
    h3_r9               TEXT,
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    element             TEXT,           -- TMAX, TMIN, PRCP, SNOW, SNWD, AWND
    value               FLOAT,
    unit                TEXT,
    mflag               CHAR(1),        -- measurement flag
    qflag               CHAR(1),        -- quality flag
    sflag               CHAR(1),        -- source flag
    source_version      TEXT,
    ingested_at         TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (station_id, dataset, date, element)
);
CREATE INDEX IF NOT EXISTS idx_climate_h3_r7 ON world_climate(h3_r7);
CREATE INDEX IF NOT EXISTS idx_climate_date ON world_climate(date);
CREATE INDEX IF NOT EXISTS idx_climate_element ON world_climate(element);
CREATE INDEX IF NOT EXISTS idx_climate_station ON world_climate(station_id);

-- ─────────────────────────────────────────────
-- WORLD OBSERVATIONS  ← Knock Jobs output
-- The proprietary dynamic layer. Company IP.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_observations (
    observation_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID,
    h3_r7               TEXT,
    h3_r9               TEXT,
    observer_id         UUID,           -- canvasser / worker
    observation_type    TEXT,           -- ask, observe, verify, document, measure, compare, monitor
    claim               TEXT,
    value               JSONB,
    unit                TEXT,
    geometry            GEOMETRY(Point, 4326),
    observed_at         TIMESTAMPTZ NOT NULL,
    evidence_uris       TEXT[],         -- photo URLs, document URLs
    source_type         TEXT,           -- knock_job, api, sensor, manual
    confidence          FLOAT,
    verification_status TEXT DEFAULT 'pending',  -- pending, verified, rejected, disputed
    methodology_id      UUID,
    job_id              UUID,
    ontology            TEXT,           -- biochar, agricultural, political, real_estate, retail, etc.
    domain_data         JSONB,          -- ontology-specific fields
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_observations_h3 ON world_observations(h3_r9);
CREATE INDEX IF NOT EXISTS idx_observations_entity ON world_observations(entity_id);
CREATE INDEX IF NOT EXISTS idx_observations_type ON world_observations(observation_type);
CREATE INDEX IF NOT EXISTS idx_observations_time ON world_observations(observed_at);
CREATE INDEX IF NOT EXISTS idx_observations_ontology ON world_observations(ontology);
CREATE INDEX IF NOT EXISTS idx_observations_geom ON world_observations USING GIST(geometry);

-- ─────────────────────────────────────────────
-- WORLD CHANGES
-- What changed, when, compared to prior state.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_changes (
    change_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_id           UUID,
    h3_r9               TEXT,
    change_type         TEXT,           -- appeared, disappeared, modified, reclassified, updated
    attribute           TEXT,           -- which attribute changed
    previous_state      JSONB,
    new_state           JSONB,
    detected_at         TIMESTAMPTZ NOT NULL,
    effective_at        TIMESTAMPTZ,
    evidence            JSONB,
    confidence          FLOAT,
    source              TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_changes_entity ON world_changes(entity_id);
CREATE INDEX IF NOT EXISTS idx_changes_h3 ON world_changes(h3_r9);
CREATE INDEX IF NOT EXISTS idx_changes_time ON world_changes(detected_at);
CREATE INDEX IF NOT EXISTS idx_changes_type ON world_changes(change_type);

-- ─────────────────────────────────────────────
-- WORLD HYPOTHESES
-- Omega's active beliefs about the world.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_hypotheses (
    hypothesis_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_entity      UUID,
    h3_r9               TEXT,
    claim               TEXT NOT NULL,
    supporting_obs      UUID[],
    contradicting_obs   UUID[],
    confidence          FLOAT,
    status              TEXT DEFAULT 'active',  -- active, confirmed, rejected, superseded
    ontology            TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_entity ON world_hypotheses(subject_entity);
CREATE INDEX IF NOT EXISTS idx_hypotheses_h3 ON world_hypotheses(h3_r9);
CREATE INDEX IF NOT EXISTS idx_hypotheses_status ON world_hypotheses(status);

-- ─────────────────────────────────────────────
-- WORLD PREDICTIONS
-- Omega's predictions with outcome tracking.
-- ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS world_predictions (
    prediction_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hypothesis_id       UUID REFERENCES world_hypotheses(hypothesis_id),
    prediction          TEXT NOT NULL,
    confidence          FLOAT,
    assumptions         JSONB,
    expected_by         TIMESTAMPTZ,
    outcome             TEXT,
    outcome_at          TIMESTAMPTZ,
    accuracy            FLOAT,          -- 0.0-1.0
    error_description   TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- ─────────────────────────────────────────────
-- SEED SOURCE REGISTRY
-- ─────────────────────────────────────────────
INSERT INTO world_source_registry (source_id, source_name, publisher, category, access_method, endpoint, license, coverage, geography, update_frequency, active) VALUES
('census_tiger_2023',  'Census TIGER/Line 2023',           'US Census Bureau',        'boundaries',   'bulk_download', 'https://www2.census.gov/geo/tiger/TIGER2023/', 'Public Domain', 'national', 'US', 'annual', true),
('geonames_us',        'GeoNames United States',           'GeoNames',                'places',        'bulk_download', 'https://download.geonames.org/export/dump/US.zip', 'CC BY 4.0', 'national', 'US', 'weekly', true),
('ms_buildings',       'Microsoft US Building Footprints', 'Microsoft',               'buildings',     'bulk_download', 'https://github.com/microsoft/USBuildingFootprints', 'ODbL', 'national', 'US', 'periodic', true),
('nlcd_2021',          'National Land Cover Database 2021','USGS',                    'land_cover',    'bulk_download', 'https://www.mrlc.gov/data', 'Public Domain', 'national', 'US', 'periodic', true),
('usda_cdl',           'USDA NASS Cropland Data Layer',    'USDA NASS',               'agricultural',  'wcs', 'https://nassgeodata.gmu.edu/axis2/services/CDLService', 'Public Domain', 'national', 'US', 'annual', true),
('usda_ssurgo',        'SSURGO Soil Survey',               'USDA NRCS',               'soil',          'api', 'https://sdmdataaccess.sc.egov.usda.gov/tabular/post.rest', 'Public Domain', 'national', 'US', 'periodic', true),
('fema_nfhl',          'National Flood Hazard Layer',      'FEMA',                    'flood_risk',    'wfs', 'https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer', 'Public Domain', 'national', 'US', 'continuous', true),
('cdc_places_2023',    'CDC PLACES 2023',                  'CDC',                     'health',        'api', 'https://chronicdata.cdc.gov/resource/cwsq-ngmh.json', 'Public Domain', 'national', 'US', 'annual', true),
('cdc_svi_2022',       'CDC/ATSDR Social Vulnerability Index 2022', 'CDC/ATSDR',      'vulnerability', 'bulk_download', 'https://svi.cdc.gov/data-and-tools-download.html', 'Public Domain', 'national', 'US', 'biennial', true),
('noaa_cdo',           'NOAA Climate Data Online (GHCND/GSOM)', 'NOAA/NCDC',          'climate',       'api', 'https://www.ncdc.noaa.gov/cdo-web/api/v2/', 'Public Domain', 'national', 'US', 'daily', true),
('overture_2024',      'Overture Maps Foundation 2024',    'Overture Maps Foundation','poi,buildings,boundaries', 'object_storage', 's3://overturemaps-us-west-2/release/', 'ODbL', 'global', 'global', 'quarterly', true)
ON CONFLICT (source_id) DO NOTHING;
