"""Real PostgreSQL tests for the profile and funding-source commands."""

from __future__ import annotations

import json
from typing import Any

import pytest

from money_tracker.administration import main
from money_tracker.domain import BalanceMode, FundingClass, initial_taxonomy
from money_tracker.persistence.database import upgrade_database
from money_tracker.persistence.finance import PostgresFinanceRepository


@pytest.fixture
def database(postgres_url: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Migrate the isolated test database and point the commands at it."""

    upgrade_database(postgres_url)
    monkeypatch.setenv("MONEY_TRACKER_DATABASE_URL", postgres_url)
    return postgres_url


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """Run one command through its real entry point and parse its JSON."""

    main(argv)
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)
    return payload


@pytest.mark.postgres
def test_profile_init_creates_the_sole_profile_and_taxonomy(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Give an operator the identifier every other command requires."""

    created = _run(["profile-init"], capsys)
    repository = PostgresFinanceRepository(database)

    assert created["status"] == "created"
    assert created["taxonomy_version"]
    profiles = repository.list_profiles()
    assert [str(item) for item in profiles] == [created["profile_id"]]
    assert {item.id for item in repository.get_taxonomy(profiles[0]).categories} == {
        item.id for item in initial_taxonomy().categories
    }


@pytest.mark.postgres
def test_profile_init_refuses_a_second_owner(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep a single-owner installation unambiguous about who owns the data."""

    _run(["profile-init"], capsys)

    with pytest.raises(SystemExit, match="single owner"):
        main(["profile-init"])
    assert len(PostgresFinanceRepository(database).list_profiles()) == 1


@pytest.mark.postgres
def test_profile_list_reports_an_empty_installation(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report nothing rather than failing before bootstrap."""

    assert _run(["profile-list"], capsys) == {"profiles": []}


@pytest.mark.postgres
def test_funding_source_add_configures_a_usable_default(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Let an operator reach the state the movement workflow requires."""

    profile_id = _run(["profile-init"], capsys)["profile_id"]

    added = _run(
        [
            "funding-source-add",
            "--profile-id",
            profile_id,
            "--name",
            "Carta",
            "--funding-class",
            "personal_funds",
            "--default",
        ],
        capsys,
    )
    listed = _run(["profile-list"], capsys)

    assert added["is_default"] is True
    assert added["funding_class"] == FundingClass.PERSONAL_FUNDS.value
    assert listed["profiles"][0]["funding_sources"] == [
        {
            "funding_class": "personal_funds",
            "id": added["id"],
            "is_default": True,
            "name": "Carta",
            "status": "active",
        }
    ]


@pytest.mark.postgres
def test_ledger_benefit_source_is_configurable(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Support the benefit wallet the split-funding journey needs."""

    profile_id = _run(["profile-init"], capsys)["profile_id"]

    added = _run(
        [
            "funding-source-add",
            "--profile-id",
            profile_id,
            "--name",
            "Buoni pasto",
            "--funding-class",
            "benefit",
            "--balance-mode",
            "ledger",
        ],
        capsys,
    )
    sources = PostgresFinanceRepository(database).get_funding_sources(
        PostgresFinanceRepository(database).list_profiles()[0]
    )

    assert added["funding_class"] == FundingClass.BENEFIT.value
    assert sources[0].balance_mode is BalanceMode.LEDGER


@pytest.mark.postgres
def test_repeated_source_name_is_refused(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep funding resolution unambiguous about which source was named."""

    profile_id = _run(["profile-init"], capsys)["profile_id"]
    arguments = [
        "funding-source-add",
        "--profile-id",
        profile_id,
        "--name",
        "Carta",
        "--funding-class",
        "personal_funds",
    ]
    _run(arguments, capsys)

    with pytest.raises(SystemExit, match="already exists"):
        main([*arguments[:4], "  carta  ", *arguments[5:]])


@pytest.mark.postgres
def test_second_default_source_is_refused(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep one unambiguous source for an omitted payment method."""

    profile_id = _run(["profile-init"], capsys)["profile_id"]
    _run(
        [
            "funding-source-add",
            "--profile-id",
            profile_id,
            "--name",
            "Carta",
            "--funding-class",
            "personal_funds",
            "--default",
        ],
        capsys,
    )

    with pytest.raises(SystemExit, match="default funding source already exists"):
        main(
            [
                "funding-source-add",
                "--profile-id",
                profile_id,
                "--name",
                "Contanti",
                "--funding-class",
                "personal_funds",
                "--default",
            ]
        )


@pytest.mark.postgres
def test_unknown_profile_and_blank_name_are_refused(
    database: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Fail before writing when the target or the name is unusable."""

    profile_id = _run(["profile-init"], capsys)["profile_id"]

    with pytest.raises(SystemExit, match="profile does not exist"):
        main(
            [
                "funding-source-add",
                "--profile-id",
                "00000000-0000-0000-0000-000000000001",
                "--name",
                "Carta",
                "--funding-class",
                "personal_funds",
            ]
        )
    with pytest.raises(SystemExit, match="--name cannot be empty"):
        main(
            [
                "funding-source-add",
                "--profile-id",
                profile_id,
                "--name",
                "   ",
                "--funding-class",
                "personal_funds",
            ]
        )
