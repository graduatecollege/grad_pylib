import threading

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine

from grad_pylib.core.config import BaseAppSettings, get_settings


def build_mssql_url(odbc_connection_string: str) -> str:
    """
    Build the connection URL for an MSSQL database using an ODBC connection string.

    Arguments:
    odbc_connection_string: str
        The ODBC connection string containing the required connection parameters
        such as driver, server, database, and authentication information.
    """
    return URL.create("mssql+pyodbc", query={"odbc_connect": odbc_connection_string}).render_as_string(
        hide_password=False
    )


def resolve_database_url(settings: BaseAppSettings | None = None) -> str:
    settings = settings or get_settings()
    if settings.database_url:
        return build_mssql_url(settings.database_url)
    raise ValueError("DATABASE_URL must be set.")


_engine: Engine | None = None
_engine_lock = threading.Lock()


def get_engine() -> Engine:
    """
    Get the SQLAlchemy Engine for the default MSSQL database.

    This function ensures that the engine is created only once and reused across
    multiple calls. It uses a lock to prevent race conditions when multiple threads
    might try to create the engine simultaneously.
    """
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                # this assignment keeps the type system happy
                eng = create_engine(
                    resolve_database_url(),
                    pool_pre_ping=True,
                    pool_size=5,
                    max_overflow=20
                )
                _engine = eng
                return eng
    return _engine
