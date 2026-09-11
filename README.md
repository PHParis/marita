# MARITA

MARITA is a tool that takes as input a relational database, and that delivers as output *rules* of the form

> PARENT(parent1,child) and PARENT(parent2,child) and RESIDENCE(parent1, city1, zip1) =>  RESIDENCE(parent2, city1, zip1)

Such a rule means that if `parent1` and `parent2` are the parents of `child`, and if `parent1` lives in `city`1 with zip code `zip1`, then `parent2` lives there as well. Such rules can hold *to a certain degree* and thus tolerate exceptions.

To run MARITA:
- clone this repository
- create a config file like [this one](https://github.com/PHParis/marita/blob/main/configs/config.example.yaml).
- run  `uv run marita run --config your_config_file` 

## Documentation

- our [scientific publication](https://suchanek.name/work/publications/iswc-2026-short-marita.pdf): goal, algorithm, and experimental results 
- `docs/architecture.md`: package structure, active modules, and runtime data flow
- `docs/cli.md`: command behavior and examples
- `docs/config.md`: configuration schema, defaults, normalization, and precedence
- `docs/reproduce-experiments.md`: benchmarks, competitors, and commands to reproduce the experiments of our paper

## Publication

If you use MARITA for academic purposes, please cite our paper

> [Pierre-Henri Paris](https://phparis.net/), [François Amat](https://www.famat.me/), [Paolo Papotti](https://papotti.eurecom.io/), [Fabian M. Suchanek](https://suchanek.name):
> “[MARITA: Mining Approximate Rules in Tables](https://suchanek.name/work/publications/iswc-2026-short-marita.pdf)”,
> International Semantic Web Conference ([ISWC](https://iswc2026.semanticweb.org/)) demo track, 2026

## Quick Start

### Linux
```bash
uv sync
uv run marita test-db
uv run marita smoke
```

### Windows

```
python -m venv .venv
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
.venv\Scripts\activate
python.exe -m pip install --upgrade pip
pip install -e .
python -m marita test-db
python -m marita smoke
```
