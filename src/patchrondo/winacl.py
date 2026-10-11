"""Windows access control for the directories agents read: inspect, grant narrowly, restore.

A reviewer started by Codex on Windows runs under a restricted token. That
token reads a file where the access control list names the user, Users,
Authenticated Users or Everyone; the rights an owner has through OWNER RIGHTS
do not count (measured with codex-cli 0.161.0 and `[windows] sandbox =
"unelevated"` on October 11, 2026). Python 3.13 creates `mkdir(mode=0o700)`
directories with a protected list of SYSTEM, Administrators and OWNER RIGHTS
only, so a worktree below such a directory is readable by the user and
unreadable by the reviewer's sandbox.

The policy here is the smallest change measured to fix that:

- `<state>/worktrees` gets one inheritable allow entry for the current user
  with read and execute. Worktrees, and the files agents write later, inherit it.
- A folder PatchRondo created above it (the state directory; `projects` and
  the home of a multi-project home) gets "read attributes" for the current
  user, on that folder only, when neither its own list nor a listable parent
  already lets the sandbox see that the folder exists. Without it the shell
  of the sandbox cannot make the worktree its working directory.
- Nothing else is touched. `tasks/`, `index/` and the configuration files keep
  the list they have, and "read attributes" opens no content and no listing,
  so task state, transcripts and locks stay unreadable from that sandbox.

Only allow entries for the current user are ever added. Entries for other
users or groups are never added, removed or rewritten, a deny entry is never
removed, and nothing here elevates. Every change is preceded by a copy of the
previous list (SDDL) that `restore` can put back.

All Windows calls go through `ctypes`; importing this module on other systems
is harmless and `AVAILABLE` is false there.
"""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat

AVAILABLE = os.name == "nt"

READ_EXECUTE = 0x001200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE: list, read, traverse; no write, no delete
READ_BITS = 0x00000001 | 0x00000020 | 0x00000080  # read data / list, execute / traverse, read attributes
READ_ATTRIBUTES = 0x00000080  # that a folder exists and what kind it is: no content, no listing
LIST = 0x00000001  # list a folder, which also shows the attributes of what is in it
OBJECT_INHERIT, CONTAINER_INHERIT, NO_PROPAGATE, INHERIT_ONLY, INHERITED = 0x01, 0x02, 0x04, 0x08, 0x10
INHERITABLE = OBJECT_INHERIT | CONTAINER_INHERIT
_GENERIC = ((0x80000000, 0x00120089), (0x40000000, 0x00120116), (0x20000000, 0x001200A0), (0x10000000, 0x001F01FF))
_SE_FILE_OBJECT = 1
_OWNER, _DACL = 0x00000001, 0x00000004
_PROTECTED, _UNPROTECTED = 0x80000000, 0x20000000
_SE_DACL_PROTECTED = 0x1000
_TOKEN_QUERY, _TOKEN_USER = 0x0008, 1
_GRANT_ACCESS, _TRUSTEE_IS_SID, _TRUSTEE_IS_USER = 1, 0, 1
# The list Python 3.13 gives a `mkdir(mode=0o700)` directory; used to build fixtures that reproduce it.
OWNER_ONLY_SDDL = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)"
SID = re.compile(r"S-1-\d+(?:-\d+){1,14}")
# Everyone, Authenticated Users, Users: groups the restricted token is also checked against.
BROAD_READERS = ("S-1-1-0", "S-1-5-11", "S-1-5-32-545")
PROJECT = re.compile(r"P-[a-f0-9]{8}")


class AclError(OSError):
    """A Windows security call failed; `winerror` carries its code."""


@dataclass(frozen=True)
class Entry:
    sid: str
    allow: bool | None  # None: an entry type this module does not interpret; it is kept, never relied on
    mask: int
    flags: int

    @property
    def inherited(self) -> bool:
        return bool(self.flags & INHERITED)


@dataclass(frozen=True)
class Acl:
    sddl: str
    protected: bool  # the list does not inherit from the parent directory
    owner: str | None
    entries: tuple[Entry, ...]
    null: bool = False  # no list at all: everyone has full access


class _Trustee(ctypes.Structure):
    _fields_ = [("pMultipleTrustee", ctypes.c_void_p), ("MultipleTrusteeOperation", ctypes.c_int),
                ("TrusteeForm", ctypes.c_int), ("TrusteeType", ctypes.c_int), ("ptstrName", ctypes.c_void_p)]


class _ExplicitAccess(ctypes.Structure):
    _fields_ = [("grfAccessPermissions", ctypes.c_ulong), ("grfAccessMode", ctypes.c_int),
                ("grfInheritance", ctypes.c_ulong), ("Trustee", _Trustee)]


class _AclHeader(ctypes.Structure):
    _fields_ = [("AclRevision", ctypes.c_ubyte), ("Sbz1", ctypes.c_ubyte), ("AclSize", ctypes.c_ushort),
                ("AceCount", ctypes.c_ushort), ("Sbz2", ctypes.c_ushort)]


class _AceHeader(ctypes.Structure):
    _fields_ = [("AceType", ctypes.c_ubyte), ("AceFlags", ctypes.c_ubyte), ("AceSize", ctypes.c_ushort),
                ("Mask", ctypes.c_ulong)]


_libraries = None


def _api():
    """advapi32 and kernel32 with the prototypes used here; loaded on first use, on Windows only."""
    global _libraries
    if _libraries is not None:
        return _libraries
    if not AVAILABLE:
        raise AclError("Windows access control lists are not available on this system")
    void, dword, text = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p
    out = ctypes.POINTER(ctypes.c_void_p)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    prototypes = (
        (advapi.GetNamedSecurityInfoW, (text, ctypes.c_int, dword, out, out, out, out, out), dword),
        (advapi.SetNamedSecurityInfoW, (text, ctypes.c_int, dword, void, void, void, void), dword),
        (advapi.SetEntriesInAclW, (dword, ctypes.POINTER(_ExplicitAccess), void, out), dword),
        (advapi.GetAce, (void, dword, out), ctypes.c_int),
        (advapi.ConvertSidToStringSidW, (void, out), ctypes.c_int),
        (advapi.ConvertStringSidToSidW, (text, out), ctypes.c_int),
        (advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW, (void, dword, dword, out, void), ctypes.c_int),
        (advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW, (text, dword, out, void), ctypes.c_int),
        (advapi.GetSecurityDescriptorDacl, (void, ctypes.POINTER(ctypes.c_int), out, ctypes.POINTER(ctypes.c_int)),
                                           ctypes.c_int),
        (advapi.GetSecurityDescriptorControl, (void, ctypes.POINTER(ctypes.c_ushort), ctypes.POINTER(dword)),
                                              ctypes.c_int),
        (advapi.OpenProcessToken, (void, dword, out), ctypes.c_int),
        (advapi.GetTokenInformation, (void, ctypes.c_int, void, dword, ctypes.POINTER(dword)), ctypes.c_int),
        (kernel.GetCurrentProcess, (), void),
        (kernel.CloseHandle, (void,), ctypes.c_int),
        (kernel.LocalFree, (void,), void),
    )
    for function, arguments, result in prototypes:
        function.argtypes, function.restype = arguments, result
    _libraries = (advapi, kernel)
    return _libraries


def _fail(code: int, action: str, path=None) -> AclError:
    error = AclError(None, f"{action}: {ctypes.FormatError(code).strip()}", None if path is None else str(path), code)
    return error


def _wide(path) -> str:
    """The path as the security calls take it; long paths need the extended prefix."""
    text = os.path.abspath(str(path))
    if len(text) < 240 or text.startswith("\\\\?\\"):
        return text
    return "\\\\?\\UNC\\" + text[2:] if text.startswith("\\\\") else "\\\\?\\" + text


def _sid_text(advapi, kernel, pointer) -> str:
    shown = ctypes.c_void_p()
    if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(shown)):
        raise _fail(ctypes.get_last_error(), "A security identifier could not be read")
    try:
        return ctypes.wstring_at(shown.value)
    finally:
        kernel.LocalFree(shown)


def current_user_sid() -> str:
    """SID of the user this process runs as, from its own token."""
    advapi, kernel = _api()
    token = ctypes.c_void_p()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token)):
        raise _fail(ctypes.get_last_error(), "The process token could not be opened")
    try:
        size = ctypes.c_ulong()
        advapi.GetTokenInformation(token, _TOKEN_USER, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, _TOKEN_USER, buffer, size, ctypes.byref(size)):
            raise _fail(ctypes.get_last_error(), "The user of the process token could not be read")
        # TOKEN_USER starts with the pointer to the SID.
        return _sid_text(advapi, kernel, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
    finally:
        kernel.CloseHandle(token)


def read(path) -> Acl:
    """The access control list of one file or directory. Reads only."""
    advapi, kernel = _api()
    owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    code = advapi.GetNamedSecurityInfoW(_wide(path), _SE_FILE_OBJECT, _OWNER | _DACL, ctypes.byref(owner), None,
                                        ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if code:
        raise _fail(code, "The access control list could not be read", path)
    try:
        control, revision = ctypes.c_ushort(), ctypes.c_ulong()
        if not advapi.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
            raise _fail(ctypes.get_last_error(), "The access control list could not be read", path)
        shown = ctypes.c_void_p()
        if not advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW(descriptor, 1, _DACL, ctypes.byref(shown), None):
            raise _fail(ctypes.get_last_error(), "The access control list could not be read", path)
        try:
            sddl = ctypes.wstring_at(shown.value)
        finally:
            kernel.LocalFree(shown)
        entries = []
        count = ctypes.cast(dacl, ctypes.POINTER(_AclHeader)).contents.AceCount if dacl.value else 0
        for index in range(count):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise _fail(ctypes.get_last_error(), "The access control list could not be read", path)
            header = ctypes.cast(ace, ctypes.POINTER(_AceHeader)).contents
            if header.AceType in (0, 1):  # ACCESS_ALLOWED_ACE, ACCESS_DENIED_ACE: the SID follows the mask
                sid = _sid_text(advapi, kernel, ace.value + ctypes.sizeof(_AceHeader))
                entries.append(Entry(sid, header.AceType == 0, header.Mask, header.AceFlags))
            else:
                entries.append(Entry("", None, header.Mask, header.AceFlags))
        return Acl(sddl, bool(control.value & _SE_DACL_PROTECTED),
                   _sid_text(advapi, kernel, owner) if owner.value else None, tuple(entries), null=not dacl.value)
    finally:
        kernel.LocalFree(descriptor)


def _rights(mask: int) -> int:
    """The mask with generic rights replaced by the file rights they stand for."""
    for generic, specific in _GENERIC:
        if mask & generic:
            mask = (mask & ~generic) | specific
    return mask


def access(acl: Acl, sid: str, *, inheritable: bool = False, rights: int = READ_EXECUTE) -> str:
    """What the list says about the reviewer's sandbox having `rights` on this object for user `sid`.

    "granted" when an allow entry for the user, or for one of `BROAD_READERS`,
    gives them; "denied" when a deny entry for one of them takes them away;
    "missing" otherwise. With `inheritable`, the grant must also pass to files
    and sub-folders created later. For reading, a deny of any part of it counts.
    """
    if acl.null:
        return "granted"
    readers = {sid, *BROAD_READERS}
    refused = READ_BITS if rights == READ_EXECUTE else rights
    applies = inherits = False
    for entry in acl.entries:
        if entry.sid not in readers or entry.allow is None:
            continue
        held = _rights(entry.mask)
        if not entry.allow:
            if held & refused:
                return "denied"
            continue
        if held & rights != rights:
            continue
        if not entry.flags & INHERIT_ONLY:
            applies = True
        if entry.flags & INHERITABLE == INHERITABLE and not entry.flags & NO_PROPAGATE:
            inherits = True
    return "granted" if applies and (inherits or not inheritable) else "missing"


def grant(path, sid: str, *, rights: int, inheritable: bool) -> None:
    """Add an allow entry for `sid` with `rights`, merged into the existing list.

    Existing entries are kept as they are, the protection of the list is not
    changed, and an entry that already gives these rights is not duplicated.
    Only the two masks of the policy can be granted.
    """
    if not SID.fullmatch(sid):
        raise ValueError("Not a security identifier")
    if rights not in (READ_EXECUTE, READ_ATTRIBUTES):
        raise ValueError("Only reading, or reading attributes, can be granted")
    advapi, kernel = _api()
    trustee, dacl, descriptor, merged = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
    if not advapi.ConvertStringSidToSidW(sid, ctypes.byref(trustee)):
        raise _fail(ctypes.get_last_error(), "The security identifier could not be used", path)
    try:
        code = advapi.GetNamedSecurityInfoW(_wide(path), _SE_FILE_OBJECT, _DACL, None, None, ctypes.byref(dacl), None,
                                            ctypes.byref(descriptor))
        if code:
            raise _fail(code, "The access control list could not be read", path)
        try:
            entry = _ExplicitAccess(rights, _GRANT_ACCESS, INHERITABLE if inheritable else 0,
                                    _Trustee(None, 0, _TRUSTEE_IS_SID, _TRUSTEE_IS_USER, trustee))
            code = advapi.SetEntriesInAclW(1, ctypes.byref(entry), dacl, ctypes.byref(merged))
            if code:
                raise _fail(code, "The new access entry could not be prepared", path)
            try:
                # Neither protection flag is passed: the list keeps the inheritance it has.
                code = advapi.SetNamedSecurityInfoW(_wide(path), _SE_FILE_OBJECT, _DACL, None, None, merged, None)
                if code:
                    raise _fail(code, "The access control list could not be changed", path)
            finally:
                kernel.LocalFree(merged)
        finally:
            kernel.LocalFree(descriptor)
    finally:
        kernel.LocalFree(trustee)


def restore(path, sddl: str, protected: bool | None = None) -> None:
    """Put back a list saved by `read(path)`: its `sddl` and whether it was `protected`."""
    advapi, kernel = _api()
    if protected is None:
        protected = bool(re.match(r"D:[A-Z]*P", sddl))
    if not protected:
        # Inherited entries come from the parent again; only the directory's own entries are written.
        def own(ace: re.Match) -> str:
            fields = ace.group(0).split(";")
            return "" if len(fields) > 1 and "ID" in fields[1] else ace.group(0)
        sddl = re.sub(r"\([^()]*\)", own, sddl)
    descriptor, dacl = ctypes.c_void_p(), ctypes.c_void_p()
    present, defaulted = ctypes.c_int(), ctypes.c_int()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise _fail(ctypes.get_last_error(), "The saved access control list could not be read", path)
    try:
        if not advapi.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl),
                                                ctypes.byref(defaulted)) or not present.value:
            raise _fail(ctypes.get_last_error() or 87, "The saved access control list could not be read", path)
        code = advapi.SetNamedSecurityInfoW(_wide(path), _SE_FILE_OBJECT, _DACL | (_PROTECTED if protected else _UNPROTECTED),
                                            None, None, dacl, None)
        if code:
            raise _fail(code, "The access control list could not be restored", path)
    finally:
        kernel.LocalFree(descriptor)


def is_reparse_point(path) -> bool:
    """Whether the path itself is a symbolic link, a junction or another reparse point."""
    attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) or os.path.islink(path)


# --- policy -------------------------------------------------------------------------

ADDED = {READ_EXECUTE: "read+execute, inherited by files and sub-folders", READ_ATTRIBUTES: "read attributes, this folder only"}


def _folders(home: Path, worktree: Path) -> tuple[Path, Path]:
    """The worktrees folder and the worktree, always built from the state directory."""
    container = Path(home) / "worktrees"
    return container, container / Path(worktree).name


def ancestors(home: Path) -> list[Path]:
    """The folders above `worktrees` that PatchRondo itself created, outermost first.

    The state directory; for a project of a home with several projects
    (`<root>/projects/<id>` beside `<root>/projects.json`) also `projects` and
    that root. Whatever is above them belongs to the user and is only read.
    """
    home = Path(home)
    if home.parent.name == "projects" and PROJECT.fullmatch(home.name) and (home.parent.parent / "projects.json").is_file():
        return [home.parent.parent, home.parent, home]
    return [home]


def managed_worktree(home: Path, worktree: Path) -> str | None:
    """Why `worktree` is not a directory this module may change; None when it is.

    The worktree must be directly inside `<home>/worktrees`, and both must be
    real directories, reached from the state directory without a symbolic
    link, junction or other reparse point.
    """
    try:
        container, target = _folders(home, worktree)
        inside = os.path.normcase(os.path.join(os.path.realpath(home), "worktrees"))
        parent = Path(os.path.abspath(worktree)).parent
        # Compared both resolved and as written: a container that is itself a link is reported as one below.
        if inside != os.path.normcase(os.path.realpath(parent)) \
                and os.path.normcase(os.path.abspath(container)) != os.path.normcase(str(parent)):
            return "The worktree is not directly inside the worktrees folder of its project"
        for folder in (container, target):
            if not os.path.lexists(folder):
                return f"{folder.name} does not exist"
            if is_reparse_point(folder):
                return f"{folder.name} is a link or a junction, not a real folder"
            if not folder.is_dir():
                return f"{folder.name} is not a folder"
        expected = os.path.join(inside, os.path.normcase(target.name))
        if os.path.normcase(os.path.realpath(target)) != expected:
            return "The worktree folder resolves to another location"
    except OSError as exc:
        return f"The worktree folders cannot be inspected: {exc}"
    return None


def _survey(home: Path, sid: str, worktree: Path | None = None) -> list[dict]:
    """Every folder the policy covers with what its list grants, outermost first. Reads only.

    An ancestor needs its attributes to be readable, which they are when its
    own list says so or when the folder it is in can be listed. Ancestors that
    are links are not surveyed: nothing is changed through a link.
    """
    def entry(folder: Path, role: str, state: str, acl: Acl) -> dict:
        return {"path": str(folder), "role": role, "access": state, "protected": acl.protected, "sddl": acl.sddl,
                "owner": acl.owner}

    folders = []
    chain = ancestors(home)
    if not any(is_reparse_point(folder) or not folder.is_dir() for folder in chain):
        try:
            listed = access(read(chain[0].parent), sid, rights=LIST) == "granted"
        except OSError:
            listed = False
        for folder in chain:
            acl = read(folder)
            state = access(acl, sid, rights=READ_ATTRIBUTES)
            folders.append(entry(folder, "ancestor", "granted" if state == "missing" and listed else state, acl))
            listed = access(acl, sid, rights=LIST) == "granted"
    container = Path(home) / "worktrees"
    for folder, role in [(container, "container")] + ([(_folders(home, worktree)[1], "worktree")] if worktree else []):
        acl = read(folder)
        folders.append(entry(folder, role, access(acl, sid, inheritable=True), acl))
    return folders


def _apply(folders: list[dict], sid: str, changed: list[dict]) -> None:
    """Grant what `folders` lack, recording each change with the list it replaces before it is made."""
    for folder in folders:
        rights = READ_ATTRIBUTES if folder["role"] == "ancestor" else READ_EXECUTE
        current, state = folder, folder["access"]
        if rights == READ_EXECUTE:
            # Read again: the grant on the worktrees folder reaches a worktree that inherits from it.
            acl = read(folder["path"])
            current, state = {"sddl": acl.sddl, "protected": acl.protected}, access(acl, sid, inheritable=True)
        if state != "missing":
            continue
        changed.append({"path": folder["path"], "role": folder["role"], "previous_sddl": current["sddl"],
                        "previous_protected": current["protected"], "added": f"allow {sid} {ADDED[rights]}"})
        grant(folder["path"], sid, rights=rights, inheritable=rights == READ_EXECUTE)


def prepare(home: Path) -> bool:
    """Make a new state directory ready for worktrees a reviewer's sandbox can read.

    Best effort, for initialization: True when the folders grant what the
    policy asks afterwards, or the system has no access control lists.
    `repair` is the checked form, with rollback.
    """
    if not AVAILABLE:
        return True
    container = Path(home) / "worktrees"
    try:
        if is_reparse_point(container) or not container.is_dir():
            return False
        sid = current_user_sid()
        _apply(_survey(home, sid), sid, [])
        return all(folder["access"] == "granted" for folder in _survey(home, sid))
    except OSError:
        return False


def inspect(home: Path, worktree: Path, sid: str | None = None) -> dict:
    """What the lists of the folders the policy covers say about the reviewer reading the worktree.

    `status` is "granted", "missing" (the allowed repair applies), "denied" (a
    deny entry names the user: a deliberate customization that is left alone),
    "unmanaged" (not a directory this module may change) or "unavailable".
    """
    if not AVAILABLE:
        return {"status": "unavailable", "reason": "Access control lists are checked on Windows only", "folders": []}
    reason = managed_worktree(home, worktree)
    if reason:
        return {"status": "unmanaged", "reason": reason, "folders": []}
    try:
        sid = sid or current_user_sid()
        folders = _survey(home, sid, worktree)
    except OSError as exc:
        return {"status": "unavailable", "reason": str(exc), "folders": []}
    states = {folder["access"] for folder in folders}
    status = "denied" if "denied" in states else "missing" if "missing" in states else "granted"
    return {"status": status, "reason": None, "sid": sid, "folders": folders}


def repair(home: Path, worktree: Path, sid: str | None = None) -> dict:
    """Give the current user what the policy allows: read access to the worktree, attributes of its ancestors.

    Idempotent: a folder whose list already grants it is not written. Each
    change is recorded with the list it replaced; if a later step fails, or the
    result does not verify, the recorded lists are put back. Returns
    {"changed": [...], "restored": bool, "error": str | None, "status": ...}.
    """
    before = inspect(home, worktree, sid)
    result = {"changed": [], "restored": False, "error": None, "status": before["status"]}
    if before["status"] != "missing":
        if before["status"] in {"unmanaged", "unavailable", "denied"}:
            result["error"] = before["reason"] or "A deny entry for the current user is set on these folders"
        return result
    sid = before["sid"]
    try:
        _apply(before["folders"], sid, result["changed"])
        after = inspect(home, worktree, sid)
        if after["status"] != "granted":
            raise AclError(f"The access entries were written but do not verify ({after['status']})")
        result["status"] = "granted"
    except OSError as exc:
        result["error"] = str(exc)
        result["restored"] = rollback(result["changed"])
        result["status"] = inspect(home, worktree, sid)["status"]
    return result


def rollback(changed: list[dict]) -> bool:
    """Put back the lists recorded by `repair`, last change first. True when all were restored."""
    done = True
    for change in reversed(changed):
        try:
            restore(change["path"], change["previous_sddl"], change.get("previous_protected"))
        except OSError:
            done = False
    return done
