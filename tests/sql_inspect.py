"""Read the output column names of a generated statement.

Used to hold the declared response columns and the SQL together: if a builder renames
a column and the API model does not follow, a test fails instead of a client.
"""

from __future__ import annotations

import re


def _depth0_tokens(sql: str) -> list[tuple[int, str]]:
    """``(position, word)`` for words outside any parentheses, upper-cased."""
    tokens: list[tuple[int, str]] = []
    depth = 0
    for match in re.finditer(r"\(|\)|[A-Za-z_][A-Za-z0-9_]*", sql):
        text = match.group()
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
        elif depth == 0:
            tokens.append((match.start(), text.upper()))
    return tokens


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _alias(expression: str) -> str:
    match = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", expression, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group(1)
    bare = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*$", expression)
    assert bare, f"cannot find a column name in: {expression!r}"
    return bare.group(1)


def final_select_aliases(sql: str) -> list[str]:
    """Column names produced by the statement's outermost final ``SELECT``.

    Handles a leading ``WITH`` clause and ``SELECT TOP n``. For a ``UNION`` the last
    top-level ``SELECT`` is read, which has the same names as the others.
    """
    sql = re.sub(r"--[^\n]*", "", sql)
    tokens = _depth0_tokens(sql)
    selects = [pos for pos, word in tokens if word == "SELECT"]
    start = selects[-1]
    end = next((pos for pos, word in tokens if word == "FROM" and pos > start), len(sql))
    select_list = sql[start + len("SELECT") : end]
    select_list = re.sub(r"^\s*(DISTINCT\s+)?(TOP\s+\d+\s+)?", "", select_list, flags=re.IGNORECASE)
    return [_alias(item) for item in _split_top_level(select_list)]
