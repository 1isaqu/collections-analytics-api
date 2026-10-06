from datetime import date
from pathlib import Path

import pytest

from collections_api.settings import (
    _SPEC,
    ENV_VAR_NAMES,
    Settings,
    SettingsError,
    build_status_policy,
    parse_product_lines,
    parse_status_ids,
)

ROOT = Path(__file__).resolve().parent.parent


def test_defaults_build_without_any_environment():
    settings = Settings.from_env({})
    assert settings.line_names == ("auto", "consumer")
    assert settings.databases == ("collections_auto", "collections_consumer")
    assert settings.database_for("consumer") == "collections_consumer"
    assert settings.require_api_auth is False
    assert settings.effectiveness_floor_date == date(2020, 1, 1)


def test_spec_covers_every_dataclass_field():
    declared = {attribute for attribute, _, _, _ in _SPEC}
    computed = {"status", "lines"}
    fields = set(Settings.__dataclass_fields__)
    assert fields == declared | computed


def test_every_variable_is_documented():
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    configuration_doc = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    for name in ENV_VAR_NAMES:
        assert name in env_example, f"{name} missing from .env.example"
        assert name in configuration_doc, f"{name} missing from docs/configuration.md"


def test_env_example_parses_to_the_defaults():
    from collections_api.settings import load_dotenv

    environ: dict[str, str] = {}
    load_dotenv(ROOT / ".env.example", environ)
    from_file = Settings.from_env(environ)
    assert from_file == Settings.defaults() or _only_placeholder_differences(from_file)


def _only_placeholder_differences(from_file: Settings) -> bool:
    defaults = Settings.defaults()
    differing = {
        name
        for name in Settings.__dataclass_fields__
        if getattr(from_file, name) != getattr(defaults, name)
    }
    # The example file ships placeholder credentials; nothing else may differ.
    return differing <= {"db_user", "db_password", "api_key", "api_token"}


def test_environment_overrides_defaults():
    settings = Settings.from_env({"GRACE_DAYS": "9", "CACHE_TTL_SECONDS": "0", "DB_PORT": "1500"})
    assert (settings.grace_days, settings.cache_ttl_seconds, settings.db_port) == (9, 0.0, 1500)


def test_unset_variable_uses_the_default_and_an_explicit_empty_value_is_honoured():
    assert Settings.from_env({}).db_read_hint == "WITH (NOLOCK)"
    assert Settings.from_env({"DB_READ_HINT": ""}).db_read_hint == ""
    with pytest.raises(SettingsError):
        Settings.from_env({"GRACE_DAYS": ""})


@pytest.mark.parametrize(
    "env",
    [
        {"GRACE_DAYS": "many"},
        {"GRACE_DAYS": "-1"},
        {"DB_SCHEMA": "dbo; DROP TABLE x"},
        {"DB_READ_HINT": "WITH (TABLOCKX)"},
        {"EXCLUDED_AGENT_PATTERNS": "bad'quote"},
        {"BREAK_CURVE_EDGES": "10,5"},
        {"PRODUCT_LINES": "auto"},
        {"PRODUCT_LINES": "all=db"},
        {"PRODUCT_LINES": "a=db1,a=db2"},
        {"STATUS_IDS": "ACTIVE=1"},
        {"AGREEMENTS_DEFAULT_LIMIT": "5000", "AGREEMENTS_MAX_LIMIT": "10"},
        {"REQUIRE_API_AUTH": "true"},
        {"PRODUCTIVITY_QUERY_OPTION": "OPTION (MAXDOP 1); DROP TABLE x"},
        {"EFFECTIVENESS_FLOOR_DATE": "yesterday"},
    ],
)
def test_malformed_values_are_rejected(env):
    with pytest.raises(SettingsError):
        Settings.from_env(env)


def test_auth_requires_both_credentials_and_disables_docs_by_default():
    settings = Settings.from_env({"REQUIRE_API_AUTH": "true", "API_KEY": "k", "API_TOKEN": "t"})
    assert settings.require_api_auth and not settings.docs_enabled
    assert Settings.from_env({"ENABLE_DOCS": "true", "REQUIRE_API_AUTH": "false"}).docs_enabled
    assert not Settings.from_env({"ENABLE_DOCS": "false"}).docs_enabled


def test_wildcard_origin_never_allows_credentials():
    settings = Settings.from_env({"CORS_ALLOW_ORIGINS": "*", "CORS_ALLOW_CREDENTIALS": "true"})
    assert settings.cors_allow_credentials is False


def test_dotenv_file_fills_gaps_but_never_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# comment\nGRACE_DAYS=7\nAPP_ENV="staging"\nCACHE_TTL_SECONDS=5\n', encoding="utf-8"
    )
    settings = Settings.from_env({"CACHE_TTL_SECONDS": "99"}, dotenv=env_file)
    assert settings.grace_days == 7
    assert settings.app_env == "staging"
    assert settings.cache_ttl_seconds == 99.0


def test_missing_dotenv_file_is_ignored(tmp_path):
    assert Settings.from_env({}, dotenv=tmp_path / "absent.env").grace_days == 3


def test_status_sets_are_derived_from_the_catalogue():
    policy = Settings.defaults().status
    assert policy.approved == (21, 23, 27)
    assert policy.generated == (21, 22, 23, 26, 27)
    assert policy.universe == (21, 22, 23, 24, 26, 27)
    assert policy.rollup == (21, 22, 23, 24, 25, 26, 27)
    assert policy.break_curve == (21, 22, 23, 27)
    assert policy.exception == (24,)
    assert policy.sql("generated") == "(21, 22, 23, 26, 27)"


def test_generated_includes_broken_and_auto_broken_but_not_exception():
    policy = Settings.defaults().status
    for broken in policy.broken + policy.auto_broken:
        assert broken in policy.generated
    assert not set(policy.exception) & set(policy.generated)
    assert set(policy.exception) <= set(policy.universe)


def test_status_set_override_replaces_a_set():
    settings = Settings.from_env({"STATUS_SET_GENERATED": "active,paid"})
    assert settings.status.generated == (21, 23)
    # Derived sets are built from the override.
    assert settings.status.universe == (21, 23, 24)


def test_status_override_with_unknown_name_is_rejected():
    with pytest.raises(SettingsError):
        Settings.from_env({"STATUS_SET_GENERATED": "ACTIVE,NOT_A_STATUS"})
    with pytest.raises(SettingsError):
        build_status_policy(
            parse_status_ids(Settings.from_env({}).status_ids_raw), {"MADE_UP": ("ACTIVE",)}
        )


def test_parsers_round_trip():
    assert parse_product_lines("Auto=db_a, consumer=db_b") == (
        ("auto", "db_a"),
        ("consumer", "db_b"),
    )
    ids = parse_status_ids(
        "ACTIVE=1,BROKEN=2,PAID=3,PENDING_EXCEPTION=4,REJECTED=5,AUTO_BROKEN=6,PAID_MANUAL=7"
    )
    assert ids["PAID_MANUAL"] == 7
