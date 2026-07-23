import pytest

from marita.algorithms.marita import MARITA


def test_marita_normalizes_legacy_keys_from_settings() -> None:
    algorithm = MARITA(
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


def test_marita_normalizes_legacy_keys_from_config_parameters() -> None:
    algorithm = MARITA(
        database=object(),
        config={
            "algorithm": {
                "name": "MARITA",
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


def test_joinability_default_is_fk() -> None:
    algorithm = MARITA(database=object())
    assert algorithm.settings["joinability"] == "fk"


def test_joinability_accepts_full() -> None:
    algorithm = MARITA(
        database=object(),
        settings={"joinability": "full"},
    )
    assert algorithm.settings["joinability"] == "full"


def test_joinability_legacy_bool_true() -> None:
    algorithm = MARITA(
        database=object(),
        settings={"full_joinability": True},
    )
    assert algorithm.settings["joinability"] == "full"


def test_joinability_legacy_bool_false() -> None:
    algorithm = MARITA(
        database=object(),
        settings={"full_joinability": False},
    )
    assert algorithm.settings["joinability"] == "fk"


def test_joinability_invalid_raises() -> None:
    with pytest.raises(ValueError, match="joinability must be"):
        MARITA(database=object(), settings={"joinability": "bad"})
