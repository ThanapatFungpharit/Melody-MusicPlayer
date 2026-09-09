"""Check runtime imports, layer direction, presentation isolation, and cycles."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src"


def imports(path: Path, module: str) -> set[str]:
    package = module if path.name == "__init__.py" else module.rpartition(".")[0]
    result: set[str] = set()

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.If) and (
            isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
            or isinstance(node.test, ast.Attribute)
            and node.test.attr == "TYPE_CHECKING"
        ):
            return
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            target = "." * node.level + (node.module or "")
            target = (
                importlib.util.resolve_name(target, package) if node.level else target
            )
            result.add(target)
            result.update(f"{target}.{alias.name}" for alias in node.names)
        elif (  # noqa: SIM102 - separate call shape from supported import functions
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
            ):
                result.add(node.args[0].value)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(path.read_text("utf-8")))
    return result


def violations(root: Path = ROOT) -> list[str]:
    paths = {
        ".".join(path.relative_to(root).with_suffix("").parts).removesuffix(
            ".__init__"
        ): path
        for path in (root / "musicplayer").rglob("*.py")
    }
    graph: dict[str, set[str]] = {name: set() for name in paths}
    problems: set[str] = set()
    for module, path in paths.items():
        for target in imports(path, module):
            if module.startswith(
                ("musicplayer.core.", "musicplayer.application.")
            ) and target.split(".")[0] in {
                "flet",
                "flet_audio",
                "flet_background_audio",
            }:
                problems.add(f"{module} imports UI framework {target}")
            if target.startswith("musicplayer."):
                if module.startswith("musicplayer.core.") and not target.startswith(
                    "musicplayer.core."
                ):
                    problems.add(
                        f"{module} reverses core dependency direction: {target}"
                    )
                if module.startswith("musicplayer.application.") and (
                    target == "musicplayer.app"
                    or target.startswith(("musicplayer.ui", "musicplayer.app."))
                ):
                    problems.add(f"{module} imports presentation/composition: {target}")
                if module.startswith("musicplayer.ui.") and target.startswith(
                    "musicplayer.core."
                ):
                    problems.add(f"{module} bypasses application contracts: {target}")
                for presentation, opposite in (
                    ("mobile", "desktop"),
                    ("desktop", "mobile"),
                ):
                    if module.startswith(
                        f"musicplayer.ui.{presentation}."
                    ) and target.startswith(f"musicplayer.ui.{opposite}."):
                        problems.add(
                            f"{module} imports opposite presentation: {target}"
                        )
                while target and target not in paths:
                    target = target.rpartition(".")[0]
                if target and target != module:
                    graph[module].add(target)
    visiting: list[str] = []
    visited: set[str] = set()

    def walk(module: str) -> None:
        if module in visiting:
            problems.add(
                "Import cycle: "
                + " -> ".join([*visiting[visiting.index(module) :], module])
            )
            return
        if module in visited:
            return
        visiting.append(module)
        for target in sorted(graph[module]):
            walk(target)
        visiting.pop()
        visited.add(module)

    for module in sorted(graph):
        walk(module)
    return sorted(problems)


if __name__ == "__main__":
    errors = violations()
    if errors:
        raise SystemExit("\n".join(errors))
    print("Architecture boundaries and import cycles checked.")
