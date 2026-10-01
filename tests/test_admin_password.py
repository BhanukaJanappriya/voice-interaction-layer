"""Resetting an admin's password from the command line: the new password
works, the old one stops working, and a lockout is cleared (the way back in
when the account was created with a different password than the one in
.env, which only seeds the very first admin)."""

import pytest

import config
from auth import admins
from auth.users import AuthError


@pytest.fixture
def an_admin(db_schema):
    admins.create_admin("opsadmin", "first-password-123")
    return "opsadmin"


def test_new_password_works_and_old_one_does_not(an_admin):
    assert admins.set_admin_password(an_admin, "second-password-456") is True
    assert admins.verify_admin(an_admin, "second-password-456", "10.9.0.1").username == an_admin
    with pytest.raises(AuthError) as refused:
        admins.verify_admin(an_admin, "first-password-123", "10.9.0.2")
    assert refused.value.reason == "invalid"


def test_reset_clears_a_lockout(an_admin):
    for attempt in range(config.LOGIN_MAX_ATTEMPTS):
        with pytest.raises(AuthError):
            admins.verify_admin(an_admin, "wrong-password-000", f"10.9.1.{attempt}")
    with pytest.raises(AuthError) as locked:
        admins.verify_admin(an_admin, "first-password-123", "10.9.1.99")
    assert locked.value.reason == "locked"

    assert admins.set_admin_password(an_admin, "third-password-789") is True
    assert admins.verify_admin(an_admin, "third-password-789", "10.9.1.100").username == an_admin


def test_unknown_admin_returns_false(db_schema):
    assert admins.set_admin_password("nobody", "some-password-123") is False


def test_too_short_password_is_refused_and_changes_nothing(an_admin):
    with pytest.raises(ValueError):
        admins.set_admin_password(an_admin, "short")
    assert admins.verify_admin(an_admin, "first-password-123", "10.9.2.1").username == an_admin


def test_command_line_tool_resets_the_password(an_admin, capsys):
    from auth import manage_users

    assert manage_users.main(["admin-password", "--username", an_admin, "--password", "cli-password-1234"]) == 0
    assert "Password replaced" in capsys.readouterr().out
    assert admins.verify_admin(an_admin, "cli-password-1234", "10.9.3.1").username == an_admin
    assert manage_users.main(["admin-password", "--username", "ghost", "--password", "cli-password-1234"]) == 1
