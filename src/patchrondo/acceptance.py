"""Acceptance criteria of a task: what each one needs, who can do it, who can verify it, and whether it is.

A task carries its criteria as structured records in `state.json`, under
`acceptance`, saved in the same atomic write as every checkpoint. For each
criterion this module answers three separate questions:

    classification   what kind of work or check it is (`categories`, `needs`), from fixed rules
                     applied to its text, or from the tags its author gave it
    feasibility      who could do it and who could verify it with the capabilities detected for
                     this task (`executor`, `verifier`, `proof`, `requires`, `feasibility`)
    verification     whether the evidence it needs exists and still counts (`status`, `evidence`)

A criterion that can be automated is not verified by that: `VERIFIED` is
reached only through records of the evidence ledger (`evidence.py`). What the
developer says in its handoff is never evidence, and what the reviewer says
about a criterion counts only for criteria the plan gives it to verify.

The reviewer answers with a verdict about the code and one outcome per
criterion. `settle_review` validates that answer against the plan. An outcome
can only make a criterion ask for more than the rules did (a check by the
user instead of a reading of the code); it never verifies a criterion that
needs a command, an authorization or a person. This is what keeps a missing
tool from sending the task back to a developer who cannot do anything about
it. For a task whose criteria were given when it was created, an approval
has to give every criterion of the reviewer an outcome of its own; only a
task read back from its task.md lets an approval cover what it left out.

Results kept apart from the workflow status (`ready`, `running`, `paused`,
`done`, `blocked`):

    code      PENDING | CHANGES_REQUIRED | APPROVED: the review approved the code, the project's
              commands passed and there are changes. "CODE_APPROVED" is this being APPROVED
    outcome   IN_PROGRESS | AWAITING_VALIDATION | AWAITING_AUTHORIZATION | RELEASE_READY:
              RELEASE_READY needs the code approved and every required criterion verified

No model is asked anything here, and nothing is run.
"""
from __future__ import annotations

from dataclasses import MISSING, asdict, dataclass, field, fields
from enum import StrEnum
import json
import re

from . import capabilities as caps
from .activity import clip, redact
from .storage import now

VERSION = 1
TEXT_LIMIT = 2000   # characters of a criterion kept in the structured record; task.md keeps all of it
NOTE_LIMIT = 600
IMPLICIT = "Complete the specified request"
CRITERION_ID = re.compile(r"AC-\d{2,4}")


class Category(StrEnum):
    CODE = "CODE"                # changes to files: logic, refactoring, documentation
    TEST = "TEST"                # automated tests and their results
    BUILD = "BUILD"              # compiling, packaging, producing artifacts
    INTEGRATION = "INTEGRATION"  # working with an application or an environment outside the project
    EXTERNAL = "EXTERNAL"        # downloads, dependencies, remote resources
    PUBLISHING = "PUBLISHING"    # commit, pull request, distribution
    MANUAL = "MANUAL"            # something only a person can do or judge


class Status(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    AWAITING_MANUAL_VALIDATION = "AWAITING_MANUAL_VALIDATION"
    UNKNOWN = "UNKNOWN"


class Feasibility(StrEnum):
    AUTOMATABLE = "automatable"                  # the agents and the project's allowed commands can do and verify it
    NEEDS_TOOLS = "needs_tools"                  # a program or a command is missing
    NEEDS_AUTHORIZATION = "needs_authorization"  # a decision of the user comes first
    MANUAL = "manual"                            # a check by the user cannot be avoided
    UNKNOWN = "unknown"                          # the kind of check could not be told


class ReviewOutcome(StrEnum):
    SATISFIED = "SATISFIED"
    CODE_CHANGES_REQUIRED = "CODE_CHANGES_REQUIRED"    # the developer has to change the code
    VALIDATION_REQUIRED = "VALIDATION_REQUIRED"        # the code may be right; a check is missing
    CAPABILITY_BLOCKED = "CAPABILITY_BLOCKED"          # the check needs tools nobody in the workflow has
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"            # an operation has to be authorized
    MANUAL_ACTION_REQUIRED = "MANUAL_ACTION_REQUIRED"  # a person has to do something
    NOT_ASSESSED = "NOT_ASSESSED"


class Code(StrEnum):
    PENDING = "PENDING"
    CHANGES_REQUIRED = "CHANGES_REQUIRED"
    APPROVED = "APPROVED"


class Outcome(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_VALIDATION = "AWAITING_VALIDATION"
    AWAITING_AUTHORIZATION = "AWAITING_AUTHORIZATION"
    RELEASE_READY = "RELEASE_READY"


# Outcomes that hand a criterion over to somebody else than the developer.
DEFERRED = frozenset({ReviewOutcome.VALIDATION_REQUIRED, ReviewOutcome.CAPABILITY_BLOCKED, ReviewOutcome.APPROVAL_REQUIRED,
                      ReviewOutcome.MANUAL_ACTION_REQUIRED, ReviewOutcome.NOT_ASSESSED})
# What an issue of a review can be about. Only the first kind is work for the developer, and it is always a change the code
# requires; an observation is a remark about the code that asks for nothing.
OBSERVATION = "OBSERVATION"
ISSUE_KINDS = (ReviewOutcome.CODE_CHANGES_REQUIRED, ReviewOutcome.VALIDATION_REQUIRED, ReviewOutcome.CAPABILITY_BLOCKED,
               ReviewOutcome.APPROVAL_REQUIRED, ReviewOutcome.MANUAL_ACTION_REQUIRED, OBSERVATION)
# Pauses of a task whose code is approved while required criteria are not verified.
PAUSES = {Outcome.AWAITING_VALIDATION: "awaiting_validation", Outcome.AWAITING_AUTHORIZATION: "awaiting_authorization"}

# What a criterion needs, the proof each need asks for, and the kinds of proof a command of the runtime can give.
PROOF = {"edit": "review", "tests": "test", "build": "build", "package": "package", "hash": "manual", "network": "manual",
         "publish": "manual", "external": "manual", "manual": "manual"}
RUNTIME_PROOFS = {"test": "tests", "build": "build", "package": "package"}
CONSENT_NEEDS = frozenset({"network", "publish"})  # operations nobody does without the user's explicit decision
DEFAULT_NEED = {Category.CODE: "edit", Category.TEST: "tests", Category.BUILD: "build", Category.INTEGRATION: "external",
                Category.EXTERNAL: "network", Category.PUBLISHING: "publish", Category.MANUAL: "manual"}
OPERATIONS = {"network": "Downloading or installing from the network", "publish": "Committing, pushing or publishing"}

REVIEW_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "summary", "issues", "criteria"],
    "properties": {
        "verdict": {"type": "string", "enum": ["APPROVED", "CHANGES_REQUESTED", "BLOCKED"]},
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["severity", "path", "description", "kind"],
            "properties": {
                "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "path": {"type": "string"}, "description": {"type": "string"},
                "kind": {"type": "string", "enum": [str(kind) for kind in ISSUE_KINDS]},
            },
        }},
        "criteria": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "outcome", "note"],
            "properties": {
                "id": {"type": "string"},
                "outcome": {"type": "string", "enum": [str(outcome) for outcome in ReviewOutcome]},
                "note": {"type": "string"},
            },
        }},
    },
}


class IncompleteReview(ValueError):
    """An approval that leaves a criterion of the reviewer without an outcome of its own, where every one needs one."""


def requires_change(issue: dict) -> bool:
    """Whether an issue of a review is a change the code needs before it can be approved.

    Its kind decides, whatever its severity: a CODE_CHANGES_REQUIRED issue
    always is one, an observation or what is missing outside the code never
    is. An issue without a kind comes from an answer from before kinds
    existed; only there does a high or critical severity say so.
    """
    if "kind" in issue:
        return issue["kind"] == ReviewOutcome.CODE_CHANGES_REQUIRED
    return issue.get("severity") in {"high", "critical"}

# --- classification ---------------------------------------------------------------------
#
# Fixed rules over the text of a criterion: (category, need, what was recognized, pattern). They read English
# and Italian, the two languages criteria have been written in so far. A rule only says that a criterion
# asks for more than a change to the code; a text that matches nothing is a criterion about the code, which
# the reviewer judges. Wrong guesses are corrected by the author with a tag such as "[manual]" or "[code]".

_MAKE = r"(?:creat\w*|generat\w*|produc\w*|build\w*|assembl\w*|prepar\w*|mak(?:e|es|ing)|crea\w*|genera\w*|produ\w+|costrui\w*|impacchett\w*)"
_ARTIFACT = r"(?:zip|7z|tar(?:ball)?|archiv\w*|installer|wheel|sdist|bundle|artifacts?|artefatt\w*|pacchett\w*)"
_RULES = tuple((category, need, label, re.compile(pattern, re.I)) for category, need, label, pattern in (
    (Category.EXTERNAL, "network", "a download", r"\b(?:download\w*|scaric\w+)\b"),
    (Category.EXTERNAL, "network", "installing dependencies",
     r"\b(?:install\w*|installa\w*)\b[^.\n]{0,60}\b(?:dependenc\w+|packages?|librar\w+|dipendenz\w+|pacchett\w+|librer\w+)\b"),
    (Category.EXTERNAL, "network", "a remote resource",
     r"\b(?:fetch\w*|retriev\w*|recuper\w+)\b[^.\n]{0,60}\b(?:internet|web|remote|remot\w+|url|hugging\s?face|github|registry)\b"),
    (Category.PUBLISHING, "publish", "a commit", r"\bcommit(?:s|ted|ting|tare)?\b"),
    (Category.PUBLISHING, "publish", "a push", r"\bgit\s+push\b|\bpush(?:ed|ing|are)?\s+(?:to|the|su|sul|al)\b"),
    (Category.PUBLISHING, "publish", "a pull request", r"\b(?:pull|merge)\s+requests?\b"),
    # With where it goes, so that a criterion about deployment code or an upload feature stays a criterion about the code.
    (Category.PUBLISHING, "publish", "publishing",
     r"\b(?:publish\w*|pubblic\w+|upload\w*|deploy\w*|distribu\w+|rilasci\w+|releas(?:e|ed|ing))\b\s+(?:[^.\n]{0,40}\s)?"
     r"(?:to|on|onto|su|sul|sulla|sullo)\b"),
    (Category.BUILD, "build", "compiling", r"\bcompil\w+\b"),
    (Category.BUILD, "build", "a build",
     r"\b(?:the|a|clean|release|debug|successful|full)\s+build\b|\bbuilds?\s+(?:pass\w*|succeed\w*|successfully|cleanly|without\s+"
     r"(?:errors?|warnings?))\b|\bbuild\s+(?:the\s+)?(?:project|solution|binar\w+|executable|dll|librar\w+|plugin|image)\b"),
    (Category.BUILD, "build", "a build tool",
     r"\b(?:cmake|msbuild|ninja|makefile|gcc|msvc)\b|(?<![\w+])(?:g\+\+|clang\+\+)(?![\w+])|\bmake\s+(?:all|install|build|clean)\b"
     r"|\b(?:cargo|go|dotnet|gradle|mvn|npm\s+run)\s+build\b"),
    (Category.BUILD, "package", "packaging",
     rf"\b{_MAKE}\b[^.\n]{{0,60}}\b{_ARTIFACT}\b|\b(?:packag(?:e|ed|ing)|bundl(?:e|ed|ing))\s+(?:the|a|it|as|into)\b|\.(?:zip|7z|whl)\b"),
    (Category.BUILD, "hash", "a checksum",
     r"\b(?:sha-?\s?(?:1|256|384|512)|md5|checksums?)\b|\b(?:comput\w+|calculat\w+|calcol\w+|verif\w+)\b[^.\n]{0,30}\bhash(?:es)?\b"),
    (Category.CODE, "edit", "writing tests", r"\b(?:add\w*|writ\w+|creat\w+|aggiung\w+|scriv\w+)\b[^.\n]{0,40}\btests?\b"),
    (Category.TEST, "tests", "passing tests",
     r"\b(?:tests?|test\s+suite|suite|pytest|unittest|specs?)\b[^.\n]{0,50}\b(?:pass\w*|green|succeed\w*|run\w*|passan\w*|superat\w*"
     r"|verd[ei]|eseguit\w*)\b|\b(?:pass\w*|run\w*|esegu\w+|lanci\w+)\b[^.\n]{0,30}\b(?:tests?|test\s+suite|pytest|unittest)\b"),
    (Category.TEST, "tests", "a test runner",
     r"\b(?:pytest|unittest|jest|vitest|mocha|ctest|phpunit|rspec|tox|nox)\b|\b(?:npm|cargo|go|dotnet|mvn|gradle)\s+test\b"),
    (Category.TEST, "tests", "regressions", r"\bno\s+regressions?\b|\bregression\s+tests?\b|\btest\s+coverage\b"),
    (Category.INTEGRATION, "external", "an external application",
     r"\b(?:mod\s+organizer(?:\s*2)?|mo2|vortex|skyrim|in[- ]game|unity\s+editor|unreal\s+editor|android\s+studio|xcode)\b"),
    (Category.INTEGRATION, "external", "a real run",
     r"\b(?:real|actual|live|reale|reali|effettiv\w+)\s+(?:inference|inferenza|device|dispositiv\w+|hardware|gpu|environment|ambiente"
     r"|server|database|data|dati|run|esecuzione)\b|\b(?:inferenza|inference|esecuzione)\s+(?:reale|real)\b"),
    (Category.INTEGRATION, "external", "a target environment",
     r"\b(?:in|on|su|in)\s+(?:production|staging|produzione)\b|\b(?:works?|runs?|funziona\w*|gira)\s+(?:on|in|inside|within|under|su"
     r"|dentro|sotto)\s+(?:windows|linux|macos|android|ios|docker|kubernetes|wsl)\b"),
    (Category.INTEGRATION, "external", "running inside another program",
     r"\b(?:inside|within|dentro|all'interno\s+di)\b[^.\n]{0,40}\b(?:app\w*|program\w*|editor|manager|launcher|client|game|gioco)\b"),
    (Category.MANUAL, "manual", "a check by a person",
     r"\b(?:manual(?:ly)?|by\s+hand|manualmente|a\s+mano|visual(?:ly)?\s+(?:check\w*|inspect\w*|verif\w+)|sign[- ]?off|user\s+acceptance"
     r"|uat|approved\s+by|approvat\w+\s+da)\b"),
))
# Wording that asks for a check PatchRondo cannot place: nothing in the text says who could do it.
_AMBIGUOUS = tuple((label, re.compile(pattern, re.I)) for label, pattern in (
    ("a licence", r"\b(?:licen[cs]\w*|licenz\w+|copyright)\b"),
    ("rules of a platform", r"\b(?:rules?|regole|polic(?:y|ies)|guidelines?|linee\s+guida|terms)\b[^.\n]{0,30}\b(?:nexus|store|marketplace"
                            r"|platform|piattaforma|service|servizio)\b|\b(?:nexus|store|marketplace|platform|piattaforma)\s+(?:rules?"
                            r"|regole|polic\w+|guidelines?)\b"),
    ("compliance", r"\b(?:complian\w+|gdpr|legal\s+review|conformit\w+)\b"),
))
# Case matters for this one: "PR" is a pull request, "pr" is part of many words.
_PULL_REQUEST = re.compile(r"(?<![A-Za-z])PRs?(?![A-Za-z])")
_TOOLCHAINS = tuple((name, re.compile(pattern, re.I)) for name, pattern in (
    ("cpp", r"c\+\+|\bcpp\b|\.cpp\b|\bcmake\b|\bmsvc\b|g\+\+|\bclang|\bgcc\b"),
    ("rust", r"\brust\b|\bcargo\b"), ("go", r"\bgolang\b|\bgo\s+build\b"),
    ("dotnet", r"\bdotnet\b|\.net\b|c#|\bmsbuild\b|\.sln\b|\.csproj\b"),
    ("java", r"\bjava\b|\bmaven\b|\bmvn\b|\bgradle\b"),
    ("node", r"\bnpm\b|\bnode(?:\.js)?\b|\btypescript\b|\btsc\b"),
))
_TAGS = re.compile(r"\s*((?:\[[^\]\n]{1,40}\]\s*)+)")
_OPTIONAL = re.compile(r"\((?:optional|opzionale|facoltativ[oa])\)", re.I)
_TAG_WORDS = {str(category).lower(): category for category in Category}


@dataclass(frozen=True)
class Classification:
    categories: tuple[str, ...]
    needs: tuple[str, ...]
    toolchains: tuple[str, ...]
    source: str            # "user" (tags or the form), "rules", "default" (nothing but the code is asked), "unclassified"
    signals: tuple[str, ...]
    note: str


def _tags(text: str) -> tuple[list[str], bool | None]:
    """Categories and the required flag given as leading tags, as in "[manual][optional] Tried in the game"."""
    match = _TAGS.match(text)
    if not match:
        return [], None
    words = [word for word in re.split(r"[\[\]\s,/+]+", match.group(1).lower()) if word]
    if not words or any(word not in _TAG_WORDS and word not in {"optional", "required"} for word in words):
        return [], None  # brackets that are not tags stay part of the text
    required = False if "optional" in words else True if "required" in words else None
    return list(dict.fromkeys(str(_TAG_WORDS[word]) for word in words if word in _TAG_WORDS)), required


def classify(text: str, categories=None) -> Classification:
    """What a criterion asks for. `categories` given by its author decide; otherwise the rules do."""
    tagged, _ = _tags(text)
    chosen = list(categories) if categories else tagged
    found: dict[str, list[str]] = {}
    signals: list[str] = []
    for category, need, label, pattern in _RULES:
        if pattern.search(text):
            found.setdefault(str(category), [])
            if need not in found[str(category)]:
                found[str(category)].append(need)
            signals.append(label)
    if _PULL_REQUEST.search(text):
        found.setdefault(str(Category.PUBLISHING), ["publish"])
        signals.append("a pull request")
    toolchains = tuple(name for name, pattern in _TOOLCHAINS if pattern.search(text))
    if chosen:
        # The author's categories stand; the rules only say what each of them needs.
        needs = [need for category in chosen for need in found.get(category) or [DEFAULT_NEED[Category(category)]]]
        build = str(Category.BUILD) in chosen
        return Classification(tuple(chosen), tuple(dict.fromkeys(needs)), toolchains if build else (), "user", tuple(signals),
                              "Set by the author of the task")
    if found:
        ordered = [str(category) for category in Category if str(category) in found]
        needs = [need for category in ordered for need in found[category]]
        return Classification(tuple(ordered), tuple(dict.fromkeys(needs)), toolchains if str(Category.BUILD) in found else (),
                              "rules", tuple(dict.fromkeys(signals)), f"It mentions {', '.join(dict.fromkeys(signals))}")
    unclear = [label for label, pattern in _AMBIGUOUS if pattern.search(text)]
    if unclear:
        return Classification((), (), (), "unclassified", tuple(unclear),
                              f"It mentions {', '.join(unclear)}: whether that is met cannot be established from the code alone, "
                              "and nothing in the text says who can check it")
    return Classification((str(Category.CODE),), ("edit",), (), "default", (),
                          "Nothing in it asks for more than a change to the project's files")


# --- criteria ---------------------------------------------------------------------------

@dataclass
class Criterion:
    id: str
    text: str
    required: bool = True
    categories: list[str] = field(default_factory=list)   # empty: the kind of check could not be told
    needs: list[str] = field(default_factory=list)
    toolchains: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    classification: dict = field(default_factory=dict)    # {"source", "signals", "note"}
    # Planned against the capabilities detected for the task.
    executor: str = "developer"                           # who does the work: developer | runtime | user
    verifier: str = "reviewer"                            # who gives the proof: reviewer | runtime | user
    proof: list[str] = field(default_factory=list)        # kinds of evidence it needs: review, test, build, package, manual
    requires: list[dict] = field(default_factory=list)    # [{"executor", "capability", "state", "source"}]
    feasibility: str = Feasibility.UNKNOWN
    authorization_required: bool = False
    # Verified against the evidence ledger.
    status: str = Status.PENDING
    reason: str = ""
    evidence: list[str] = field(default_factory=list)     # records that count for it now
    missing: list[str] = field(default_factory=list)      # kinds of proof it still lacks
    authorization: dict | None = None                     # the user's last decision: {"decision", "at", "evidence"}
    review: dict | None = None                            # the reviewer's last outcome: {"outcome", "note", "iteration"}

    @classmethod
    def from_dict(cls, raw) -> "Criterion":
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not CRITERION_ID.fullmatch(raw["id"]) \
                or not isinstance(raw.get("text"), str):
            raise ValueError("A saved criterion has no valid id and text")
        values = {}
        for item in fields(cls):
            if item.name not in raw:
                continue  # a key this version does not know is left out, a key it lacks takes its default
            value = raw[item.name]
            default = item.default_factory() if item.default_factory is not MISSING else item.default
            kind = next((kind for kind in (bool, str, list, dict) if isinstance(default, kind)), str if default is MISSING else None)
            fits = (value is None or isinstance(value, dict)) if kind is None else type(value) is bool if kind is bool \
                else isinstance(value, kind)
            if not fits:
                raise ValueError(f"Criterion {raw['id']}: {item.name} has the wrong type")
            values[item.name] = value
        criterion = cls(**values)
        if not _within(criterion.categories, Category) or not _within([criterion.status], Status) \
                or not _within(criterion.proof + criterion.missing, PROOF.values()) or not _within(criterion.needs, PROOF) \
                or not _within([criterion.feasibility], Feasibility) or not _within(criterion.depends_on + criterion.evidence, None):
            raise ValueError(f"Criterion {raw['id']}: unknown category, need, proof or status")
        return criterion


def _within(values, allowed) -> bool:
    """Whether every value is text, and one of `allowed` when that is given."""
    names = None if allowed is None else {str(item) for item in allowed}
    return all(isinstance(value, str) and (names is None or value in names) for value in values)


def parse_items(raw) -> list[dict]:
    """Criteria as they arrive from the command line or the interface: text, or {"text", "required", "categories", "depends_on"}."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("acceptance must be a list of criteria")
    items = []
    for entry in raw:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) \
                or set(entry) - {"text", "required", "categories", "depends_on"}:
            raise ValueError("A criterion is text, or an object with text, required, categories and depends_on")
        text = entry["text"].strip()
        if not text:
            continue
        required, categories, depends = entry.get("required"), entry.get("categories"), entry.get("depends_on") or []
        if required is not None and type(required) is not bool:
            raise ValueError("required must be a JSON boolean (true/false)")
        if categories is not None and (not isinstance(categories, list) or not _within(categories, Category)):
            raise ValueError(f"categories may only contain: {', '.join(Category)}")
        if not isinstance(depends, list) or any(not isinstance(item, str) or not CRITERION_ID.fullmatch(item) for item in depends):
            raise ValueError("depends_on must list criterion IDs such as AC-01")
        items.append({"text": text, "required": required, "categories": list(dict.fromkeys(categories or [])) or None,
                      "depends_on": list(dict.fromkeys(depends))})
    return items


def from_task_text(document: str) -> list[dict]:
    """The criteria written in a task.md, for a task created before they were tracked."""
    section = re.search(r"^## Acceptance criteria\s*\n(.*?)(?=^## |\Z)", document, flags=re.M | re.S)
    lines = [line[2:].strip() for line in (section.group(1) if section else "").splitlines() if line.startswith("- ")]
    return parse_items([line for line in lines if line and line != IMPLICIT])


def _criteria(items: list[dict]) -> list[Criterion]:
    if not items:
        # A task without criteria still has one thing to get right: what its description asks for.
        return [Criterion(id="AC-01", text=IMPLICIT, categories=[str(Category.CODE)], needs=["edit"],
                          classification={"source": "implicit", "signals": [], "note": "The task names no criterion of its own"})]
    criteria = []
    for number, item in enumerate(items, 1):
        _, flag = _tags(item["text"])
        found = classify(item["text"], item.get("categories"))
        required = item["required"] if item.get("required") is not None else flag if flag is not None \
            else not _OPTIONAL.search(item["text"])
        criteria.append(Criterion(
            id=f"AC-{number:02d}", text=clip(item["text"], TEXT_LIMIT), required=required, categories=list(found.categories),
            needs=list(found.needs), toolchains=list(found.toolchains), depends_on=list(item.get("depends_on") or []),
            classification={"source": found.source, "signals": list(found.signals), "note": found.note}))
    known = {criterion.id for criterion in criteria}
    for criterion in criteria:
        if criterion.id in criterion.depends_on or set(criterion.depends_on) - known:
            raise ValueError(f"{criterion.id} depends on a criterion that does not exist, or on itself")
    _ordered(criteria)  # refuses a cycle
    return criteria


def _ordered(criteria: list[Criterion]) -> list[Criterion]:
    """Criteria with every one after those it depends on."""
    by_id, done, order = {criterion.id: criterion for criterion in criteria}, set(), []

    def visit(criterion: Criterion, trail: tuple) -> None:
        if criterion.id in done:
            return
        if criterion.id in trail:
            raise ValueError(f"Criteria depend on each other in a circle: {', '.join((*trail, criterion.id))}")
        for other in criterion.depends_on:
            if other in by_id:
                visit(by_id[other], (*trail, criterion.id))
        done.add(criterion.id)
        order.append(criterion)

    for criterion in criteria:
        visit(criterion, ())
    return order


# --- evidence as proof -------------------------------------------------------------------

class _Proofs:
    """What the ledger proves now. Only records that were read intact and not invalidated since count.

    `implicit` lets an approval of the code as a whole cover a criterion the
    reviewer did not list: only for a task read back from its task.md.
    """

    def __init__(self, ledger, implicit: bool = False):
        self.ledger = ledger
        self.implicit = implicit
        self.review = ledger.last("review")
        commands = [record for record in ledger.records if record.get("kind") == "command"]
        last = commands[-1].get("run") if commands else None
        # The commands of the most recent run of the project's commands, in the order they ran.
        self.run = [record for record in commands if record.get("run") == last] if last else []

    def _passed(self, record: dict) -> bool:
        return self.ledger.counts(record) and record.get("result") == "passed"

    def _manual(self, criterion: str) -> dict | None:
        return self.ledger.last("manual", criterion)

    def proves(self, proof: str, criterion: str) -> list[str]:
        """IDs of the records that give this kind of proof for a criterion; empty when it is not proved."""
        if proof == "review":
            review = self.review
            if not self.ledger.counts(review):
                return []
            outcomes = review.get("outcomes") or {}
            # A criterion the reviewer did not list is not judged, unless the task keeps the answers from before outcomes.
            covered = self.implicit and review.get("verdict") == "APPROVED"
            judged = outcomes.get(criterion, ReviewOutcome.SATISFIED if covered else None)
            return [review["id"]] if judged == ReviewOutcome.SATISFIED else []
        if proof == "test":
            whole = bool(self.run) and len(self.run) == self.run[0].get("of") and all(self._passed(record) for record in self.run)
            return [record["id"] for record in self.run] if whole else []
        if proof in RUNTIME_PROOFS:
            ran = [record["id"] for record in self.run if proof in (record.get("covers") or []) and self._passed(record)]
            if ran:
                return ran
        manual = self._manual(criterion)
        return [manual["id"]] if manual and self._passed(manual) else []

    def failure(self, proof: str, criterion: str) -> str | None:
        """Why this kind of proof failed, when the last attempt at it did."""
        if proof in RUNTIME_PROOFS:
            kind = RUNTIME_PROOFS[proof]
            failed = next((record for record in self.run if self.ledger.counts(record) and record.get("result") != "passed"
                           and (proof == "test" or kind in (record.get("covers") or []))), None)
            if failed:
                how = "timed out" if failed.get("result") == "timeout" else f"failed (exit {failed.get('exit_code')})"
                return f"A command of the project {how}: {caps.shown(failed.get('command') or [])}"
        if proof != "review" and proof != "test":
            manual = self._manual(criterion)
            if manual and self.ledger.counts(manual) and manual.get("result") == "failed":
                return f"Your check did not pass: {manual.get('summary') or 'no details were given'}"
        return None

    def user_failure(self, criterion: str) -> dict | None:
        manual = self._manual(criterion)
        return manual if manual and self.ledger.counts(manual) and manual.get("result") == "failed" else None

    def decision(self, criterion: str) -> dict | None:
        """The user's last decision about the operation of a criterion. It does not depend on the files."""
        record = self.ledger.last("authorization", criterion)
        if not record or not record["trusted"]:
            return None
        return {"decision": record.get("result"), "at": record.get("at"), "evidence": record["id"]}


# --- the acceptance record of a task -----------------------------------------------------

@dataclass
class Acceptance:
    criteria: list[Criterion]
    capabilities: dict | None = None
    code: dict = field(default_factory=lambda: {"status": str(Code.PENDING)})
    outcome: str = Outcome.IN_PROGRESS
    ledger: dict = field(default_factory=lambda: {"count": 0, "head": ""})
    source: str = "task"            # "task": given when the task was created; "task.md": read back for an older task
    planned_at: str = ""
    updated_at: str = ""

    # --- persistence -----------------------------------------------------------------

    @classmethod
    def create(cls, items: list[dict], source: str = "task") -> "Acceptance":
        return cls(criteria=_criteria(items), source=source, planned_at=now(), updated_at=now())

    @classmethod
    def load(cls, state: dict) -> "Acceptance | None":
        """The record saved with a task; None for a task that has none. Raises ValueError when it cannot be used."""
        raw = state.get("acceptance")
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("criteria"), list) \
                or not raw["criteria"]:
            raise ValueError("The acceptance record of the task is not one this version can read")
        criteria = [Criterion.from_dict(item) for item in raw["criteria"]]
        known = {criterion.id for criterion in criteria}
        if len(known) != len(criteria):
            raise ValueError("The acceptance record of the task names a criterion twice")
        if any(set(criterion.depends_on) - known for criterion in criteria):
            raise ValueError("The acceptance record of the task depends on a criterion it does not have")
        _ordered(criteria)  # refuses criteria that wait for each other in a circle
        code = raw.get("code") if isinstance(raw.get("code"), dict) and _within([raw["code"].get("status")], Code) \
            else {"status": str(Code.PENDING)}
        ledger = raw.get("ledger") if isinstance(raw.get("ledger"), dict) else {"count": 0, "head": ""}
        return cls(criteria=criteria, capabilities=raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else None,
                   code=dict(code), outcome=raw.get("outcome") if _within([raw.get("outcome")], Outcome) else Outcome.IN_PROGRESS,
                   ledger=dict(ledger), source=str(raw.get("source") or "task"), planned_at=str(raw.get("planned_at") or ""),
                   updated_at=str(raw.get("updated_at") or ""))

    def dump(self) -> dict:
        return {"version": VERSION, "source": self.source, "planned_at": self.planned_at, "updated_at": self.updated_at,
                "criteria": [asdict(criterion) for criterion in self.criteria], "capabilities": self.capabilities,
                "code": dict(self.code), "outcome": str(self.outcome), "results": self.results(), "ledger": dict(self.ledger),
                "summary": self.summary()}

    def get(self, identifier) -> Criterion:
        found = next((criterion for criterion in self.criteria if criterion.id == identifier), None)
        if found is None:
            raise LookupError(f"Criterion not found: {identifier}")
        return found

    def toolchains(self) -> list[str]:
        return sorted({name for criterion in self.criteria for name in criterion.toolchains})

    @property
    def explicit_outcomes(self) -> bool:
        """Whether an approval has to give every criterion of the reviewer an outcome of its own.

        True for every task whose criteria were given when it was created. A
        task read back from its task.md keeps the answers from before
        outcomes existed: there an approval covers what it did not list.
        """
        return self.source != "task.md"

    def for_reviewer(self) -> list[str]:
        """IDs of the criteria the plan gives to the reviewer: those that need a reading of the code."""
        return [criterion.id for criterion in self.criteria if "review" in {PROOF[need] for need in criterion.needs}]

    def unjudged(self, listed) -> list[str]:
        """Criteria of the reviewer that an approval listing `listed` left without an outcome of their own."""
        return [name for name in self.for_reviewer() if name not in listed] if self.explicit_outcomes else []

    # --- planning ----------------------------------------------------------------------

    def _plan(self, criterion: Criterion) -> None:
        """Who does it, who proves it and what that takes, with the capabilities detected for the task."""
        needs = list(criterion.needs) or ["manual"]  # unclassified: only a person can settle it
        proof = list(dict.fromkeys(PROOF[need] for need in needs))
        outcome = (criterion.review or {}).get("outcome")
        if outcome in DEFERRED:
            # The reviewer handed it over: a check by the user takes the place of the reading of the code.
            proof = [kind for kind in proof if kind != "review"]
            if "manual" not in proof:
                proof.append("manual")
        wanted: list[tuple[str, str]] = []
        for need in needs:
            if need == "edit":
                wanted += [("developer", caps.FILES_WRITE), ("reviewer", caps.FILES_READ)]
            elif need in ("tests", "build", "package"):
                wanted.append(("runtime", caps.COMMAND_KINDS[need]))
                if need == "build":
                    wanted += [("runtime", caps.tool(name)) for name in criterion.toolchains]
            elif need == "hash":
                wanted += [("runtime", caps.HASH), ("user", caps.MANUAL)]
            elif need in CONSENT_NEEDS:
                wanted += [("user", caps.CONSENT), ("user", caps.EXTERNAL)]
            else:
                wanted.append(("user", caps.EXTERNAL if need == "external" else caps.MANUAL))
        if "manual" in proof and not any(executor == "user" for executor, _ in wanted):
            wanted.append(("user", caps.MANUAL))
        criterion.requires = [{"executor": executor, "capability": capability, "label": caps.label(capability),
                               **{key: value for key, value in caps.lookup(self.capabilities, executor, capability).items()
                                  if key in {"state", "source", "consent"}}}
                              for executor, capability in dict.fromkeys(wanted)]
        criterion.proof = proof
        criterion.authorization_required = bool(set(needs) & CONSENT_NEEDS) or outcome == ReviewOutcome.APPROVAL_REQUIRED
        commands = [kind for kind in proof if kind in RUNTIME_PROOFS]
        uncovered = [kind for kind in commands if self._commands(kind)["state"] not in caps.AVAILABLE
                     and self._commands(kind).get("consent") != "missing"]
        # A build or a package that no allowed command produces can still be done and proved by the user. Tests cannot:
        # they are PatchRondo's to run, once the project has commands for them.
        fallback = [kind for kind in uncovered if kind != "test"]
        criterion.executor = "developer" if "review" in proof else "user" if "manual" in proof or fallback else "runtime"
        criterion.verifier = "user" if "manual" in proof or fallback else "runtime" if commands else "reviewer"
        granted = (criterion.authorization or {}).get("decision") == "granted"
        if not criterion.categories:
            feasibility = Feasibility.UNKNOWN
        elif fallback:
            feasibility = Feasibility.NEEDS_TOOLS
        elif (criterion.authorization_required and not granted) or uncovered \
                or any(self._commands(kind).get("consent") == "missing" for kind in commands):
            feasibility = Feasibility.NEEDS_AUTHORIZATION
        elif "manual" in proof:
            feasibility = Feasibility.MANUAL
        else:
            feasibility = Feasibility.AUTOMATABLE
        criterion.feasibility = str(feasibility)

    def _commands(self, proof: str) -> dict:
        return caps.lookup(self.capabilities, "runtime", caps.COMMAND_KINDS[RUNTIME_PROOFS[proof]])

    # --- verification ------------------------------------------------------------------

    def _judge(self, criterion: Criterion, proofs: _Proofs, statuses: dict, developing: bool) -> tuple[str, str, list[str]]:
        held, found = [], []
        for proof in criterion.proof:
            records = proofs.proves(proof, criterion.id)
            found += records
            if not records:
                held.append(proof)
        criterion.missing = list(held)
        decision = criterion.authorization or {}
        granted = decision.get("decision") == "granted"
        if granted:
            found.append(decision["evidence"])
        waiting = [other for other in criterion.depends_on if statuses.get(other) != Status.VERIFIED]
        if not held and not waiting and (granted or not criterion.authorization_required):
            return Status.VERIFIED, "Verified by recorded evidence", found
        if waiting:
            return Status.PENDING, f"It waits for {', '.join(waiting)}", found
        review = criterion.review or {}
        note = f": {review['note']}" if review.get("note") else ""
        if "review" in held and developing:
            return Status.IN_PROGRESS, "The developer is working on it", found
        if "review" in held and review.get("outcome") == ReviewOutcome.CODE_CHANGES_REQUIRED:
            return Status.FAILED, f"The reviewer asked for a change to the code{note}", found
        failure = next((text for proof in held if (text := proofs.failure(proof, criterion.id))), None)
        if failure:
            return Status.FAILED, failure, found
        if criterion.authorization_required and not granted:
            operation = next((OPERATIONS[need] for need in criterion.needs if need in OPERATIONS), "This operation")
            if decision.get("decision") == "denied":
                return Status.AWAITING_AUTHORIZATION, "You did not authorize this operation, so nothing was done and the " \
                                                      "criterion stays open", found
            return Status.AWAITING_AUTHORIZATION, f"{operation} is never done by the agents or by PatchRondo. It needs your " \
                                                  "explicit decision; after that it is yours to do and to prove", found
        for proof in held:
            if proof not in RUNTIME_PROOFS:
                continue
            entry = self._commands(proof)
            if entry["state"] in caps.AVAILABLE:
                continue  # it runs with the project's commands after the next development step
            if entry.get("consent") == "missing":
                return Status.AWAITING_AUTHORIZATION, "The project's commands are configured but not allowed to run. Allow " \
                                                      "them where the task asks for it, or in Settings → Tests", found
            if proof == "test":
                return Status.AWAITING_AUTHORIZATION, "PatchRondo runs tests only with commands you configured and allowed, " \
                                                      "and this project has none", found
            absent = next((item for item in criterion.requires if proof == "build" and item["capability"].startswith("tool.")
                           and item["state"] == caps.ABSENT), None)
            what = "runs a build" if proof == "build" else "creates a package"
            if absent:
                return Status.BLOCKED_CAPABILITY, f"It needs {caps.TOOLCHAIN_LABELS[absent['capability'][5:]]}, and none is " \
                    f"installed on this computer ({absent['source']}). Install one and add the command to the project's " \
                    "commands, or do it elsewhere and attach the evidence", found
            return Status.BLOCKED_CAPABILITY, f"No command you allowed for this project {what}, and an agent's word is not " \
                "proof of it. Add the command in Settings → Tests, or do it yourself and attach the evidence", found
        if "manual" in held:
            if review.get("outcome") == ReviewOutcome.CAPABILITY_BLOCKED:
                return Status.BLOCKED_CAPABILITY, f"The reviewer could not verify it with what this workflow has{note}", found
            if not criterion.categories:
                return Status.UNKNOWN, f"PatchRondo could not tell what kind of check this needs. " \
                                       f"{criterion.classification.get('note') or ''}".strip(), found
            if review.get("outcome") in DEFERRED:
                return Status.AWAITING_MANUAL_VALIDATION, f"The reviewer could not settle it by reading the code{note}", found
            return Status.AWAITING_MANUAL_VALIDATION, "It needs a check that neither the agents nor PatchRondo can do. Do " \
                                                      "it, then attach where its proof can be found", found
        if "review" in held:
            return Status.PENDING, "The reviewer judges it after development and tests", found
        return Status.PENDING, "PatchRondo runs the project's commands after development", found

    def refresh(self, ledger, *, capabilities: dict | None = None, developing: bool = False) -> None:
        """Plan and judge every criterion again from the ledger; nothing is kept from the previous judgement."""
        if capabilities is not None:
            self.capabilities = capabilities
        proofs = _Proofs(ledger, implicit=not self.explicit_outcomes)
        statuses: dict[str, str] = {}
        for criterion in _ordered(self.criteria):
            criterion.authorization = proofs.decision(criterion.id)
            self._plan(criterion)
            status, reason, found = self._judge(criterion, proofs, statuses, developing)
            criterion.status, criterion.reason, criterion.evidence = str(status), reason, list(dict.fromkeys(found))
            statuses[criterion.id] = status
        approval = ledger.get(self.code.get("evidence")) if self.code.get("status") == Code.APPROVED else None
        if self.code.get("status") == Code.APPROVED and not ledger.counts(ledger.get(self.code.get("evidence"))):
            self.code = {"status": str(Code.PENDING), "reason": "The review that approved the code no longer counts: the files "
                                                                "changed after it, or its record cannot be trusted"}
        elif approval is not None and (unjudged := self.unjudged(approval.get("outcomes") or {})):
            # An approval saved before every criterion needed its own outcome: the code is reviewed again.
            self.code = {"status": str(Code.PENDING), "reason": "The review that approved the code gave no outcome of its own "
                                                                f"to {', '.join(unjudged)}"}
        self.outcome = self._outcome()
        self.ledger = {"count": ledger.count, "head": ledger.head}
        self.updated_at = now()

    def _outcome(self) -> str:
        if self.code.get("status") != Code.APPROVED:
            return Outcome.IN_PROGRESS
        open_ = [criterion for criterion in self.criteria if criterion.required and criterion.status != Status.VERIFIED]
        if not open_:
            return Outcome.RELEASE_READY
        if any(criterion.status == Status.AWAITING_AUTHORIZATION for criterion in open_):
            return Outcome.AWAITING_AUTHORIZATION
        return Outcome.AWAITING_VALIDATION

    def results(self) -> list[str]:
        """The logical results that hold now, such as ["CODE_APPROVED", "AWAITING_VALIDATION"]."""
        approved = ["CODE_APPROVED"] if self.code.get("status") == Code.APPROVED else []
        return approved + ([str(self.outcome)] if self.outcome != Outcome.IN_PROGRESS else [])

    # --- the review --------------------------------------------------------------------

    def settle_review(self, review: dict, iteration: int) -> dict:
        """Validate a parsed review against the plan and return it with its verdict about the code settled.

        The outcome the reviewer gave to each criterion is recorded with the
        criterion. A change to the code is asked for a criterion only where a
        change to the files can satisfy it (`for_reviewer`): asked for any
        other, it is listed in `refused_changes` and sends nothing to the
        developer. Two contradictions are resolved from the structured answer,
        never from its prose: an approval that names a change to the code is
        not an approval, and a request for changes that names none the
        developer can make, while every criterion of the developer is
        satisfied or handed over, asks nothing of the developer. An approval
        that leaves a criterion of the reviewer without an outcome, where
        every one needs its own (`explicit_outcomes`), raises
        `IncompleteReview` and records nothing.
        """
        known = {criterion.id for criterion in self.criteria}
        listed: dict[str, dict] = {}
        for entry in review.get("criteria") or []:
            if entry["id"] in known and entry["id"] not in listed:
                listed[entry["id"]] = entry
        settled = {**review, "criteria": list(listed.values())}
        ignored = len(review.get("criteria") or []) - len(listed)
        if ignored:
            settled["ignored_criteria"] = ignored  # unknown or repeated IDs: recorded, never applied
        code_issues = [issue for issue in review["issues"] if issue.get("kind", ReviewOutcome.CODE_CHANGES_REQUIRED)
                       == ReviewOutcome.CODE_CHANGES_REQUIRED]
        mine = self.for_reviewer()
        named = sorted(name for name, entry in listed.items() if entry["outcome"] == ReviewOutcome.CODE_CHANGES_REQUIRED)
        changes = [name for name in named if name in mine]
        refused = [name for name in named if name not in mine]
        if refused:
            settled["refused_changes"] = refused  # no change to the files can satisfy them: recorded, never sent back
        deferred = [name for name, entry in listed.items() if entry["outcome"] in DEFERRED]
        if review["verdict"] == "APPROVED" and changes:
            settled.update(verdict="CHANGES_REQUESTED", reported_verdict="APPROVED", normalized="code_change_named",
                           summary=f"Inconsistent review: it approves, and asks for a change to the code for {', '.join(changes)}. "
                                   + review["summary"])
        elif review["verdict"] == "CHANGES_REQUESTED" and not code_issues and not changes and (deferred or refused) \
                and all(name in listed and (listed[name]["outcome"] == ReviewOutcome.SATISFIED or name in deferred) for name in mine):
            found = [f"what it found open is recorded with the criteria ({', '.join(sorted(deferred))})"] if deferred else []
            found += [f"the change it asked for {', '.join(refused)} is not one a change to the files can make"] if refused else []
            settled.update(verdict="APPROVED", reported_verdict="CHANGES_REQUESTED", normalized="no_code_change_requested",
                           summary="The review asked for no change to the code that the developer can make: "
                                   + "; ".join(found) + ". " + review["summary"])
        unjudged = self.unjudged(listed) if settled["verdict"] == "APPROVED" else []
        if unjudged:
            raise IncompleteReview(f"Incomplete review: it approves the code and gives no outcome to {', '.join(unjudged)}. "
                                   "Every criterion the reviewer verifies needs an outcome of its own")
        for criterion in self.criteria:
            entry = listed.get(criterion.id)
            criterion.review = {"outcome": entry["outcome"], "note": clip(redact(entry.get("note") or ""), NOTE_LIMIT),
                                "iteration": iteration} if entry else None
        return settled

    def developed(self) -> None:
        """After a development step: what the reviewer said about the code was said about other files."""
        for criterion in self.criteria:
            if criterion.review and criterion.review.get("outcome") not in DEFERRED:
                criterion.review = None

    def approve_code(self, evidence_id: str, iteration: int) -> None:
        self.code = {"status": str(Code.APPROVED), "evidence": evidence_id, "iteration": iteration, "at": now()}

    def request_changes(self, iteration: int, reason: str) -> None:
        self.code = {"status": str(Code.CHANGES_REQUIRED), "iteration": iteration, "at": now(), "reason": reason}

    # --- questions the engine asks -----------------------------------------------------

    def open_required(self) -> list[Criterion]:
        return [criterion for criterion in self.criteria if criterion.required and criterion.status != Status.VERIFIED]

    def for_developer(self) -> list[Criterion]:
        """Criteria that still lack a review of the code: the developer's work."""
        return [criterion for criterion in self.criteria if "review" in criterion.missing]

    def awaiting_commands(self) -> list[Criterion]:
        """Required criteria that a new run of the project's commands would prove, and that it may run."""
        return [criterion for criterion in self.open_required() if criterion.status == Status.PENDING
                and any(kind in RUNTIME_PROOFS and self._commands(kind)["state"] in caps.AVAILABLE for kind in criterion.missing)]

    def refused_by_user(self, ledger) -> list[tuple[Criterion, dict]]:
        """Required criteria whose last check by the user failed: a change request from the person."""
        proofs = _Proofs(ledger)
        return [(criterion, record) for criterion in self.open_required()
                if criterion.status == Status.FAILED and (record := proofs.user_failure(criterion.id))]

    def manual_refusal(self, criterion: Criterion) -> str | None:
        """Why a check by the user cannot be recorded for a criterion; None when it can."""
        if "manual" not in criterion.proof and not (set(criterion.proof) & {"build", "package"}):
            who = "the review of the code" if criterion.verifier == "reviewer" else "the project's own commands"
            return f"{criterion.id} is verified by {who}: a statement cannot take its place"
        waiting = [other for other in criterion.depends_on if self.get(other).status != Status.VERIFIED]
        if waiting:
            return f"{criterion.id} waits for {', '.join(waiting)}"
        if criterion.authorization_required and (criterion.authorization or {}).get("decision") != "granted":
            return f"{criterion.id} is about an operation that needs your authorization first"
        return None

    def pending_message(self) -> str:
        """One sentence for a person about why an approved task is not finished."""
        open_ = self.open_required()
        names = ", ".join(criterion.id for criterion in open_[:6]) + ("…" if len(open_) > 6 else "")
        one = len(open_) == 1
        first = "A decision of yours comes first. " if self.outcome == Outcome.AWAITING_AUTHORIZATION else ""
        return (f"The code is approved. {len(open_)} required {'criterion is' if one else 'criteria are'} not verified yet ({names}): "
                f"{'it needs' if one else 'they need'} a check or a decision that the agents cannot give. {first}"
                "Open “Criteria and checks” to see what each needs.")

    # --- summaries ---------------------------------------------------------------------

    def summary(self) -> dict:
        """Counts for the preflight and for progress, and the capabilities that are missing."""
        count = lambda test: sum(1 for criterion in self.criteria if test(criterion))  # noqa: E731
        missing: dict[tuple, dict] = {}
        for criterion in self.criteria:
            if criterion.status == Status.VERIFIED:
                continue
            for item in criterion.requires:
                # A person can always be asked; what is missing is what an agent or PatchRondo would need.
                if item["executor"] != "user" and item["state"] not in caps.AVAILABLE:
                    entry = missing.setdefault((item["executor"], item["capability"]), {**item, "criteria": []})
                    entry["criteria"].append(criterion.id)
        required = [criterion for criterion in self.criteria if criterion.required]
        return {
            "total": len(self.criteria), "required": len(required),
            "verified": count(lambda c: c.status == Status.VERIFIED),
            "required_verified": sum(1 for criterion in required if criterion.status == Status.VERIFIED),
            "failed": count(lambda c: c.status == Status.FAILED),
            "blocked": count(lambda c: c.status == Status.BLOCKED_CAPABILITY),
            "awaiting_authorization": count(lambda c: c.status == Status.AWAITING_AUTHORIZATION),
            "awaiting_manual": count(lambda c: c.status == Status.AWAITING_MANUAL_VALIDATION),
            "unknown": count(lambda c: c.status == Status.UNKNOWN),
            "open": count(lambda c: c.status in (Status.PENDING, Status.IN_PROGRESS)),
            "feasibility": {str(kind): count(lambda c, kind=kind: c.feasibility == kind) for kind in Feasibility},
            "missing": list(missing.values()),
        }

    def compact(self) -> dict:
        """What a list of tasks shows: results and counts, no criterion."""
        counts = self.summary()
        return {"outcome": str(self.outcome), "code": self.code.get("status"), "results": self.results(),
                **{key: counts[key] for key in ("total", "required", "verified", "required_verified", "failed", "blocked",
                                                "awaiting_authorization", "awaiting_manual", "unknown", "open")}}

    # --- what the agents are told ------------------------------------------------------

    def developer_brief(self) -> str:
        """The part of the developer's prompt that says which criteria are its work and which are not."""
        mine, done, others = [], [], []
        for criterion in self.criteria:
            line = f"- {criterion.id} [{'required' if criterion.required else 'optional'}] {criterion.text}"
            if criterion.status == Status.VERIFIED:
                done.append(line)
            elif "review" in criterion.missing:
                note = f" — {criterion.reason}" if criterion.status == Status.FAILED else ""
                mine.append(line + note)
            else:
                who = "the orchestrator checks it with the project's own commands after you finish" \
                    if criterion.verifier == "runtime" else "it needs the user"
                others.append(f"{line} — {who}")
        parts = ["## Acceptance criteria: what is yours",
                 "The orchestrator tracks every criterion, who can satisfy it and who can verify it. A criterion counts only "
                 "with evidence the orchestrator records; saying that it is done is not evidence.",
                 "", "Yours to satisfy by changing files in the worktree:", *(mine or ["- (none left)"])]
        if others:
            parts += ["", "Not yours. Do not attempt them, do not simulate their result and do not work around them; your "
                          "tools are not what they need:", *others]
        if done:
            parts += ["", "Already verified. Keep them true:", *done]
        return "\n".join(parts) + "\n"

    def reviewer_brief(self, ledger) -> str:
        """The part of the reviewer's prompt with the criteria, the capabilities and the evidence, and how to answer."""
        rows = [{"id": criterion.id, "text": criterion.text, "required": criterion.required, "categories": criterion.categories,
                 "done_by": criterion.executor, "verified_by": criterion.verifier, "status": criterion.status,
                 "reason": criterion.reason} for criterion in self.criteria]
        able = []
        for name in caps.EXECUTORS:
            found = (((self.capabilities or {}).get("executors") or {}).get(name) or {}).get("capabilities") or {}
            have = [key for key, entry in found.items() if entry.get("state") in caps.AVAILABLE]
            lack = [key for key, entry in found.items() if entry.get("state") == caps.ABSENT]
            able.append(f"- {name}: can {', '.join(have) or 'nothing known'}; cannot {', '.join(lack) or 'nothing known'}")
        proofs = _Proofs(ledger)
        recorded = [f"- {record['id']} command `{caps.shown(record.get('command') or [])}`: {record.get('result')} "
                    f"(exit {record.get('exit_code')})" for record in proofs.run if ledger.counts(record)]
        recorded += [f"- {record['id']} check by the user for {', '.join(record.get('criteria') or [])}: {record.get('result')}"
                     for record in ledger.records if record.get("kind") == "manual" and ledger.counts(record)]
        return "\n".join([
            "## Acceptance criteria (tracked by the orchestrator)",
            json.dumps(rows, ensure_ascii=False, indent=2)[:12000],
            "", "## Who can do what in this workflow", *able,
            "", "## Evidence recorded so far", *(recorded or ["- (none)"]),
            "", "## How to judge",
            "`verdict` is about the code only: APPROVED when the changes are correct and every criterion the developer can "
            "satisfy by changing files is satisfied.",
            "Give every criterion one entry in `criteria`, with one outcome" + (
                ". An APPROVED verdict that leaves out a criterion you verify is not accepted:" if self.explicit_outcomes else ":"),
            "- SATISFIED: you checked it by reading the worktree and the evidence above.",
            "- CODE_CHANGES_REQUIRED: the developer has to change the code for it. Only for a criterion a change to the files "
            "can satisfy (category CODE); for any other it is recorded and asks nothing of the developer.",
            "- VALIDATION_REQUIRED: the code may be correct, but a check is missing.",
            "- CAPABILITY_BLOCKED: checking it needs tools that nobody in this workflow has.",
            "- APPROVAL_REQUIRED: an operation has to be authorized by the user first.",
            "- MANUAL_ACTION_REQUIRED: a person has to do something.",
            "- NOT_ASSESSED: you could not judge it.",
            "A criterion the developer cannot satisfy with its tools is never a reason for CHANGES_REQUESTED or BLOCKED: give "
            "it its outcome and judge the code on its own. Never answer SATISFIED for something you could not check.",
            "Every entry of `issues` has a `kind`. A CODE_CHANGES_REQUIRED issue is a change the code needs, whatever its "
            "severity: the code is not approved with one, and only these go back to the developer. Use OBSERVATION for a "
            "remark about the code that asks for no change, and the other kinds for what is missing outside the code.",
        ]) + "\n"


def feedback_parts(acceptance: Acceptance | None, review: dict | None) -> tuple[dict | None, list[str]]:
    """A review as the developer should read it: its work only, and separately what is not its work."""
    if not isinstance(review, dict) or acceptance is None:
        return review, []
    mine = {criterion.id for criterion in acceptance.criteria if "review" in criterion.missing}
    changeable = set(acceptance.for_reviewer())  # a change asked for any other criterion is not the developer's work
    code = lambda issue: issue.get("kind", ReviewOutcome.CODE_CHANGES_REQUIRED) == ReviewOutcome.CODE_CHANGES_REQUIRED  # noqa: E731
    shown = {**review, "issues": [issue for issue in review.get("issues") or [] if code(issue)],
             "criteria": [entry for entry in review.get("criteria") or [] if entry["id"] in mine
                          or (entry["outcome"] == ReviewOutcome.CODE_CHANGES_REQUIRED and entry["id"] in changeable)]}
    apart = [f"- {criterion.id} {criterion.text} — {criterion.status}: {criterion.reason}"
             for criterion in acceptance.criteria if criterion.id not in mine and criterion.status != Status.VERIFIED]
    apart += [f"- ({issue['kind']}) {issue['description']}" for issue in review.get("issues") or []
              if not code(issue) and issue["kind"] != OBSERVATION]  # an observation asks for nothing, of anybody
    return shown, apart


def _labelled(detected: dict | None) -> dict | None:
    """Capabilities as they are listed for people: one executor after the other, each capability with its label."""
    if not isinstance(detected, dict):
        return None
    executors = detected.get("executors") if isinstance(detected.get("executors"), dict) else {}
    return {"detected_at": detected.get("detected_at"), "executors": [
        {"name": name, "provider": (executors.get(name) or {}).get("provider"),
         "capabilities": [{"name": key, "label": caps.label(key), **entry}
                          for key, entry in ((executors.get(name) or {}).get("capabilities") or {}).items() if isinstance(entry, dict)]}
        for name in caps.EXECUTORS]}


def view(acceptance: Acceptance | None, ledger, *, pending: list[dict] | None = None) -> dict:
    """Everything the interface and the command line show about the criteria of one task.

    A task created before criteria were tracked has no record: its criteria
    are listed as written (`pending`), with nothing said about them.
    """
    if acceptance is None:
        return {"tracked": False, "criteria": [{"text": item["text"]} for item in pending or []], "problems": list(ledger.problems)}
    proofs = _Proofs(ledger)
    run = {record["id"] for record in proofs.run}
    criteria = []
    for criterion in acceptance.criteria:
        kinds = {RUNTIME_PROOFS[proof] for proof in criterion.proof if proof in RUNTIME_PROOFS}
        records = [record for record in ledger.records if criterion.id in (record.get("criteria") or [])
                   or (record.get("kind") == "command" and record["id"] in run
                       and ("tests" in kinds or kinds & set(record.get("covers") or [])))]
        refusal = acceptance.manual_refusal(criterion)
        open_ = criterion.status != Status.VERIFIED
        criteria.append({**asdict(criterion), "records": [record["id"] for record in records[-12:]],
                         "actions": {"evidence": open_ and refusal is None, "evidence_refusal": refusal if open_ else None,
                                     "authorize": criterion.authorization_required}})
    shown = [{key: value for key, value in record.items() if key not in {"prev", "hash"}} | {"current": ledger.counts(record)}
             for record in ledger.records]
    return {"tracked": True, "source": acceptance.source, "criteria": criteria, "capabilities": _labelled(acceptance.capabilities),
            "code": dict(acceptance.code), "outcome": str(acceptance.outcome), "results": acceptance.results(),
            "summary": acceptance.summary(), "updated_at": acceptance.updated_at, "evidence": shown[-200:],
            "problems": list(ledger.problems)}
