"""The activity journal and the provider stream parsers. No provider CLI is started."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from patchrondo import activity, streams
from patchrondo.process import LineReader

T0 = datetime(2026, 10, 11, 9, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        self.now += timedelta(milliseconds=250)
        return self.now


def lines(*objects) -> bytes:
    return "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in objects).encode("utf-8")


class Recorder:
    """Stands in for a journal: keeps what a parser emits."""

    def __init__(self):
        self.events = []

    def emit(self, kind, /, **fields):
        self.events.append({"type": kind, **{name: value for name, value in fields.items() if value is not None}})

    def of(self, kind):
        return [event for event in self.events if event["type"] == kind]


class LineReaderTests(unittest.TestCase):
    def read(self, chunks, **options):
        found = []
        reader = LineReader(found.append, **options)
        for chunk in chunks:
            reader.feed(chunk)
        reader.close()
        return found, reader

    def test_lines_and_characters_split_between_chunks_are_put_back_together(self):
        data = 'first {"text": "caffè ↔ 日本語"}\r\nsecond\n\nlast without newline'.encode("utf-8")
        whole = self.read([data])[0]
        self.assertEqual(whole, ['first {"text": "caffè ↔ 日本語"}', "second", "", "last without newline"])
        for size in (1, 2, 3, 5, 7):  # every multi-byte character is cut at every position at some size
            with self.subTest(size=size):
                self.assertEqual(self.read([data[index:index + size] for index in range(0, len(data), size)])[0], whole)

    def test_an_oversized_line_is_dropped_whole_and_memory_stays_bounded(self):
        found, reader = self.read([b"a\n", b"x" * 700, b"x" * 700, b"tail-of-the-long-line\n", b"b\n", b"y" * 5000], limit=1000)
        self.assertEqual(found, ["a", "b"])
        self.assertEqual(reader.oversized, 2)
        self.assertLessEqual(len(reader.pending), 1000)

    def test_bytes_that_are_not_utf8_do_not_stop_the_reader(self):
        self.assertEqual(self.read([b"ok \xff\xfe bytes\n", b"next\n"])[0], ["ok �� bytes", "next"])


class RedactionTests(unittest.TestCase):
    def test_values_that_look_like_credentials_are_masked(self):
        secret = "s3cr3t-value-0123456789"
        # The marker of a key block is put together here: written out, the publication scanner
        # would report this made-up fixture as a possible private key in every scan.
        block = "PRIVATE " + "KEY-----"
        cases = (f"API_KEY={secret}", f'export GITHUB_TOKEN="{secret}"', f'{{"client_secret": "{secret}"}}',
                 f"Authorization: Bearer {secret}", "key sk-" + "a" * 30, "ghp_" + "b" * 36,
                 f"-----BEGIN RSA {block}\n{secret}\n-----END RSA {block}", f"password: {secret}")
        for text in cases:
            with self.subTest(text=text[:30]):
                self.assertNotIn(secret, activity.redact(text))
                self.assertNotIn("a" * 30, activity.redact(text))
                self.assertNotIn("b" * 36, activity.redact(text))
        plain = "Edited src/app.py and ran 12 tests in 3.2 s"
        self.assertEqual(activity.redact(plain), plain)

    def test_a_secret_cut_by_the_length_limit_is_still_masked(self):
        text = "x" * (activity.FIELD_LIMIT - 12) + " API_KEY=" + "k" * 300
        cleaned = activity._clean(text, "summary")
        self.assertNotIn("kkkk", cleaned)
        self.assertIn("characters]", cleaned)


class JournalCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "T-0123456789ab"
        self.path.mkdir()
        self.file = self.path / activity.JOURNAL
        self.clock = Clock()

    def journal(self, role="developer", provider="claude", iteration=1):
        journal = activity.Journal(self.path, iteration=iteration, role=role, provider=provider, clock=self.clock)
        self.addCleanup(journal.close)
        return journal

    def records(self):
        return [json.loads(line) for line in self.file.read_text(encoding="utf-8").splitlines()]


class JournalTests(JournalCase):
    def test_every_event_names_its_task_step_attempt_and_position(self):
        journal = self.journal()
        journal.emit("message", text="Reading the code")
        journal.emit("tool", status="started", name="Read", summary="src/app.py", id="call-1")
        header, first, second = self.records()
        self.assertEqual((header["type"], header["task"]), ("journal", "T-0123456789ab"))
        self.assertRegex(header["id"], r"^J-[0-9a-f]{12}$")
        for event in (first, second):
            self.assertEqual({key: event[key] for key in ("task", "iteration", "role", "provider", "attempt")},
                             {"task": "T-0123456789ab", "iteration": 1, "role": "developer", "provider": "claude",
                              "attempt": journal.base["attempt"]})
            self.assertEqual(datetime.fromisoformat(event["at"]).tzinfo, timezone.utc)
        self.assertEqual([(first["seq"], first["type"]), (second["seq"], second["type"])], [(1, "message"), (2, "tool")])
        self.assertLess(first["at"], second["at"])
        if os.name == "posix":
            self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_retries_and_resumes_append_and_never_rewrite(self):
        first = self.journal()
        first.emit("message", text="attempt one")
        first.close()
        before = self.file.read_bytes()
        second = self.journal()
        second.emit("message", text="attempt two")
        third = self.journal(role="reviewer", provider="codex")
        third.emit("message", text="review")
        self.assertTrue(self.file.read_bytes().startswith(before))
        events = self.records()[1:]
        self.assertEqual([event["seq"] for event in events], [1, 2, 3])
        self.assertEqual(len({event["attempt"] for event in events}), 3)
        self.assertEqual([event["role"] for event in events], ["developer", "developer", "reviewer"])

    def test_a_line_cut_by_a_crash_is_closed_and_skipped_and_numbering_continues(self):
        journal = self.journal()
        for index in range(3):
            journal.emit("message", text=f"event {index}")
        journal.close()
        whole = self.file.read_bytes()
        self.file.write_bytes(whole[:-25])  # the run died in the middle of its last line
        cursor = activity.read(self.path)["next"]
        self.assertEqual([event["seq"] for event in activity.read(self.path)["events"]], [1, 2])
        resumed = self.journal()
        resumed.emit("message", text="after the crash")
        page = activity.read(self.path, cursor)
        self.assertEqual(([event["text"] for event in page["events"]], page["skipped"], page["reset"]),
                         (["after the crash"], 1, False))
        self.assertEqual(page["events"][0]["seq"], 3)  # the damaged line no longer counts; nothing is numbered twice
        self.assertEqual([event["seq"] for event in activity.read(self.path)["events"]], [1, 2, 3])

    def test_text_and_collections_are_bounded_and_redacted_on_the_way_in(self):
        journal = self.journal()
        journal.emit("message", text="a" * 50_000)
        journal.emit("tool", name="shell", summary="b" * 5000, output="TOKEN=abcdef0123456789 " + "c" * 9000,
                     items=[{"text": "d" * 50}] * 500, nested={"a": {"b": {"c": {"d": "too deep"}}}})
        message, tool = self.records()[1:]
        self.assertLess(len(message["text"]), activity.TEXT_LIMIT + 60)
        self.assertIn("+46000 characters", message["text"])
        self.assertLess(len(tool["summary"]), activity.FIELD_LIMIT + 60)
        self.assertNotIn("abcdef0123456789", tool["output"])
        self.assertEqual(len(tool["items"]), activity.LIST_LIMIT)
        self.assertIsNone(tool["nested"]["a"]["b"]["c"])
        self.assertTrue(all(len(line) <= activity.RECORD_BYTES for line in self.file.read_bytes().splitlines()))
        journal.emit("status", items=[{"text": "é" * 399} for _ in range(40)], more=[{"text": "é" * 399} for _ in range(40)])
        last = self.records()[-1]
        self.assertEqual((last["truncated"], last["seq"], "items" in last, last["type"]), (True, 3, False, "status"))
        journal.emit("message", text="\U0001f600" * 4000, output="\U0001f600" * 4000)  # four bytes each: too long
        last = self.records()[-1]
        self.assertEqual((last["truncated"], last["seq"], "text" in last, last["attempt"]), (True, 4, False, journal.base["attempt"]))
        self.assertTrue(all(len(line) <= activity.RECORD_BYTES for line in self.file.read_bytes().splitlines()))

    def test_an_attempt_stops_recording_at_its_limit_but_always_records_its_end(self):
        with patch.object(activity, "ATTEMPT_EVENTS", 5):
            with self.assertRaises(RuntimeError):
                with activity.attempt(self.path, iteration=1, role="developer", provider="codex", clock=self.clock) as journal:
                    for index in range(20):
                        journal.emit("message", text=f"line {index}")
                    raise RuntimeError("the step failed")
        events = self.records()[1:]
        self.assertEqual([event["type"] for event in events], ["status"] + ["message"] * 4 + ["error", "completion"])
        self.assertEqual((events[-1]["outcome"], events[-1]["kind"], events[-1]["unrecorded"]), ("failed", "system_error", 16))
        self.assertEqual(events[-2]["text"], "the step failed")

    def test_an_attempt_records_how_it_ended_and_changes_nothing(self):
        class Quota(RuntimeError):
            kind = "quota"

        outcomes = {}
        for name, error in (("completed", None), ("failed", Quota("usage limit")), ("timeout", TimeoutError("slow")),
                            ("interrupted", KeyboardInterrupt())):
            if isinstance(error, TimeoutError):
                error.kind = "timeout"
            try:
                with activity.attempt(self.path, iteration=2, role="reviewer", provider="claude", clock=self.clock):
                    if error is not None:
                        raise error
            except BaseException as caught:  # noqa: BLE001 - the same object must come back out
                self.assertIs(caught, error)
            outcomes[name] = self.records()[-1]
        self.assertEqual({name: event["outcome"] for name, event in outcomes.items()},
                         {"completed": "completed", "failed": "failed", "timeout": "timeout", "interrupted": "interrupted"})
        self.assertEqual(outcomes["failed"]["kind"], "quota")
        self.assertNotIn("kind", outcomes["completed"])

    def test_a_journal_that_cannot_be_written_never_stops_the_work(self):
        missing = self.path / "no-such-folder"
        with activity.attempt(missing, iteration=1, role="developer", provider="claude") as journal:
            journal.emit("message", text="not recorded")
            result = "the step still returned"
        self.assertEqual(result, "the step still returned")
        self.assertFalse(missing.exists())
        working = self.journal()
        working.emit("message", text="recorded")
        os.close(working.fd)  # the disk goes away under an open journal
        working.emit("message", text="lost")
        working.emit("message", text="also lost")
        self.assertIsNone(working.fd)
        self.assertEqual([event["text"] for event in self.records()[1:]], ["recorded"])

    def test_events_from_two_threads_are_whole_lines_with_distinct_numbers(self):
        journal = self.journal()
        threads = [threading.Thread(target=lambda name=name: [journal.emit("message", text=f"{name}-{index}") for index in range(200)])
                   for name in ("a", "b", "c")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        events = self.records()[1:]
        self.assertEqual([event["seq"] for event in events], list(range(1, 601)))


class ReadTests(JournalCase):
    def fill(self, count, text="event"):
        journal = self.journal()
        for index in range(count):
            journal.emit("message", text=f"{text} {index}")
        journal.close()

    def test_nothing_recorded_is_an_empty_page_not_an_error(self):
        page = activity.read(self.path)
        self.assertEqual((page["journal"], page["events"], page["next"], page["reset"]), (None, [], None, False))
        self.assertTrue(activity.read(self.path, "J-0123456789ab:10")["reset"])  # a cursor for a journal that is gone

    def test_pages_follow_each_other_without_gaps_or_repeats(self):
        self.fill(25)
        end = activity.read(self.path, limit=10)
        self.assertEqual([event["seq"] for event in end["events"]], list(range(16, 26)))
        self.assertTrue(end["more_before"])
        seen, before = [], end["start"]
        while True:
            page = activity.read(self.path, before=before, limit=7)
            seen = [event["seq"] for event in page["events"]] + seen
            before = page["start"]
            if not page["more_before"]:
                break
        self.assertEqual(seen, list(range(1, 16)))
        forward, cursor = [], f"{end['journal']}:0"
        while True:
            page = activity.read(self.path, cursor, limit=4)
            forward += [event["seq"] for event in page["events"]]
            cursor = page["next"]
            if not page["more"]:
                break
        self.assertEqual(forward, list(range(1, 26)))
        self.assertEqual(activity.read(self.path, cursor)["events"], [])
        self.fill(2, "later")
        self.assertEqual([event["seq"] for event in activity.read(self.path, cursor)["events"]], [26, 27])

    def test_a_cursor_that_does_not_fit_is_answered_with_the_end_and_reset(self):
        self.fill(6)
        page = activity.read(self.path)
        journal, offset = page["next"].split(":")
        inside = int(page["start"].split(":")[1]) + 5
        for name, cursor in (("another journal", f"J-000000000000:{offset}"), ("beyond the end", f"{journal}:{int(offset) + 99}"),
                             ("inside a line", f"{journal}:{inside}"), ("inside the header", f"{journal}:3"),
                             ("not a cursor", "abc"), ("negative", f"{journal}:-1"), ("a list", ["x"])):
            for mode in ("cursor", "before"):
                with self.subTest(cursor=name, mode=mode):
                    answer = activity.read(self.path, cursor) if mode == "cursor" else activity.read(self.path, before=cursor)
                    self.assertTrue(answer["reset"])
                    self.assertEqual([event["seq"] for event in answer["events"]], [1, 2, 3, 4, 5, 6])
        self.assertFalse(activity.read(self.path, page["next"])["reset"])
        self.assertFalse(activity.read(self.path, f"{journal}:0")["reset"])
        # The file was truncated and written again: the old position is no longer a line start, or is past the end.
        self.file.write_bytes(self.file.read_bytes()[:int(page["start"].split(":")[1])])
        self.assertTrue(activity.read(self.path, page["next"])["reset"])

    def test_a_line_being_written_is_not_read_until_it_is_complete(self):
        self.fill(2)
        cursor = activity.read(self.path)["next"]
        half = b'{"v":1,"seq":3,"at":"2026-10-11T09:00:05.000+00:00","type":"message","text":"half'
        with self.file.open("ab") as stream:
            stream.write(half)
        self.assertEqual((activity.read(self.path, cursor)["events"], activity.read(self.path, cursor)["next"]), ([], cursor))
        self.assertEqual(len(activity.read(self.path)["events"]), 2)
        with self.file.open("ab") as stream:
            stream.write(b' and half"}\n')
        self.assertEqual([event["text"] for event in activity.read(self.path, cursor)["events"]], ["half and half"])

    def test_a_long_journal_is_read_in_bounded_pages(self):
        self.fill(1200, "x" * 300)
        self.assertGreater(self.file.stat().st_size, 400_000)
        with patch.object(activity, "PAGE_BYTES", 50_000):
            end = activity.read(self.path)
            self.assertLessEqual(len(end["events"]), activity.PAGE_EVENTS)
            self.assertEqual(end["events"][-1]["seq"], 1200)
            self.assertTrue(end["more_before"])
            count, cursor, calls = 0, f"{end['journal']}:0", 0
            while True:
                page = activity.read(self.path, cursor)
                count, cursor, calls = count + len(page["events"]), page["next"], calls + 1
                self.assertLessEqual(len(page["events"]), activity.PAGE_EVENTS)
                if not page["more"]:
                    break
            self.assertEqual(count, 1200)
            self.assertGreater(calls, 5)

    def test_a_damaged_first_line_still_gives_one_stable_journal(self):
        self.fill(3)
        data = self.file.read_bytes()
        self.file.write_bytes(b"garbage, not a header\n" + data.split(b"\n", 1)[1])
        first, second = activity.read(self.path), activity.read(self.path)
        self.assertEqual((first["journal"], [event["seq"] for event in first["events"]]), (second["journal"], [1, 2, 3]))
        self.assertFalse(activity.read(self.path, first["next"])["reset"])


class ClaudeStreamTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()).resolve() / "worktree"
        self.out = Recorder()

    def parse(self, data: bytes, role="developer", size=None):
        stream = streams.open_stream("claude", role, self.root, self.out.emit)
        for index in range(0, len(data), size or len(data) or 1):
            stream.feed(data[index:index + (size or len(data))])
        stream.close()
        return stream

    def run_of_a_developer(self):
        target = str(self.root / "src" / "app.py")
        return lines(
            {"type": "system", "subtype": "init", "model": "claude-test", "cwd": str(self.root), "tools": ["Read", "Edit"],
             "apiKeySource": "none", "mcp_servers": [{"name": "private-server"}]},
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "PRIVATE-REASONING step by step", "signature": "sig"},
                {"type": "text", "text": "I will read the file — è così."},
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {"file_path": target}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1",
                                                      "content": [{"type": "text", "text": "FILE-CONTENT print('hello')"}]}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "redacted_thinking", "data": "PRIVATE-REDACTED"},
                {"type": "tool_use", "id": "toolu_2", "name": "Edit", "input": {"file_path": target, "old_string": "a", "new_string": "b"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_2", "content": "ok"}]}},
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "toolu_3", "name": "Write", "input": {"file_path": "/etc/outside", "content": "x"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_3", "is_error": True,
                                                      "content": "Permission denied for /etc/outside"}]}},
            {"type": "stream_event", "event": {"delta": {"type": "thinking_delta", "thinking": "PRIVATE-DELTA"}}},
            {"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 10, "retry_delay_ms": 2000,
             "error_status": 529, "error": "overloaded"},
            {"type": "something_new", "payload": "PRIVATE-UNKNOWN"},
            {"type": "result", "subtype": "success", "is_error": False, "num_turns": 4, "duration_ms": 1234,
             "result": "## Changes\n\nDone."})

    def test_public_activity_becomes_events_and_reasoning_is_dropped_everywhere(self):
        stream = self.parse(self.run_of_a_developer())
        kinds = [(event["type"], event.get("status") or event.get("change")) for event in self.out.events]
        self.assertEqual(kinds, [("status", "session"), ("message", None), ("tool", "started"), ("tool", "completed"),
                                 ("tool", "started"), ("tool", "completed"), ("file", "edited"), ("tool", "started"),
                                 ("tool", "failed"), ("status", "retry"), ("status", "result")])
        self.assertEqual(self.out.of("message")[0]["text"], "I will read the file — è così.")
        read, done = self.out.of("tool")[0], self.out.of("tool")[1]
        self.assertEqual((read["name"], read["summary"], read["id"]), ("Read", "src/app.py", "toolu_1"))  # relative to the worktree
        self.assertEqual((done["id"], done["size"], "output" in done), ("toolu_1", 27, False))  # file content is not recorded
        self.assertEqual(self.out.of("file"), [{"type": "file", "path": "src/app.py", "change": "edited"}])
        failed = self.out.of("tool")[-1]
        self.assertEqual((failed["status"], failed["output"]), ("failed", "Permission denied for /etc/outside"))
        self.assertEqual(self.out.of("tool")[-2]["summary"], "/etc/outside")  # outside the worktree: shown as it is
        self.assertEqual(self.out.of("status")[1], {"type": "status", "status": "retry", "attempt": 1, "max_retries": 10,
                                                   "retry_delay_ms": 2000, "http_status": 529, "reason": "overloaded"})
        everything = json.dumps(self.out.events) + stream.transcript.text()
        for private in ("PRIVATE-REASONING", "PRIVATE-REDACTED", "PRIVATE-DELTA", "PRIVATE-UNKNOWN", "private-server", "sig"):
            self.assertNotIn(private, everything)
        self.assertIn("I will read the file", stream.transcript.text())
        self.assertIn("something_new", stream.transcript.text())  # named, not shown
        self.assertEqual((stream.result["result"], stream.errors), ("## Changes\n\nDone.", []))

    def test_any_fragmentation_gives_the_same_events(self):
        data = self.run_of_a_developer()
        whole = self.parse(data)
        expected, self.out.events = self.out.events, []
        for size in (1, 3, 17, 4096):
            with self.subTest(size=size):
                stream = self.parse(data, size=size)
                self.assertEqual(self.out.events, expected)
                self.assertEqual(stream.result, whole.result)
                self.out.events = []

    def test_a_cut_stream_keeps_what_was_complete_and_has_no_result(self):
        data = self.run_of_a_developer()
        stream = self.parse(data[:data.index(b'"toolu_2"') + 4])  # the process died in the middle of a line
        self.assertEqual([event["type"] for event in self.out.events], ["status", "message", "tool", "tool"])
        self.assertIsNone(stream.result)
        self.assertEqual(stream.recognized, 3)

    def test_an_error_result_is_reported_as_an_error(self):
        stream = self.parse(lines({"type": "result", "subtype": "success", "is_error": True, "result": "Usage limit reached"}))
        self.assertEqual(stream.errors, ["Usage limit reached"])
        self.assertEqual(self.out.of("error"), [{"type": "error", "text": "Usage limit reached"}])

    def test_a_reviewers_answer_is_never_shown_as_a_verdict(self):
        verdict = {"verdict": "APPROVED", "summary": "fine", "issues": []}
        data = lines(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Checking the diff against the criteria."}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "This looks APPROVED to me."}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "```json\n" + json.dumps(verdict) + "\n```"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "StructuredOutput", "input": verdict}]}},
            {"type": "result", "is_error": False, "structured_output": verdict, "result": ""})
        self.parse(data, role="reviewer")
        messages = self.out.of("message")
        self.assertEqual([message.get("withheld", False) for message in messages], [False, True, True])
        self.assertEqual(messages[1]["text"], streams.WITHHELD)
        self.assertEqual(self.out.of("tool")[0]["summary"], "")
        self.assertNotIn("APPROVED", json.dumps(self.out.events))
        self.assertNotIn("verdict", json.dumps(self.out.events).replace("validated", ""))
        # The same words from a developer are ordinary text.
        self.out.events = []
        self.parse(lines({"type": "assistant", "message": {"content": [{"type": "text", "text": "The enum has APPROVED and BLOCKED."}]}}))
        self.assertEqual(self.out.of("message")[0]["text"], "The enum has APPROVED and BLOCKED.")

    def test_unexpected_shapes_and_plain_text_do_not_stop_the_stream(self):
        data = (b"Warning: plain text from the CLI\n" + b'{"type": "assistant", "message": "not an object"}\n'
                + b'{"type": "assistant", "message": {"content": [17, {"type": "tool_use", "input": "text"}]}}\n'
                + b'{"type": "user", "message": {"content": [{"type": "tool_result"}]}}\n' + b"[1, 2]\n" + b'{"broken": \n'
                + lines({"type": "assistant", "message": {"content": [{"type": "text", "text": "still here"}]}}))
        stream = self.parse(data)
        self.assertEqual(self.out.of("message"), [{"type": "message", "text": "still here"}])
        self.assertIn("Warning: plain text from the CLI", stream.transcript.text())

    def test_an_emitter_that_fails_is_contained_by_the_line_it_failed_on(self):
        calls = []

        def emit(kind, /, **fields):
            calls.append(kind)
            if len(calls) == 2:
                raise OSError("disk full")

        stream = streams.open_stream("claude", "developer", self.root, emit)
        stream.feed(self.run_of_a_developer())
        stream.close()
        self.assertGreater(len(calls), 6)
        self.assertEqual(stream.result["result"], "## Changes\n\nDone.")


class CodexStreamTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()).resolve() / "worktree"
        self.out = Recorder()

    def parse(self, data: bytes, role="developer", size=None):
        stream = streams.open_stream("codex", role, self.root, self.out.emit)
        for index in range(0, len(data), size or len(data) or 1):
            stream.feed(data[index:index + (size or len(data))])
        stream.close()
        return stream

    def run_of_a_developer(self):
        return lines(
            {"type": "thread.started", "thread_id": "0199-thread"},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"id": "item_0", "type": "reasoning", "text": "PRIVATE-REASONING summary"}},
            {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc 'ls'",
                                              "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
            {"type": "item.updated", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc 'ls'",
                                              "aggregated_output": "README", "exit_code": None, "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "bash -lc 'ls'",
                                                "aggregated_output": "x" * 5000 + "README.md\n", "exit_code": 0, "status": "completed"}},
            {"type": "item.started", "item": {"id": "item_2", "type": "todo_list", "items": [
                {"text": "Write the file", "completed": True}, {"text": "Run the tests", "completed": False}]}},
            {"type": "item.completed", "item": {"id": "item_3", "type": "file_change", "status": "completed", "changes": [
                {"path": str(self.root / "feature.txt"), "kind": "add"}, {"path": "docs/old.md", "kind": "delete"}]}},
            {"type": "item.started", "item": {"id": "item_4", "type": "command_execution", "command": "pytest -q",
                                              "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "item_4", "type": "command_execution", "command": "pytest -q",
                                                "aggregated_output": "1 failed", "exit_code": 1, "status": "failed"}},
            {"type": "item.completed", "item": {"id": "item_5", "type": "mcp_tool_call", "server": "docs", "tool": "search",
                                                "arguments": {"q": "PRIVATE-ARGUMENT"}, "result": {"content": ["PRIVATE-RESULT"]},
                                                "error": None, "status": "completed"}},
            {"type": "item.completed", "item": {"id": "item_6", "type": "web_search", "query": "python tempfile", "action": {}}},
            {"type": "item.completed", "item": {"id": "item_7", "type": "error", "message": "A hook was skipped"}},
            {"type": "item.completed", "item": {"id": "item_8", "type": "new_kind", "secret": "PRIVATE-UNKNOWN"}},
            {"type": "item.completed", "item": {"id": "item_9", "type": "agent_message", "text": "Created feature.txt."}},
            {"type": "turn.completed", "usage": {"input_tokens": 1200, "cached_input_tokens": 100, "output_tokens": 80,
                                                 "reasoning_output_tokens": 40}})

    def test_items_become_events_and_reasoning_is_dropped_everywhere(self):
        stream = self.parse(self.run_of_a_developer())
        kinds = [(event["type"], event.get("status") or event.get("change")) for event in self.out.events]
        self.assertEqual(kinds, [("status", "session"), ("tool", "started"), ("tool", "completed"), ("status", "plan"),
                                 ("file", "add"), ("file", "delete"), ("tool", "started"), ("tool", "failed"),
                                 ("tool", "completed"), ("tool", "completed"), ("error", None), ("message", None),
                                 ("status", "turn_completed")])
        started, done = self.out.of("tool")[0], self.out.of("tool")[1]
        self.assertEqual((started["name"], started["kind"], started["summary"], started["id"]), ("shell", "command", "bash -lc 'ls'", "item_1"))
        self.assertEqual((done["exit_code"], len(done["output"]), done["output"].endswith("README.md\n")), (0, streams.OUTPUT_TAIL, True))
        self.assertEqual([(event["path"], event["change"]) for event in self.out.of("file")], [("feature.txt", "add"), ("docs/old.md", "delete")])
        self.assertEqual((self.out.of("tool")[3]["status"], self.out.of("tool")[3]["exit_code"]), ("failed", 1))
        self.assertEqual((self.out.of("tool")[4]["name"], self.out.of("tool")[5]["summary"]), ("docs.search", "python tempfile"))
        plan = self.out.of("status")[1]
        self.assertEqual((plan["done"], plan["total"], plan["items"][1]), (1, 2, {"text": "Run the tests", "completed": False}))
        self.assertEqual(self.out.of("status")[-1], {"type": "status", "status": "turn_completed", "input_tokens": 1200, "output_tokens": 80})
        self.assertEqual(stream.errors, [])  # an error item does not end the run
        everything = json.dumps(self.out.events) + stream.transcript.text()
        for private in ("PRIVATE-REASONING", "PRIVATE-ARGUMENT", "PRIVATE-RESULT", "PRIVATE-UNKNOWN", "0199-thread-not-present"):
            self.assertNotIn(private, everything)
        self.assertIn("Created feature.txt.", stream.transcript.text())

    def test_any_fragmentation_gives_the_same_events(self):
        data = self.run_of_a_developer()
        self.parse(data)
        expected, self.out.events = self.out.events, []
        for size in (1, 5, 64, 1000):
            with self.subTest(size=size):
                self.parse(data, size=size)
                self.assertEqual(self.out.events, expected)
                self.out.events = []

    def test_failures_reported_by_the_cli_are_kept_for_classification(self):
        message = "You've hit your usage limit. Try again at 3:45 PM."
        stream = self.parse(lines({"type": "error", "message": message}, {"type": "turn.failed", "error": {"message": message}},
                                  {"type": "turn.failed", "error": {"message": "stream disconnected"}}))
        self.assertEqual(stream.errors, [message, "stream disconnected"])
        self.assertEqual([event["text"] for event in self.out.of("error")], [message, "stream disconnected"])  # the repeat is shown once

    def test_a_reviewers_answer_is_never_shown_as_a_verdict(self):
        answer = json.dumps({"verdict": "APPROVED", "summary": "ok", "issues": []})
        self.parse(lines({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Reading the handoff."}},
                         {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": answer}}), role="reviewer")
        self.assertEqual([message.get("withheld", False) for message in self.out.of("message")], [False, True])
        self.assertNotIn("APPROVED", json.dumps(self.out.events))

    def test_a_very_large_stream_is_read_in_bounded_memory(self):
        stream = streams.open_stream("codex", "developer", self.root, self.out.emit)
        item = lines({"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "cat big",
                                                         "aggregated_output": "y" * 20000, "exit_code": 0, "status": "completed"}})
        for _ in range(600):  # about 12 MB
            stream.feed(item)
        stream.feed(b'{"type":"item.completed","item":{"id":"huge","type":"agent_message","text":"' + b"z" * 3_000_000 + b'"}}\n')
        stream.close()
        self.assertEqual(len(self.out.of("tool")), 600)
        self.assertLess(len(stream.transcript.text()), 30_000)
        self.assertEqual(stream.reader.oversized, 1)
        self.assertEqual(self.out.events[-1]["code"], "oversized_lines")
        self.assertEqual(self.out.of("message"), [])


class SwitchTests(unittest.TestCase):
    def test_refusals_of_the_streaming_options_are_told_from_other_failures(self):
        refusals = (("claude", "error: unknown option '--verbose'"),
                    ("claude", "error: option '--output-format <format>' argument 'stream-json' is invalid. Allowed choices are text, json."),
                    ("codex", "error: unexpected argument '--json' found\n\n  tip: to pass '--json' as a value, use '-- --json'"))
        for provider, text in refusals:
            with self.subTest(text=text[:40]):
                self.assertTrue(streams.refused(provider, text))
        others = (("claude", "error: unknown option '--restricted'"), ("claude", "Usage limit reached"),
                  ("codex", "error: unexpected argument '--output-schema' found"), ("codex", "Not logged in"),
                  ("claude", "error: --json-schema is not a valid JSON Schema"), ("codex", ""))
        for provider, text in others:
            with self.subTest(text=text[:40]):
                self.assertFalse(streams.refused(provider, text))

    def test_the_environment_switch(self):
        for value, expected in (("0", False), ("false", False), ("OFF", False), ("no", False), ("1", True), ("", True), ("yes", True)):
            with self.subTest(value=value), patch.dict(os.environ, {streams.SWITCH: value}):
                self.assertEqual(streams.enabled(), expected)
        with patch.dict(os.environ):
            os.environ.pop(streams.SWITCH, None)
            self.assertTrue(streams.enabled())


if __name__ == "__main__":
    unittest.main()
