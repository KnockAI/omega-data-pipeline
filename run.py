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
  python run.py all                 # Run all sources
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
def geonames():
    """Load GeoNames US place names."""
    from sources.geonames import GeoNamesUSAdapter
    conn = get_conn()
    GeoNamesUSAdapter().run(conn)
    conn.close()


@cli.command()
def tiger():
    """Load Census TIGER boundaries (counties, tracts, precincts)."""
    from sources.census_tiger import CensusTigerAdapter
    conn = get_conn()
    CensusTigerAdapter(STATE_FIPS).run(conn)
    conn.close()


@cli.command("cdc-places")
def cdc_places():
    """Load CDC PLACES health outcomes."""
    from sources.cdc_places import CDCPlacesAdapter
    conn = get_conn()
    CDCPlacesAdapter(ABBRS).run(conn)
    conn.close()


@cli.command()
def svi():
    """Load CDC/ATSDR Social Vulnerability Index."""
    from sources.svi import SVIAdapter
    conn = get_conn()
    SVIAdapter(STATE_FIPS).run(conn)
    conn.close()


@cli.command()
@click.option("--start", default="2020-01-01", help="Start date YYYY-MM-DD")
@click.option("--end", default=None, help="End date YYYY-MM-DD (default: yesterday)")
@click.option("--dataset", default="GSOM", help="NOAA dataset: GHCND, GSOM, GSOY")
def noaa(start, end, dataset):
    """Load NOAA climate data (weather stations)."""
    from sources.noaa_climate import NOAAClimateAdapter
    conn = get_conn()
    NOAAClimateAdapter(STATE_FIPS, dataset=dataset,
                       start_date=start, end_date=end).run(conn)
    conn.close()


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
    print("\n✓ All sources ingested.")


if __name__ == "__main__":
    cli()
