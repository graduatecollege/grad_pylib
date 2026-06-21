import re
import threading
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import URL, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, DBAPIError

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


class SqlServerErrorType(Enum):
    DEADLOCK = "deadlock"  # Code 1205
    LOCK_TIMEOUT = "lock_timeout"  # Code 1222
    DUPLICATE_KEY = "duplicate_key"  # Codes 2601, 2627
    FOREIGN_KEY_VIOLATION = "foreign_key"  # Code 547
    NOT_NULL_VIOLATION = "not_null"  # Code 515
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ParsedSqlError:
    error_type: SqlServerErrorType
    native_code: int | None
    driver_message: str
    is_idempotency_hit: bool


def parse_mssql_error(e: DBAPIError, idempotency_markers: tuple[str, ...] = ()) -> ParsedSqlError:
    """
    Parses a pyodbc-based SQLAlchemy exception into a structured data object.
    Safe for any framework, script, or retry loop.
    """
    if not e.orig or not hasattr(e.orig, "args") or len(e.orig.args) < 2:
        return ParsedSqlError(SqlServerErrorType.UNKNOWN, 0, '', False)

    # FIX 1: Extract ONLY the text payload element, not the entire tuple string representation
    driver_message = str(e.orig.args[1])
    sql_state = str(e.orig.args[0])

    # Extract the native trailing token: "(ErrorNumber) (CursorFunction)"
    match = re.search(r"\((\d+)\)\s+\([A-Za-z0-9_]+\)$", driver_message)
    native_code = int(match.group(1)) if match else None

    # Check for transient conditions (Both should be retried!)
    if native_code == 1205:
        return ParsedSqlError(SqlServerErrorType.DEADLOCK, native_code, driver_message, False)

    if native_code == 1222:  # <-- Added lock timeout handling
        return ParsedSqlError(SqlServerErrorType.LOCK_TIMEOUT, native_code, driver_message, False)

    if sql_state != '23000':
        return ParsedSqlError(SqlServerErrorType.UNKNOWN, native_code, driver_message, False)

    # 4. Determine structural constraint classification
    if native_code in (2601, 2627):
        is_idempotent = any(marker in driver_message for marker in idempotency_markers)
        return ParsedSqlError(SqlServerErrorType.DUPLICATE_KEY, native_code, driver_message, is_idempotent)

    elif native_code == 547:
        return ParsedSqlError(SqlServerErrorType.FOREIGN_KEY_VIOLATION, native_code, driver_message, False)

    elif native_code == 515:
        return ParsedSqlError(SqlServerErrorType.NOT_NULL_VIOLATION, native_code, driver_message, False)

    return ParsedSqlError(SqlServerErrorType.UNKNOWN, native_code, driver_message, False)