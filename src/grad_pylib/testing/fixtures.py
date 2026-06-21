import os
import re
from typing import Any, Callable, Generator
from dataclasses import dataclass
from sqlalchemy import create_engine, text, Engine
from sqlalchemy.orm import sessionmaker, Session
import pytest
from _pytest.config import Config
from xdist.workermanage import WorkerController

from grad_pylib.tools.rebuild_models import DEFAULT_SQL_SERVER_IMAGE


@dataclass(frozen=True, slots=True)
class SqlServerFixtureConfig:
    migration_runner: Callable[[Engine], None]
    tables_to_clean: tuple[str, ...]
    image: str = DEFAULT_SQL_SERVER_IMAGE
    password: str = "Test@12345!"
    database_prefix: str = "AppTest"


class SharedSqlServerState:
    def __init__(self) -> None:
        self.container: Any | None = None
        self.admin_url: str | None = None


def is_xdist_controller(config: Config) -> bool:
    num_processes = getattr(config.option, "numprocesses", None)
    return not hasattr(config, "workerinput") and bool(num_processes)


def _build_pyodbc_url(base_pymssql_url: str) -> str:
    """Helper to convert testcontainers default pymssql string into a valid pyodbc URL."""
    pyodbc_url = base_pymssql_url.replace("mssql+pymssql://", "mssql+pyodbc://")
    return (
        f"{pyodbc_url}"
        "?driver=ODBC+Driver+18+for+SQL+Server"
        "&TrustServerCertificate=yes"
    )


def start_controller_container(state: SharedSqlServerState, fixture_config: SqlServerFixtureConfig) -> str:
    if state.container is not None and state.admin_url is not None:
        return state.admin_url

    from testcontainers.mssql import SqlServerContainer

    # Keep default dialect here so testcontainers can safely check health natively via pymssql internally
    container = SqlServerContainer(
        image=fixture_config.image,
        password=fixture_config.password,
        dbname="master",
    )

    # Start the container FIRST before requesting network/port strings
    container.start()

    # Generate the safe pyodbc production-ready connection string
    connection_url = _build_pyodbc_url(container.get_connection_url())

    state.container = container
    state.admin_url = connection_url
    return state.admin_url


def stop_controller_container(state: SharedSqlServerState) -> None:
    if state.container is not None:
        state.container.stop()
    state.container = None
    state.admin_url = None


def configure_worker_node(
        node: WorkerController,
        *,
        state: SharedSqlServerState,
        fixture_config: SqlServerFixtureConfig,
) -> None:
    admin_url = start_controller_container(state, fixture_config)
    worker_id = node.gateway.id
    node.workerinput["shared_mssql_admin_url"] = admin_url
    node.workerinput["shared_mssql_db_name"] = f"{fixture_config.database_prefix}_{worker_id}"


def create_mssql_engine(request: pytest.FixtureRequest, fixture_config: SqlServerFixtureConfig) -> Generator[Engine, None, None]:
    config = request.config
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "master")

    # Mode A: Running via xdist worker nodes
    if hasattr(config, "workerinput"):
        admin_url = config.workerinput["shared_mssql_admin_url"]
        database_name = config.workerinput["shared_mssql_db_name"]

        database_url = create_database(admin_url, database_name)
        engine = create_engine(database_url, future=True, pool_pre_ping=True)
        fixture_config.migration_runner(engine)
        try:
            yield engine
        finally:
            engine.dispose()
        return

    # Mode B: Running sequentially without xdist (Master mode)
    from testcontainers.mssql import SqlServerContainer

    with SqlServerContainer(
            image=fixture_config.image,
            password=fixture_config.password,
            dbname="master",
    ) as container:
        # Enforce pyodbc connection engine mapping here as well
        admin_url = _build_pyodbc_url(container.get_connection_url())
        database_name = f"{fixture_config.database_prefix}_{worker_id}"
        database_url = create_database(admin_url, database_name)

        engine = create_engine(database_url, future=True, pool_pre_ping=True)
        fixture_config.migration_runner(engine)
        try:
            yield engine
        finally:
            engine.dispose()


def create_db_session_fixture(mssql_engine: Engine, fixture_config: SqlServerFixtureConfig) -> Generator[Session, None, None]:
    SessionLocal = sessionmaker(bind=mssql_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    session = SessionLocal()

    try:
        yield session
    finally:
        # Close the connection and abort outstanding uncommitted changes first
        session.rollback()
        session.close()

        # Explicit transaction context block to cleanly scrub tables after the test runs
        with mssql_engine.begin() as conn:
            for table in fixture_config.tables_to_clean:
                conn.execute(text(f"DELETE FROM [{table}]"))


def create_database(admin_url: str, db_name: str) -> str:
    """Helper to provision a dedicated child database on the shared instance with RCSI enabled."""
    master_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with master_engine.connect() as conn:
        # Drop if it exists from a dead previous session run, then recreate clean
        conn.execute(text(f"IF DB_ID('{db_name}') IS NOT NULL DROP DATABASE [{db_name}]"))
        conn.execute(text(f"CREATE DATABASE [{db_name}]"))
        # Lock in RCSI parity with production immediately on fork
        conn.execute(text(f"ALTER DATABASE [{db_name}] SET READ_COMMITTED_SNAPSHOT ON"))
    master_engine.dispose()

    # Route connection string directly to the new database catalog name
    # Using regex to swap out the /master path name for the test database fork name
    return re.sub(r"/master(\?)", f"/{db_name}\\1", admin_url)
