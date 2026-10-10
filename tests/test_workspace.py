"""Project registry, global settings, in-place use of 0.1 homes and command-line resolution."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from patchrondo.cli import main
from patchrondo.core import config, create_task, initialize
from patchrondo.storage import read_json, save_json
from patchrondo.workspace import DEFAULT_SETTINGS, NotFound, Workspace

from support import make_repo


def tree(root: Path) -> dict:
    """Content hash of every file under a directory (worktrees and Git internals excluded)."""
    return {str(file.relative_to(root)): hashlib.sha256(file.read_bytes()).hexdigest()
            for file in sorted(root.rglob("*")) if file.is_file() and "worktrees" not in file.parts}


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.ws = Workspace(self.root / "home")

    def cli(self, *args):
        with patch("sys.stdout", new_callable=io.StringIO) as out, patch("sys.stderr", new_callable=io.StringIO) as err:
            code = main(["--home", str(self.ws.root), *args])
        return code, out.getvalue(), err.getvalue()


class RegistryTests(Case):
    def test_reading_an_empty_home_writes_nothing(self):
        self.assertEqual(self.ws.projects(), [])
        self.assertEqual(self.ws.settings(), DEFAULT_SETTINGS)
        self.assertFalse(self.ws.root.exists())

    def test_projects_get_stable_ids_and_separate_state_directories(self):
        alpha = self.ws.add(make_repo(self.root / "alpha"))
        beta = self.ws.add(make_repo(self.root / "beta"), "Second")
        self.assertRegex(alpha.id, r"^P-[a-f0-9]{8}$")
        self.assertNotEqual(alpha.id, beta.id)
        self.assertEqual((alpha.name, beta.name), ("alpha", "Second"))
        self.assertEqual(alpha.home, self.ws.root / "projects" / alpha.id)
        self.assertEqual([p.id for p in Workspace(self.ws.root).projects()], [alpha.id, beta.id])
        saved = read_json(self.ws.root / "projects.json")["projects"][0]
        self.assertEqual(saved["home"], f"projects/{alpha.id}")  # relative: the home can be moved as a whole
        self.assertNotIn("repository", saved)  # the project's own config.json stays the only source

    def test_a_new_project_starts_with_tests_disabled_and_untrusted(self):
        project = self.ws.add(make_repo(self.root / "alpha"))
        self.assertEqual(config(project.home)["tests"], {"enabled": False, "trust_acknowledged": False, "commands": []})

    def test_projects_keep_configuration_and_tasks_apart(self):
        alpha = self.ws.add(make_repo(self.root / "alpha"))
        beta = self.ws.add(make_repo(self.root / "beta"))
        cfg = read_json(alpha.home / "config.json")
        cfg["workflow"]["max_iterations"] = 2
        save_json(alpha.home / "config.json", cfg)
        task_id, worktree = create_task(alpha.home, title="A", description="a", acceptance=[], developer="claude", reviewer="codex")
        self.assertEqual(config(beta.home)["workflow"]["max_iterations"], 6)
        self.assertTrue(worktree.is_relative_to(alpha.home))
        self.assertFalse((beta.home / "tasks" / task_id).exists())
        self.assertEqual(self.ws.find_task(task_id).id, alpha.id)
        self.assertIsNone(self.ws.find_task("T-000000000000"))

    def test_invalid_repositories_are_refused_and_leave_nothing_behind(self):
        repo = make_repo(self.root / "alpha")
        self.ws.add(repo)
        (repo / "sub").mkdir()
        (self.root / "plain").mkdir()
        for path, message in ((repo, "already registered"), (repo / "sub", "repository root"),
                              (self.root / "plain", "Git"), (self.root / "missing", "not found")):
            with self.subTest(path=path.name), self.assertRaisesRegex((ValueError, RuntimeError), message):
                self.ws.add(path)
        self.assertEqual(len(self.ws.projects()), 1)
        self.assertEqual(len(list((self.ws.root / "projects").iterdir())), 1)

    def test_state_directory_inside_the_repository_is_refused(self):
        repo = make_repo(self.root / "alpha")
        inside = Workspace(repo / ".state")
        with self.assertRaisesRegex(ValueError, "inside that repository"):
            inside.add(repo)
        self.assertFalse((repo / ".state").exists())

    def test_a_failed_registration_is_rolled_back(self):
        repo = make_repo(self.root / "alpha")
        real = save_json

        def failing(path, obj):
            if path.name == "projects.json":
                raise OSError("disk full")
            real(path, obj)

        with patch("patchrondo.workspace.save_json", side_effect=failing), self.assertRaises(OSError):
            self.ws.add(repo)
        self.assertEqual(self.ws.projects(), [])
        self.assertFalse(any((self.ws.root / "projects").glob("P-*")))
        self.assertEqual(self.ws.add(repo).name, "alpha")  # and it can be added once the cause is gone

    def test_rename_changes_only_the_display_name(self):
        project = self.ws.add(make_repo(self.root / "alpha"))
        renamed = self.ws.rename(project.id, "  My   API ")
        self.assertEqual((renamed.name, renamed.id, renamed.home), ("My API", project.id, project.home))
        for bad in ("", "   ", "x" * 81, 7):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                self.ws.rename(project.id, bad)
        with self.assertRaises(NotFound):
            self.ws.rename("P-00000000", "x")

    def test_remove_forgets_the_project_and_deletes_nothing(self):
        repo = make_repo(self.root / "alpha")
        project = self.ws.add(repo)
        task_id, worktree = create_task(project.home, title="A", description="a", acceptance=[], developer="claude", reviewer="codex")
        self.ws.update_settings({"selected_project": project.id})
        before = tree(project.home)
        self.ws.remove(project.id)
        self.assertEqual(self.ws.projects(), [])
        self.assertIsNone(self.ws.settings()["selected_project"])
        self.assertEqual(tree(project.home), before)
        self.assertTrue(worktree.is_dir() and (repo / "README.md").is_file())
        # The state directory can be registered again with its task.
        again = self.ws.import_home(project.home)
        self.assertTrue((again.home / "tasks" / task_id / "state.json").is_file())

    def test_remove_is_refused_while_a_task_holds_its_lock(self):
        project = self.ws.add(make_repo(self.root / "alpha"))
        task_id, _ = create_task(project.home, title="A", description="a", acceptance=[], developer="claude", reviewer="codex")
        (project.home / "tasks" / task_id / ".run.lock").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "run lock"):
            self.ws.remove(project.id)
        self.assertEqual(len(self.ws.projects()), 1)

    def test_unreadable_registry_is_reported_not_replaced(self):
        self.ws.root.mkdir(parents=True)
        (self.ws.root / "projects.json").write_text('{"version": 9}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid project registry"):
            self.ws.projects()
        with self.assertRaises(ValueError):
            self.ws.add(make_repo(self.root / "alpha"))
        self.assertEqual((self.ws.root / "projects.json").read_text(encoding="utf-8"), '{"version": 9}')


class LegacyTests(Case):
    """A home written by PatchRondo 0.1 is one project, used where it is."""

    def legacy(self, home: Path, name: str = "old"):
        repo = make_repo(self.root / name)
        initialize(home, repo)
        cfg = read_json(home / "config.json")
        del cfg["rag"], cfg["recovery"]  # sections that did not exist in the first configurations
        save_json(home / "config.json", cfg)
        task_id, _ = create_task(home, title="Legacy", description="d", acceptance=["x"], developer="codex", reviewer="claude")
        state = read_json(home / "tasks" / task_id / "state.json")
        del state["recovery"]
        save_json(home / "tasks" / task_id / "state.json", state)
        return repo, task_id

    def test_root_configuration_is_registered_in_place_without_changing_its_files(self):
        repo, task_id = self.legacy(self.ws.root)
        before = tree(self.ws.root)
        projects = self.ws.projects()
        self.assertEqual(len(projects), 1)
        project = projects[0]
        self.assertEqual((project.home, project.origin, project.name, project.repository), (self.ws.root, "legacy", "old", str(repo)))
        after = tree(self.ws.root)
        self.assertEqual(set(after) - set(before), {"projects.json"})
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertEqual(self.ws.projects()[0].id, project.id)  # stable across calls
        self.assertEqual(config(project.home)["rag"]["enabled"], False)  # missing section still means disabled
        self.assertEqual(self.ws.find_task(task_id).id, project.id)

    def test_removed_root_project_is_not_registered_again_by_itself(self):
        self.legacy(self.ws.root)
        self.ws.remove(self.ws.projects()[0].id)
        self.assertEqual(self.ws.projects(), [])
        self.assertTrue((self.ws.root / "config.json").is_file())
        self.assertEqual(self.ws.import_home(self.ws.root).origin, "legacy")  # only on request

    def test_import_registers_another_home_by_reference(self):
        other = self.root / "other-home"
        repo, task_id = self.legacy(other)
        before = tree(other)
        project = self.ws.import_home(other, "Imported")
        self.assertEqual((project.home, project.origin, project.name), (other, "imported", "Imported"))
        self.assertEqual(read_json(self.ws.root / "projects.json")["projects"][0]["home"], str(other))
        self.assertEqual(tree(other), before)
        with self.assertRaisesRegex(ValueError, "already registered"):
            self.ws.import_home(other)
        with self.assertRaisesRegex(ValueError, "config.json is missing"):
            self.ws.import_home(self.root)
        (self.root / "broken").mkdir()
        (self.root / "broken" / "config.json").write_text('{"version": 1, "repository": "relative"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.ws.import_home(self.root / "broken")
        self.assertEqual(len(self.ws.projects()), 1)

    def test_cli_on_a_legacy_home_behaves_as_before_and_writes_no_registry(self):
        repo, task_id = self.legacy(self.ws.root)
        code, out, _ = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn(f"{task_id} · ready    · codex → claude · Legacy", out)
        code, out, _ = self.cli("status", task_id)
        self.assertEqual((code, json.loads(out)["title"]), (0, "Legacy"))
        code, out, _ = self.cli("new", "--title", "T2", "--description", "d")
        self.assertEqual(code, 0, out)
        self.assertIn(f'Run: patchrondo --home "{self.ws.root}" run T-', out)
        self.assertFalse((self.ws.root / "projects.json").exists())
        code, _, err = self.cli("init", "--repo", str(repo))
        self.assertEqual(code, 1)
        self.assertIn("Configuration already exists", err)

    def test_init_on_a_new_home_keeps_the_0_1_layout(self):
        repo = make_repo(self.root / "alpha")
        code, out, _ = self.cli("init", "--repo", str(repo))
        self.assertEqual(code, 0)
        self.assertIn(f"Configuration created: {self.ws.root / 'config.json'}", out)
        self.assertFalse((self.ws.root / "projects.json").exists())
        self.assertEqual(config(self.ws.root)["repository"], str(repo))


class SettingsTests(Case):
    def test_settings_are_validated_and_persisted(self):
        project = self.ws.add(make_repo(self.root / "alpha"))
        saved = self.ws.update_settings({"theme": "light", "defaults": {"reviewer": "claude"}, "selected_project": project.id,
                                         "onboarding_completed": True})
        self.assertEqual(saved["defaults"], {"developer": "claude", "reviewer": "claude"})
        self.assertEqual(Workspace(self.ws.root).settings(), saved)
        before = (self.ws.root / "settings.json").read_bytes()
        for bad in ({"theme": "neon"}, {"defaults": {"developer": "gpt"}}, {"defaults": {"judge": "claude"}}, {"password": "x"},
                    {"onboarding_completed": "yes"}, {"selected_project": "../x"}, {"version": 2}):
            with self.subTest(patch=bad), self.assertRaises(ValueError):
                self.ws.update_settings(bad)
        with self.assertRaises(NotFound):
            self.ws.update_settings({"selected_project": "P-00000000"})
        self.assertEqual((self.ws.root / "settings.json").read_bytes(), before)
        self.assertEqual(self.ws.update_settings({"selected_project": None})["selected_project"], None)


class CommandLineTests(Case):
    def test_commands_resolve_the_project_from_selection_name_or_task(self):
        code, _, err = self.cli("list")
        self.assertEqual(code, 1)
        self.assertIn("patchrondo init --repo", err)
        alpha = self.ws.add(make_repo(self.root / "alpha"))
        code, out, _ = self.cli("new", "--title", "Only", "--description", "d")  # a single project needs no selector
        self.assertEqual(code, 0, out)
        beta = self.ws.add(make_repo(self.root / "beta"))
        task_b, _ = create_task(beta.home, title="In beta", description="d", acceptance=[], developer="claude", reviewer="codex")
        code, _, err = self.cli("new", "--title", "Where", "--description", "d")
        self.assertEqual(code, 1)
        self.assertIn("--project", err)
        code, out, _ = self.cli("status", task_b)  # a task ID is enough
        self.assertEqual((code, json.loads(out)["title"]), (0, "In beta"))
        code, out, _ = self.cli("--project", "BETA", "list")
        self.assertEqual(code, 0)
        self.assertIn("In beta", out)
        self.assertNotIn("Only", out)
        code, out, _ = self.cli("--project", alpha.id, "list")
        self.assertIn("Only", out)
        code, out, _ = self.cli("list")  # no selector: every project, labelled
        self.assertIn(f"# alpha ({alpha.id})", out)
        self.assertIn("In beta", out)
        self.ws.update_settings({"selected_project": beta.id})
        code, out, _ = self.cli("new", "--title", "Selected", "--description", "d")
        self.assertEqual(code, 0, out)
        self.assertTrue(any("Selected" in read_json(f)["title"] for f in beta.home.glob("tasks/*/state.json")))
        code, _, err = self.cli("--project", "nope", "list")
        self.assertEqual(code, 1)
        self.assertIn("No single project matches", err)

    def test_projects_command_and_init_on_a_registry(self):
        code, out, _ = self.cli("projects")
        self.assertIn("No projects yet", out)
        alpha = self.ws.add(make_repo(self.root / "alpha"))
        code, out, _ = self.cli("init", "--repo", str(make_repo(self.root / "beta")))
        self.assertEqual(code, 0)
        self.assertIn("Project registered: beta (P-", out)
        code, out, _ = self.cli("projects")
        self.assertEqual(len(out.strip().splitlines()), 2)
        self.assertIn(f"{alpha.id} · alpha · ", out)
        self.assertFalse((self.ws.root / "config.json").exists())

    def test_no_command_opens_the_interface_without_any_configuration(self):
        with patch("patchrondo.ui.serve") as serve:
            self.assertEqual(self.cli()[0], 0)
            serve.assert_called_once_with(self.ws.root, 8765, open_browser=True)
            serve.reset_mock()
            self.assertEqual(self.cli("--port", "0", "--no-browser")[0], 0)
            serve.assert_called_once_with(self.ws.root, 0, open_browser=False)
            serve.reset_mock()
            self.assertEqual(self.cli("ui", "--port", "9001")[0], 0)
            serve.assert_called_once_with(self.ws.root, 9001, open_browser=True)
            serve.reset_mock()
            self.assertEqual(self.cli("--port", "9002", "--no-browser", "ui")[0], 0)  # options before the command are kept
            serve.assert_called_once_with(self.ws.root, 9002, open_browser=False)
        self.assertFalse(self.ws.root.exists())


if __name__ == "__main__":
    unittest.main()
