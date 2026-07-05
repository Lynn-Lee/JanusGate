from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.database import Base
from app.models.vault import SecretRecordModel
from app.vault.provider import (
    AsyncEnvelopeEncryptedSecretProvider,
    SqlAlchemySecretRecordStore,
)
from tests.vault.test_provider import RecordingKmsProvider


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def test_sqlalchemy_secret_record_store_persists_envelope_records(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    kms = RecordingKmsProvider()

    async with session_factory() as session:
        provider = AsyncEnvelopeEncryptedSecretProvider(
            kms_provider=kms,
            record_store=SqlAlchemySecretRecordStore(session),
        )

        secret = await provider.create_secret(name="root-password", plaintext="S3cret!")
        await session.commit()

    async with session_factory() as session:
        stored_model = await session.scalar(
            select(SecretRecordModel).where(SecretRecordModel.id == secret.id)
        )
        assert stored_model is not None
        assert stored_model.ciphertext != "S3cret!"
        assert stored_model.encrypted_data_key is not None

        reloaded_provider = AsyncEnvelopeEncryptedSecretProvider(
            kms_provider=kms,
            record_store=SqlAlchemySecretRecordStore(session),
        )

        assert await reloaded_provider.unwrap(secret.id) == "S3cret!"
