import datetime
import decimal

from sqlalchemy import (
    DECIMAL,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Unicode,
    text, Table, Column,
)
from sqlalchemy.dialects.mssql import DATETIME2
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class FooBase(DeclarativeBase):
    pass


class FooNomination(FooBase):
    """
    Nomination model copied from the conference-awards project for testing purposes.
    """
    __tablename__ = 'nominations'

    id: Mapped[int] = mapped_column(Integer, Identity(start=1, increment=1), primary_key=True,
                                    comment='Nomination unique identifier.')
    department_code: Mapped[str] = mapped_column(String(4), nullable=False,
                                                 comment='Department code. Refers to Codebook.dbo.departments table, but not a foreign key.')
    term_code: Mapped[str] = mapped_column(String(6), nullable=False, comment='Foreign key to terms table.')
    submitted_at: Mapped[datetime.datetime] = mapped_column(DATETIME2, nullable=False,
                                                            server_default=text('(getutcdate())'),
                                                            comment='Timestamp of initial submission.')
    requested_amount: Mapped[decimal.Decimal | None] = mapped_column(DECIMAL(10, 2),
                                                                     comment='Amount of award being requested.')


t_foo_view = Table(
    'department_enrollment_preview', FooBase.metadata,
    Column('term_code', String(6), nullable=False),
    Column('department_code', String(4), nullable=False),
    Column('eligible_enrollment', Integer),
    Column('eligible_program_count', Integer),
    Column('total_program_count', Integer)
)
