import math

import pandas as pd
from sqlalchemy import URL, create_engine, types
from sqlalchemy.sql import text

from src.module.helper import logging_timer
from src.module.settings import settings

# oracle connection string for SQL alchemy engine
oracle_connection_url = URL.create(
    drivername='oracle+oracledb',
    password=settings.oracle_password,
    username=settings.oracle_username,
    port=settings.oracle_port,
    host=settings.oracle_hostname,
    query={'service_name': settings.oracle_service},
)


def col_length(str_len):
    """Calculate column lengths. Used for allocating column size"""
    # threshold = 2**(pw-1)
    pw = int(math.log(str_len, 2))
    return int(math.ceil((str_len + 2 ** (pw - 1)) / 2 ** (pw - 1)) * 2 ** (pw - 1))


def sql_col(data_frame):
    """Convert python data types to sql data types"""
    dtypes_dict = {}
    for column, dtype in zip(data_frame.columns, data_frame.dtypes):
        if "object" in str(dtype):
            col_as_str = data_frame[column].astype("string")
            max_len = int(col_as_str.str.len().fillna(0).max())
            str_max_len = col_length(max(max_len, 1))
            dtypes_dict.update({column: types.VARCHAR(length=str_max_len)})
        if "datetime" in str(dtype):
            dtypes_dict.update({column: types.DateTime()})
        if "float" in str(dtype):
            dtypes_dict.update({column: types.FLOAT})
        if "int" in str(dtype):
            dtypes_dict.update({column: types.INT()})
    return dtypes_dict


def sql_open(filepath):
    """Load sql file
    Do not include ; in a query
    Only one query in a file is allowed
    """
    query = open(filepath, encoding='utf-8').read()
    return query


@logging_timer()
def oracle_export(data_frame, table_name, index=False, if_exists='replace'):
    """Export to oracle DB"""
    engine = create_engine(oracle_connection_url)
    output_dtypes_dict = sql_col(data_frame)
    data_frame.to_sql(
        table_name.lower(),
        con=engine,
        if_exists=if_exists,
        index=index,
        dtype=output_dtypes_dict,
    )


@logging_timer()
def oracle_execute(query):
    """Executes query"""
    engine = create_engine(oracle_connection_url)
    with engine.connect() as connection:
        connection.execute(text(query))
        connection.commit()


@logging_timer()
def oracle_import(query):
    """Import from oracle DB"""
    engine = create_engine(oracle_connection_url).raw_connection()
    data_frame = pd.read_sql(query, engine)
    return data_frame


@logging_timer()
def oracle_sysdate(before_today=0):
    """Get current sysdate from Oracle DB"""
    query = f"SELECT TO_CHAR(SYSDATE - {before_today}, 'YYYYMMDD') FROM dual"
    return oracle_import(query).iloc[0, 0]
