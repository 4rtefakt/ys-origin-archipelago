"""Static hygiene checks on ``ys_origin/options.py``.

Run directly::

    python -m tests.test_options_hygiene

``options.py`` imports Archipelago's ``Options`` module, so it cannot be imported
offline. These checks parse it as an AST instead, which is enough to catch the
class of mistake that broke the Launcher for every installed game:

  * ``visibility`` must be a ``Visibility`` flag, never a bare int. Archipelago
    tests it with ``visibility_level in option.visibility``
    (``Options.get_option_groups``), which needs an ``IntFlag``. A plain ``0``
    raises ``TypeError: argument of type 'int' is not iterable``, and because
    ``generate_yaml_templates`` loops over every world, that one bad attribute
    aborted template generation for the WHOLE install — "Generate Template
    Options" silently did nothing until this apworld was removed (playtest,
    v1.9.2).
"""

from __future__ import annotations

import ast
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_OPTIONS = _ROOT / "ys_origin" / "options.py"
_TREE = ast.parse(_OPTIONS.read_text("utf-8"), filename=str(_OPTIONS))


def _class_assignments(name: str):
    """Yield (class_name, value_node) for every ``<name> = ...`` in a class body."""
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        yield node.name, stmt.value


def test_visibility_is_a_flag_not_an_int():
    bad = []
    for cls_name, value in _class_assignments("visibility"):
        ok = (
            isinstance(value, ast.Attribute)
            and isinstance(value.value, ast.Name)
            and value.value.id == "Visibility"
        ) or (
            # a composed flag, e.g. Visibility.template | Visibility.spoiler
            isinstance(value, ast.BinOp)
        )
        if not ok:
            bad.append(f"{cls_name}: visibility = {ast.dump(value)[:60]}")
    assert not bad, (
        "visibility must be a Visibility flag (AP does "
        "`visibility_level in option.visibility`, which needs an IntFlag): "
        + "; ".join(bad)
    )


def test_visibility_import_present_when_used():
    used = any(True for _ in _class_assignments("visibility"))
    if not used:
        return
    imported = any(
        isinstance(n, ast.ImportFrom)
        and n.module == "Options"
        and any(a.name == "Visibility" for a in n.names)
        for n in ast.walk(_TREE)
    )
    assert imported, "options.py sets visibility but never imports Visibility"


def test_every_option_class_has_a_docstring():
    """The template writes each option's docstring as its yaml comment."""
    missing = []
    for node in ast.walk(_TREE):
        if isinstance(node, ast.ClassDef) and node.bases:
            if node.name.endswith("Options"):      # the dataclass, not an option
                continue
            if not ast.get_docstring(node):
                missing.append(node.name)
    assert not missing, f"option classes with no docstring: {missing}"


def _run_all() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
