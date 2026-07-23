# Benchmark Datasets

Relational benchmark databases are prepared with:

```bash
uv sync --extra datasets
uv run marita download-databases --output data/relational
```

The command discovers available datasets from `relational.fel.cvut.cz`, creates SQL dumps with `mysqldump`, converts them to SQLite, and writes `conversion_report.txt` in the output directory.

Use a prepared database by pointing config values at the output directory:

```yaml
database:
  path: data/relational
  name: Mondial.db
```

Then run MARITA or one baseline:

```bash
uv run marita run --config configs/config.example.yaml
uv run marita benchmark --config configs/config.example.yaml --baseline AMIE3
uv run marita benchmark --config configs/config.example.yaml --baseline SPIDER
uv run marita benchmark --config configs/config.example.yaml --baseline POPPER
```

The remote service defaults are the public guest access used by the original repository. Override them with `MARITA_RELATIONAL_*` environment variables when needed.

## Fast regression testing

For a quick, reproducible MARITA regression check, use `Mesh.db`. The July 19, 2026 benchmark run measured approximately 15 seconds on a database with 29 tables and approximately 2,700 rows. It produced 1,173 rules and a constraint graph with 297 nodes and 14,103 edges. This makes Mesh a useful broad-coverage default after changes to discovery, graph construction, or rule normalization.

For a fuller local check, run the following three-database suite:

| Database | Use | Reference runtime | Reference rules |
| --- | --- | ---: | ---: |
| `Mesh` | Fast broad coverage | ~15 s | 1,173 |
| `SAT` | Richer stress case | ~100 s | 3,139 |
| `Biodegradability` | Stable paper/reference case | ~45 s | 39 |

Run each database with the normal MARITA command and a config whose `database.name` is the corresponding `.db` file. The captured benchmark artifacts are in [`results/marita_tipi01_20260719`](../results/marita_tipi01_20260719/). Future runs should compare both rule hashes and rule counts, as well as runtime, against that stored baseline. Small runtime variation is expected; a changed hash set or count warrants investigation.

Datasets that were timeout-heavy in the July 19 run—`Airline`, `Hockey`, `Basketball_men`, and `Mooney_Family`—are better treated as periodic benchmark or performance checks rather than per-change regression tests.
