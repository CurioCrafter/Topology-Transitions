from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SidebarPriorityTests(unittest.TestCase):
    def test_mesh_integrity_is_first_sidebar_tool(self) -> None:
        source = (ROOT / "topology_transitions" / "ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
        }

        def operator_calls(function):
            return sorted(
                (
                    node
                    for node in ast.walk(function)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "operator"
                ),
                key=lambda node: (node.lineno, node.col_offset),
            )

        panel = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "QT_PT_sidebar"
        )
        draw = next(
            node
            for node in panel.body
            if isinstance(node, ast.FunctionDef) and node.name == "draw"
        )
        called = [
            node.func.id
            for node in ast.walk(draw)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        ]
        self.assertEqual(called[0], "_draw_start_here")
        first = operator_calls(functions["_draw_start_here"])[0]
        self.assertEqual(first.args[0].value, "mesh.quad_transition_check_manifold")
        button_text = next(
            keyword.value.value for keyword in first.keywords if keyword.arg == "text"
        )
        self.assertEqual(button_text, "Find Holes / Check Manifold")

        integrity = operator_calls(functions["_draw_mesh_integrity"])
        self.assertEqual(
            integrity[0].args[0].value, "mesh.quad_transition_check_manifold"
        )
        auto_repair = next(
            node
            for node in integrity
            if node.args[0].value == "mesh.quad_transition_solve_all_nonmanifold"
        )
        self.assertLess(integrity[0].lineno, auto_repair.lineno)
        order = [
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.startswith("QT_PT_")
        ]
        self.assertEqual(order[:2], ["QT_PT_sidebar", "QT_PT_inspect"])

    def test_readme_leads_with_manifold_workflow(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("## Start Here: Find Holes and Check Manifold Issues")
        install = readme.index("## Install")
        repair = readme.index("## Automatic Non-Manifold Repair")
        self.assertLess(start, install)
        self.assertLess(install, repair)


if __name__ == "__main__":
    unittest.main()
