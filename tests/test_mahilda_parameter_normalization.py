from mahilda.algorithms.mahilda import MAHILDA


def test_mahilda_normalizes_legacy_keys_from_settings() -> None:
    algorithm = MAHILDA(
        database=object(),
        settings={
            "max_table": 7,
            "max_vars": 11,
            "nb_occurrence": 5,
            "disjoint_semantic": "true",
            "recursivity": 3,
        },
    )

    assert algorithm.settings["max_tables"] == 7
    assert algorithm.settings["max_variables"] == 11
    assert algorithm.settings["walk_length"] == 4
    assert algorithm.settings["disjoint_semantics"] is True
    assert algorithm.settings["recursivity"] == 3


def test_mahilda_normalizes_legacy_keys_from_config_parameters() -> None:
    algorithm = MAHILDA(
        database=object(),
        config={
            "algorithm": {
                "name": "MAHILDA",
                "parameters": {
                    "max_table": 8,
                    "max_vars": 12,
                    "nb_occurrence": 6,
                    "recursivity": 4,
                },
            }
        },
    )

    assert algorithm.settings["max_tables"] == 8
    assert algorithm.settings["max_variables"] == 12
    assert algorithm.settings["walk_length"] == 5
    assert algorithm.settings["recursivity"] == 4
