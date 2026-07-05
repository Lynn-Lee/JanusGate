"""SecretProvider abstraction and local encrypted development provider."""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vault import SecretRecordModel
from app.policy.schemas import ApprovalState


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
    encrypted_data_key: str | None = None
    status: SecretStatus = SecretStatus.ACTIVE


class KmsKeyProvider(Protocol):
    def wrap_key(self, plaintext_key: bytes) -> str: ...

    def unwrap_key(self, wrapped_key: str) -> bytes: ...


class LocalKmsEnvelopeKeyProvider:
    """AES-GCM KMS adapter for self-hosted or development deployments."""

    _WRAPPED_KEY_PREFIX = "local-aesgcm-v1"

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != 32:
            raise ValueError("KMS_MASTER_KEY_MUST_BE_32_BYTES")
        self._master_key = master_key

    @classmethod
    def from_base64_master_key(cls, encoded_master_key: str) -> LocalKmsEnvelopeKeyProvider:
        try:
            master_key = base64.urlsafe_b64decode(encoded_master_key.encode())
        except ValueError as exc:
            raise ValueError("KMS_MASTER_KEY_INVALID") from exc
        return cls(master_key=master_key)

    def wrap_key(self, plaintext_key: bytes) -> str:
        if len(plaintext_key) != 32:
            raise ValueError("SECRET_DATA_KEY_INVALID")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._master_key).encrypt(
            nonce,
            plaintext_key,
            self._WRAPPED_KEY_PREFIX.encode(),
        )
        return ":".join(
            [
                self._WRAPPED_KEY_PREFIX,
                base64.urlsafe_b64encode(nonce).decode(),
                base64.urlsafe_b64encode(ciphertext).decode(),
            ]
        )

    def unwrap_key(self, wrapped_key: str) -> bytes:
        try:
            prefix, encoded_nonce, encoded_ciphertext = wrapped_key.split(":", maxsplit=2)
            if prefix != self._WRAPPED_KEY_PREFIX:
                raise ValueError("UNSUPPORTED_KMS_WRAPPED_KEY")
            nonce = base64.urlsafe_b64decode(encoded_nonce.encode())
            ciphertext = base64.urlsafe_b64decode(encoded_ciphertext.encode())
            data_key = AESGCM(self._master_key).decrypt(
                nonce,
                ciphertext,
                self._WRAPPED_KEY_PREFIX.encode(),
            )
        except (InvalidTag, ValueError) as exc:
            raise ValueError("KMS_UNWRAP_DENIED") from exc
        if len(data_key) != 32:
            raise ValueError("SECRET_DATA_KEY_INVALID")
        return data_key


class SecretRecordStore(Protocol):
    def put(self, record: SecretRecord) -> None: ...

    def get(self, secret_id: str) -> SecretRecord | None: ...


class AsyncSecretRecordStore(Protocol):
    async def put(self, record: SecretRecord) -> None: ...

    async def get(self, secret_id: str) -> SecretRecord | None: ...


class InMemorySecretRecordStore:
    def __init__(self) -> None:
        self._records: dict[str, SecretRecord] = {}

    def put(self, record: SecretRecord) -> None:
        self._records[record.id] = record

    def get(self, secret_id: str) -> SecretRecord | None:
        return self._records.get(secret_id)


class SqlAlchemySecretRecordStore:
    """AsyncSession-backed store for envelope-encrypted secret records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def put(self, record: SecretRecord) -> None:
        model = await self._session.get(SecretRecordModel, record.id)
        if model is None:
            model = SecretRecordModel(id=record.id)
            self._session.add(model)

        model.name = record.name
        model.nonce = record.nonce
        model.ciphertext = record.ciphertext
        model.version = record.version
        model.encrypted_data_key = record.encrypted_data_key
        model.status = record.status.value
        await self._session.flush()

    async def get(self, secret_id: str) -> SecretRecord | None:
        result = await self._session.execute(
            select(SecretRecordModel).where(SecretRecordModel.id == secret_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        return SecretRecord(
            id=model.id,
            name=model.name,
            nonce=model.nonce,
            ciphertext=model.ciphertext,
            version=model.version,
            encrypted_data_key=model.encrypted_data_key,
            status=SecretStatus(model.status),
        )


def _validate_secret_unwrap_approval(secret_id: str, approval: ApprovalState | None) -> None:
    if approval is None or not approval.is_approved_now():
        raise ValueError("SECRET_UNWRAP_APPROVAL_REQUIRED")
    if not approval.grant_id or not approval.workflow_request_id:
        raise ValueError("SECRET_UNWRAP_APPROVAL_IDENTITY_REQUIRED")
    if not _approval_scope_includes_secret(secret_id, approval):
        raise ValueError("SECRET_UNWRAP_APPROVAL_SCOPE_MISMATCH")


def _approval_scope_includes_secret(secret_id: str, approval: ApprovalState) -> bool:
    constraints = approval.constraints
    if constraints.get("vault_secret_id") == secret_id:
        return True

    vault_secret_ids = constraints.get("vault_secret_ids")
    return isinstance(vault_secret_ids, list) and secret_id in vault_secret_ids


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

    def unwrap_after_approval(self, secret_id: str, approval: ApprovalState | None) -> str:
        _validate_secret_unwrap_approval(secret_id, approval)
        return self.unwrap(secret_id)

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


class EnvelopeEncryptedSecretProvider:
    """Envelope-encrypted provider backed by an external KMS contract."""

    def __init__(
        self,
        kms_provider: KmsKeyProvider,
        record_store: SecretRecordStore | None = None,
    ) -> None:
        self._kms_provider = kms_provider
        self._record_store = record_store or InMemorySecretRecordStore()

    def create_secret(self, name: str, plaintext: str) -> SecretRecord:
        secret_id = f"sec_{uuid4().hex}"
        record = self._encrypt_record(secret_id=secret_id, name=name, plaintext=plaintext, version=1)
        self._record_store.put(record)
        return record

    def get_record(self, secret_id: str) -> SecretRecord:
        record = self._record_store.get(secret_id)
        if record is None:
            raise ValueError("SECRET_NOT_FOUND")
        return record

    def unwrap(self, secret_id: str) -> str:
        record = self.get_record(secret_id)
        if record.status == SecretStatus.REVOKED:
            raise ValueError("SECRET_REVOKED")
        return self._decrypt_record(record)

    def unwrap_after_approval(self, secret_id: str, approval: ApprovalState | None) -> str:
        _validate_secret_unwrap_approval(secret_id, approval)
        return self.unwrap(secret_id)

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
        self._record_store.put(rotated)
        return rotated

    def revoke(self, secret_id: str) -> None:
        current = self.get_record(secret_id)
        current.status = SecretStatus.REVOKED
        self._record_store.put(current)

    def _encrypt_record(self, secret_id: str, name: str, plaintext: str, version: int) -> SecretRecord:
        data_key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode(), secret_id.encode())
        return SecretRecord(
            id=secret_id,
            name=name,
            nonce=base64.urlsafe_b64encode(nonce).decode(),
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
            version=version,
            encrypted_data_key=self._kms_provider.wrap_key(data_key),
        )

    def _decrypt_record(self, record: SecretRecord) -> str:
        if record.encrypted_data_key is None:
            raise ValueError("SECRET_DATA_KEY_MISSING")
        data_key = self._kms_provider.unwrap_key(record.encrypted_data_key)
        if len(data_key) != 32:
            raise ValueError("SECRET_DATA_KEY_INVALID")
        try:
            nonce = base64.urlsafe_b64decode(record.nonce.encode())
            ciphertext = base64.urlsafe_b64decode(record.ciphertext.encode())
            return AESGCM(data_key).decrypt(nonce, ciphertext, record.id.encode()).decode()
        except (InvalidTag, ValueError) as exc:
            raise ValueError("SECRET_DECRYPT_FAILED") from exc


class AsyncEnvelopeEncryptedSecretProvider:
    """Envelope-encrypted provider backed by an async record store."""

    def __init__(
        self,
        kms_provider: KmsKeyProvider,
        record_store: AsyncSecretRecordStore,
    ) -> None:
        self._kms_provider = kms_provider
        self._record_store = record_store

    async def create_secret(self, name: str, plaintext: str) -> SecretRecord:
        secret_id = f"sec_{uuid4().hex}"
        record = self._encrypt_record(secret_id=secret_id, name=name, plaintext=plaintext, version=1)
        await self._record_store.put(record)
        return record

    async def get_record(self, secret_id: str) -> SecretRecord:
        record = await self._record_store.get(secret_id)
        if record is None:
            raise ValueError("SECRET_NOT_FOUND")
        return record

    async def unwrap(self, secret_id: str) -> str:
        record = await self.get_record(secret_id)
        if record.status == SecretStatus.REVOKED:
            raise ValueError("SECRET_REVOKED")
        return self._decrypt_record(record)

    async def unwrap_after_approval(
        self,
        secret_id: str,
        approval: ApprovalState | None,
    ) -> str:
        _validate_secret_unwrap_approval(secret_id, approval)
        return await self.unwrap(secret_id)

    async def rotate(self, secret_id: str, new_plaintext: str) -> SecretRecord:
        current = await self.get_record(secret_id)
        if current.status == SecretStatus.REVOKED:
            raise ValueError("SECRET_REVOKED")
        rotated = self._encrypt_record(
            secret_id=secret_id,
            name=current.name,
            plaintext=new_plaintext,
            version=current.version + 1,
        )
        await self._record_store.put(rotated)
        return rotated

    async def revoke(self, secret_id: str) -> None:
        current = await self.get_record(secret_id)
        current.status = SecretStatus.REVOKED
        await self._record_store.put(current)

    def _encrypt_record(self, secret_id: str, name: str, plaintext: str, version: int) -> SecretRecord:
        data_key = os.urandom(32)
        nonce = os.urandom(12)
        ciphertext = AESGCM(data_key).encrypt(nonce, plaintext.encode(), secret_id.encode())
        return SecretRecord(
            id=secret_id,
            name=name,
            nonce=base64.urlsafe_b64encode(nonce).decode(),
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode(),
            version=version,
            encrypted_data_key=self._kms_provider.wrap_key(data_key),
        )

    def _decrypt_record(self, record: SecretRecord) -> str:
        if record.encrypted_data_key is None:
            raise ValueError("SECRET_DATA_KEY_MISSING")
        data_key = self._kms_provider.unwrap_key(record.encrypted_data_key)
        if len(data_key) != 32:
            raise ValueError("SECRET_DATA_KEY_INVALID")
        try:
            nonce = base64.urlsafe_b64decode(record.nonce.encode())
            ciphertext = base64.urlsafe_b64decode(record.ciphertext.encode())
            return AESGCM(data_key).decrypt(nonce, ciphertext, record.id.encode()).decode()
        except (InvalidTag, ValueError) as exc:
            raise ValueError("SECRET_DECRYPT_FAILED") from exc
