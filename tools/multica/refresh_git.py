"""Exact, clean two-parent merge verification and narrowly scoped Git I/O.

The caller must hold the refresh reservation, validate live grant/evidence and
serialize cooperating writers. A normal push is not server-side compare-and-swap.
This module never updates master as part of candidate publication or deletes refs.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable, Iterator

from .candidate_refresh import PreparedCandidate, RefreshRequest, parse_request


_ORIGIN = "https://github.com/codeExploreHub/Eventra.git"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError("refresh Git: " + reason)


def _sha(value: object) -> str:
    _require(type(value) is str and _SHA.fullmatch(value) is not None, "invalid commit identity")
    return value


def _ref(value: object) -> str:
    _require(type(value) is str and len(value) <= 320 and value.startswith("refs/heads/"), "invalid ref")
    suffix = value.removeprefix("refs/heads/")
    _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", suffix) is not None and ".." not in suffix,
             "invalid ref")
    _require(all(part and not part.startswith(".") and not part.endswith((".", ".lock"))
                 for part in suffix.split("/")), "invalid ref")
    return value


def _checked(request: RefreshRequest) -> dict:
    _require(type(request) is RefreshRequest, "invalid request")
    parsed = parse_request({"payload": request.payload(), "digest": request.digest,
                            "staging_ref": request.staging_ref})
    _require(parsed == request, "invalid request")
    return parsed.payload()


def _subprocess(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


class RefreshGit:
    def __init__(self, repository_dir: Path, *, run: Callable = _subprocess):
        self.repository_dir = Path(repository_dir).resolve(strict=True)
        self._transport = run

    def _call(self, directory: Path, args: list[str]) -> subprocess.CompletedProcess:
        # Never inherit alternate object dirs, injected config, replace refs or trace files.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_SYSTEM=os.devnull,
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1",
                   GIT_TERMINAL_PROMPT="0", GIT_ALLOW_PROTOCOL="https")
        argv = ["git", "--no-replace-objects", "-c", "core.hooksPath=" + os.devnull,
                "-c", "core.attributesFile=" + os.devnull, "-c", "core.fsmonitor=false",
                "-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential",
                *args]
        try:
            return self._transport(argv, cwd=directory, env=env)
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            # Do not expose raw stderr (credentials, URLs or private config can occur there).
            raise RuntimeError("refresh Git: operation unavailable or timed out") from None

    def _git(self, directory: Path, *args: str) -> str:
        result = self._call(directory, list(args))
        _require(result.returncode == 0, "command failed")
        return result.stdout.strip()

    @contextmanager
    def _objects(self) -> Iterator[Path]:
        """Use only objects from the source; never its index, attributes or local config."""
        source = self.repository_dir
        _require(self._git(source, "rev-parse", "--is-shallow-repository") == "false", "shallow repository")
        _require(not self._git(source, "for-each-ref", "--format=%(refname)", "refs/replace/"), "replace refs present")
        common = Path(self._git(source, "rev-parse", "--path-format=absolute", "--git-common-dir"))
        objects = (common / "objects").resolve(strict=True)
        _require(not any(char in str(objects) for char in "\r\n"), "invalid object directory")
        with tempfile.TemporaryDirectory(prefix="eventra-refresh-objects-") as directory:
            root = Path(directory)
            self._git(root, "init", "--bare", "--template=", str(root))
            # The alternate is created only inside our disposable verifier, not the source.
            (root / "objects/info/alternates").write_text(str(objects) + "\n", encoding="utf-8")
            yield root

    def _commit(self, root: Path, sha: str) -> tuple[str, list[str]]:
        _sha(sha)
        _require(self._git(root, "cat-file", "-t", sha) == "commit", "object is not a commit")
        header = self._git(root, "cat-file", "commit", sha).split("\n\n", 1)[0].splitlines()
        trees = [line[5:] for line in header if line.startswith("tree ")]
        parents = [line[7:] for line in header if line.startswith("parent ")]
        _require(len(trees) == 1, "invalid commit tree")
        _sha(trees[0])
        for parent in parents:
            _sha(parent)
        return trees[0], parents

    def _attributes(self, root: Path, commit: str) -> None:
        listing = self._git(root, "ls-tree", "-r", "-z", commit)
        for record in listing.split("\0"):
            if not record:
                continue
            meta, filename = record.split("\t", 1)
            if filename.rsplit("/", 1)[-1] != ".gitattributes":
                continue
            mode, kind, oid = meta.split()
            _require(mode in {"100644", "100755"} and kind == "blob", "nonregular attributes")
            content = self._git(root, "cat-file", "blob", oid)
            for line in content.splitlines():
                if line.lstrip().startswith("#"):
                    continue
                for driver in re.findall(r"(?:^|\s)merge=([^\s]+)", line):
                    _require(driver in {"text", "binary", "union"}, "untrusted merge driver")

    def _expected(self, root: Path, source: str, prerequisite: str, version: str) -> str:
        self._commit(root, source)
        self._commit(root, prerequisite)
        _require(self._git(root, "version") == version, "Git version drift")
        ancestry = self._call(root, ["merge-base", "--is-ancestor", prerequisite, source])
        _require(ancestry.returncode == 1, "prerequisite already included or ancestry unavailable")
        bases = self._git(root, "merge-base", "--all", source, prerequisite).splitlines()
        _require(bool(bases), "missing common ancestor")
        for commit in (source, prerequisite, *bases):
            self._attributes(root, commit)
        merged = self._call(root, ["merge-tree", "--write-tree", source, prerequisite])
        _require(merged.returncode == 0, "merge conflict or computation failure")
        return _sha(merged.stdout.strip())

    def expected_tree(self, source: str, prerequisite: str, git_version: str) -> str:
        _sha(source)
        _sha(prerequisite)
        with self._objects() as root:
            return self._expected(root, source, prerequisite, git_version)

    def _read_ref(self, root: Path, ref: str) -> str | None:
        _ref(ref)
        result = self._call(root, ["ls-remote", "--exit-code", "--refs", _ORIGIN, ref])
        if result.returncode == 2 and not result.stdout.strip():
            return None
        _require(result.returncode == 0, "cannot read remote ref")
        lines = result.stdout.strip().splitlines()
        _require(len(lines) == 1 and lines[0].split("\t")[1:] == [ref], "ambiguous remote ref")
        return _sha(lines[0].split("\t")[0])

    def read_ref(self, ref: str) -> str | None:
        _ref(ref)  # Reject malicious input before even consulting Git.
        with self._objects() as root:
            return self._read_ref(root, ref)

    def _verify(self, root: Path, payload: dict, target: str) -> str:
        tree, parents = self._commit(root, target)
        source = payload["source"]["sha"]
        prerequisite = payload["prerequisite"]["merge_sha"]
        _require(parents == [source, prerequisite], "candidate parents mismatch")
        _require(tree == self._expected(root, source, prerequisite, payload["git_version"]), "candidate tree mismatch")
        return tree

    def _remote_candidate(self, root: Path, request: RefreshRequest, payload: dict, target: str) -> str:
        _require(self._read_ref(root, request.staging_ref) == target, "staging ref mismatch")
        self._git(root, "fetch", "--no-tags", "--no-write-fetch-head", _ORIGIN, request.staging_ref)
        _require(self._read_ref(root, request.staging_ref) == target, "staging ref changed during fetch")
        return self._verify(root, payload, target)

    def verify_candidate(self, request: RefreshRequest, target_sha: str) -> str:
        payload = _checked(request)
        _sha(target_sha)
        with self._objects() as root:
            return self._remote_candidate(root, request, payload, target_sha)

    def _push(self, root: Path, target: str, ref: str) -> None:
        try:
            # Do not retry blindly: success, rejection and lost ACK all require a fresh read.
            self._call(root, ["push", _ORIGIN, target + ":" + ref])
        except RuntimeError:
            pass
        _require(self._read_ref(root, ref) == target, "publication not confirmed; preserve reservation")

    def publish_staging(self, request: RefreshRequest, target_sha: str) -> bool:
        payload = _checked(request)
        _sha(target_sha)
        with self._objects() as root:
            self._verify(root, payload, target_sha)
            existing = self._read_ref(root, request.staging_ref)
            _require(existing in (None, target_sha), "staging ref conflict")
            if existing == target_sha:
                return False
            self._push(root, target_sha, request.staging_ref)
            return True

    def publish_candidate(self, request: RefreshRequest, prepared: PreparedCandidate) -> bool:
        """Publish a registered, independently validated candidate; not an authorization API."""
        payload = _checked(request)
        _require(type(prepared) is PreparedCandidate, "invalid prepared candidate")
        _require(prepared.request_digest == request.digest and prepared.staging_ref == request.staging_ref
                 and prepared.source_sha == payload["source"]["sha"]
                 and prepared.prerequisite_sha == payload["prerequisite"]["merge_sha"], "prepared identity mismatch")
        _sha(prepared.target_sha)
        with self._objects() as root:
            tree = self._remote_candidate(root, request, payload, prepared.target_sha)
            _require(tree == prepared.tree_sha, "prepared tree mismatch")
            base_ref = "refs/heads/" + payload["pr"]["base_ref"]
            _require(self._read_ref(root, base_ref) == payload["prerequisite"]["base_sha"], "base drift")
            head_ref = "refs/heads/" + payload["pr"]["head_ref"]
            head = self._read_ref(root, head_ref)
            _require(head in (prepared.source_sha, prepared.target_sha), "managed head drift")
            if head == prepared.target_sha:
                return False
            self._push(root, prepared.target_sha, head_ref)
            _require(self._read_ref(root, base_ref) == payload["prerequisite"]["base_sha"], "base drift after publication")
            return True
