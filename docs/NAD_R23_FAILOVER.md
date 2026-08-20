# NAD R23 official transport failover

The bulk TXT ZIP remains the preferred immutable release artifact.  A failed
bulk request does not block the national objective: the official ArcGIS
FeatureServer path is available through `nad_transport.py` when reachable.

Probe the service without acquiring records:

```sh
PYTHONPATH=. python3 -m engine.acquisition.nad_transport --probe
```

Acquire one or more bounded official partitions, then pass the generated CSV
to the existing `NADR23Ingest` promotion path:

```sh
PYTHONPATH=. python3 -m engine.acquisition.nad_transport \
  --where "State='IN'" --output /var/tmp/nad-r23-IN.csv
PYTHONPATH=. python3 -m engine.acquisition.nad_r23 /var/tmp/nad-r23-IN.csv \
  --workdir /var/lib/omega/nad-r23 --generation r23-20260630
```

The transport obtains object IDs first and fetches bounded ID chunks (default
1,800, below the service's normal 2,000-record ceiling).  It fingerprints
service and layer metadata before and after acquisition.  Any source drift
deletes the partition and fails closed, preventing a mixed-release snapshot.
The `probe()` result reports whether `supportsCreateReplica` is actually
advertised; no replica request is made unless that capability is present.  The
official WFS discovery URL is exposed by `wfs_capabilities_url()` for an
explicit probe, with no third-party fallback.

Evidence: `evidence/nad_r23_transport_probe.json`.
