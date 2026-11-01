from __future__ import annotations

import shutil
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from wl import watchlist_file
from wl.config import Company, Sources, load_watchlist
from wl.watchlist_file import WatchlistEditError

if TYPE_CHECKING:
    from collections.abc import Callable

    from wl.config import Watchlist

EXAMPLE = Path(__file__).parents[1] / "watchlist.example.yaml"


@pytest.fixture
def path(tmp_path: Path) -> Path:
    """Copy the example watchlist (comments, groups, flow maps) somewhere writable."""
    copy = tmp_path / "watchlist.yaml"
    shutil.copy(EXAMPLE, copy)
    return copy


def company(slug: str = "nvda", **changes: object) -> Company:
    fields: dict[str, object] = {
        "slug": slug,
        "name": "NVIDIA",
        "group": "Technology",
        "isin": "US67066G1040",
        "cik": 1045810,
        "fiscal_year_end_month": 1,
        "listing": {"mic": "XNGS", "ticker": "NVDA", "ccy": "USD", "yahoo": "NVDA"},
        "reporting_ccy": "USD",
        "sources": {"prices": "twelvedata", "financials": "sec"},
    }
    return Company.model_validate(fields | changes)


def euronext_on_nyse() -> Company:
    """Build an entry the model refuses, without validation, so the file check must catch it."""
    return company("eu-x").model_copy(
        update={"sources": Sources(prices="euronext", financials="sec")}
    )


def slugs(watchlist: Watchlist) -> list[str]:
    return [c.slug for c in watchlist.companies]


def removed_lines(before: str, after: str) -> list[str]:
    remaining = after.splitlines()
    return [line for line in before.splitlines() if line not in remaining]


def test_entries_render_in_the_files_own_layout() -> None:
    text = EXAMPLE.read_text()
    for entry in load_watchlist(EXAMPLE).companies:
        assert "".join(watchlist_file.render(entry)) in text


def test_quotes_values_that_would_read_back_differently() -> None:
    listing = {"mic": "XTKS", "ticker": "7203", "ccy": "JPY", "yahoo": "7203.T"}
    odd = company(name="Odd: Co #1", listing=listing)
    rendered = "".join(watchlist_file.render(odd))
    assert 'name: "Odd: Co #1"' in rendered
    assert 'ticker: "7203"' in rendered


def test_add_goes_after_the_last_company_of_its_group(path: Path) -> None:
    before = path.read_text()
    returned = watchlist_file.add(path, company())
    assert returned == before
    assert slugs(load_watchlist(path)) == ["aapl", "msft", "asml", "nvda", "novo-b", "sap"]
    assert removed_lines(before, path.read_text()) == []  # nothing else changed
    assert "fiscal_year_end_month: 1\n" in path.read_text()
    assert (path.parent / "watchlist.yaml.bak").read_text() == before


def test_add_with_a_new_group_goes_at_the_end(path: Path) -> None:
    watchlist_file.add(path, company(group="Semiconductors", fiscal_year_end_month=12))
    text = path.read_text()
    assert slugs(load_watchlist(path))[-1] == "nvda"
    assert "fiscal_year_end_month" not in text.split("slug: nvda")[1]  # the default is left out
    assert text.endswith("sources: { prices: twelvedata, financials: sec }\n")


def test_replace_keeps_the_entry_in_place(path: Path) -> None:
    before = path.read_text()
    msft = load_watchlist(path).get("msft")
    watchlist_file.replace(path, msft.model_copy(update={"lei": "INR2EJN1ERAN0W5ZP974"}))
    after = path.read_text()
    assert slugs(load_watchlist(path)) == slugs(load_watchlist(EXAMPLE))
    assert load_watchlist(path).get("msft").lei == "INR2EJN1ERAN0W5ZP974"
    assert removed_lines(before, after) == []
    assert "    lei: INR2EJN1ERAN0W5ZP974\n" in after


def test_replace_keeps_inline_comments(path: Path) -> None:
    text = path.read_text().replace(
        "    reporting_ccy: EUR\n", "    reporting_ccy: EUR  # note\n", 1
    )
    path.write_text(text)
    asml = load_watchlist(path).get("asml")
    watchlist_file.replace(path, asml.model_copy(update={"lei": "724500Y6DUVHQD6OXN27"}))
    assert "    reporting_ccy: EUR  # note\n" in path.read_text()


def test_replace_moves_an_entry_whose_group_changed(path: Path) -> None:
    before = path.read_text()
    aapl = load_watchlist(path).get("aapl")
    watchlist_file.replace(path, aapl.model_copy(update={"group": "Healthcare"}))
    assert slugs(load_watchlist(path)) == ["msft", "asml", "novo-b", "aapl", "sap"]
    assert (path.parent / "watchlist.yaml.bak").read_text() == before


def test_remove_hands_the_group_header_to_the_next_entry(path: Path) -> None:
    before = path.read_text()
    watchlist_file.remove(path, "asml")
    after = path.read_text()
    assert slugs(load_watchlist(path)) == ["aapl", "msft", "novo-b", "sap"]
    assert "  # EU listings" in after
    assert after.index("  # EU listings") < after.index("slug: novo-b")
    assert not any(line.lstrip().startswith("#") for line in removed_lines(before, after))


def test_remove_drops_the_header_of_an_emptied_group(path: Path) -> None:
    watchlist_file.remove(path, "sap")
    text = path.read_text()
    assert "German listings" not in text
    assert text.endswith("sources: { prices: yahoo, financials: esef }\n")


def test_remove_the_first_entry_keeps_the_header_for_the_next(path: Path) -> None:
    watchlist_file.remove(path, "aapl")
    text = path.read_text()
    assert "  # US listings" in text
    assert slugs(load_watchlist(path))[0] == "msft"


def test_restore_puts_the_previous_text_back(path: Path) -> None:
    previous = watchlist_file.remove(path, "msft")
    watchlist_file.restore(path, previous)
    assert path.read_text() == EXAMPLE.read_text()


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (partial(watchlist_file.add, company=company("aapl")), "duplicate slugs"),
        (partial(watchlist_file.add, company=euronext_on_nyse()), "Euronext listing"),
        (partial(watchlist_file.replace, company=company("nope")), "'nope' is not on the list"),
        (partial(watchlist_file.remove, slug="nope"), "'nope' is not on the list"),
    ],
)
def test_invalid_edits_leave_the_file_alone(
    path: Path, edit: Callable[[Path], object], message: str
) -> None:
    before = path.read_text()
    with pytest.raises(WatchlistEditError, match=message):
        edit(path)
    assert path.read_text() == before
    assert not (path.parent / "watchlist.yaml.bak").exists()


def test_a_file_without_entries_is_refused(tmp_path: Path) -> None:
    empty = tmp_path / "watchlist.yaml"
    empty.write_text("companies: []\n")
    with pytest.raises(WatchlistEditError, match="add the first one by hand"):
        watchlist_file.add(empty, company())
