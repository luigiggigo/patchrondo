"""Run the unittest suite with measured durations, optionally one deterministic shard of it.

Without options it runs what `python -m unittest discover -s tests` runs, in
the same order, and measures each test from its start to its stop as unittest
reports them (setUp, the test, tearDown and cleanups): never the time between
two lines of a log. Class and module fixtures run outside any test; their time
is reported once, as time outside tests, never added to a test. So is the time
to collect the tests (importing their modules) and the runner's own.

`--shard I/N` runs the I-th of N parts. A part is made of whole test classes,
so a class keeps its fixtures and the order of its tests. Classes are spread
by measured duration (`--timings`), longest first, each to the part with the
least time so far, ties to the lower part; a class the timings do not know is
estimated from the median test. The same collected tests and the same timings
give the same parts on every platform. Before running, the runner checks that
the N parts hold every collected test exactly once; a part without tests fails.
Timings only balance the parts: they never decide whether a test runs.

The exit status is 0 only when every test of the run passed or was skipped.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
TIMINGS = ROOT / "tools" / "test_timings.json"
SLOWEST = 20


def flatten(suite) -> list[unittest.TestCase]:
    """The tests of a suite in the order unittest would run them."""
    if isinstance(suite, unittest.TestSuite):
        return [test for item in suite for test in flatten(item)]
    return [suite]


def unit_of(test_id: str) -> str:
    """The class of a test ID (`module.Class`): the smallest unit a shard holds."""
    return test_id.rsplit(".", 1)[0]


def collect(start: str = "tests", pattern: str = "test*.py", top: str | None = None) -> list[unittest.TestCase]:
    """What `python -m unittest discover -s START -p PATTERN` collects, failed imports included as failing tests."""
    return flatten(unittest.TestLoader().discover(start, pattern=pattern, top_level_dir=top))


def load_timings(path: Path | None) -> dict[str, float]:
    """Seconds per test class from a timings file or a report of this runner; {} when there is none."""
    if path is None or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if "classes" in data:
        return {str(name): float(seconds) for name, seconds in data["classes"].items()}
    classes: dict[str, float] = defaultdict(float)
    for record in data.get("tests", []):
        classes[unit_of(record["id"])] += float(record["seconds"])
    return dict(classes)


def partition(test_ids: list[str], shards: int, timings: dict[str, float]) -> list[list[str]]:
    """Split test IDs into `shards` lists of whole classes, balanced by `timings`, each list in collection order."""
    if shards < 1:
        raise ValueError("the number of shards must be at least 1")
    units: dict[str, list[str]] = {}
    for test_id in test_ids:
        units.setdefault(unit_of(test_id), []).append(test_id)
    known = [timings[name] / len(units[name]) for name in units if name in timings and units[name]]
    per_test = statistics.median(known) if known else 1.0
    estimate = {name: timings.get(name, per_test * len(ids)) for name, ids in units.items()}
    load = [0.0] * shards
    owner: dict[str, int] = {}
    for name in sorted(units, key=lambda name: (-estimate[name], name)):
        index = min(range(shards), key=lambda i: (load[i], i))
        owner[name] = index
        load[index] += estimate[name]
    parts: list[list[str]] = [[] for _ in range(shards)]
    for test_id in test_ids:
        parts[owner[unit_of(test_id)]].append(test_id)
    return parts


def check_partition(test_ids: list[str], parts: list[list[str]]) -> None:
    """Raise unless the parts hold every collected test exactly once."""
    if len(test_ids) != len(set(test_ids)):
        raise RuntimeError("the suite collects a test ID more than once; shards could not tell them apart")
    seen = [test_id for part in parts for test_id in part]
    missing, extra = set(test_ids) - set(seen), set(seen) - set(test_ids)
    if len(seen) != len(test_ids) or missing or extra:
        raise RuntimeError(f"shards lose or repeat tests: {len(seen)} assigned for {len(test_ids)} collected, "
                           f"{len(missing)} missing, {len(extra)} unknown")


def parse_shard(text: str) -> tuple[int, int]:
    """`I/N` with 1 <= I <= N."""
    try:
        index, total = (int(part) for part in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError("a shard is written I/N, for example 2/3") from None
    if not 1 <= index <= total:
        raise argparse.ArgumentTypeError("a shard I/N needs 1 <= I <= N")
    return index, total


class TimedResult(unittest.TextTestResult):
    """A text result that also records, for every test, its measured duration and its outcome."""

    latest: TimedResult | None = None  # the result of the current run, read when the run is interrupted

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        TimedResult.latest = self
        self.records: list[dict] = []
        self.current: str | None = None
        self.interrupted: str | None = None
        self._started = 0.0
        self._outcome = "passed"
        self._detail: str | None = None

    def startTest(self, test):
        self.current, self._outcome, self._detail = test.id(), "passed", None
        self._started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        super().stopTest(test)
        seconds = time.perf_counter() - self._started
        if isinstance(sys.exc_info()[1], KeyboardInterrupt):
            # unittest stops a test in a `finally`: one cut short by an interrupt has neither passed nor failed.
            self._outcome, self.interrupted = "interrupted", test.id()
        record = {"id": test.id(), "seconds": round(seconds, 4), "outcome": self._outcome}
        if self._detail:
            record["detail"] = self._detail
        self.records.append(record)
        self.current = None

    def _mark(self, outcome: str, detail: str | None = None) -> None:
        # Failure outranks error outranks skip: a test with one failing subtest failed.
        rank = {"passed": 0, "expected_failure": 1, "skipped": 2, "unexpected_success": 3, "error": 4, "failed": 5}
        if rank[outcome] >= rank[self._outcome]:
            self._outcome, self._detail = outcome, detail

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._mark("failed")

    def addError(self, test, err):
        super().addError(test, err)
        if self.current is None:
            # A class or module fixture failed outside any test: it is a test of its own in the report.
            self.records.append({"id": str(test), "seconds": 0.0, "outcome": "error", "detail": "fixture"})
        else:
            self._mark("error")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.current is None:
            self.records.append({"id": str(test), "seconds": 0.0, "outcome": "skipped", "detail": str(reason)[:200]})
        else:
            self._mark("skipped", str(reason)[:200])

    def addExpectedFailure(self, test, err):
        super().addExpectedFailure(test, err)
        self._mark("expected_failure")

    def addUnexpectedSuccess(self, test):
        super().addUnexpectedSuccess(test)
        self._mark("unexpected_success")

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._mark("failed" if issubclass(err[0], test.failureException) else "error")


def summarize(records: list[dict], wall: float, label: str, slowest: int = SLOWEST, interrupted: str | None = None) -> dict:
    """Durations per test, class and module, the slowest tests and the tests that did not pass.

    `interrupted` names the test that was running when the run was stopped from outside: the summary then
    covers the tests that ended before it.
    """
    tests = [record for record in records if record.get("detail") != "fixture"]
    classes: dict[str, float] = defaultdict(float)
    modules: dict[str, float] = defaultdict(float)
    for record in tests:
        classes[unit_of(record["id"])] += record["seconds"]
        modules[record["id"].split(".", 1)[0]] += record["seconds"]
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record["outcome"]] += 1
    in_tests = sum(record["seconds"] for record in tests)
    return {
        "version": 1, "label": label, "python": platform.python_version(), "platform": platform.platform(),
        "interrupted": interrupted, "wall_seconds": round(wall, 3), "test_seconds": round(in_tests, 3),
        "outside_tests_seconds": round(max(wall - in_tests, 0.0), 3),
        "counts": dict(sorted(counts.items())), "tests": records,
        "classes": {name: round(seconds, 3) for name, seconds in sorted(classes.items())},
        "modules": {name: round(seconds, 3) for name, seconds in sorted(modules.items(), key=lambda kv: -kv[1])},
        "slowest": sorted(tests, key=lambda record: (-record["seconds"], record["id"]))[:slowest],
        "not_passed": [record for record in records if record["outcome"] in {"failed", "error", "unexpected_success"}],
    }


def code(text: str, cell: bool = False) -> str:
    """`text` as one Markdown code span on one line, whatever it holds: a test ID can hold any character.

    The fence is one backtick longer than the longest run of backticks in `text`, with a space inside it when
    `text` starts or ends with a backtick or a space (Markdown removes one space from each side). A line break
    would end the row or the item: it is shown as \\r or \\n. In a table cell (`cell`) every pipe is escaped:
    GitHub splits a row into cells before it reads code spans, then drops the backslash. Only what people read
    is changed; the report and `--list` keep the IDs as they are.
    """
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if cell:
        text = text.replace("|", "\\|")
    fence = "`" * (max((len(run) for run in re.findall("`+", text)), default=0) + 1)
    pad = " " if text[:1] in ("`", " ") or text[-1:] in ("`", " ") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def render(summary: dict, markdown: bool = False) -> str:
    """The summary for people: totals, the slowest tests and modules, what did not pass."""
    counts = ", ".join(f"{name} {value}" for name, value in summary["counts"].items()) or "no tests"
    head = (f"{summary['label']}: {len(summary['tests'])} tests ({counts}) in {summary['wall_seconds']:.1f} s, "
            f"{summary['test_seconds']:.1f} s inside tests, {summary['outside_tests_seconds']:.1f} s outside "
            f"(class and module fixtures, collection, runner)")
    if summary.get("interrupted"):
        running = code(summary["interrupted"]) if markdown else summary["interrupted"]
        head = f"INTERRUPTED from outside while {running} ran: not a test failure, and not a pass. Before it, {head}"
    modules = list(summary["modules"].items())[:10]
    if markdown:
        lines = [f"### {summary['label']}", "", head, "", "| Slowest tests | s |", "|---|---:|"]
        lines += [f"| {code(record['id'], cell=True)} | {record['seconds']:.2f} |" for record in summary["slowest"]]
        lines += ["", "| Slowest modules | s |", "|---|---:|"]
        lines += [f"| {code(name, cell=True)} | {seconds:.1f} |" for name, seconds in modules]
        if summary["not_passed"]:
            lines += ["", "**Did not pass:**", ""] + [f"- {code(r['id'])}: {r['outcome']}" for r in summary["not_passed"]]
        return "\n".join(lines) + "\n"
    lines = ["", head, f"Slowest {len(summary['slowest'])} tests (seconds, measured from start to stop):"]
    lines += [f"  {record['seconds']:8.2f}  {record['id']}" for record in summary["slowest"]]
    lines += ["Slowest modules (seconds inside their tests):"]
    lines += [f"  {seconds:8.1f}  {name}" for name, seconds in modules]
    if summary["not_passed"]:
        lines += ["Did not pass:"] + [f"  {record['outcome']:<18} {record['id']}" for record in summary["not_passed"]]
    return "\n".join(lines) + "\n"


def publish(summary: dict, args: argparse.Namespace) -> None:
    """Print the summary, write the report and the timings that were asked for, and add it to the GitHub job page."""
    sys.stdout.write(render(summary))
    sys.stdout.flush()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
    if args.write_timings and not summary["interrupted"]:  # a run cut short would make its classes look fast
        data = {"version": 1, "unit": "class", "python": summary["python"], "platform": summary["platform"],
                "classes": {name: round(seconds, 2) for name, seconds in summary["classes"].items()}}
        args.write_timings.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        with open(step_summary, "a", encoding="utf-8", newline="\n") as stream:
            stream.write(render(summary, markdown=True))


def collect_only(tests: list[unittest.TestCase], counts: list[int], timings: dict[str, float]) -> int:
    """Fail when a test module cannot be imported, or when parts of a shard count would lose, repeat or miss tests."""
    failed = [test for test in tests if isinstance(test, unittest.loader._FailedTest)]
    for test in failed:
        print(f"cannot import {test.id().rsplit('.', 1)[-1]}:\n{test._exception}", file=sys.stderr)
    ids = [test.id() for test in tests]
    for count in counts:
        parts = partition(ids, count, timings)
        check_partition(ids, parts)
        if not all(parts):
            print(f"{count} shards would leave a part without tests", file=sys.stderr)
            return 1
        print(f"{count} shards hold {', '.join(str(len(part)) for part in parts)} tests")
    print(f"collected {len(ids)} tests; {len(failed)} modules could not be imported")
    return 1 if failed or not ids else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("-s", "--start-directory", default="tests")
    parser.add_argument("-p", "--pattern", default="test*.py")
    parser.add_argument("-t", "--top-level-directory", default=None)
    parser.add_argument("-v", "--verbose", dest="verbosity", action="store_const", const=2, default=1)
    parser.add_argument("-q", "--quiet", dest="verbosity", action="store_const", const=0)
    parser.add_argument("--shard", type=parse_shard, default=None, metavar="I/N", help="run the I-th of N parts")
    parser.add_argument("--timings", type=Path, default=TIMINGS,
                        help="seconds per class used to balance shards (a timings file or a report)")
    parser.add_argument("--report", type=Path, default=None, help="write the measured durations as JSON")
    parser.add_argument("--write-timings", type=Path, default=None,
                        help="write the seconds per class of this run, for --timings")
    parser.add_argument("--slowest", type=int, default=SLOWEST)
    parser.add_argument("--list", action="store_true", help="print the IDs of the tests the run would hold, then stop")
    parser.add_argument("--collect-only", action="store_true",
                        help="import every test module and check the shards of --shard-counts; fail on a module that "
                             "cannot be imported")
    parser.add_argument("--shard-counts", type=lambda text: [int(n) for n in text.split(",")], default=[],
                        metavar="N,M", help="with --collect-only: shard counts whose parts must hold every test once")
    args = parser.parse_args(argv)

    started = time.perf_counter()  # collecting the tests is part of the run: time outside tests
    tests = collect(args.start_directory, args.pattern, args.top_level_directory)
    ids = [test.id() for test in tests]
    if args.collect_only:
        return collect_only(tests, args.shard_counts, load_timings(args.timings))
    label = "all tests"
    if args.shard is not None:
        index, total = args.shard
        parts = partition(ids, total, load_timings(args.timings))
        check_partition(ids, parts)
        wanted = set(parts[index - 1])
        tests = [test for test in tests if test.id() in wanted]
        label = f"shard {index}/{total}"
        print(f"{label}: {len(tests)} of {len(ids)} collected tests; parts hold "
              f"{', '.join(str(len(part)) for part in parts)} tests", flush=True)
        if not tests:
            print(f"{label} holds no test: use fewer shards", file=sys.stderr)
            return 1
    if args.list:
        print("\n".join(test.id() for test in tests))
        return 0

    runner = unittest.TextTestRunner(verbosity=args.verbosity, resultclass=TimedResult)
    try:
        result = runner.run(unittest.TestSuite(tests))
    except KeyboardInterrupt:
        # A job stopped at its time limit or cancelled is not a test result: say where it stopped, what took the
        # time until then, and leave with the interrupt.
        partial = TimedResult.latest
        if partial is not None:
            publish(summarize(partial.records, time.perf_counter() - started, label, args.slowest,
                              interrupted=partial.interrupted or partial.current or "no test"), args)
        print(f"\nINTERRUPTED: {label} was stopped from outside; this is not a test failure", file=sys.stderr)
        raise
    publish(summarize(result.records, time.perf_counter() - started, label, args.slowest), args)
    if not result.testsRun:
        print("no test ran", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
