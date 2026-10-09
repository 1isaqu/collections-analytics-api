"""The collections-api command line."""

from __future__ import annotations

import pytest

from collections_api.__main__ import main


def test_help_is_plain_text(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "serve the Collections Analytics API with uvicorn" in out
    assert "``" not in out
    for flag in ("--host", "--port", "--env-file"):
        assert flag in out
