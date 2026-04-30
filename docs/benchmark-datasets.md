# Benchmark Datasets

Relational benchmark databases are prepared with:

```bash
uv sync --extra datasets
uv run mahilda download-databases --output data/relational
```

The command discovers available datasets from `relational.fel.cvut.cz`, creates SQL dumps with `mysqldump`, converts them to SQLite, and writes `conversion_report.txt` in the output directory.

Use a prepared database by pointing config values at the output directory:

```yaml
database:
  path: data/relational
  name: Mondial.db
```

Then run MAHILDA or one baseline:

```bash
uv run mahilda run --config configs/config.example.yaml
uv run mahilda benchmark --config configs/config.example.yaml --baseline AMIE3
uv run mahilda benchmark --config configs/config.example.yaml --baseline SPIDER
uv run mahilda benchmark --config configs/config.example.yaml --baseline POPPER
```

The remote service defaults are the public guest access used by the original repository. Override them with `MAHILDA_RELATIONAL_*` environment variables when needed.
