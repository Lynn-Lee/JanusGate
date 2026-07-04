import pytest

from app.vault.provider import EnvelopeEncryptedSecretProvider, LocalEncryptedSecretProvider


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
