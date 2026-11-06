import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

from prompt_studio import SQLAlchemyStorage
from prompt_studio.sqlalchemy_storage import versions
from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError
from sqlalchemy.schema import CreateSchema, DropSchema

from schema_migrations import apply_migrations, schema_transaction


@unittest.skipUnless(os.getenv("LYRA_TEST_DATABASE_URL"), "Set LYRA_TEST_DATABASE_URL for PostgreSQL checks")
class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        engine = SQLAlchemyStorage.from_postgres_url(os.environ["LYRA_TEST_DATABASE_URL"]).engine
        self.addCleanup(engine.dispose)
        prefix = "lyra_migration_test_" + uuid4().hex
        self.schema, self.prompt_schema = prefix, prefix + "_prompts"
        with engine.begin() as connection:
            connection.execute(CreateSchema(self.schema))
            connection.execute(CreateSchema(self.prompt_schema))
        self.addCleanup(self.drop_schemas, engine)
        self.engine = engine.execution_options(schema_translate_map={
            "agent_studio": self.schema, "prompt_studio": self.prompt_schema,
        })
        versions.create(self.engine)

    def drop_schemas(self, engine):
        with engine.begin() as connection:
            connection.execute(DropSchema(self.schema, cascade=True))
            connection.execute(DropSchema(self.prompt_schema, cascade=True))

    def migrate(self):
        with schema_transaction(self.engine) as connection:
            apply_migrations(connection)

    def test_concurrent_fresh_migrations_and_newer_schema_guard(self):
        ready = Barrier(2)

        def worker():
            ready.wait(timeout=5)
            self.migrate()

        with ThreadPoolExecutor(max_workers=2) as pool:
            workers = [pool.submit(worker) for _ in range(2)]
            for future in workers:
                future.result(timeout=30)
        with self.engine.begin() as conn:
            ledger = f"{self.schema}.lyra_schema_migrations"
            applied = conn.execute(text(f"SELECT revision FROM {ledger} ORDER BY revision")).scalars().all()
            self.assertEqual(applied, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11])
            keys = inspect(conn).get_foreign_keys("agent_definitions", schema=self.schema)
            self.assertEqual([key["referred_schema"] for key in keys if key["referred_table"] == "prompt_versions"],
                             [self.prompt_schema, self.prompt_schema])
            conn.execute(text(f"INSERT INTO {ledger} (revision) VALUES (9999)"))
        with self.assertRaisesRegex(RuntimeError, "incompatible"):
            self.migrate()

    def test_failed_upgrade_rolls_back_and_can_be_retried(self):
        self.migrate()
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP TABLE {self.schema}.lyra_schema_migrations"))
            conn.execute(text(f"ALTER TABLE {self.schema}.agent_definitions DROP COLUMN output_schema"))
            conn.execute(text(f"""INSERT INTO {self.schema}.model_groups (id, library, name, models, revision)
                                  VALUES (:id, 'test', 'legacy', 'null'::jsonb, 1)"""), {"id": uuid4()})
        with self.assertRaises(DataError):
            self.migrate()
        with self.engine.begin() as conn:
            inspector = inspect(conn)
            self.assertFalse(inspector.has_table("lyra_schema_migrations", schema=self.schema))
            columns = inspector.get_columns("agent_definitions", schema=self.schema)
            self.assertNotIn("output_schema", {column["name"] for column in columns})
            conn.execute(text(f"""UPDATE {self.schema}.model_groups SET models = '["legacy-model"]'::jsonb"""))
        self.migrate()
        with self.engine.begin() as conn:
            names = conn.execute(text(f"SELECT name FROM {self.schema}.models")).scalars().all()
        self.assertEqual(names, ["legacy-model"])
