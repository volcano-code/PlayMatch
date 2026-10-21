from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from playmatch import catalog_search  # noqa: F401 -- register ORM search-column events


class Database:
    def __init__(self, url: str):
        parsed = make_url(url)
        if parsed.drivername.startswith("postgresql") and "+" not in parsed.drivername:
            parsed = parsed.set(drivername="postgresql+psycopg")
        if parsed.drivername == "sqlite" and parsed.database not in {None, "", ":memory:"}:
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        kwargs = (
            {"connect_args": {"check_same_thread": False}} if parsed.drivername == "sqlite" else {}
        )
        self.engine = create_engine(parsed, pool_pre_ping=True, **kwargs)
        if parsed.drivername == "sqlite":

            @event.listens_for(self.engine, "connect")
            def sqlite_options(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA journal_mode=WAL")

        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def close(self) -> None:
        self.engine.dispose()
