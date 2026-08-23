from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SidebarPriorityTests(unittest.TestCase):
    def test_mesh_integrity_is_first_sidebar_tool(self) -> None:
        source = (ROOT / "topology_transitions" / "ui.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)
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
        integrity_call = next(
            node
            for node in ast.walk(draw)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_draw_mesh_integrity"
        )
        first_direct_box = min(
            node.lineno
            for node in ast.walk(draw)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "layout"
            and node.func.attr == "box"
        )
        self.assertLess(integrity_call.lineno, first_direct_box)

        helper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_draw_mesh_integrity"
        )
        operators = sorted(
            (
                node
                for node in ast.walk(helper)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "operator"
            ),
            key=lambda node: (node.lineno, node.col_offset),
        )
        first_operator = operators[0]
        self.assertEqual(
            first_operator.args[0].value,
            "mesh.quad_transition_check_manifold",
        )
        button_text = next(
            keyword.value.value
            for keyword in first_operator.keywords
            if keyword.arg == "text"
        )
        self.assertEqual(button_text, "Find Holes / Check Manifold")
        auto_repair = next(
            node
            for node in operators
            if node.args[0].value
            == "mesh.quad_transition_solve_all_nonmanifold"
        )
        self.assertLess(first_operator.lineno, auto_repair.lineno)

    def test_readme_leads_with_manifold_workflow(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        start = readme.index("## Start Here: Find Holes and Check Manifold Issues")
        install = readme.index("## Install")
        repair = readme.index("## Automatic Non-Manifold Repair")
        self.assertLess(start, install)
        self.assertLess(install, repair)


if __name__ == "__main__":
    unittest.main()
