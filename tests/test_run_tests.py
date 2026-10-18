"""The test runner of CI: measured durations, and shards that hold every test exactly once."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_tests.py"
SPEC = importlib.util.spec_from_file_location("run_tests", TOOL)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

# A small suite with every outcome the runner reports: a class fixture that takes time outside its
# tests, a measured test, a failure, a failing subtest, a skip and a module that cannot be imported.
SUITE = {
    "test_alpha.py": '''
        import time
        import unittest

        class Slow(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                time.sleep(0.3)

            def test_quick(self):
                pass

            def test_sleeps(self):
                time.sleep(0.15)

        class Mixed(unittest.TestCase):
            def test_fails(self):
                self.fail("planned failure")

            def test_subtest_fails(self):
                for value in range(2):
                    with self.subTest(value=value):
                        self.assertEqual(value, 0)

            @unittest.skip("planned skip")
            def test_skipped(self):
                pass
        ''',
    "test_beta.py": '''
        import unittest

        class Plain(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass

        class More(unittest.TestCase):
            def test_three(self):
                pass
        ''',
    "test_broken.py": '''
        raise ImportError("planned import failure")
        ''',
    # Not collected by the default pattern: a run stopped from outside, as a job at its time limit is.
    "interrupt_now.py": '''
        import unittest

        class Stopped(unittest.TestCase):
            def test_a_first(self):
                pass

            def test_b_stopped(self):
                raise KeyboardInterrupt

            def test_c_never_started(self):
                pass
        ''',
    # Not collected by the default pattern: importing it, part of collecting the tests, takes half a second.
    "slow_collection.py": '''
        import time
        import unittest

        time.sleep(0.5)

        class Collected(unittest.TestCase):
            def test_quick(self):
                pass
        ''',
    # Not collected by the default pattern: unittest takes any name of a class or a method, and so any test ID,
    # with what Markdown reads in it. A fixture fails and the last test is stopped from outside.
    "odd_names.py": r'''
        import unittest

        def passes(self):
            pass

        def fails(self):
            self.fail("planned failure")

        def fixture_fails(cls):
            raise RuntimeError("planned fixture failure")

        def stops(self):
            raise KeyboardInterrupt

        A = type("A", (unittest.TestCase,), {"__module__": "odd`|\nmodule", "test_` | injected": passes,
                                             "test_``fence`` ends with `": passes, "test_crlf\r\n|---|---|": fails,
                                             "test_line\nbreak | here": fails})
        Fixture = type("Fixture `|\n|---|", (unittest.TestCase,), {"setUpClass": classmethod(fixture_fails),
                                                                   "test_never_runs": passes})
        Stop = type("Stop", (unittest.TestCase,), {"test_stopped `|\n| x |": stops})
        ''',
    # Not collected by the default pattern: a method name may hold dots, so no split of an ID tells its class.
    "dotted_names.py": '''
        import unittest

        def passes(self):
            pass

        A = type("A", (unittest.TestCase,), {"test_a": passes, "test_a.b": passes, "test_a.b.c": passes})

        class B(unittest.TestCase):
            def test_one(self):
                pass

            def test_two(self):
                pass
        ''',
}
ODD = ["odd`|\nmodule.A.test_` | injected", "odd`|\nmodule.A.test_``fence`` ends with `",
       "odd`|\nmodule.A.test_crlf\r\n|---|---|", "odd`|\nmodule.A.test_line\nbreak | here",
       "odd_names.Fixture `|\n|---|.test_never_runs", "odd_names.Stop.test_stopped `|\n| x |"]
FAILING = {"test_alpha.Mixed.test_fails", "test_alpha.Mixed.test_subtest_fails", "unittest.loader._FailedTest.test_broken"}


class PartitionTests(unittest.TestCase):
    # The class of each test, as the runner reads it from the collected tests.
    CLASSES = {f"{name}.{method}": name for name, method in (("a.A", "t1"), ("a.A", "t2"), ("a.B", "t1"), ("b.C", "t1"),
                                                             ("b.C", "t2"), ("b.C", "t3"), ("b.D", "t1"), ("c.E", "t1"))}
    IDS = list(CLASSES)

    def test_every_test_is_in_exactly_one_part_with_its_whole_class_in_collection_order(self):
        timings = {"a.A": 4.0, "a.B": 1.0, "b.C": 6.0, "b.D": 2.0, "c.E": 3.0}
        for shards in range(1, 7):
            with self.subTest(shards=shards):
                parts = runner.partition(self.IDS, shards, timings, self.CLASSES)
                self.assertEqual(len(parts), shards)
                runner.check_partition(self.IDS, parts)
                self.assertEqual(sorted(test for part in parts for test in part), sorted(self.IDS))
                owners = {self.CLASSES[test]: index for index, part in enumerate(parts) for test in part}
                for test in self.IDS:
                    self.assertIn(test, parts[owners[self.CLASSES[test]]])
                for part in parts:
                    self.assertEqual(part, [test for test in self.IDS if test in part])

    def test_the_same_tests_and_timings_give_the_same_parts(self):
        timings = {"a.A": 4.0, "b.C": 6.0}
        first = runner.partition(self.IDS, 3, timings, self.CLASSES)
        self.assertEqual(runner.partition(list(self.IDS), 3, dict(reversed(list(timings.items()))), dict(self.CLASSES)),
                         first)

    def test_parts_are_balanced_longest_class_first(self):
        timings = {"a.A": 4.0, "a.B": 1.0, "b.C": 6.0, "b.D": 2.0, "c.E": 3.0}
        parts = runner.partition(self.IDS, 2, timings, self.CLASSES)
        loads = [sum(timings[unit] for unit in {self.CLASSES[test] for test in part}) for part in parts]
        # Longest first, each to the least loaded part: C(6)->1st, A(4)->2nd, E(3)->2nd, D(2)->1st, B(1)->2nd.
        self.assertEqual(loads, [8.0, 8.0])
        self.assertEqual([sorted({self.CLASSES[test] for test in part}) for part in parts],
                         [["b.C", "b.D"], ["a.A", "a.B", "c.E"]])

    def test_a_class_without_timings_is_estimated_from_the_median_test(self):
        # A and C take 1 s per test; the unknown E (one test) counts 1 s, so it joins the lighter part.
        parts = runner.partition(self.IDS, 2, {"a.A": 2.0, "b.C": 3.0, "a.B": 1.0, "b.D": 1.0}, self.CLASSES)
        self.assertIn("c.E.t1", parts[1])
        runner.check_partition(self.IDS, parts)

    def test_a_lost_or_repeated_test_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "lose or repeat"):
            runner.check_partition(self.IDS, [self.IDS[:-1]])
        with self.assertRaisesRegex(RuntimeError, "lose or repeat"):
            runner.check_partition(self.IDS, [self.IDS, self.IDS[:1]])
        with self.assertRaisesRegex(RuntimeError, "more than once"):
            runner.check_partition(self.IDS + self.IDS[:1], [self.IDS + self.IDS[:1]])

    def test_a_shard_is_written_i_of_n(self):
        self.assertEqual(runner.parse_shard("2/3"), (2, 3))
        for text in ("0/3", "4/3", "3", "a/b", "1/0"):
            with self.subTest(text=text), self.assertRaises(Exception):
                runner.parse_shard(text)
        with self.assertRaises(ValueError):
            runner.partition(self.IDS, 0, {}, self.CLASSES)

    def test_the_committed_timings_spread_this_suite_into_complete_parts(self):
        timings = runner.load_timings(runner.TIMINGS)
        self.assertTrue(timings)
        self.assertTrue(all(seconds >= 0 for seconds in timings.values()))
        tests = runner.collect(str(ROOT / "tests"), top=str(ROOT / "tests"))
        ids, classes = [test.id() for test in tests], {test.id(): runner.class_of(test) for test in tests}
        self.assertIn(self.id(), ids)
        self.assertEqual(classes[self.id()], "test_run_tests.PartitionTests")
        for shards in (2, 3, 4):
            with self.subTest(shards=shards):
                parts = runner.partition(ids, shards, timings, classes)
                runner.check_partition(ids, parts)
                self.assertTrue(all(parts))


WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


@unittest.skipUnless(WORKFLOW.exists(), "the CI workflow is in the repository, not in the source package")
class WorkflowTests(unittest.TestCase):
    """A shard the CI matrix leaves out would never run, and no runner could tell: the matrix must list them all."""

    def test_each_system_runs_every_part_of_its_shards_for_both_pythons(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        targets = re.findall(r'-\s*\{\s*os:\s*([\w.-]+),\s*shard:\s*"(\d+)/(\d+)"\s*\}', text)
        shards: dict[str, list[tuple[int, int]]] = {}
        for system, index, total in targets:
            shards.setdefault(system, []).append((int(index), int(total)))
        self.assertEqual(set(shards), {"ubuntu-latest", "windows-latest", "macos-latest"})
        counts = set()
        for system, listed in shards.items():
            with self.subTest(system=system):
                totals = {total for _, total in listed}
                self.assertEqual(len(totals), 1)
                total = totals.pop()
                self.assertEqual(sorted(index for index, _ in listed), list(range(1, total + 1)))
                counts.add(total)
        checked = re.search(r"--collect-only --shard-counts ([\d,]+)", text)
        self.assertIsNotNone(checked)
        self.assertTrue(counts <= {int(count) for count in checked.group(1).split(",")})
        self.assertIn('python: ["3.11", "3.13"]', text)
        self.assertIn("fail-fast: false", text)
        self.assertIn("python tools/run_tests.py -v --shard ${{ matrix.target.shard }}", text)


class RunnerTests(unittest.TestCase):
    """The runner on a synthetic suite, in a process of its own as CI starts it."""

    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.folder.cleanup)
        cls.root = Path(cls.folder.name).resolve()
        cls.suite = cls.root / "suite"
        cls.suite.mkdir()
        for name, text in SUITE.items():
            (cls.suite / name).write_text(textwrap.dedent(text), encoding="utf-8", newline="\n")
        cls.timings = cls.root / "timings.json"
        cls.timings.write_text(json.dumps({"classes": {"test_alpha.Slow": 5.0, "test_alpha.Mixed": 3.0,
                                                       "test_beta.Plain": 2.0, "test_beta.More": 1.0}}), encoding="utf-8")
        cls.full = cls.run_tool(["--report", str(cls.root / "full.json")])
        cls.report = json.loads((cls.root / "full.json").read_text(encoding="utf-8"))

    @classmethod
    def run_tool(cls, args, **env):
        environment = {key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"}
        return subprocess.run([sys.executable, str(TOOL), "-s", str(cls.suite), "--timings", str(cls.timings), *args],
                              cwd=cls.root, capture_output=True, text=True, timeout=120, env={**environment, **env})

    def test_a_full_run_holds_what_unittest_discover_collects_in_its_order(self):
        listed = subprocess.run([sys.executable, "-c", "import sys, unittest\n"
                                 "def walk(s):\n"
                                 "    return [t for i in s for t in walk(i)] if isinstance(s, unittest.TestSuite) else [s.id()]\n"
                                 f"print('\\n'.join(walk(unittest.TestLoader().discover({str(self.suite)!r}))))"],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual([record["id"] for record in self.report["tests"]], listed.stdout.split())
        self.assertEqual(len(self.report["tests"]), 9)

    def test_durations_are_measured_from_start_to_stop_and_fixtures_stay_outside(self):
        durations = {record["id"]: record["seconds"] for record in self.report["tests"]}
        self.assertGreaterEqual(durations["test_alpha.Slow.test_sleeps"], 0.15)
        self.assertLess(durations["test_alpha.Slow.test_quick"], 0.3)  # setUpClass ran before it, outside it
        self.assertGreaterEqual(self.report["outside_tests_seconds"], 0.25)
        self.assertEqual(self.report["slowest"][0]["id"], "test_alpha.Slow.test_sleeps")
        self.assertAlmostEqual(self.report["test_seconds"], sum(durations.values()), places=2)
        self.assertGreaterEqual(self.report["classes"]["test_alpha.Slow"], 0.15)
        self.assertIn("Slowest", self.full.stdout)

    def test_outcomes_failures_and_skips_are_reported_and_fail_the_run(self):
        self.assertEqual(self.full.returncode, 1, self.full.stderr)
        outcomes = {record["id"]: record["outcome"] for record in self.report["tests"]}
        self.assertEqual({test for test, outcome in outcomes.items() if outcome in {"failed", "error"}}, FAILING)
        self.assertEqual(outcomes["test_alpha.Mixed.test_subtest_fails"], "failed")
        self.assertEqual(outcomes["unittest.loader._FailedTest.test_broken"], "error")
        skipped = next(record for record in self.report["tests"] if record["outcome"] == "skipped")
        self.assertEqual((skipped["id"], skipped["detail"]), ("test_alpha.Mixed.test_skipped", "planned skip"))
        self.assertEqual({record["id"] for record in self.report["not_passed"]}, FAILING)

    def test_shards_hold_each_test_once_and_fail_on_their_own(self):
        seen = []
        for index in (1, 2, 3):
            with self.subTest(shard=index):
                report = self.root / f"shard-{index}.json"
                done = self.run_tool(["--shard", f"{index}/3", "--report", str(report)])
                ids = [record["id"] for record in json.loads(report.read_text(encoding="utf-8"))["tests"]]
                self.assertTrue(ids)
                self.assertEqual(done.returncode, 1 if FAILING & set(ids) else 0, done.stdout + done.stderr)
                self.assertIn(f"shard {index}/3: {len(ids)} of 9 collected tests", done.stdout)
                seen += ids
        self.assertEqual(sorted(seen), sorted(record["id"] for record in self.report["tests"]))
        self.assertEqual(len(seen), len(set(seen)))

    def test_a_shard_without_tests_fails_and_list_prints_the_part(self):
        empty = self.run_tool(["--shard", "6/6"])
        self.assertEqual(empty.returncode, 1)
        self.assertIn("holds no test", empty.stderr)
        listed = self.run_tool(["--shard", "1/2", "--list"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        records = self.report["tests"]
        part = runner.partition([record["id"] for record in records], 2, runner.load_timings(self.timings),
                                {record["id"]: record["class"] for record in records})[0]
        self.assertEqual(listed.stdout.splitlines()[1:], part)

    def test_a_class_stays_in_one_part_whatever_its_method_names_hold(self):
        whole = ["dotted_names.A.test_a", "dotted_names.A.test_a.b", "dotted_names.A.test_a.b.c"]
        other = ["dotted_names.B.test_one", "dotted_names.B.test_two"]
        parts = []
        for index in (1, 2):
            listed = self.run_tool(["-p", "dotted*.py", "--shard", f"{index}/2", "--list"])
            self.assertEqual(listed.returncode, 0, listed.stderr)
            parts.append(listed.stdout.splitlines()[1:])
        holding = [part for part in parts if set(whole) & set(part)]
        self.assertEqual(len(holding), 1, parts)  # A's tests in one part, in collection order
        self.assertEqual([test for test in holding[0] if test in whole], whole)
        seen = [test for part in parts for test in part]
        self.assertEqual(sorted(seen), sorted(whole + other))
        self.assertEqual(len(seen), len(set(seen)))
        # The report keeps the IDs, and counts the time of each test to its class.
        report = self.root / "dotted.json"
        done = self.run_tool(["-p", "dotted*.py", "--report", str(report)])
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual([(record["id"], record["class"]) for record in data["tests"]],
                         [(test, "dotted_names.A") for test in whole] + [(test, "dotted_names.B") for test in other])
        self.assertEqual(sorted(data["classes"]), ["dotted_names.A", "dotted_names.B"])

    def test_collect_only_fails_on_a_module_that_cannot_be_imported_or_an_empty_part(self):
        broken = self.run_tool(["--collect-only", "--shard-counts", "2,3"])
        self.assertEqual(broken.returncode, 1)
        self.assertIn("cannot import test_broken", broken.stderr)
        self.assertIn("planned import failure", broken.stderr)
        clean = self.run_tool(["--collect-only", "--shard-counts", "2,4", "-p", "*a.py"])  # test_alpha and test_beta
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertIn("collected 8 tests; 0 modules could not be imported", clean.stdout)
        self.assertIn("4 shards hold", clean.stdout)
        too_many = self.run_tool(["--collect-only", "--shard-counts", "5", "-p", "*a.py"])  # four classes
        self.assertEqual(too_many.returncode, 1)
        self.assertIn("5 shards would leave a part without tests", too_many.stderr)

    def test_collecting_the_tests_counts_as_time_outside_them(self):
        report = self.root / "slow-collection.json"
        done = self.run_tool(["-p", "slow*.py", "--report", str(report)])
        self.assertEqual(done.returncode, 0, done.stderr)
        data = json.loads(report.read_text(encoding="utf-8"))
        # Its module sleeps 0.5 s when it is imported, before any test starts: outside tests, and in no test.
        self.assertEqual([(record["id"], record["outcome"]) for record in data["tests"]],
                         [("slow_collection.Collected.test_quick", "passed")])
        self.assertGreaterEqual(data["wall_seconds"], 0.45)
        self.assertGreaterEqual(data["outside_tests_seconds"], 0.45)
        self.assertLess(data["test_seconds"], 0.45)

    def test_an_interrupted_run_says_where_it_stopped_and_is_never_a_pass(self):
        report, timings = self.root / "interrupted.json", self.root / "kept-timings.json"
        timings.write_text("kept\n", encoding="utf-8")
        done = self.run_tool(["-p", "interrupt*.py", "--report", str(report), "--write-timings", str(timings)])
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(timings.read_text(encoding="utf-8"), "kept\n")  # a run cut short would make classes look fast
        self.assertIn("INTERRUPTED from outside while interrupt_now.Stopped.test_b_stopped ran", done.stdout)
        self.assertIn("this is not a test failure", done.stderr)
        data = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(data["interrupted"], "interrupt_now.Stopped.test_b_stopped")
        self.assertEqual([(record["id"], record["outcome"]) for record in data["tests"]],
                         [("interrupt_now.Stopped.test_a_first", "passed"),
                          ("interrupt_now.Stopped.test_b_stopped", "interrupted")])
        self.assertIsNone(self.report["interrupted"])

    def test_the_summary_goes_to_github_when_asked(self):
        summary = self.root / "summary.md"
        done = self.run_tool(["--shard", "2/3", "-q"], GITHUB_STEP_SUMMARY=str(summary))
        text = summary.read_text(encoding="utf-8")
        self.assertIn("### shard 2/3", text)
        self.assertIn("| Slowest tests | s |", text)
        self.assertEqual("Did not pass" in text, done.returncode == 1)

    def span(self, text: str) -> str:
        """What a Markdown code span shows, after checking that `text` is exactly one."""
        fence = re.match(r"`*", text).group()
        self.assertTrue(fence and len(text) > 2 * len(fence), text)
        self.assertEqual(re.search(r"`*$", text).group(), fence, text)
        inner = text[len(fence):-len(fence)]
        self.assertNotIn(len(fence), [len(run) for run in re.findall(r"`+", inner)], text)  # it would end the span
        if inner.startswith(" ") and inner.endswith(" ") and inner.strip(" "):
            inner = inner[1:-1]
        return inner

    def cells(self, row: str) -> list[str]:
        """The cells of a row of a GitHub table: split at each pipe no backslash escapes, the backslash then dropped."""
        self.assertTrue(row.startswith("| ") and row.endswith(" |"), row)
        return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row[1:-1])]

    def test_unusual_test_ids_stay_whole_in_the_summary_and_as_they_are_in_the_report(self):
        summary, report = self.root / "odd.md", self.root / "odd.json"
        done = self.run_tool(["-p", "odd*.py", "--report", str(report)], GITHUB_STEP_SUMMARY=str(summary))
        self.assertNotEqual(done.returncode, 0)  # stopped from outside
        data = json.loads(report.read_text(encoding="utf-8"))
        # The report and --list keep every ID as unittest gave it.
        fixture = "setUpClass (odd_names.Fixture `|\n|---|)"
        self.assertEqual([record["id"] for record in data["tests"]], ODD[:4] + [fixture, ODD[5]])
        self.assertEqual([record["id"] for record in data["not_passed"]], [ODD[2], ODD[3], fixture])
        self.assertEqual(data["interrupted"], ODD[5])
        listed = subprocess.run([sys.executable, str(TOOL), "-s", str(self.suite), "-p", "odd*.py", "--list"],
                                cwd=self.root, capture_output=True, timeout=60,
                                env={key: value for key, value in os.environ.items() if key != "GITHUB_STEP_SUMMARY"})
        self.assertEqual(listed.returncode, 0, listed.stderr)
        # Bytes: a text pipe would read the \r of an ID as a line break, and Windows writes \n as \r\n.
        self.assertEqual(listed.stdout.replace(os.linesep.encode(), b"\n").decode(), "\n".join(ODD) + "\n")

        # Markdown shows each in one code span on one line, in its own cell of the table.
        def shown(test_id):
            return test_id.replace("\r", "\\r").replace("\n", "\\n")

        text = summary.read_bytes().decode("utf-8")
        self.assertNotIn("\r", text)
        lines = text.split("\n")
        prefix, suffix = "INTERRUPTED from outside while ", " ran: not a test failure, and not a pass."
        self.assertTrue(lines[2].startswith(prefix) and suffix in lines[2], lines[2])
        self.assertEqual(self.span(lines[2][len(prefix):lines[2].index(suffix)]), shown(ODD[5]))
        for title, expected in (("| Slowest tests | s |", [record["id"] for record in data["slowest"]]),
                                ("| Slowest modules | s |", list(data["modules"]))):
            start = lines.index(title) + 2
            rows = lines[start:lines.index("", start)]
            with self.subTest(table=title):
                self.assertTrue(rows)
                for row in rows:
                    self.assertEqual(len(self.cells(row)), 2, row)
                self.assertEqual([self.span(self.cells(row)[0]) for row in rows], [shown(name) for name in expected])
        self.assertIn("odd`|\nmodule", data["modules"])
        items = lines[lines.index("**Did not pass:**") + 2:-1]
        self.assertEqual(len(items), len(data["not_passed"]))
        for item, record in zip(items, data["not_passed"]):
            end = f": {record['outcome']}"
            self.assertTrue(item.startswith("- ") and item.endswith(end), item)
            self.assertEqual(self.span(item[2:-len(end)]), shown(record["id"]))


if __name__ == "__main__":
    unittest.main()
