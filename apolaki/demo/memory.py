"""An empty, read-only memory for the hosted demo: nothing a visitor writes is kept, and no embedding model is needed.

It has the four methods the drafting path calls on ApolakiMemoryStore. Reads find nothing; `store` refuses, so a code path
that would remember a visitor's text fails loudly instead of keeping it."""
from __future__ import annotations


class EmptyMemory:
    db_path = None

    def list_by_type(self, types=None):
        return []

    def search(self, query, n_results=10, memory_type=None, use_graph=True):
        return []

    def store(self, *args, **kwargs):
        raise RuntimeError("the demo keeps nothing a visitor writes")

    def close(self):
        pass
