import pytest

from app.vault.provider import LocalEncryptedSecretProvider


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
