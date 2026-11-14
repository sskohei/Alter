"""AES-256-GCM, random nonce and agent/version associated data prevent ciphertext swaps."""

import base64
import os
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ProfileCipher:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except ValueError:
            raise ValueError("Profile encryption key is not configured correctly") from None
        if len(key) != 32:
            raise ValueError("Profile encryption key must encode 32 bytes")
        self._cipher = AESGCM(key)

    @staticmethod
    def _aad(agent_id: UUID, version: int) -> bytes:
        return f"alter:profile:{agent_id}:{version}:1".encode()

    def encrypt(self, value: str, agent_id: UUID, version: int) -> bytes:
        nonce = os.urandom(12)
        return nonce + self._cipher.encrypt(nonce, value.encode(), self._aad(agent_id, version))

    def decrypt(self, value: bytes, agent_id: UUID, version: int) -> str:
        return self._cipher.decrypt(value[:12], value[12:], self._aad(agent_id, version)).decode()
