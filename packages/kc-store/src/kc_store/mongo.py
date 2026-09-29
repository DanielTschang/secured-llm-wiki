"""DocStore on one MongoDB database (per-space database, per-space dynamic user)."""

from typing import Any

from pymongo.database import Database
from pymongo.errors import DuplicateKeyError

from kc_store.backends import Doc

__all__ = ["MongoDocs"]


class MongoDocs:
    def __init__(self, db: Database[Any]) -> None:
        self._db = db

    def get(self, collection: str, doc_id: str) -> Doc | None:
        return self._db[collection].find_one({"_id": doc_id})

    def replace_if_revision_below(self, collection: str, doc_id: str, doc: Doc) -> bool:
        try:
            self._db[collection].replace_one(
                {"_id": doc_id, "revision": {"$lt": doc["revision"]}},
                {**doc, "_id": doc_id},
                upsert=True,
            )
        except DuplicateKeyError:
            # The document exists with an equal or newer revision: the upsert's insert
            # collides on _id, which is exactly the fencing rejection.
            return False
        return True

    def update_if_revision_equals(
        self, collection: str, doc_id: str, revision: int, fields: Doc
    ) -> bool:
        res = self._db[collection].update_one(
            {"_id": doc_id, "revision": revision}, {"$set": fields}
        )
        return res.matched_count == 1

    def upsert(self, collection: str, doc_id: str, doc: Doc) -> None:
        self._db[collection].replace_one({"_id": doc_id}, {**doc, "_id": doc_id}, upsert=True)

    def find(self, collection: str, equals: Doc) -> list[Doc]:
        return list(self._db[collection].find(equals).sort("_id", 1))

    def delete(self, collection: str, doc_id: str) -> None:
        self._db[collection].delete_one({"_id": doc_id})
