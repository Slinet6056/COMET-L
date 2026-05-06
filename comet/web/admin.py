from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error

from .storage import (
    DuplicateUserError,
    LastActiveAdminError,
    SafeUserRecord,
    UserNotFoundError,
    WebDatabase,
    WebDatabaseError,
)

PASSWORD_HASHER = PasswordHasher()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m comet.web.admin")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_admin = subparsers.add_parser("create-admin", help="Create an initial admin user")
    create_admin.add_argument("--username", required=True)
    create_admin.add_argument("--password", required=True)

    create_user_parser = subparsers.add_parser("create-user", help="Create a user")
    create_user_parser.add_argument("--username", required=True)
    create_user_parser.add_argument("--password", required=True)
    create_user_parser.add_argument("--role", choices=["admin", "user"], default="user")

    subparsers.add_parser("list-users", help="List users")

    disable_user_parser = subparsers.add_parser("disable-user", help="Disable a user")
    disable_user_parser.add_argument("--user-id", required=True, type=int)

    reset_password_parser = subparsers.add_parser("reset-password", help="Reset a user's password")
    reset_password_parser.add_argument("--user-id", required=True, type=int)
    reset_password_parser.add_argument("--password", required=True)

    promote_user_parser = subparsers.add_parser("promote-user", help="Promote a user to admin")
    promote_user_parser.add_argument("--user-id", required=True, type=int)

    demote_user_parser = subparsers.add_parser("demote-user", help="Demote an admin to user")
    demote_user_parser.add_argument("--user-id", required=True, type=int)
    return parser


def create_admin(*, username: str, password: str, workspace_root: Path | str | None = None) -> int:
    database = WebDatabase.for_workspace(workspace_root)
    database.bootstrap()

    password_hash = _hash_password(password)
    try:
        return database.create_admin(username=username, password_hash=password_hash)
    except DuplicateUserError:
        raise


def create_user(
    *,
    username: str,
    password: str,
    role: str = "user",
    workspace_root: Path | str | None = None,
) -> SafeUserRecord:
    database = _bootstrapped_database(workspace_root)
    user_id = database.create_user(
        username=username,
        password_hash=_hash_password(password),
        role=role,
    )
    user = database.get_safe_user_by_id(user_id)
    if user is None:  # pragma: no cover - create_user returning a missing id is unexpected
        raise WebDatabaseError(f"Created user cannot be loaded: {user_id}")
    return user


def _bootstrapped_database(workspace_root: Path | str | None = None) -> WebDatabase:
    database = WebDatabase.for_workspace(workspace_root)
    database.bootstrap()
    return database


def _hash_password(password: str) -> str:
    try:
        return PASSWORD_HASHER.hash(password)
    except Argon2Error as exc:  # pragma: no cover - argon2 errors are unexpected here
        raise WebDatabaseError(f"Failed to hash password: {exc}") from exc


def _user_to_dict(user: SafeUserRecord) -> dict[str, object]:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "isActive": user.is_active,
        "createdAt": user.created_at,
        "updatedAt": user.updated_at,
        "disabledAt": user.disabled_at,
        "passwordChangedAt": user.password_changed_at,
    }


def _print_user(user: SafeUserRecord) -> None:
    print(json.dumps(_user_to_dict(user), ensure_ascii=False, sort_keys=True))


def _handle_user_operation_error(exc: WebDatabaseError) -> int:
    if isinstance(exc, LastActiveAdminError):
        print("last_admin_protected", file=sys.stderr)
        return 1
    if isinstance(exc, UserNotFoundError):
        print(str(exc), file=sys.stderr)
        return 1
    print(str(exc), file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "create-admin":
        try:
            create_admin(username=args.username, password=args.password)
        except DuplicateUserError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except WebDatabaseError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Created admin user {args.username.strip().lower()}")
        return 0

    if args.command == "create-user":
        try:
            user = create_user(username=args.username, password=args.password, role=args.role)
        except DuplicateUserError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        _print_user(user)
        return 0

    if args.command == "list-users":
        try:
            database = _bootstrapped_database()
            users = database.list_users()
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        print(
            json.dumps([_user_to_dict(user) for user in users], ensure_ascii=False, sort_keys=True)
        )
        return 0

    if args.command == "disable-user":
        try:
            user = _bootstrapped_database().disable_user(args.user_id)
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        _print_user(user)
        return 0

    if args.command == "reset-password":
        try:
            user = _bootstrapped_database().reset_user_password(
                args.user_id,
                password_hash=_hash_password(args.password),
            )
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        _print_user(user)
        return 0

    if args.command == "promote-user":
        try:
            user = _bootstrapped_database().update_user_role(args.user_id, role="admin")
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        _print_user(user)
        return 0

    if args.command == "demote-user":
        try:
            user = _bootstrapped_database().update_user_role(args.user_id, role="user")
        except WebDatabaseError as exc:
            return _handle_user_operation_error(exc)
        _print_user(user)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
