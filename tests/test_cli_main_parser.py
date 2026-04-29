import pytest

from mahilda.cli.main import main


def test_main_requires_subcommand() -> None:
    with pytest.raises(SystemExit):
        main([])
