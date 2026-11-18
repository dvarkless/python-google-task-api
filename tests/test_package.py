"""Tests for the package surface: version, public exports, metadata and import hygiene."""

import ast
import inspect
import sys
import tomllib
import unittest
from pathlib import Path

import gtasks
from gtasks.cli import main, run
from gtasks.errors import ApiError, AuthError, GtasksError, NotFoundError

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gtasks"


class PackageSurfaceTests(unittest.TestCase):
    def test_version(self):
        self.assertEqual(gtasks.__version__, "0.2.0")

    def test_core_public_exports(self):
        expected = [
            "GoogleTasksClient",
            "HttpTransport",
            "TaskListsResource",
            "TasksResource",
            "TaskList",
            "Task",
            "TaskFilter",
            "filter_tasks",
            "SyncEngine",
            "SyncState",
            "SyncReport",
            "SyncChange",
            "Token",
            "TokenStore",
            "Credentials",
            "OAuth2Flow",
            "GtasksError",
            "ApiError",
            "NotFoundError",
            "AuthError",
            "UNSET",
            "parse_rfc3339",
            "to_rfc3339",
            "DEFAULT_CONFIG_DIR",
            "DEFAULT_CLIENT_SECRET_PATH",
            "resolve_client_secret_path",
            "save_client_config",
        ]
        missing = [name for name in expected if not hasattr(gtasks, name)]
        self.assertEqual(missing, [], f"missing public exports: {missing}")

    def test_cli_entry_points(self):
        self.assertTrue(callable(main))
        self.assertTrue(callable(run))
        signature = inspect.signature(main)
        self.assertIn("argv", signature.parameters)
        self.assertIn("client", signature.parameters)


class ErrorHierarchyTests(unittest.TestCase):
    def test_hierarchy(self):
        self.assertTrue(issubclass(GtasksError, Exception))
        self.assertTrue(issubclass(AuthError, GtasksError))
        self.assertTrue(issubclass(ApiError, GtasksError))
        self.assertTrue(issubclass(NotFoundError, ApiError))

    def test_api_error_attributes_and_str(self):
        error = ApiError(400, "Bad request", payload={"error": {"message": "Bad request"}}, body="raw")
        self.assertEqual(error.status, 400)
        self.assertEqual(error.message, "Bad request")
        self.assertEqual(error.payload, {"error": {"message": "Bad request"}})
        self.assertEqual(error.body, "raw")
        self.assertEqual(str(error), "HTTP 400: Bad request")

    def test_api_error_defaults(self):
        error = ApiError(500, "boom")
        self.assertIsNone(error.payload)
        self.assertEqual(error.body, "")


class ImportHygieneTests(unittest.TestCase):
    def test_no_third_party_imports_in_src(self):
        files = sorted(SRC.rglob("*.py"))
        self.assertTrue(files, f"no sources found under {SRC}")
        allowed = set(sys.stdlib_module_names) | {"gtasks"}
        offenders = []
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                roots = []
                if isinstance(node, ast.Import):
                    roots = [alias.name.split(".")[0] for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    roots = [node.module.split(".")[0]]
                for root in roots:
                    if root not in allowed:
                        offenders.append(f"{path.name}:{node.lineno} imports {root}")
        self.assertEqual(offenders, [])

class PackagingMetadataTests(unittest.TestCase):
    def setUp(self):
        pyproject = ROOT / "pyproject.toml"
        self.assertTrue(pyproject.exists(), "pyproject.toml missing")
        self.data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        self.project = self.data["project"]

    def test_project_metadata(self):
        self.assertEqual(self.project["name"], "gtasks")
        self.assertEqual(self.project["version"], "0.2.0")
        self.assertEqual(self.project["requires-python"], ">=3.10")
        self.assertFalse(self.project.get("dependencies"))

    def test_console_script(self):
        self.assertEqual(
            self.project["scripts"]["gtasks"], "gtasks.cli:run"
        )

    def test_setuptools_src_layout(self):
        build_system = self.data["build-system"]
        self.assertTrue(
            any("setuptools" in requirement for requirement in build_system["requires"])
        )
        self.assertEqual(build_system["build-backend"], "setuptools.build_meta")
        tool = self.data.get("tool", {})
        packages = tool.get("setuptools", {}).get("packages", {})
        find = tool.get("setuptools", {}).get("package-dir", {})
        self.assertTrue(
            packages == {"find": {"where": ["src"]}} or find.get("") == "src",
            f"unexpected setuptools src configuration: {tool.get('setuptools')}",
        )


if __name__ == "__main__":
    unittest.main()
