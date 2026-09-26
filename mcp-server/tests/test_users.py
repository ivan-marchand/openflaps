from pathlib import Path

import pytest

from mcp_server.users import ROLE_ADMIN, ROLE_MANAGER, UserError, UserStore, normalize_email

ADMIN = "admin@gmail.com"


def make_store(tmp_path: Path) -> UserStore:
    return UserStore(tmp_path / "users.json", ADMIN)


def test_admin_email_gets_the_admin_role_case_insensitively(tmp_path: Path):
    store = UserStore(tmp_path / "users.json", "Admin@Gmail.com")

    assert store.role_for("admin@gmail.com") == ROLE_ADMIN
    assert store.role_for("  ADMIN@GMAIL.COM ") == ROLE_ADMIN


def test_unknown_email_has_no_role(tmp_path: Path):
    assert make_store(tmp_path).role_for("stranger@gmail.com") is None


def test_invited_manager_gets_the_manager_role(tmp_path: Path):
    store = make_store(tmp_path)

    assert store.add_manager("Bob@Gmail.com") == ["bob@gmail.com"]

    assert store.role_for("bob@gmail.com") == ROLE_MANAGER


def test_inviting_twice_is_idempotent(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("bob@gmail.com")

    assert store.add_manager("bob@gmail.com") == ["bob@gmail.com"]


def test_managers_are_listed_sorted(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("zed@gmail.com")
    store.add_manager("amy@gmail.com")

    assert store.list_managers() == ["amy@gmail.com", "zed@gmail.com"]


@pytest.mark.parametrize("bad", ["", "no-at-sign", "two@@gmail.com", "a b@gmail.com", "a@nodot", "x" * 250 + "@a.com"])
def test_invalid_emails_are_rejected(tmp_path: Path, bad: str):
    store = make_store(tmp_path)

    with pytest.raises(UserError) as exc_info:
        store.add_manager(bad)

    assert exc_info.value.code == "invalid_email"
    assert store.list_managers() == []


def test_inviting_the_admin_address_is_rejected(tmp_path: Path):
    store = make_store(tmp_path)

    with pytest.raises(UserError) as exc_info:
        store.add_manager(ADMIN)

    assert exc_info.value.code == "is_admin"
    assert store.list_managers() == []


def test_removing_a_manager_revokes_the_role(tmp_path: Path):
    store = make_store(tmp_path)
    store.add_manager("bob@gmail.com")

    assert store.remove_manager("bob@gmail.com") == []

    assert store.role_for("bob@gmail.com") is None


def test_removing_the_admin_is_rejected(tmp_path: Path):
    with pytest.raises(UserError) as exc_info:
        make_store(tmp_path).remove_manager(ADMIN)

    assert exc_info.value.code == "cannot_remove_admin"


def test_removing_someone_who_is_not_a_manager_is_rejected(tmp_path: Path):
    with pytest.raises(UserError) as exc_info:
        make_store(tmp_path).remove_manager("nobody@gmail.com")

    assert exc_info.value.code == "not_manager"


def test_managers_survive_a_restart(tmp_path: Path):
    make_store(tmp_path).add_manager("bob@gmail.com")

    reloaded = make_store(tmp_path)

    assert reloaded.role_for("bob@gmail.com") == ROLE_MANAGER


def test_invalid_admin_email_is_rejected(tmp_path: Path):
    with pytest.raises(UserError):
        UserStore(tmp_path / "users.json", "not-an-email")


def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Bob@Gmail.COM ") == "bob@gmail.com"
