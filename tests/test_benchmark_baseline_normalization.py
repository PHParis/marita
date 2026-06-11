from mahilda.cli.benchmark import normalise_baseline_name


def test_normalise_baseline_name_accepts_canonical_values() -> None:
    assert normalise_baseline_name("amie3") == "AMIE3"
    assert normalise_baseline_name("  SPIDER ") == "SPIDER"
    assert normalise_baseline_name("popper") == "POPPER"
    assert normalise_baseline_name("matilda") == "MATILDA"


def test_normalise_baseline_name_maps_ilp_alias() -> None:
    assert normalise_baseline_name("ILP") == "POPPER"
    assert normalise_baseline_name("ilp") == "POPPER"
