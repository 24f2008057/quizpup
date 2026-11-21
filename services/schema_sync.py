import logging

from sqlalchemy import inspect, text

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


def relax_user_phone():
    engine = db.engine
    if engine.dialect.name != "postgresql":
        return False
    preparer = engine.dialect.identifier_preparer
    statement = "ALTER TABLE {} ALTER COLUMN {} DROP NOT NULL".format(
        preparer.quote("user"), preparer.quote("phone")
    )
    with engine.begin() as connection:
        connection.execute(text(statement))
    return True