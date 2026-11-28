"""The review gate holds at the engine boundary (FACTORY.md product principle).

No workflow or activity may publish, unpublish or write a manifest: the
workflow ends at staged and publishing stays an HTTP action bound to the
reviewed bytes. This guards every module under src/workshop/temporal.
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
    return names


def test_no_temporal_module_reaches_a_publish_or_manifest_function() -> None:
    modules = sorted(TEMPORAL_PACKAGE.glob("*.py"))
    assert {"client.py", "codec.py", "spike.py"} <= {module.name for module in modules}
    for module in modules:
        assert _names_used(module).isdisjoint(FORBIDDEN), module.name
