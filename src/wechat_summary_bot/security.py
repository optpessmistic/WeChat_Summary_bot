from __future__ import annotations

import os
from threading import Lock

import keyring
from keyring.errors import KeyringError, NoKeyringError

SERVICE_NAME = "WeChatSummaryBot"


class SecretStore:
    """Persist provider keys in the OS credential store, with a process-only fallback."""

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        self._lock = Lock()

    def set(self, provider_id: str, secret: str) -> bool:
        value = str(secret or "").strip()
        if not value:
            self.delete(provider_id)
            return True
        try:
            keyring.set_password(SERVICE_NAME, provider_id, value)
            with self._lock:
                self._memory.pop(provider_id, None)
            return True
        except (KeyringError, NoKeyringError, RuntimeError):
            with self._lock:
                self._memory[provider_id] = value
            return False

    def get(self, provider_id: str) -> str:
        try:
            value = keyring.get_password(SERVICE_NAME, provider_id)
            if value:
                return value
        except (KeyringError, NoKeyringError, RuntimeError):
            pass
        with self._lock:
            value = self._memory.get(provider_id, "")
        if value:
            return value
        return (
            os.environ.get(f"WECHAT_SUMMARY_API_KEY_{provider_id.replace('-', '_').upper()}", "")
            or os.environ.get("WECHAT_SUMMARY_API_KEY", "")
            or os.environ.get("OPENAI_API_KEY", "")
        ).strip()

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))

    def delete(self, provider_id: str) -> None:
        try:
            keyring.delete_password(SERVICE_NAME, provider_id)
        except (KeyringError, NoKeyringError, RuntimeError):
            pass
        with self._lock:
            self._memory.pop(provider_id, None)

