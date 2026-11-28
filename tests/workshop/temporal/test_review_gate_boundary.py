"""The review gate holds at the engine boundary (FACTORY.md product principle).

No workflow or activity may publish, unpublish or write a manifest: the
workflow ends at staged and publishing stays an HTTP action bound to the
reviewed bytes. This guards every module under src/workshop/temporal,
subpackages included, and catches a writer named through an import alias, an
attribute or a string (getattr). A call through a helper defined elsewhere is
not caught here; group 2 checks the worker's registered activities (task 2.8).
"""

import ast
from pathlib import Path

TEMPORAL_PACKAGE = Path(__file__).resolve().parents[3] / "src" / "workshop" / "temporal"

FORBIDDEN = {
    "publish_story",
    "unpublish_story",
    "delete_staged_story",
    "_publish_manifest",
    "_write_manifest",
    "_upsert_story",
    "restore_manifest",
    "write_snapshot",
    "put_snapshot",
}


def _names_used(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    return names


def _modules(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _violations(root: Path) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for module in _modules(root):
        hits = _names_used(module) & FORBIDDEN
        if hits:
            found[str(module.relative_to(root))] = hits
    return found


def test_no_temporal_module_reaches_a_publish_or_manifest_function() -> None:
    names = {module.name for module in _modules(TEMPORAL_PACKAGE)}
    assert {"client.py", "codec.py", "spike.py"} <= names
    assert _violations(TEMPORAL_PACKAGE) == {}


def test_the_guard_sees_a_writer_in_a_subpackage(tmp_path: Path) -> None:
    (tmp_path / "activities").mkdir()
    (tmp_path / "activities" / "__init__.py").write_text("")
    (tmp_path / "activities" / "stage.py").write_text(
        "from src.pipeline.publish import publish_story as stage\n"
    )
    assert _violations(tmp_path) == {"activities/stage.py": {"publish_story"}}


def test_the_guard_sees_a_writer_reached_by_an_indirect_name(tmp_path: Path) -> None:
    (tmp_path / "steps.py").write_text(
        "import importlib\n"
        "publish = importlib.import_module('src.pipeline.publish')\n"
        "writer = getattr(publish, 'unpublish_story')\n"
    )
    assert _violations(tmp_path) == {"steps.py": {"unpublish_story"}}


def test_the_guard_sees_a_writer_reached_through_a_module_alias(tmp_path: Path) -> None:
    (tmp_path / "steps.py").write_text(
        "from src.pipeline import publish as p\n\nresult = p._write_manifest\n"
    )
    assert _violations(tmp_path) == {"steps.py": {"_write_manifest"}}
