"""Import one legacy studio database and its JSON traces into Lyra PostgreSQL."""

import argparse
import json
import os
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from agent_studio import PostgresTraceAdapter
from agent_studio.postgres_tracing import traces
from dotenv import load_dotenv
from prompt_studio import SQLAlchemyStorage
from prompt_studio.sqlalchemy_storage import groups, members, versions
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert


def copy_row(conn, table, row):
    """Accept an exact replay, but never silently replace an immutable record."""
    result = conn.execute(insert(table).values(**row).on_conflict_do_nothing().returning(*table.primary_key))
    if result.first() is not None:
        return
    pk = {col.name: row[col.name] for col in table.primary_key}
    saved = conn.execute(select(table).filter_by(**pk)).mappings().one()
    if dict(saved) != row:
        raise ValueError(f"Conflicting existing {table.name} record: {pk}")


def import_legacy(storage, database, trace_dir):
    if not database.is_file() or not trace_dir.is_dir():
        raise ValueError("The source SQLite file and trace directory must both exist")
    storage.create_schema()
    PostgresTraceAdapter(storage.engine).create_schema()
    counts = {}
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        with storage.engine.begin() as dest:
            dest.execute(text("LOCK TABLE prompt_studio.prompt_versions, prompt_studio.prompt_groups, "
                              "prompt_studio.prompt_group_members, agent_studio.traces IN SHARE ROW EXCLUSIVE MODE"))
            for table in (versions, groups, members):
                counts[table.name] = 0
                for record in source.execute(f"SELECT * FROM {table.name}"):
                    row = dict(record)
                    if "created_at" in row:
                        # The SQLite adapter stored UTC timestamps without offsets.
                        stamp = datetime.fromisoformat(row["created_at"])
                        row["created_at"] = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
                    if table is groups and dest.execute(select(groups.c.name).filter_by(
                            library=row["library"], name=row["name"])).first():
                        key = (row["library"], row["name"])
                        expected = source.execute("SELECT prompt_name, version FROM prompt_group_members "
                                                  "WHERE library=? AND group_name=?", key)
                        actual = dest.execute(select(members.c.prompt_name, members.c.version)
                                              .filter_by(library=key[0], group_name=key[1]))
                        if set(map(tuple, expected)) != set(actual):
                            raise ValueError(f"Conflicting immutable group membership: {key}")
                    copy_row(dest, table, row)
                    counts[table.name] += 1
            counts["traces"] = 0
            for path in sorted(trace_dir.glob("*.json")):
                with path.open(encoding="utf-8") as stream:
                    copy_row(dest, traces, PostgresTraceAdapter.row(json.load(stream)))
                counts["traces"] += 1
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("traces", type=Path)
    args = parser.parse_args()
    load_dotenv(Path(__file__).with_name(".env"))
    target = SQLAlchemyStorage.from_postgres_url(os.environ["DATABASE_URL"])
    try:
        print(import_legacy(target, args.database, args.traces))
    finally:
        target.engine.dispose()
