from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WEB_DB_ENV_VAR = "COMET_WEB_DB_PATH"
WEB_DB_RELATIVE_PATH = Path("state") / "web" / "comet-web.sqlite3"
EXPECTED_TABLES = {"users", "sessions", "runs", "uploads", "audit_events"}
INCOMPATIBLE_DATABASE_MESSAGE = (
    "incompatible existing COMET-L Web database. "
    "Start from a clean database or restore a matching-version backup."
)


class WebDatabaseError(RuntimeError):
    """Raised when the Web database cannot be bootstrapped safely."""


class DuplicateUserError(WebDatabaseError):
    """Raised when an admin/user creation request conflicts with an existing username."""


class UserNotFoundError(WebDatabaseError):
    """Raised when a user management operation targets a missing user."""


class LastActiveAdminError(WebDatabaseError):
    """Raised when an operation would remove the last active admin."""


@dataclass(frozen=True, slots=True)
class UserRecord:
    id: int
    username: str
    password_hash: str
    role: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class SafeUserRecord:
    id: int
    username: str
    role: str
    is_active: bool
    created_at: str
    updated_at: str
    disabled_at: str | None
    password_changed_at: str


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: int
    username: str
    role: str


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: str
    user_id: int | None
    status: str
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None
    project_source_type: str
    project_path: str
    bug_reports_path: str | None
    path_metadata: dict[str, Any]
    paths: dict[str, str]
    path_snapshot: dict[str, str]
    config_snapshot: dict[str, Any]
    config_path: str
    error: str | None
    queue_position: int | None
    cancel_requested: bool
    cancellation_reason: str | None


@dataclass(frozen=True, slots=True)
class UploadRecord:
    id: str
    user_id: int | None
    status: str
    kind: str
    original_filename: str
    storage_path: str
    extracted_path: str
    size_bytes: int
    created_at: str
    updated_at: str
    used_at: str | None
    path_metadata: dict[str, Any]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_web_db_path(workspace_root: Path | str | None = None) -> Path:
    env_path = os.getenv(WEB_DB_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()

    root = Path(workspace_root or ".").expanduser().resolve()
    return root / WEB_DB_RELATIVE_PATH


class WebDatabase:
    def __init__(self, db_path: Path | str) -> None:
        self.db_path: Path = Path(db_path).expanduser().resolve()

    @classmethod
    def for_workspace(cls, workspace_root: Path | str | None = None) -> "WebDatabase":
        return cls(resolve_web_db_path(workspace_root))

    def bootstrap(self) -> None:
        existed = self.db_path.exists()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            existing_tables = self._existing_tables(connection)
            if existed and existing_tables and not EXPECTED_TABLES.issubset(existing_tables):
                raise WebDatabaseError(INCOMPATIBLE_DATABASE_MESSAGE)
            if not existing_tables:
                self._create_schema(connection)
            self._verify_schema(connection)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        connection = sqlite3.connect(str(self.db_path), timeout=5.0)
        connection.row_factory = sqlite3.Row
        try:
            journal_mode_row = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            journal_mode = journal_mode_row[0] if journal_mode_row is not None else None
            if str(journal_mode).lower() != "wal":
                raise WebDatabaseError(
                    f"Unable to enable WAL mode for Web database: {journal_mode}"
                )
            _ = connection.execute("PRAGMA busy_timeout = 5000")
            _ = connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_admin(self, *, username: str, password_hash: str) -> int:
        return self.create_user(username=username, password_hash=password_hash, role="admin")

    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        is_active: bool = True,
    ) -> int:
        normalized_username = normalize_username(username)
        if role not in {"admin", "user"}:
            raise WebDatabaseError(f"Unsupported user role: {role}")
        now = utc_now_iso()
        try:
            with self.connect() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO users (
                        username,
                        password_hash,
                        role,
                        is_active,
                        created_at,
                        updated_at,
                        password_changed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (normalized_username, password_hash, role, int(is_active), now, now, now),
                )
                user_id = cursor.lastrowid
                if user_id is None:
                    raise WebDatabaseError("Failed to create user.")
                return user_id
        except sqlite3.IntegrityError as exc:
            raise DuplicateUserError(f"User already exists: {normalized_username}") from exc

    def get_user_by_username(self, username: str) -> UserRecord | None:
        normalized_username = normalize_username(username)
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, username, password_hash, role, is_active
                FROM users
                WHERE username = ?
                """,
                (normalized_username,),
            ).fetchone()
        if row is None:
            return None
        return UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            role=str(row["role"]),
            is_active=bool(row["is_active"]),
        )

    def get_safe_user_by_id(self, user_id: int) -> SafeUserRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT id, username, role, is_active, created_at, updated_at,
                       disabled_at, password_changed_at
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_safe_user_record(row)

    def list_users(self) -> list[SafeUserRecord]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, username, role, is_active, created_at, updated_at,
                       disabled_at, password_changed_at
                FROM users
                ORDER BY username ASC, id ASC
                """
            ).fetchall()
        return [_row_to_safe_user_record(row) for row in rows]

    def disable_user(self, user_id: int) -> SafeUserRecord:
        now = utc_now_iso()
        with self.connect() as connection:
            row = self._get_user_for_update(connection, user_id)
            if row is None:
                raise UserNotFoundError(f"User does not exist: {user_id}")
            self._ensure_not_last_active_admin(connection, row)
            connection.execute(
                """
                UPDATE users
                SET is_active = 0,
                    disabled_at = COALESCE(disabled_at, ?),
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, user_id),
            )
            updated = self._get_user_for_update(connection, user_id)
        assert updated is not None
        return _row_to_safe_user_record(updated)

    def reset_user_password(self, user_id: int, *, password_hash: str) -> SafeUserRecord:
        now = utc_now_iso()
        with self.connect() as connection:
            row = self._get_user_for_update(connection, user_id)
            if row is None:
                raise UserNotFoundError(f"User does not exist: {user_id}")
            connection.execute(
                """
                UPDATE users
                SET password_hash = ?, password_changed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (password_hash, now, now, user_id),
            )
            updated = self._get_user_for_update(connection, user_id)
        assert updated is not None
        return _row_to_safe_user_record(updated)

    def update_user_role(self, user_id: int, *, role: str) -> SafeUserRecord:
        if role not in {"admin", "user"}:
            raise WebDatabaseError(f"Unsupported user role: {role}")
        now = utc_now_iso()
        with self.connect() as connection:
            row = self._get_user_for_update(connection, user_id)
            if row is None:
                raise UserNotFoundError(f"User does not exist: {user_id}")
            if role == "user":
                self._ensure_not_last_active_admin(connection, row)
            connection.execute(
                """
                UPDATE users
                SET role = ?, updated_at = ?
                WHERE id = ?
                """,
                (role, now, user_id),
            )
            updated = self._get_user_for_update(connection, user_id)
        assert updated is not None
        return _row_to_safe_user_record(updated)

    def create_session(
        self,
        *,
        user_id: int,
        token_hash: str,
        expires_at: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> int:
        now = utc_now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO sessions (
                    user_id,
                    token_hash,
                    created_at,
                    expires_at,
                    last_seen_at,
                    user_agent,
                    ip_address
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, token_hash, now, expires_at, now, user_agent, ip_address),
            )
            session_id = cursor.lastrowid
            if session_id is None:
                raise WebDatabaseError("Failed to create session.")
            return session_id

    def get_active_session_user(
        self, *, token_hash: str, now: str | None = None
    ) -> AuthenticatedUser | None:
        checked_at = now or utc_now_iso()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.username, users.role
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ?
                  AND sessions.revoked_at IS NULL
                  AND sessions.expires_at > ?
                  AND users.is_active = 1
                """,
                (token_hash, checked_at),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE sessions
                SET last_seen_at = ?
                WHERE token_hash = ?
                """,
                (checked_at, token_hash),
            )
        return AuthenticatedUser(
            id=int(row["id"]),
            username=str(row["username"]),
            role=str(row["role"]),
        )

    def revoke_session(self, *, token_hash: str) -> bool:
        now = utc_now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE sessions
                SET revoked_at = ?
                WHERE token_hash = ? AND revoked_at IS NULL
                """,
                (now, token_hash),
            )
            return cursor.rowcount > 0

    def create_run_record(
        self,
        *,
        run_id: str,
        user_id: int | None,
        status: str,
        created_at: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        project_source_type: str,
        project_path: str,
        bug_reports_path: str | None,
        path_metadata: dict[str, Any],
        paths: dict[str, str],
        path_snapshot: dict[str, str],
        config_snapshot: dict[str, Any],
        config_path: str,
        error: str | None = None,
        queue_position: int | None = None,
        cancel_requested: bool = False,
        cancellation_reason: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id,
                    user_id,
                    status,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at,
                    project_source_type,
                    project_path,
                    bug_reports_path,
                    path_metadata_json,
                    paths_json,
                    path_snapshot_json,
                    config_snapshot_json,
                    config_path,
                    error,
                    queue_position,
                    cancel_requested,
                    cancellation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    user_id,
                    status,
                    created_at,
                    created_at,
                    started_at,
                    finished_at,
                    project_source_type,
                    project_path,
                    bug_reports_path,
                    _json_dump(path_metadata),
                    _json_dump(paths),
                    _json_dump(path_snapshot),
                    _json_dump(config_snapshot),
                    config_path,
                    error,
                    queue_position,
                    int(cancel_requested),
                    cancellation_reason,
                ),
            )

    def update_run_record(
        self,
        run_id: str,
        *,
        status: str | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        path_metadata: dict[str, Any] | None = None,
        paths: dict[str, str] | None = None,
        path_snapshot: dict[str, str] | None = None,
        config_snapshot: dict[str, Any] | None = None,
        config_path: str | None = None,
        error: str | None = None,
        queue_position: int | None = None,
        cancel_requested: bool | None = None,
        cancellation_reason: str | None = None,
    ) -> None:
        assignments = ["updated_at = ?"]
        values: list[Any] = [utc_now_iso()]
        optional_values: dict[str, Any] = {
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "path_metadata_json": _json_dump(path_metadata) if path_metadata is not None else None,
            "paths_json": _json_dump(paths) if paths is not None else None,
            "path_snapshot_json": _json_dump(path_snapshot) if path_snapshot is not None else None,
            "config_snapshot_json": _json_dump(config_snapshot)
            if config_snapshot is not None
            else None,
            "config_path": config_path,
            "error": error,
            "queue_position": queue_position,
            "cancel_requested": int(cancel_requested) if cancel_requested is not None else None,
            "cancellation_reason": cancellation_reason,
        }
        for column, value in optional_values.items():
            if value is None:
                continue
            assignments.append(f"{column} = ?")
            values.append(value)
        values.append(run_id)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE runs SET {', '.join(assignments)} WHERE id = ?",
                values,
            )

    def get_run_record(self, run_id: str) -> RunRecord | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        return _row_to_run_record(row)

    def list_run_records(
        self,
        *,
        user_id: int | None = None,
        include_all: bool = False,
    ) -> list[RunRecord]:
        with self.connect() as connection:
            if include_all:
                rows = connection.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC, id DESC"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM runs WHERE user_id = ? ORDER BY created_at DESC, id DESC",
                    (user_id,),
                ).fetchall()
        return [_row_to_run_record(row) for row in rows]

    def list_run_records_by_statuses(self, statuses: set[str]) -> list[RunRecord]:
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        with self.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM runs
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                """,
                tuple(sorted(statuses)),
            ).fetchall()
        return [_row_to_run_record(row) for row in rows]

    def count_run_records_by_statuses(
        self,
        statuses: set[str],
        *,
        user_id: int | None = None,
    ) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        values: list[Any] = list(sorted(statuses))
        user_filter = ""
        if user_id is not None:
            user_filter = " AND user_id = ?"
            values.append(user_id)
        with self.connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS count FROM runs
                WHERE status IN ({placeholders}){user_filter}
                """,
                values,
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def list_upload_records(
        self,
        *,
        user_id: int | None = None,
        include_all: bool = False,
        status: str | None = None,
        kind: str | None = None,
    ) -> list[UploadRecord]:
        clauses = []
        values: list[Any] = []
        if not include_all:
            clauses.append("user_id = ?")
            values.append(user_id)
        if status is not None:
            clauses.append("status = ?")
            values.append(status)
        if kind is not None:
            clauses.append("kind = ?")
            values.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM uploads{where} ORDER BY created_at ASC, id ASC",
                values,
            ).fetchall()
        return [_row_to_upload_record(row) for row in rows]

    def delete_upload_record(self, upload_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM uploads WHERE id = ?", (upload_id,))
            return cursor.rowcount > 0

    def mark_stale_run_records(self, statuses: set[str], *, error: str) -> int:
        if not statuses:
            return 0
        placeholders = ", ".join("?" for _ in statuses)
        now = utc_now_iso()
        with self.connect() as connection:
            cursor = connection.execute(
                f"""
                UPDATE runs
                SET status = 'stale',
                    updated_at = ?,
                    finished_at = COALESCE(finished_at, ?),
                    error = COALESCE(error, ?),
                    queue_position = NULL
                WHERE status IN ({placeholders})
                """,
                (now, now, error, *tuple(sorted(statuses))),
            )
            return cursor.rowcount

    def update_run_queue_position(self, run_id: str, queue_position: int | None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE runs
                SET updated_at = ?, queue_position = ?
                WHERE id = ?
                """,
                (utc_now_iso(), queue_position, run_id),
            )

    def create_upload_record(
        self,
        *,
        upload_id: str,
        user_id: int,
        status: str,
        kind: str,
        original_filename: str,
        storage_path: str,
        extracted_path: str,
        size_bytes: int,
        path_metadata: dict[str, Any],
    ) -> None:
        now = utc_now_iso()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO uploads (
                    id,
                    user_id,
                    status,
                    kind,
                    original_filename,
                    storage_path,
                    extracted_path,
                    size_bytes,
                    created_at,
                    updated_at,
                    path_metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    upload_id,
                    user_id,
                    status,
                    kind,
                    original_filename,
                    storage_path,
                    extracted_path,
                    size_bytes,
                    now,
                    now,
                    _json_dump(path_metadata),
                ),
            )

    def get_upload_record(self, upload_id: str) -> UploadRecord | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_upload_record(row)

    def mark_upload_used_once(
        self,
        *,
        upload_id: str,
        user_id: int,
        kind: str,
        status: str,
    ) -> UploadRecord | None:
        now = utc_now_iso()
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM uploads
                WHERE id = ? AND user_id = ? AND kind = ? AND status = ?
                """,
                (upload_id, user_id, kind, status),
            ).fetchone()
            if row is None:
                return None
            if row["used_at"] is not None:
                return None
            cursor = connection.execute(
                """
                UPDATE uploads
                SET used_at = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND kind = ? AND status = ? AND used_at IS NULL
                """,
                (now, now, upload_id, user_id, kind, status),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM uploads WHERE id = ?",
                (upload_id,),
            ).fetchone()
        if updated is None:
            return None
        return _row_to_upload_record(updated)

    def reset_uploads_used_at(self, upload_ids: list[str]) -> None:
        if not upload_ids:
            return
        placeholders = ", ".join("?" for _ in upload_ids)
        with self.connect() as connection:
            connection.execute(
                f"""
                UPDATE uploads
                SET used_at = NULL, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (utc_now_iso(), *upload_ids),
            )

    def _get_user_for_update(
        self, connection: sqlite3.Connection, user_id: int
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT id, username, role, is_active, created_at, updated_at,
                   disabled_at, password_changed_at
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()

    def _ensure_not_last_active_admin(
        self, connection: sqlite3.Connection, row: sqlite3.Row
    ) -> None:
        if str(row["role"]) != "admin" or not bool(row["is_active"]):
            return
        active_admin_count = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM users
            WHERE role = 'admin' AND is_active = 1
            """
        ).fetchone()
        if int(active_admin_count["count"] if active_admin_count is not None else 0) <= 1:
            raise LastActiveAdminError("Cannot disable or demote the last active admin.")

    def _verify_schema(self, connection: sqlite3.Connection) -> None:
        existing_tables = self._existing_tables(connection)
        if not EXPECTED_TABLES.issubset(existing_tables):
            missing = ", ".join(sorted(EXPECTED_TABLES - existing_tables))
            raise WebDatabaseError(
                f"{INCOMPATIBLE_DATABASE_MESSAGE} Missing required tables: {missing}."
            )

        expected_columns = {
            "users": {
                "id",
                "username",
                "password_hash",
                "role",
                "is_active",
                "created_at",
                "updated_at",
                "disabled_at",
                "password_changed_at",
            },
            "sessions": {
                "id",
                "user_id",
                "token_hash",
                "created_at",
                "expires_at",
                "revoked_at",
                "last_seen_at",
                "user_agent",
                "ip_address",
            },
            "runs": {
                "id",
                "user_id",
                "status",
                "created_at",
                "updated_at",
                "started_at",
                "finished_at",
                "project_source_type",
                "project_path",
                "bug_reports_path",
                "path_metadata_json",
                "paths_json",
                "path_snapshot_json",
                "config_snapshot_json",
                "config_path",
                "error",
                "queue_position",
                "cancel_requested",
                "cancellation_reason",
            },
            "uploads": {
                "id",
                "user_id",
                "status",
                "kind",
                "original_filename",
                "created_at",
                "updated_at",
                "storage_path",
                "extracted_path",
                "size_bytes",
                "used_at",
                "path_metadata_json",
            },
            "audit_events": {
                "id",
                "user_id",
                "actor_user_id",
                "event_type",
                "created_at",
                "ip_address",
                "user_agent",
                "metadata_json",
            },
        }
        for table, columns in expected_columns.items():
            existing_columns = {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not columns.issubset(existing_columns):
                missing = ", ".join(sorted(columns - existing_columns))
                raise WebDatabaseError(
                    f"{INCOMPATIBLE_DATABASE_MESSAGE} "
                    f"Table {table} is missing required columns: {missing}."
                )

    def _existing_tables(self, connection: sqlite3.Connection) -> set[str]:
        return {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        _ = connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'user')),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                disabled_at TEXT,
                password_changed_at TEXT NOT NULL
            );

            CREATE TABLE sessions (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                last_seen_at TEXT,
                user_agent TEXT,
                ip_address TEXT
            );

            CREATE TABLE runs (
                id TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                project_source_type TEXT NOT NULL DEFAULT 'local',
                project_path TEXT NOT NULL DEFAULT '',
                bug_reports_path TEXT,
                path_metadata_json TEXT NOT NULL DEFAULT '{}',
                paths_json TEXT NOT NULL DEFAULT '{}',
                path_snapshot_json TEXT NOT NULL DEFAULT '{}',
                config_snapshot_json TEXT NOT NULL DEFAULT '{}',
                config_path TEXT NOT NULL DEFAULT 'config.yaml',
                error TEXT,
                queue_position INTEGER,
                cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
                cancellation_reason TEXT
            );

            CREATE TABLE uploads (
                id TEXT PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                status TEXT NOT NULL,
                kind TEXT NOT NULL,
                original_filename TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                extracted_path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                used_at TEXT,
                path_metadata_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                event_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                ip_address TEXT,
                user_agent TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not normalized:
        raise WebDatabaseError("Username must not be empty.")
    return normalized


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _json_load_object(value: str, *, field_name: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise WebDatabaseError(f"Invalid JSON in runs.{field_name}.") from exc
    if not isinstance(loaded, dict):
        raise WebDatabaseError(f"runs.{field_name} must contain a JSON object.")
    return loaded


def _json_load_str_dict(value: str, *, field_name: str) -> dict[str, str]:
    loaded = _json_load_object(value, field_name=field_name)
    result: dict[str, str] = {}
    for key, item in loaded.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise WebDatabaseError(f"runs.{field_name} must contain string keys and values.")
        result[key] = item
    return result


def _row_to_safe_user_record(row: sqlite3.Row) -> SafeUserRecord:
    return SafeUserRecord(
        id=int(row["id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        disabled_at=str(row["disabled_at"]) if row["disabled_at"] is not None else None,
        password_changed_at=str(row["password_changed_at"]),
    )


def _row_to_run_record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        id=str(row["id"]),
        user_id=int(row["user_id"]) if row["user_id"] is not None else None,
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        started_at=str(row["started_at"]) if row["started_at"] is not None else None,
        finished_at=str(row["finished_at"]) if row["finished_at"] is not None else None,
        project_source_type=str(row["project_source_type"]),
        project_path=str(row["project_path"]),
        bug_reports_path=str(row["bug_reports_path"])
        if row["bug_reports_path"] is not None
        else None,
        path_metadata=_json_load_object(row["path_metadata_json"], field_name="path_metadata_json"),
        paths=_json_load_str_dict(row["paths_json"], field_name="paths_json"),
        path_snapshot=_json_load_str_dict(
            row["path_snapshot_json"], field_name="path_snapshot_json"
        ),
        config_snapshot=_json_load_object(
            row["config_snapshot_json"], field_name="config_snapshot_json"
        ),
        config_path=str(row["config_path"]),
        error=str(row["error"]) if row["error"] is not None else None,
        queue_position=int(row["queue_position"]) if row["queue_position"] is not None else None,
        cancel_requested=bool(row["cancel_requested"]),
        cancellation_reason=str(row["cancellation_reason"])
        if row["cancellation_reason"] is not None
        else None,
    )


def _row_to_upload_record(row: sqlite3.Row) -> UploadRecord:
    return UploadRecord(
        id=str(row["id"]),
        user_id=int(row["user_id"]) if row["user_id"] is not None else None,
        status=str(row["status"]),
        kind=str(row["kind"]),
        original_filename=str(row["original_filename"]),
        storage_path=str(row["storage_path"]),
        extracted_path=str(row["extracted_path"]),
        size_bytes=int(row["size_bytes"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        used_at=str(row["used_at"]) if row["used_at"] is not None else None,
        path_metadata=_json_load_object(row["path_metadata_json"], field_name="path_metadata_json"),
    )
