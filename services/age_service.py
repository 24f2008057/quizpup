import logging

from sqlalchemy import MetaData, inspect, text

from extensions import db

logger = logging.getLogger("quizpup")


def add_missing_columns():
    engine = db.engine
    preparer = engine.dialect.identifier_preparer
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    added = []
    for table in db.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present or column.primary_key:
                continue
            column_type = column.type.compile(dialect=engine.dialect)
            statement = "ALTER TABLE {} ADD COLUMN {} {}".format(
                preparer.quote(table.name), preparer.quote(column.name), column_type
            )
            with engine.begin() as connection:
                connection.execute(text(statement))
            added.append("{}.{}".format(table.name, column.name))
    if added:
        logger.info("Added missing columns: %s", ", ".join(added))
    return added


def _sqlite_phone_is_required(connection):
    rows = connection.execute(text('PRAGMA table_info("user")')).fetchall()
    for row in rows:
        if row[1] == "phone":
            return bool(row[3])
    return False


def _rebuild_sqlite_user_table():
    from models import User

    engine = db.engine
    temp_name = "user__rebuild"
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("PRAGMA foreign_keys=OFF"))
        try:
            connection.execute(text('DROP TABLE IF EXISTS "{}"'.format(temp_name)))
            temp_table = User.__table__.to_metadata(MetaData(), name=temp_name)
            temp_table.create(connection)
            old_columns = {
                row[1] for row in connection.execute(text('PRAGMA table_info("user")')).fetchall()
            }
            shared = [col.name for col in User.__table__.columns if col.name in old_columns]
            column_list = ", ".join('"{}"'.format(name) for name in shared)
            connection.execute(text(
                'INSERT INTO "{}" ({}) SELECT {} FROM "user"'.format(temp_name, column_list, column_list)
            ))
            connection.execute(text('DROP TABLE "user"'))
            connection.execute(text('ALTER TABLE "{}" RENAME TO "user"'.format(temp_name)))
        finally:
            connection.execute(text("PRAGMA foreign_keys=ON"))


def relax_user_phone():
    engine = db.engine
    dialect = engine.dialect.name
    if dialect == "postgresql":
        preparer = engine.dialect.identifier_preparer
        statement = "ALTER TABLE {} ALTER COLUMN {} DROP NOT NULL".format(
            preparer.quote("user"), preparer.quote("phone")
        )
        with engine.begin() as connection:
            connection.execute(text(statement))
        return True
    if dialect == "sqlite":
        with engine.connect() as connection:
            required = _sqlite_phone_is_required(connection)
        if required:
            _rebuild_sqlite_user_table()
            logger.info("Rebuilt the user table so phone is optional")
            return True
    return False