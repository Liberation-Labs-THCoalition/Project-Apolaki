"""The memory store's dependencies: every module apolaki/agent imports is declared, and the store refuses to open,
naming the install step, when the entity graph's spaCy or its model is missing (2026-10-20: both store() and search()
raised ModuleNotFoundError on an install from the [agent] extra, and store() had already committed its row)."""
import ast
import importlib.machinery
import importlib.util
import sys
import tomllib
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")      # memory_store imports numpy at the top; it's in the [agent] extra
from apolaki.agent import memory_store as ms

ROOT = Path(__file__).resolve().parents[2]

# module name -> distribution name, for every third-party module apolaki/agent may import
DISTRIBUTION = {
    "numpy": "numpy",
    "pydantic": "pydantic",
    "requests": "requests",
    "sentence_transformers": "sentence-transformers",
    "spacy": "spacy",
    "sqlite_vec": "sqlite-vec",
}


def _declared():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    specs = project["dependencies"] + project["optional-dependencies"]["agent"]
    return {s.split(">")[0].split("=")[0].split("[")[0].strip().lower() for s in specs}


def _third_party_imports():
    found = {}
    for p in sorted((ROOT / "apolaki" / "agent").rglob("*.py")):
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "apolaki":
                    found.setdefault(top, f"{p.relative_to(ROOT)}:{node.lineno}")
    return found


def test_every_module_the_agent_imports_is_declared():
    declared = _declared()
    imports = _third_party_imports()
    assert "spacy" in imports, "the scan no longer sees memory_store's spaCy import"
    for module, where in imports.items():
        assert module in DISTRIBUTION, f"{module} ({where}) has no distribution name here; add it and declare it"
        assert DISTRIBUTION[module] in declared, f"{module} ({where}) is imported but {DISTRIBUTION[module]} isn't declared"


def _fake_spacy(monkeypatch, has_model):
    spacy = types.ModuleType("spacy")
    spacy.__spec__ = importlib.machinery.ModuleSpec("spacy", None)
    util = types.ModuleType("spacy.util")
    util.__spec__ = importlib.machinery.ModuleSpec("spacy.util", None)
    util.is_package = lambda name: has_model and name == ms.NLP_MODEL
    spacy.util = util
    monkeypatch.setitem(sys.modules, "spacy", spacy)
    monkeypatch.setitem(sys.modules, "spacy.util", util)


def _paths(tmp_path):
    return tmp_path / "db" / "apolaki.db", tmp_path / "wiki"


def test_without_spacy_the_store_refuses_before_writing_anything(tmp_path, monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.delitem(sys.modules, "spacy", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "spacy" else real(name, *a))
    db, wiki = _paths(tmp_path)
    with pytest.raises(RuntimeError) as e:
        ms.ApolakiMemoryStore(db_path=db, wiki_path=wiki)
    assert "spaCy is not installed" in str(e.value)
    assert 'pip install "apolaki[agent]"' in str(e.value)
    assert f"python -m spacy download {ms.NLP_MODEL}" in str(e.value)
    assert not db.exists() and not db.parent.exists() and not wiki.exists()


def test_without_the_model_the_store_refuses_before_writing_anything(tmp_path, monkeypatch):
    _fake_spacy(monkeypatch, has_model=False)
    db, wiki = _paths(tmp_path)
    with pytest.raises(RuntimeError) as e:
        ms.ApolakiMemoryStore(db_path=db, wiki_path=wiki)
    assert f"{ms.NLP_MODEL} model is not installed" in str(e.value)
    assert f"python -m spacy download {ms.NLP_MODEL}" in str(e.value)
    assert not db.exists() and not db.parent.exists() and not wiki.exists()


def test_with_spacy_and_the_model_the_store_opens(tmp_path, monkeypatch):
    _fake_spacy(monkeypatch, has_model=True)
    db, wiki = _paths(tmp_path)
    try:
        store = ms.ApolakiMemoryStore(db_path=db, wiki_path=wiki)
    except RuntimeError as e:
        pytest.fail(f"the store refused with spaCy and {ms.NLP_MODEL} both present: {e}")
    try:
        assert db.exists() and wiki.is_dir()
        assert store.conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
    finally:
        store.conn.close()
