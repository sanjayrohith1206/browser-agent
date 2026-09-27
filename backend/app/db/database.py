"""The database and its repositories."""

from __future__ import annotations

from sqlalchemy import text

from app.config import Settings
from app.db.browser_sessions import BrowserSessionLog
from app.db.crypto import Cipher, EncryptionConfigError, load_or_create_key_file
from app.db.engine import create_engine, migrate
from app.db.memory import MemoryStore
from app.db.users import UserStore
from app.tasks.store import SqlTaskStore


class Database:
    def __init__(self, url: str, cipher: Cipher) -> None:
        self.url = url
        self.engine = create_engine(url)
        self.cipher = cipher
        self.tasks = SqlTaskStore(self.engine, cipher)
        self.users = UserStore(self.engine)
        self.memory = MemoryStore(self.engine, cipher)
        self.browser_sessions = BrowserSessionLog(self.engine)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        url = settings.resolved_database_url
        if url.startswith("sqlite"):
            settings.data_dir.mkdir(parents=True, exist_ok=True)
        return cls(url, cipher_from_settings(settings))

    async def migrate(self) -> None:
        await migrate(self.engine)

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def close(self) -> None:
        await self.engine.dispose()


def cipher_from_settings(settings: Settings) -> Cipher:
    if settings.data_encryption_keys is not None:
        raw = settings.data_encryption_keys.get_secret_value()
        return Cipher([k.strip() for k in raw.split(",") if k.strip()])
    if settings.is_production:
        raise EncryptionConfigError("DATA_ENCRYPTION_KEYS is required in production")
    return Cipher([load_or_create_key_file(settings.data_dir / "encryption.key")])
