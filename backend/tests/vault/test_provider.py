import base64

import pytest

from app.policy.schemas import ApprovalState
from app.vault.provider import (
    AsyncEnvelopeEncryptedSecretProvider,
    EnvelopeEncryptedSecretProvider,
    InMemorySecretRecordStore,
    LocalEncryptedSecretProvider,
    LocalKmsEnvelopeKeyProvider,
)


def test_local_provider_never_persists_plaintext():
    provider = LocalEncryptedSecretProvider(master_key=b"0" * 32)

    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    stored = provider.get_record(secret.id)
    assert stored.ciphertext != "S3cret!"
    assert stored.nonce
    assert provider.unwrap(secret.id) == "S3cret!"


def test_local_provider_rejects_tampered_ciphertext():
    provider = LocalEncryptedSecretProvider(master_key=b"0" * 32)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")
    record = provider.get_record(secret.id)
    record.ciphertext = record.ciphertext[:-2] + "AA"

    with pytest.raises(ValueError, match="SECRET_DECRYPT_FAILED"):
        provider.unwrap(secret.id)


def test_revoked_secret_cannot_be_unwrapped():
    provider = LocalEncryptedSecretProvider(master_key=b"0" * 32)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    provider.revoke(secret.id)

    with pytest.raises(ValueError, match="SECRET_REVOKED"):
        provider.unwrap(secret.id)


def test_rotate_creates_new_version():
    provider = LocalEncryptedSecretProvider(master_key=b"0" * 32)
    secret = provider.create_secret(name="root-password", plaintext="old")

    rotated = provider.rotate(secret.id, new_plaintext="new")

    assert rotated.id == secret.id
    assert rotated.version == 2
    assert provider.unwrap(secret.id) == "new"


class RecordingKmsProvider:
    def __init__(self) -> None:
        self.wrap_calls: list[bytes] = []
        self.unwrap_calls: list[bytes] = []
        self.reject_unwrap = False

    def wrap_key(self, plaintext_key: bytes) -> str:
        self.wrap_calls.append(plaintext_key)
        return plaintext_key.hex()

    def unwrap_key(self, wrapped_key: str) -> bytes:
        if self.reject_unwrap:
            raise ValueError("KMS_UNWRAP_DENIED")
        key = bytes.fromhex(wrapped_key)
        self.unwrap_calls.append(key)
        return key


class AsyncInMemorySecretRecordStore:
    def __init__(self) -> None:
        self._store = InMemorySecretRecordStore()

    async def put(self, record):
        self._store.put(record)

    async def get(self, secret_id: str):
        return self._store.get(secret_id)


def test_envelope_provider_wraps_per_secret_data_key():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)

    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    stored = provider.get_record(secret.id)
    assert stored.ciphertext != "S3cret!"
    assert stored.encrypted_data_key is not None
    assert stored.nonce
    assert len(kms.wrap_calls) == 1
    assert kms.wrap_calls[0] != b"0" * 32
    assert provider.unwrap(secret.id) == "S3cret!"
    assert kms.unwrap_calls == kms.wrap_calls


def test_envelope_provider_fails_closed_when_kms_unwrap_denied():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    kms.reject_unwrap = True

    with pytest.raises(ValueError, match="KMS_UNWRAP_DENIED"):
        provider.unwrap(secret.id)


def test_envelope_provider_can_reload_records_from_persistent_store():
    kms = RecordingKmsProvider()
    store = InMemorySecretRecordStore()
    first_provider = EnvelopeEncryptedSecretProvider(kms_provider=kms, record_store=store)

    secret = first_provider.create_secret(name="root-password", plaintext="S3cret!")

    reloaded_provider = EnvelopeEncryptedSecretProvider(kms_provider=kms, record_store=store)
    assert reloaded_provider.unwrap(secret.id) == "S3cret!"


def test_envelope_provider_requires_approved_grant_for_approval_unwrap():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    with pytest.raises(ValueError, match="SECRET_UNWRAP_APPROVAL_REQUIRED"):
        provider.unwrap_after_approval(secret.id, approval=None)

    with pytest.raises(ValueError, match="SECRET_UNWRAP_APPROVAL_REQUIRED"):
        provider.unwrap_after_approval(
            secret.id,
            ApprovalState(status="pending", grant_id="grant-1", workflow_request_id="wf-1"),
        )


def test_envelope_provider_rejects_approval_without_grant_identity():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    with pytest.raises(ValueError, match="SECRET_UNWRAP_APPROVAL_IDENTITY_REQUIRED"):
        provider.unwrap_after_approval(secret.id, ApprovalState(status="approved"))


def test_envelope_provider_rejects_approval_for_different_secret():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    with pytest.raises(ValueError, match="SECRET_UNWRAP_APPROVAL_SCOPE_MISMATCH"):
        provider.unwrap_after_approval(
            secret.id,
            ApprovalState(
                status="approved",
                grant_id="grant-1",
                workflow_request_id="wf-1",
                constraints={"vault_secret_id": "sec_other"},
            ),
        )


def test_envelope_provider_unwraps_with_matching_approved_grant():
    kms = RecordingKmsProvider()
    provider = EnvelopeEncryptedSecretProvider(kms_provider=kms)
    secret = provider.create_secret(name="root-password", plaintext="S3cret!")

    plaintext = provider.unwrap_after_approval(
        secret.id,
        ApprovalState(
            status="approved",
            grant_id="grant-1",
            workflow_request_id="wf-1",
            constraints={"vault_secret_ids": [secret.id]},
        ),
    )

    assert plaintext == "S3cret!"


async def test_async_envelope_provider_unwraps_with_matching_approved_grant():
    kms = RecordingKmsProvider()
    store = AsyncInMemorySecretRecordStore()
    provider = AsyncEnvelopeEncryptedSecretProvider(kms_provider=kms, record_store=store)
    secret = await provider.create_secret(name="root-password", plaintext="S3cret!")

    plaintext = await provider.unwrap_after_approval(
        secret.id,
        ApprovalState(
            status="approved",
            grant_id="grant-1",
            workflow_request_id="wf-1",
            constraints={"vault_secret_id": secret.id},
        ),
    )

    assert plaintext == "S3cret!"


def test_local_kms_provider_encrypts_wrapped_data_keys():
    kms = LocalKmsEnvelopeKeyProvider(master_key=b"k" * 32)
    data_key = b"d" * 32

    wrapped = kms.wrap_key(data_key)

    assert wrapped != data_key.hex()
    assert "d" * 64 not in wrapped
    assert kms.unwrap_key(wrapped) == data_key


def test_local_kms_provider_requires_32_byte_master_key():
    with pytest.raises(ValueError, match="KMS_MASTER_KEY_MUST_BE_32_BYTES"):
        LocalKmsEnvelopeKeyProvider(master_key=b"short")


def test_local_kms_provider_fails_closed_for_wrong_key():
    wrapped = LocalKmsEnvelopeKeyProvider(master_key=b"a" * 32).wrap_key(b"d" * 32)

    with pytest.raises(ValueError, match="KMS_UNWRAP_DENIED"):
        LocalKmsEnvelopeKeyProvider(master_key=b"b" * 32).unwrap_key(wrapped)


def test_local_kms_provider_can_load_base64_master_key():
    encoded_key = base64.urlsafe_b64encode(b"k" * 32).decode()

    kms = LocalKmsEnvelopeKeyProvider.from_base64_master_key(encoded_key)

    assert kms.unwrap_key(kms.wrap_key(b"d" * 32)) == b"d" * 32
