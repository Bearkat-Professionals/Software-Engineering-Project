"""Development-only CRUD server (#19): list, get, create, update and delete rows
in every table. The page that uses it is the React app in frontend/crud-react.

NOT part of the product. It skips login, roles, validation, auditing and
conflict checks (SEC-01, POL-01 to POL-04, CM-01), so it:
  * listens on 127.0.0.1 only, so other machines can't reach it
  * uses the TEST database unless told otherwise
  * never returns password hashes (User.to_dict() removes them)
Remove or switch it off before submission (M7).

Start it from the project root, then start the React page and open http://localhost:5173/

    .\\dev crud                                    # test database
    .\\dev crud --database network_device_scanner  # real database
    cd frontend\\crud-react && npm run dev          # the page (npm install once first)
"""
import argparse
import os
from dataclasses import replace
from typing import Any, Callable

from flask import Flask, abort, jsonify, request
from werkzeug.exceptions import HTTPException

from backend.config import DatabaseConfig
from backend.data_access.database_engine import (DatabaseConnectionError, DatabaseEngine,
                                                 IntegrityConstraintError)
from backend.data_access.repository import RecordNotFoundError, Repository
from backend.records import ALL_ENTITIES
from backend.records.entity import Entity

REACT_PAGE = "http://localhost:5173/"
TABLES: dict[str, type[Entity]] = {entity.TABLE: entity for entity in ALL_ENTITIES}

RepositoryFactory = Callable[[type[Entity]], Any]

WHERE_IS_THE_PAGE = (
    "This is the CRUD server's API (the /api routes). The test page is the React app.\n"
    "\n"
    "    cd frontend\\crud-react\n"
    "    npm install        (first time only)\n"
    "    npm run dev\n"
    "\n"
    f"Then open {REACT_PAGE}\n"
)


def create_app(make_repository: RepositoryFactory) -> Flask:
    app = Flask(__name__)
    app.json.sort_keys = False          # keep fields in column order instead of A-Z
    app.config["TRUSTED_HOSTS"] = ["127.0.0.1", "localhost"]   # refuse DNS-rebinding requests
    repositories = {name: make_repository(entity) for name, entity in TABLES.items()}

    # ---- helpers -----------------------------------------------------------

    def repository_for(table: str):
        if table not in repositories:
            abort(404, description=f"unknown table '{table}'")
        return repositories[table]

    def entity_from_body(table: str, pk: int | None = None) -> Entity:
        """Build an entity from the JSON body. PUT replaces the whole row, so
        the body must contain every required column."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, description="the request body must be a JSON object")
        entity_type = TABLES[table]
        if pk is not None:
            data = {**data, entity_type.PK: pk}          # the id in the URL wins
        try:
            return entity_type.from_row(data)
        except (TypeError, ValueError) as exc:           # a required column is missing, or a bad enum value
            abort(400, description=str(exc))

    # ---- routes ------------------------------------------------------------

    @app.get("/")
    def where_is_the_page():
        """The old HTML page lived here; point anyone with an old link to the React page."""
        return WHERE_IS_THE_PAGE, 200, {"Content-Type": "text/plain; charset=utf-8"}

    @app.get("/api/tables")
    def list_tables():
        """Every table with its primary key and columns, so the page can build its forms."""
        return jsonify({name: {"primary_key": entity.PK, "columns": entity.column_names()}
                        for name, entity in TABLES.items()})

    @app.get("/api/<table>")
    def list_rows(table: str):
        repository = repository_for(table)
        filters = request.args.to_dict()                 # ?status=active -> {"status": "active"}
        unknown = set(filters) - set(TABLES[table].column_names())
        if unknown:
            abort(400, description=f"{table} has no column(s): {', '.join(sorted(unknown))}")
        return jsonify([row.to_dict() for row in repository.list(**filters)])

    @app.get("/api/<table>/<int:pk>")
    def get_row(table: str, pk: int):
        row = repository_for(table).get(pk)
        if row is None:
            abort(404, description=f"{table} {pk} does not exist")
        return jsonify(row.to_dict())

    @app.post("/api/<table>")
    def create_row(table: str):
        repository = repository_for(table)
        created = repository.create(entity_from_body(table))
        return jsonify(repository.get(created.pk).to_dict()), 201           # 201 Created

    @app.put("/api/<table>/<int:pk>")
    def update_row(table: str, pk: int):
        repository = repository_for(table)
        repository.update(entity_from_body(table, pk))
        return jsonify(repository.get(pk).to_dict())        # as stored

    @app.delete("/api/<table>/<int:pk>")
    def delete_row(table: str, pk: int):
        repository_for(table).delete(pk)
        return "", 204                                   # 204 No Content

    # ---- errors: always JSON {"error": "..."} so the page can show the message --

    @app.errorhandler(HTTPException)
    def http_error(exc: HTTPException):
        return jsonify(error=exc.description), exc.code

    @app.errorhandler(RecordNotFoundError)
    def not_found(exc: RecordNotFoundError):
        return jsonify(error=str(exc)), 404

    @app.errorhandler(IntegrityConstraintError)
    def broke_a_database_rule(exc: IntegrityConstraintError):
        return jsonify(error=str(exc)), 409              # 409 Conflict: duplicate, or a row still in use

    @app.errorhandler(Exception)
    def unexpected(exc: Exception):
        return jsonify(error=f"{type(exc).__name__}: {exc}"), 500

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Development-only CRUD server (the API behind the React test page).")
    parser.add_argument("--database", help="database to use (default: DB_NAME + '_test')")
    parser.add_argument("--port", type=int, default=5000)
    args = parser.parse_args()

    config = DatabaseConfig.from_env()
    database = args.database or os.getenv("TEST_DB_NAME", f"{config.database}_test")
    try:
        engine = DatabaseEngine(replace(config, database=database))
    except DatabaseConnectionError as exc:
        parser.exit(1, f"{exc}\n")

    app = create_app(lambda entity: Repository(engine, entity))
    print(f"DEVELOPMENT ONLY: CRUD API on http://127.0.0.1:{args.port}/api using database '{database}'")
    print(f"Test page: run 'npm run dev' in frontend/crud-react, then open {REACT_PAGE}")
    app.run(host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
