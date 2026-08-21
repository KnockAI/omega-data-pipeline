# OMEGA location graph — next implementation contract

Status: implementation-ready; no production promotion.

## Canonical graph contract

`ADDRESS` is the queryable physical-location node. Edges are immutable
provenance-bearing relationships:

- `ADDRESS --located_on--> PROPERTY` (parcel/TMK/APN, confidence and source)
- `PROPERTY --contains--> STRUCTURE` (building/structure identifier,
  confidence and source)
- `ADDRESS --in_zone--> H3_ZONE` (resolution, cell id, geometry version)
- `ADDRESS --observed_by--> OBSERVATION` (source id, release, observed time,
  rights, provenance hash)
- `ORGANIZATION --operates_at--> ADDRESS` only when an authoritative source
  or verified field outcome supports it.

Nearest-structure is diagnostic only and must not create a `contains` edge.

## Interface

```text
resolve_location(address_id) -> Address + active source + rights + freshness
neighbors(address_id, relation, confidence_min) -> edges
zone_members(zone_id, jurisdiction_gate) -> Address[]
signals(address_id, as_of) -> Signal[]
score(address_id, objective_config) -> Score + feature provenance
recommend(address_id, objective_config) -> Recommendation + rationale
```

Every downstream consumer receives the same `jurisdiction_requested ->
coverage decision -> authorized source -> query` gate used by V1. Pending
rights jurisdictions return `COVERAGE_PENDING_RIGHTS`; they are never silently
filled from NAD or an unapproved source.

## Intelligence use

Knock targeting uses zone membership and eligibility; field verification uses
address/structure edges plus GPS evidence; inspections and utilities use
property/structure relationships with confidence thresholds; insurance uses
structure observations and freshness; commercial research uses organization
edges only when source-authorized. All verticals are configuration, not new
schemas.

## Acceptance tests for the next package

1. address → property → structure traversal preserves source/provenance.
2. nearest structure does not become `contains` without evidence.
3. H3 zone membership is deterministic across replay.
4. stale/revoked observation is excluded by freshness/rights gates.
5. tenant and jurisdiction isolation reject cross-scope graph queries.
6. a verified field outcome creates an observation and updates calibration,
   never the underlying source record.

## Ranked next objective

`OMEGA_LOCATION_GRAPH_AND_INTELLIGENCE_READY`: implement the immutable edge
store, confidence-aware traversal, and signal adapter on top of the proven V1
location query service. Do not add new acquisition or rights research.
