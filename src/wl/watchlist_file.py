"""Edit ``watchlist.yaml`` in place: add, replace or remove one company entry.

Edits work on the text, one entry block at a time, so comments, group headers and the
``{ mic: ..., ticker: ... }`` style survive. Each edit is checked by loading the new text and
comparing it with the intended list before anything is written; the previous version is kept
as ``watchlist.yaml.bak``.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from pydantic import TypeAdapter, ValidationError

from wl.config import Watchlist

if TYPE_CHECKING:
    from pathlib import Path

    from wl.config import Company

_ITEM = re.compile(r"^(\s*)- ")
_DEFAULT_YEAR_END = 12  # left out of entries, like in the example
_ENTRY = TypeAdapter(tuple[dict[str, object]])  # one list item holding one mapping
_INLINE = "  # "  # how inline comments are written in the watchlist
_FILLER = re.compile(r"^\s*(#.*)?$")  # blank and comment lines between entries


class WatchlistEditError(ValueError):
    """The edit would make the watchlist invalid, or the file's layout is not one we can edit."""


@dataclass(frozen=True)
class _Block:
    slug: str
    group: str
    start: int  # first line of the entry
    end: int  # one past its last line (trailing blank and comment lines excluded)


def add(path: Path, company: Company) -> str:
    """Insert a company after the last one in its group (or at the end); return the old text."""
    text = path.read_text(encoding="utf-8")
    lines, blocks = _parse(text)
    expected = [*_load(text).companies, company]
    in_group = [b for b in blocks if b.group == company.group]
    at = (in_group or blocks)[-1].end
    lines[at:at] = ["\n", *render(company)]
    return _write(path, text, lines, expected)


def replace(path: Path, company: Company) -> str:
    """Replace the entry with the same slug where it stands (moving it if its group changed)."""
    text = path.read_text(encoding="utf-8")
    lines, blocks = _parse(text)
    block = _find(blocks, company.slug)
    if block.group != company.group:
        remove(path, company.slug)
        add(path, company)
        path.with_name(f"{path.name}.bak").write_text(text, encoding="utf-8")
        return text
    expected = [company if c.slug == company.slug else c for c in _load(text).companies]
    old_entry = lines[block.start : block.end]
    lines[block.start : block.end] = _keep_inline_comments(old_entry, render(company))
    return _write(path, text, lines, expected)


def remove(path: Path, slug: str) -> str:
    """Delete one entry; its group header goes too when no other entry is left under it."""
    text = path.read_text(encoding="utf-8")
    lines, blocks = _parse(text)
    block = _find(blocks, slug)
    index = blocks.index(block)
    before_start = blocks[index - 1].end if index else _first_gap_start(lines, block.start)
    after_end = blocks[index + 1].start if index + 1 < len(blocks) else block.end
    header_before = any(line.strip().startswith("#") for line in lines[before_start : block.start])
    header_after = any(line.strip().startswith("#") for line in lines[block.end : after_end])
    if header_before and not header_after and index + 1 < len(blocks):
        del lines[block.start : after_end]  # the next entry inherits this one's group header
    else:
        del lines[before_start : block.end]
    expected = [c for c in _load(text).companies if c.slug != slug]
    return _write(path, text, lines, expected)


def restore(path: Path, text: str) -> None:
    """Put back a previous version returned by ``add``, ``replace`` or ``remove``."""
    _replace_file(path, text)


def render(company: Company) -> list[str]:
    """One entry in the watchlist's own layout."""
    fields: list[tuple[str, object]] = [
        ("slug", company.slug),
        ("name", company.name),
        ("group", company.group),
        ("isin", company.isin),
        ("lei", company.lei),
        ("cik", company.cik),
    ]
    entry = [f"{key}: {_scalar(value)}" for key, value in fields if value is not None]
    if company.fiscal_year_end_month != _DEFAULT_YEAR_END:
        entry.append(f"fiscal_year_end_month: {company.fiscal_year_end_month}")
    entry += [
        f"listing: {_flow(company.listing.model_dump())}",
        f"reporting_ccy: {_scalar(company.reporting_ccy)}",
        f"sources: {_flow(company.sources.model_dump())}",
    ]
    return [f"  - {entry[0]}\n", *(f"    {line}\n" for line in entry[1:])]


# ---------------------------------------------------------------------- helpers
def _parse(text: str) -> tuple[list[str], list[_Block]]:
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if (m := _ITEM.match(line)) and _is_item(m)]
    if not starts:
        msg = "no company entries found in the watchlist; add the first one by hand"
        raise WatchlistEditError(msg)
    blocks: list[_Block] = []
    for start, stop in zip(starts, [*starts[1:], len(lines)], strict=True):
        end = stop
        while end > start + 1 and _FILLER.match(lines[end - 1]):
            end -= 1
        try:
            (fields,) = _ENTRY.validate_python(
                yaml.safe_load(textwrap.dedent("".join(lines[start:end])))
            )
        except ValidationError as exc:
            msg = f"cannot read the watchlist entry at line {start + 1}; edit it by hand"
            raise WatchlistEditError(msg) from exc
        blocks.append(_Block(str(fields.get("slug")), str(fields.get("group")), start, end))
    return lines, blocks


def _keep_inline_comments(old: list[str], new: list[str]) -> list[str]:
    """Carry ``  # ...`` comments over to the new lines with the same key."""
    comments = {
        _key(code): f"  # {comment}"
        for code, sep, comment in (line.rstrip("\n").partition(_INLINE) for line in old)
        if sep
    }
    return [
        f"{line.rstrip('\n')}{comments.get(_key(line), '')}\n" if _key(line) in comments else line
        for line in new
    ]


def _key(line: str) -> str:
    return line.strip().removeprefix("- ").partition(":")[0]


def _is_item(match: re.Match[str]) -> bool:
    return len(match.group(1)) == 2  # noqa: PLR2004  (entries under `companies:`)


def _first_gap_start(lines: list[str], start: int) -> int:
    """Where the comment lines above the first entry begin (the line after ``companies:``)."""
    while start > 0 and _FILLER.match(lines[start - 1]):
        start -= 1
    return start


def _find(blocks: list[_Block], slug: str) -> _Block:
    for block in blocks:
        if block.slug == slug:
            return block
    msg = f"{slug!r} is not on the list"
    raise WatchlistEditError(msg)


def _load(text: str) -> Watchlist:
    try:
        return Watchlist.model_validate(yaml.safe_load(text))
    except ValidationError as exc:
        raise WatchlistEditError(_errors(exc)) from exc


def _write(path: Path, old: str, lines: list[str], expected: list[Company]) -> str:
    """Check the edited text against the intended list, then keep a backup and write it."""
    new = "".join(lines)
    try:
        intended = Watchlist(companies=tuple(expected))
    except ValidationError as exc:
        raise WatchlistEditError(_errors(exc)) from exc
    if _by_slug(_load(new)) != _by_slug(intended):  # entries may sit anywhere in the file
        msg = "the edit did not come out as intended; the watchlist layout may be unusual"
        raise WatchlistEditError(msg)
    path.with_name(f"{path.name}.bak").write_text(old, encoding="utf-8")
    _replace_file(path, new)
    return old


def _by_slug(watchlist: Watchlist) -> dict[str, Company]:
    return {company.slug: company for company in watchlist.companies}


def _replace_file(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _errors(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()
    )


def _flow(mapping: dict[str, object]) -> str:
    items = ", ".join(f"{key}: {_scalar(value, flow=True)}" for key, value in mapping.items())
    return f"{{ {items} }}"


def _scalar(value: object, *, flow: bool = False) -> str:
    """Plain YAML when it reads back as the same value, otherwise a double-quoted string."""
    plain = str(value)
    probe = f"{{ k: {plain} }}" if flow else f"k: {plain}"
    try:
        if yaml.safe_load(probe) == {"k": value}:
            return plain
    except yaml.YAMLError:
        pass
    return json.dumps(value, ensure_ascii=False)
