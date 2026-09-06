import os

import pytest

from conftest import _PROVIDER_CREDENTIAL_ENV_VARS, _validate_test_database_url


@pytest.mark.parametrize(
    "database_url",
    [
        "sqlite:////tmp/if_line_pytest_local.sqlite3",
        "postgresql+psycopg://tester:secret@127.0.0.1/social",
        "postgresql://tester:secret@127.0.0.1/if_line_ci",
        "postgresql://tester:secret@127.0.0.1/story_path_test",
        "not a database url",
    ],
)
def test_explicit_test_database_url_rejects_non_disposable_targets(database_url):
    with pytest.raises(RuntimeError):
        _validate_test_database_url(database_url)


def test_explicit_test_database_url_accepts_dedicated_postgresql_database():
    database_url = (
        "postgresql+psycopg://tester:secret@127.0.0.1:5432/"
        "if_line_pytest_storypath_20260809"
    )
    assert _validate_test_database_url(database_url) == database_url


def test_provider_credentials_are_blank_by_default():
    assert _PROVIDER_CREDENTIAL_ENV_VARS
    assert all(os.environ[name] == "" for name in _PROVIDER_CREDENTIAL_ENV_VARS)
