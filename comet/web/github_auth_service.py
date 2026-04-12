from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol, cast
from urllib.parse import urlencode

import httpx

from comet.config.settings import GitHubConfig


class GitHubAuthError(RuntimeError):
    """GitHub 认证流程错误。"""


@dataclass(slots=True)
class GitHubAuthStatus:
    connected: bool
    requires_reauth: bool
    message: str

    def to_payload(self) -> dict[str, object]:
        return {
            "provider": "github-oauth-app",
            "connected": self.connected,
            "requiresReauth": self.requires_reauth,
            "message": self.message,
        }


@dataclass(slots=True)
class GitHubRepository:
    name: str
    full_name: str
    url: str
    description: str | None
    private: bool
    updated_at: str | None

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "fullName": self.full_name,
            "url": self.url,
            "description": self.description,
            "private": self.private,
            "updatedAt": self.updated_at,
        }


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, account_name: str) -> str | None: ...

    def set_password(self, service_name: str, account_name: str, password: str) -> None: ...

    def delete_password(self, service_name: str, account_name: str) -> None: ...


class GitHubTokenStorage:
    def __init__(
        self,
        *,
        keyring_service_name: str = "comet-l.github.oauth",
        keyring_account_name: str = "default-user",
        keyring_backend: KeyringBackend | None = None,
    ) -> None:
        self._keyring_service_name: str = keyring_service_name
        self._keyring_account_name: str = keyring_account_name
        self._keyring_backend: KeyringBackend | None = keyring_backend

    def read_token(self, github_config: GitHubConfig) -> str | None:
        token = self._read_token_from_keyring()
        if token is not None:
            return token
        return self._read_token_from_encrypted_file(github_config)

    def write_token(self, github_config: GitHubConfig, token: str) -> None:
        normalized_token = token.strip()
        if not normalized_token:
            raise GitHubAuthError("GitHub token 为空，无法保存。")

        if self._write_token_to_keyring(normalized_token):
            self._clear_encrypted_files(github_config)
            return

        self._write_token_to_encrypted_file(github_config, normalized_token)

    def clear_token(self, github_config: GitHubConfig) -> None:
        self._clear_token_from_keyring()
        self._clear_encrypted_files(github_config)

    def _keyring_module(self) -> KeyringBackend | None:
        if self._keyring_backend is not None:
            return self._keyring_backend

        try:
            keyring = importlib.import_module("keyring")
        except Exception:
            return None

        return cast(KeyringBackend, cast(object, keyring))

    def _read_token_from_keyring(self) -> str | None:
        keyring_module = self._keyring_module()
        if keyring_module is None:
            return None

        try:
            token = keyring_module.get_password(
                self._keyring_service_name,
                self._keyring_account_name,
            )
        except Exception:
            return None

        if token is None:
            return None
        return token.strip() or None

    def _write_token_to_keyring(self, token: str) -> bool:
        keyring_module = self._keyring_module()
        if keyring_module is None:
            return False

        try:
            keyring_module.set_password(
                self._keyring_service_name,
                self._keyring_account_name,
                token,
            )
            return True
        except Exception:
            return False

    def _clear_token_from_keyring(self) -> None:
        keyring_module = self._keyring_module()
        if keyring_module is None:
            return

        try:
            keyring_module.delete_password(
                self._keyring_service_name,
                self._keyring_account_name,
            )
        except Exception:
            return

    def _read_token_from_encrypted_file(self, github_config: GitHubConfig) -> str | None:
        token_path = Path(github_config.encrypted_token_store_path).expanduser()
        key_path = Path(github_config.encrypted_key_store_path).expanduser()
        if not token_path.is_file() or not key_path.is_file():
            return None

        try:
            key = base64.urlsafe_b64decode(key_path.read_text(encoding="utf-8").encode("utf-8"))
            payload_raw = json.loads(token_path.read_text(encoding="utf-8"))
            payload = cast(dict[str, object], payload_raw)
            nonce = base64.urlsafe_b64decode(str(payload["nonce"]).encode("utf-8"))
            ciphertext = base64.urlsafe_b64decode(str(payload["ciphertext"]).encode("utf-8"))
            signature = base64.urlsafe_b64decode(str(payload["signature"]).encode("utf-8"))
        except Exception as exc:
            raise GitHubAuthError("本地 GitHub 凭据损坏，请重新授权。") from exc

        expected_signature = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(expected_signature, signature):
            raise GitHubAuthError("本地 GitHub 凭据校验失败，请重新授权。")

        try:
            plaintext = self._xor_stream(ciphertext, key, nonce)
            return plaintext.decode("utf-8")
        except Exception as exc:
            raise GitHubAuthError("本地 GitHub 凭据解密失败，请重新授权。") from exc

    def _write_token_to_encrypted_file(self, github_config: GitHubConfig, token: str) -> None:
        token_path = Path(github_config.encrypted_token_store_path).expanduser()
        key_path = Path(github_config.encrypted_key_store_path).expanduser()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        key_path.parent.mkdir(parents=True, exist_ok=True)

        key = os.urandom(32)
        nonce = os.urandom(16)
        plaintext = token.encode("utf-8")
        ciphertext = self._xor_stream(plaintext, key, nonce)
        signature = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()

        payload = {
            "version": 1,
            "nonce": base64.urlsafe_b64encode(nonce).decode("utf-8"),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("utf-8"),
            "signature": base64.urlsafe_b64encode(signature).decode("utf-8"),
        }
        self._write_private_text(
            key_path,
            base64.urlsafe_b64encode(key).decode("utf-8"),
        )
        self._write_private_text(
            token_path,
            json.dumps(payload, ensure_ascii=False, indent=2),
        )

    def _clear_encrypted_files(self, github_config: GitHubConfig) -> None:
        for path in (
            Path(github_config.encrypted_token_store_path).expanduser(),
            Path(github_config.encrypted_key_store_path).expanduser(),
        ):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                continue

    def _write_private_text(self, file_path: Path, content: str) -> None:
        descriptor = os.open(
            file_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(content)

    @staticmethod
    def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
        output = bytearray(len(data))
        counter = 0
        offset = 0
        while offset < len(data):
            block = hmac.new(
                key,
                nonce + counter.to_bytes(8, "big"),
                hashlib.sha256,
            ).digest()
            chunk = data[offset : offset + len(block)]
            for index, value in enumerate(chunk):
                output[offset + index] = value ^ block[index]
            offset += len(chunk)
            counter += 1
        return bytes(output)


class GitHubOAuthService:
    def __init__(
        self,
        *,
        storage: GitHubTokenStorage | None = None,
        http_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._storage: GitHubTokenStorage = storage or GitHubTokenStorage()
        self._http_client_factory: Callable[[], httpx.Client] = (
            http_client_factory or self._default_http_client_factory
        )
        self._pending_states: dict[str, float] = {}

    def build_connect_url(self, github_config: GitHubConfig) -> str:
        client_id = (github_config.oauth_client_id or "").strip()
        if not client_id:
            raise GitHubAuthError("GitHub OAuth App 未配置 client_id。")

        state = secrets.token_urlsafe(24)
        now = time.time()
        self._pending_states[state] = now + float(github_config.oauth_state_ttl_seconds)
        self._cleanup_expired_states(now)

        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": github_config.oauth_redirect_uri,
                "scope": github_config.oauth_scope,
                "state": state,
            }
        )
        return f"{github_config.oauth_authorize_url}?{query}"

    def handle_callback(
        self, github_config: GitHubConfig, *, code: str, state: str
    ) -> GitHubAuthStatus:
        self._validate_state(github_config, state)
        token = self._exchange_code_for_token(github_config, code)
        self._storage.write_token(github_config, token)
        status = self.get_status(github_config)
        if not status.connected:
            self._storage.clear_token(github_config)
            raise GitHubAuthError("GitHub 授权成功但 token 校验失败，请重试授权。")
        return status

    def get_status(self, github_config: GitHubConfig) -> GitHubAuthStatus:
        token = self._storage.read_token(github_config)
        if token is None:
            return GitHubAuthStatus(
                connected=False,
                requires_reauth=False,
                message="尚未连接 GitHub。",
            )

        validation = self._validate_token(github_config, token)
        if validation == "valid":
            return GitHubAuthStatus(
                connected=True,
                requires_reauth=False,
                message="GitHub 已连接。",
            )
        if validation == "invalid":
            return GitHubAuthStatus(
                connected=False,
                requires_reauth=True,
                message="GitHub 授权已失效，请重新授权。",
            )
        return GitHubAuthStatus(
            connected=False,
            requires_reauth=False,
            message="GitHub 状态检查失败，请稍后重试。",
        )

    def disconnect(self, github_config: GitHubConfig) -> None:
        self._storage.clear_token(github_config)

    def get_access_token(self, github_config: GitHubConfig) -> str:
        token = self._storage.read_token(github_config)
        if token is None or not token.strip():
            raise GitHubAuthError("未检测到 GitHub 授权，请先完成授权。")

        validation = self._validate_token(github_config, token)
        if validation == "valid":
            return token
        if validation == "invalid":
            raise GitHubAuthError("GitHub 授权已失效，请重新授权。")
        raise GitHubAuthError("GitHub 状态检查失败，请稍后重试。")

    def list_repositories(self, github_config: GitHubConfig) -> list[GitHubRepository]:
        token = self.get_access_token(github_config)
        with self._http_client_factory() as http_client:
            response = http_client.get(
                f"{github_config.oauth_api_base_url}/user/repos",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
                params={
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": 100,
                },
            )

        if response.status_code == 200:
            payload = cast(list[dict[str, object]], response.json())
            repositories: list[GitHubRepository] = []
            for item in payload:
                name = str(item.get("name", "")).strip()
                full_name = str(item.get("full_name", "")).strip()
                url = str(item.get("html_url", "")).strip()
                if not name or not full_name or not url:
                    continue
                repositories.append(
                    GitHubRepository(
                        name=name,
                        full_name=full_name,
                        url=url,
                        description=str(item["description"])
                        if item.get("description") is not None
                        else None,
                        private=bool(item.get("private", False)),
                        updated_at=(
                            str(item["updated_at"]) if item.get("updated_at") is not None else None
                        ),
                    )
                )
            return repositories

        if response.status_code in {401, 403}:
            raise GitHubAuthError("GitHub 授权已失效，请重新授权。")
        raise GitHubAuthError("无法获取 GitHub 仓库列表，请稍后重试。")

    def _validate_state(self, github_config: GitHubConfig, state: str) -> None:
        now = time.time()
        self._cleanup_expired_states(now)
        expires_at = self._pending_states.pop(state, None)
        if expires_at is None or expires_at <= now:
            raise GitHubAuthError("OAuth 回调状态无效或已过期，请重新发起授权。")

        if github_config.oauth_state_ttl_seconds <= 0:
            raise GitHubAuthError("GitHub OAuth 配置无效：state TTL 必须大于 0。")

    def _cleanup_expired_states(self, now: float) -> None:
        expired = [state for state, expires_at in self._pending_states.items() if expires_at <= now]
        for state in expired:
            _ = self._pending_states.pop(state, None)

    def _exchange_code_for_token(self, github_config: GitHubConfig, code: str) -> str:
        client_id = (github_config.oauth_client_id or "").strip()
        client_secret = (github_config.oauth_client_secret or "").strip()
        if not client_id or not client_secret:
            raise GitHubAuthError("GitHub OAuth App 未完整配置 client_id/client_secret。")

        with self._http_client_factory() as http_client:
            response = http_client.post(
                github_config.oauth_token_exchange_url,
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": github_config.oauth_redirect_uri,
                },
            )

        if response.status_code != 200:
            raise GitHubAuthError("GitHub 授权失败，请稍后重试。")

        payload = cast(dict[str, object], response.json())
        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            error = str(payload.get("error", "")).strip()
            if error:
                raise GitHubAuthError(f"GitHub 授权失败：{error}。")
            raise GitHubAuthError("GitHub 授权失败，未返回 access_token。")

        return access_token

    def _validate_token(self, github_config: GitHubConfig, token: str) -> str:
        client_id = (github_config.oauth_client_id or "").strip()
        client_secret = (github_config.oauth_client_secret or "").strip()
        if client_id and client_secret:
            return self._validate_token_with_oauth_application_api(
                github_config,
                token,
                client_id,
                client_secret,
            )
        return self._validate_token_with_user_api(github_config, token)

    def _validate_token_with_oauth_application_api(
        self,
        github_config: GitHubConfig,
        token: str,
        client_id: str,
        client_secret: str,
    ) -> str:
        validate_url = f"{github_config.oauth_api_base_url}/applications/{client_id}/token"
        with self._http_client_factory() as http_client:
            response = http_client.post(
                validate_url,
                auth=(client_id, client_secret),
                headers={"Accept": "application/json"},
                json={"access_token": token},
            )

        if response.status_code == 200:
            return "valid"
        if response.status_code in {401, 404, 422}:
            return "invalid"
        return "error"

    def _validate_token_with_user_api(self, github_config: GitHubConfig, token: str) -> str:
        with self._http_client_factory() as http_client:
            response = http_client.get(
                f"{github_config.oauth_api_base_url}/user",
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {token}",
                },
            )

        if response.status_code == 200:
            return "valid"
        if response.status_code == 401:
            return "invalid"
        return "error"

    @staticmethod
    def _default_http_client_factory() -> httpx.Client:
        return httpx.Client(timeout=10.0)
