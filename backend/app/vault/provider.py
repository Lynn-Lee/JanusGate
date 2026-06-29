"""SecretProvider abstraction and local encrypted development provider."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from enum import StrEnum
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SecretStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass
class SecretRecord:
    id: str
    name: str
    nonce: str
    ciphertext: str
    version: int
    status: SecretStatus = SecretStatus.ACTIVE


class LocalEncryptedSecretProvider:
    """Development SecretProvider backed by in-memory AES-GCM records.

    This provider is intentionally simple and non-persistent. Production should
    use a KMS or Vault-backed provider implementing the same semantics.
    """

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("MASTER_KEY_MUST_BE_32_BYTES")
        self._master_key = master_key
        self._records: dict[str, SecretRecord] = {}

    def create_secret(self, name: str, plaintext: str) -> SecretRecord:
        secret_id = f"sec_{uuid4().hex}"
        record = self._encrypt_record(secret_id=secret_id, name=name, plaintext=plaintext, version=1)
        self._records[secret_id] = record
        return record

    def get_record(self, secret_id: str) -> SecretRecord:
        record = self._records.get(secret_id)
        if record is None:
            raise ValueError("SECRET_NOT_FOUND")
        return record

    def unwrap(self, secret_id: str) -> str:
        record = self.get_record(secret_id)
        if record.status == SecretStatus.REVOKED:
            raise ValueError("SECRET_REVOKED")
        return self._decrypt_record(record)

    def rotate(self, secret_id: str, new_plaintext: str) -> SecretRecord:
        current = self.get_record(secret_id)
        if current.status == SecretStatus.REVOKED:
            raise ValueError("SECRET_REVOKED")
        rotated = self._encrypt_record(
            secret_id=secret_id,
            name=current.name,
            plaintext=new_plaintext,
            version=current.version + 1,
        )
        self._records[secret_id] = rotated
        return rotated

    def revoke(self, secret_id: str) -> None:
        current = self.get_record(secret_id)
        current.status = SecretStatus.REVOKED

    def _encrypt_record(self, secret_id: str, name: str, plaintext: str, version: int) -> SecretRecord:
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._master_key).encrypt(nonce, plaintext.encode(), secret_id.encode())
        return SecretRecord(
            id=secret_id,
            name=name,
            nonce=base64.urlsafe_b64encode(nonce).decode(),
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
            version=version,
        )

    def _decrypt_record(self, record: SecretRecord) -> str:
        try:
            nonce = base64.urlsafe_b64decode(record.nonce.encode())
            ciphertext = base64.urlsafe_b64decode(record.ciphertext.encode())
            return AESGCM(self._master_key).decrypt(nonce, ciphertext, record.id.encode()).decode()
        except (InvalidTag, ValueError) as exc:
            raise ValueError("SECRET_DECRYPT_FAILED") from exc
