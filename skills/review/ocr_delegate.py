#!/usr/bin/env python3
"""Binary-free stand-in for `ocr delegate preview` and `ocr delegate rule`.

Uses OCR's own data files in rules/ (see rules/SOURCE). Stdlib only.

  ocr_delegate.py preview [--commit C | --from A --to B] [--exclude p1,p2]
  ocr_delegate.py rule <path>...
  ocr_delegate.py selftest
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

RULES = Path(__file__).resolve().parent / "rules"


def load(name):
    return json.loads((RULES / name).read_text())


def expand_braces(p):
    m = re.search(r"\{([^}]*)\}", p)
    if not m:
        return [p]
    return [x for opt in m.group(1).split(",") for x in expand_braces(p[: m.start()] + opt + p[m.end():])]


@lru_cache(maxsize=None)
def glob_re(p):
    """doublestar-style glob: ** spans dirs, * and ? stay inside one segment."""
    out, i = "", 0
    while i < len(p):
        if p.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif p.startswith("/**", i) and i + 3 == len(p):
            out, i = out + "(?:/.*)?", i + 3
        elif p.startswith("**", i):
            out, i = out + ".*", i + 2
        elif p[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif p[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(p[i]), i + 1
    return re.compile(out + r"\Z")


def matches(pattern, path):
    path = path.lower()
    return any(glob_re(p.lower()).match(path) for p in expand_braces(pattern))


def ext_of(path):
    base = path.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


# Mirrors providerDirIgnoreDirs in OCR's internal/diff/git.go (case-sensitive, repo-root prefix).
PROVIDER_DIRS = [".idea/", ".vscode/", ".svn/", ".git/", "vendor/", "node_modules/", "target/",
                 ".happypack/", ".cachefile/", "_packages/", "rpm/"]


def is_secret(path):
    base = path.lower().rsplit("/", 1)[-1]
    if base in (".env.example", ".env.sample", ".env.template"):
        pass
    elif base == ".env" or base.startswith(".env."):
        return True
    return any(matches(x, path) for x in load("default_secret_patterns.json"))


def exclude_reason(f, user_excludes):
    # Same order as OCR's selectFiles/whyExcluded. Skips too_large: delegate mode sets no token limit.
    p = f["path"]
    if any(p == d[:-1] or p.startswith(d) for d in PROVIDER_DIRS):
        return "provider_directory"
    if f["binary"]:
        return "binary"
    if is_secret(p) or (f.get("old_path") and is_secret(f["old_path"])):
        return "secret_exclude"
    if any(matches(x, p) for x in user_excludes):
        return "user_exclude"
    ext = ext_of(p)
    if ext and ext not in {e.lower() for e in load("supported_file_types.json")}:
        return "unsupported_ext"
    if any(matches(x, p) for x in load("default_exclude_patterns.json")):
        return "default_path"
    if f["status"] == "deleted":
        return "deleted"
    return ""


def resolve_rule(path):
    sr = load("system_rules.json")
    for pattern, doc in sr["path_rule_map"].items():  # JSON order = OCR first-match order
        if matches(pattern, path):
            return pattern, doc
    return "default", sr["default_rule"]


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def changed_files(diff_args):
    """Return [{path, status, insertions, deletions, binary}] via -z numstat + name-status."""
    statuses, toks = {}, git(*diff_args, "-M", "-z", "--name-status").split("\0")
    names = {"A": "added", "D": "deleted", "M": "modified", "R": "renamed", "C": "copied", "T": "modified"}
    i = 0
    while i < len(toks) and toks[i]:
        code = toks[i][0]
        if code in "RC":
            statuses[toks[i + 2]] = names[code]
            i += 3
        else:
            statuses[toks[i + 1]] = names.get(code, "modified")
            i += 2
    files, toks, i = [], git(*diff_args, "-M", "-z", "--numstat").split("\0"), 0
    while i < len(toks) and toks[i]:
        ins, dels, path = toks[i].split("\t", 2)
        old = None
        if path == "":  # rename: old and new follow as separate tokens
            old, path, i = toks[i + 1], toks[i + 2], i + 3
        else:
            i += 1
        binary = ins == "-"
        files.append({"path": path, "old_path": old, "status": statuses.get(path, "modified"),
                      "insertions": 0 if binary else int(ins), "deletions": 0 if binary else int(dels),
                      "binary": binary})
    return files


def untracked_files():
    out = []
    for path in filter(None, git("ls-files", "--others", "--exclude-standard", "-z").split("\0")):
        data = Path(path).read_bytes()
        binary = b"\0" in data[:8000]
        out.append({"path": path, "status": "added", "binary": binary, "deletions": 0,
                    "insertions": 0 if binary else len(data.splitlines())})
    return out


def workspace_base():
    """HEAD, or the empty tree in a repo with no commits yet (where `git diff HEAD` fails)."""
    if subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], capture_output=True).returncode == 0:
        return "HEAD"
    return subprocess.run(["git", "hash-object", "-t", "tree", "--stdin"], input="",
                          capture_output=True, text=True, check=True).stdout.strip()


def preview(a):
    if a.commit:
        meta = {"mode": "commit", "commit": a.commit}
        # First-parent diff, so merge commits show their changes; --root covers the initial commit.
        has_parent = subprocess.run(["git", "rev-parse", "-q", "--verify", a.commit + "^1"],
                                    capture_output=True).returncode == 0
        files = changed_files(["diff", a.commit + "^1", a.commit] if has_parent
                              else ["diff-tree", "--no-commit-id", "-r", "--root", a.commit])
    elif a.from_ or a.to:
        to = a.to or "HEAD"
        mb = git("merge-base", a.from_, to).strip()
        meta = {"mode": "range", "from": a.from_, "to": to, "merge_base": mb}
        files = changed_files(["diff", mb, to])
    else:
        meta = {"mode": "workspace"}
        files = changed_files(["diff", workspace_base()]) + untracked_files()
    excludes = [x.strip() for x in (a.exclude or "").split(",") if x.strip()]
    for f in files:
        f["exclude_reason"] = exclude_reason(f, excludes)
    keep = [f for f in files if not f["exclude_reason"]]
    drop = [f for f in files if f["exclude_reason"]]
    for f in files:
        del f["binary"]
        f.pop("old_path", None)
        if not f["exclude_reason"]:
            del f["exclude_reason"]
    print(json.dumps({**meta, "repository": git("rev-parse", "--show-toplevel").strip(),
                      "total_files": len(files), "reviewable_count": len(keep), "excluded_count": len(drop),
                      "total_insertions": sum(f["insertions"] for f in files),
                      "total_deletions": sum(f["deletions"] for f in files),
                      "reviewable_files": keep, "excluded_files": drop}, indent=2))


def rule(paths):
    groups = {}
    for p in paths:
        pattern, doc = resolve_rule(p)
        groups.setdefault(doc, (pattern, []))[1].append(p)
    for n, (doc, (pattern, files)) in enumerate(groups.items(), 1):
        if n > 1:
            print("\n---\n")
        print(f"### Rule Group {n}: system / {pattern}\n\nApplies to:")
        print("\n".join(f"- {f}" for f in files))
        print("\n#### Content\n")
        print((RULES / "rule_docs" / doc).read_text().rstrip("\n"))


def selftest():
    assert matches("**/*.{ts,js,tsx,jsx}", "a/b/c.tsx")
    assert matches("**/*.go", "main.go")  # **/ matches zero dirs
    assert not matches(".github/**/*.{yaml,yml}", "x/.github/a.yml")
    assert matches(".github/workflows/**/*.{yaml,yml}", ".github/workflows/ci.yml")
    assert matches("**/__tests__/**", "src/__tests__/a.ts")
    assert matches("**/*.R", "pkg/analysis.r")  # case-insensitive, like OCR
    assert not matches("**/*.go", "a/b.gox")
    assert resolve_rule(".github/workflows/ci.yml")[1] == "github_workflows.md"
    assert resolve_rule(".github/dependabot.yml")[1] == "github_config.md"
    assert resolve_rule("conf/app.yaml")[1] == "yaml.md"
    assert resolve_rule("Makefile")[1] == "default.md"
    f = lambda p, s="modified", b=False: exclude_reason({"path": p, "status": s, "binary": b}, ["docs/**"])
    assert f("img.png", b=True) == "binary"
    assert f("docs/x.py") == "user_exclude"
    assert f("uv.lock") == "unsupported_ext"
    assert f("pkg/foo_test.py") == "default_path"
    assert f("pkg/foo.py", "deleted") == "deleted"
    assert f("pkg/foo.py") == ""
    assert f("Dockerfile") == ""  # no extension is reviewable
    assert f("pkg/test_foo.py") == "default_path"
    assert f("vendor/lib/a.go") == "provider_directory"
    assert f("app/.env.prod") == "secret_exclude"
    assert f(".env.example") == "unsupported_ext"  # template exception, then normal gates
    assert f("home/.ssh/config") == "secret_exclude"
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:  # repo with no commits: staged file must still show up
        os.chdir(d)
        try:
            git("init", "-q")
            Path("a.py").write_text("x\n")
            git("add", "a.py")
            got = changed_files(["diff", workspace_base()])
            assert [(x["path"], x["status"], x["insertions"]) for x in got] == [("a.py", "added", 1)], got
        finally:
            os.chdir(cwd)
    print("selftest ok")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("preview")
    p.add_argument("-c", "--commit")
    p.add_argument("--from", dest="from_")
    p.add_argument("--to")
    p.add_argument("--exclude")
    r = sub.add_parser("rule")
    r.add_argument("paths", nargs="+")
    sub.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "preview":
        if a.to and not a.from_:
            sys.exit("--to requires --from")
        preview(a)
    elif a.cmd == "rule":
        rule(a.paths)
    else:
        selftest()


if __name__ == "__main__":
    main()
