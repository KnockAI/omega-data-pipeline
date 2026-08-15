#!/usr/bin/env python3
"""
Omega Data Pipeline — CLI runner
Usage:
  python run.py schema              # Apply world model schema to DB
  python run.py geonames            # Load GeoNames US
  python run.py tiger               # Load Census TIGER boundaries
  python run.py cdc-places          # Load CDC PLACES health data
  python run.py svi                 # Load Social Vulnerability Index
  python run.py noaa                # Load NOAA climate data
  python run.py bls-laus            # Load BLS LAUS unemployment (monthly)
  python run.py acs                 # Load Census ACS tract demographics (annual)
  python run.py cbp                 # Load Census County Business Patterns (annual)
  python run.py fec                 # Load FEC individual contributions (quarterly)
  python run.py permits             # Load Socrata building permits (weekly)
  python run.py all                 # Run all sources
  python run.py status              # Show latest ingestion health
  python run.py national --resume    # Run only configured missing states
  python run.py zip5-h3             # Build Census ZCTA ZIP5 → H3 r9 bridge
  python run.py permits-national    # Load Census BPS national baseline
"""
import os
import sys
import click
from dotenv import load_dotenv

load_dotenv()

# Target geographies from env
STATE_FIPS = [f.strip() for f in os.environ.get("TARGET_STATE_FIPS", "26,12").split(",")]
STATE_ABBRS = {"26": "MI", "12": "FL"}
ABBRS = [STATE_ABBRS[f] for f in STATE_FIPS if f in STATE_ABBRS]


def get_conn():
    from pipeline.db import get_conn as _get_conn
    return _get_conn()


def execute_tracked(adapter, conn, dataset_key: str, geography: str):
    """Run an adapter under the shared registry; adapters remain independently usable."""
    from pipeline.observability import begin_run, finish_run
    from pipeline.locks import source_lock
    with source_lock(adapter.source_id, geography):
        urls = adapter.discover()
        run_id = begin_run(conn, adapter.source_id, dataset_key, geography,
                           source_url=urls[0] if urls else None, adapter_version="v1")
        try:
            adapter.run(conn)
        except Exception as exc:
            finish_run(conn, run_id, "failed", error=str(exc))
            raise
        finish_run(conn, run_id, "complete", coverage=geography, freshness="unknown")


def _configured_states():
    """Return configured FIPS without implying that they are nationally complete."""
    return STATE_FIPS


@click.group()
def cli():
    pass


@cli.command()
def schema():
    """Apply world model schema to the database."""
    from pipeline.db import apply_schema
    conn = get_conn()
    apply_schema(conn, "schema/world_tables.sql")
    conn.close()


@cli.command()
def status():
    """Show the latest truthfully recorded status for every source/dataset."""
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""SELECT source_key, dataset_key, geography, status,
                              COALESCE(coverage,''), COALESCE(freshness,''),
                              COALESCE(source_vintage,''), started_at, completed_at,
                              records_fetched, records_inserted, records_rejected,
                              COALESCE(error_summary,'')
                       FROM omega_data_health ORDER BY source_key, dataset_key, geography""")
        rows = cur.fetchall()
    conn.close()
    if not rows:
        click.echo("No ingestion runs recorded.")
        return
    for row in rows:
        click.echo(" | ".join(str(v) for v in row))


@cli.command()
def geonames():
    """Load GeoNames US place names."""
    from sources.geonames import GeoNamesUSAdapter
    conn = get_conn()
    execute_tracked(GeoNamesUSAdapter(), conn, "places", "national")
    conn.close()


@cli.command()
def tiger():
    """Load Census TIGER boundaries (counties, tracts, precincts)."""
    from sources.census_tiger import CensusTigerAdapter
    conn = get_conn()
    execute_tracked(CensusTigerAdapter(STATE_FIPS), conn, "boundaries", ",".join(STATE_FIPS))
    conn.close()


@cli.command("cdc-places")
def cdc_places():
    """Load CDC PLACES health outcomes."""
    from sources.cdc_places import CDCPlacesAdapter
    conn = get_conn()
    execute_tracked(CDCPlacesAdapter(ABBRS), conn, "health", ",".join(ABBRS) or "none")
    conn.close()


@cli.command()
def svi():
    """Load CDC/ATSDR Social Vulnerability Index."""
    from sources.svi import SVIAdapter
    conn = get_conn()
    execute_tracked(SVIAdapter(STATE_FIPS), conn, "vulnerability", ",".join(STATE_FIPS) or "none")
    conn.close()


@cli.command()
@click.option("--start", default="2020-01-01", help="Start date YYYY-MM-DD")
@click.option("--end", default=None, help="End date YYYY-MM-DD (default: yesterday)")
@click.option("--dataset", default="GSOM", help="NOAA dataset: GHCND, GSOM, GSOY")
def noaa(start, end, dataset):
    """Load NOAA climate data (weather stations)."""
    from sources.noaa_climate import NOAAClimateAdapter
    conn = get_conn()
    execute_tracked(NOAAClimateAdapter(STATE_FIPS, dataset=dataset,
                       start_date=start, end_date=end), conn, dataset, ",".join(STATE_FIPS) or "none")
    conn.close()


@cli.command("bls-laus")
def bls_laus():
    """Load BLS LAUS county unemployment statistics (monthly)."""
    from sources.bls_laus import BLSLAUSAdapter
    conn = get_conn()
    execute_tracked(BLSLAUSAdapter(STATE_FIPS), conn, "laus", ",".join(STATE_FIPS) or "none")
    conn.close()


@cli.command("acs")
@click.option("--year", default=2023, help="ACS 5-year vintage year (default: 2023)")
def acs(year):
    """Load Census ACS 5-year tract demographics (annual)."""
    from sources.acs_demographics import ACSAdapter
    conn = get_conn()
    execute_tracked(ACSAdapter(STATE_FIPS, year=year), conn, f"acs5_{year}", ",".join(STATE_FIPS) or "none")
    conn.close()


@cli.command("cbp")
@click.option("--year", default=2022, help="CBP data year (default: 2022)")
def cbp(year):
    """Load Census County Business Patterns (annual)."""
    from sources.cbp_business import CBPAdapter
    conn = get_conn()
    execute_tracked(CBPAdapter(STATE_FIPS, year=year), conn, f"cbp_{year}", ",".join(STATE_FIPS) or "none")
    conn.close()


@cli.command("fec")
def fec():
    """Load FEC individual contributions bulk file (quarterly cycle)."""
    from sources.fec_contributions import FECContributionsAdapter
    from pipeline.db import get_zip5_h3_map
    conn = get_conn()
    zip5_h3 = get_zip5_h3_map(conn)
    FECContributionsAdapter(zip5_h3).run(conn)
    conn.close()


@cli.command("zip5-h3")
def zip5_h3():
    """Build the authoritative Census ZCTA centroid bridge at H3 r9."""
    from sources.zip5_h3 import ZIP5H3Adapter
    conn = get_conn()
    execute_tracked(ZIP5H3Adapter(), conn, "zcta_h3_bridge", "national")
    conn.close()


@cli.command("permits")
def permits():
    """Load Socrata building permits (weekly refresh)."""
    from sources.building_permits import BuildingPermitsAdapter
    conn = get_conn()
    BuildingPermitsAdapter().run(conn)
    conn.close()


@cli.command("permits-national")
def permits_national():
    """Load Census BPS baseline; Socrata remains optional enrichment."""
    from sources.national_permits import NationalPermitsAdapter
    conn = get_conn()
    execute_tracked(NationalPermitsAdapter(), conn, "bps", "national")
    conn.close()


@cli.command("national")
@click.option("--resume/--no-resume", default=True)
def national(resume):
    """Run configured states only; resume means no duplicate state expansion."""
    click.echo("National runner is resumable; configured FIPS: " + ",".join(_configured_states()))
    click.echo("Use source-specific commands with TARGET_STATE_FIPS to process remaining states.")
    click.echo("No national completeness is claimed until status reports every intended geography complete.")


@cli.command("all")
@click.pass_context
def run_all(ctx):
    """Run all ingestion sources in priority order."""
    ctx.invoke(schema)
    ctx.invoke(geonames)
    ctx.invoke(tiger)
    ctx.invoke(cdc_places)
    ctx.invoke(svi)
    ctx.invoke(noaa)
    ctx.invoke(bls_laus)
    ctx.invoke(acs)
    ctx.invoke(cbp)
    ctx.invoke(permits)
    print("\n All sources ingested.")


if __name__ == "__main__":
    cli()
