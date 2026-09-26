from __future__ import annotations

import json
import re
from pathlib import Path

from .storage import write_json_atomic

ROLE_ADMIN = "admin"
ROLE_MANAGER = "manager"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ERROR_MESSAGES = {
    "invalid_email": "That doesn't look like a valid email address.",
    "is_admin": "That address is the admin and already has full access.",
    "cannot_remove_admin": "The admin can't be removed here; change MCP_ADMIN_EMAIL in .env instead.",
    "not_manager": "That address isn't an invited manager.",
}


class UserError(ValueError):
    """An invite/remove request that can't be honored. `code` keys ERROR_MESSAGES."""

    def __init__(self, code: str):
        super().__init__(ERROR_MESSAGES[code])
        self.code = code


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or not _EMAIL_RE.match(email):
        raise UserError("invalid_email")
    return email


class UserStore:
    """The allowlist: one admin from configuration plus invited managers on disk."""

    def __init__(self, path: Path, admin_email: str):
        self._path = path
        self._admin_email = normalize_email(admin_email)
        self._managers: set[str] = set()
        if path.exists():
            data = json.loads(path.read_text())
            self._managers = {str(email).strip().lower() for email in data.get("managers", [])}

    @property
    def admin_email(self) -> str:
        return self._admin_email

    def role_for(self, email: str) -> str | None:
        email = email.strip().lower()
        if email == self._admin_email:
            return ROLE_ADMIN
        if email in self._managers:
            return ROLE_MANAGER
        return None

    def list_managers(self) -> list[str]:
        return sorted(self._managers)

    def add_manager(self, email: str) -> list[str]:
        email = normalize_email(email)
        if email == self._admin_email:
            raise UserError("is_admin")
        if email not in self._managers:
            self._managers.add(email)
            self._save()
        return self.list_managers()

    def remove_manager(self, email: str) -> list[str]:
        email = normalize_email(email)
        if email == self._admin_email:
            raise UserError("cannot_remove_admin")
        if email not in self._managers:
            raise UserError("not_manager")
        self._managers.remove(email)
        self._save()
        return self.list_managers()

    def _save(self) -> None:
        write_json_atomic(self._path, {"managers": self.list_managers()})
