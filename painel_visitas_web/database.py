import sqlite3
from pathlib import Path
import pandas as pd


class DatabaseManager:
    def __init__(self, db_path: str | Path = 'painel_visitas.db'):
        self.db_path = str(db_path)

    def connect(self):
        return sqlite3.connect(self.db_path)

    def save_dataframe(self, df: pd.DataFrame, table_name: str, if_exists: str = 'replace') -> None:
        with self.connect() as conn:
            df.to_sql(table_name, conn, index=False, if_exists=if_exists)

    def read_table(self, table_name: str) -> pd.DataFrame:
        with self.connect() as conn:
            return pd.read_sql_query(f'SELECT * FROM {table_name}', conn)
