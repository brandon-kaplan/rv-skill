---
description: Report-only code review of the working tree, a commit, a ref range, or a GitHub PR, using Open Code Review's per-language rule checklists. Use when the user runs /rv or /rv:review, or asks for a review that reports findings without editing files. Never applies fixes.
---

Run an Open Code Review (OCR) style review in delegation mode, without the `ocr` binary.
`ocr_delegate.py` (stdlib Python 3, in this skill's folder) reproduces `ocr delegate preview`
and `ocr delegate rule` from OCR v1.13.6's own data files in `rules/` (see `rules/SOURCE`).
It picks the files and the rules; you perform the review.

In the commands below, `<skill-dir>` is the base directory Claude Code printed when this skill loaded.

## Workflow

### Step 1: Preview

```bash
python3 <skill-dir>/ocr_delegate.py preview [user-args]
```

- Default (no user arguments): workspace mode (staged + unstaged + untracked).
- If the user provides `--commit` or `-c`: pass through as-is.
- If the user provides `--from` and `--to`: pass through as-is.
- `--exclude 'glob1,glob2'` passes through as-is.
- If the user names a PR instead of refs, fetch it first, then pass the merge base and
  head as `--from`/`--to`:
  ```bash
  git fetch origin <base>
  git fetch origin pull/<N>/head:refs/heads/review/pr-<N> --force
  git merge-base origin/<base> refs/heads/review/pr-<N>
  ```
  Check out that ref into a worktree (EnterWorktree, or `git worktree add`) so you can
  read PR files for context with normal tools. The worktree is for inspection only, never for
  applying fixes. The primary checkout stays untouched.
- If the user gives business context (`--background`/`-b`), keep it in mind for the review.
  The script takes no background flag. In commit mode, read the commit message with `git log -1 <commit>`.

This prints JSON with mode/ref metadata (`merge_base` in range mode), `reviewable_files`,
and `excluded_files` with an `exclude_reason`. Use those refs verbatim in Step 3.

Not mirrored from OCR: project or global `.opencodereview/rule.json` layers and include rules,
the `.m` MATLAB/Objective-C content sniff, and the token-size gate. If a repo has
`.opencodereview/rule.json`, read it and apply its rules by hand.

### Step 2: Get rules

Pass all reviewable file paths to get their review checklists:

```bash
python3 <skill-dir>/ocr_delegate.py rule <path1> <path2> ...
```

### Step 3: Get diffs and review

For each reviewable file, get its diff using git (based on mode/ref from Step 1):
- Range: `git diff <merge_base>..<to> -- <path>`
- Commit: `git show <commit> -- <path>`
- Workspace: `git diff HEAD -- <path>` (read untracked files directly, and in a repo with no commits yet, read every file directly)

**Large reviews (>5 files or >1,000 insertions):** dispatch one read-only subagent per file group (source, tests, config/YAML) with this step's rules, the Step 2 rule text, and the merge-base/head refs, plus one subagent to run the test suites. Include the Step 4 report-only constraint in every subagent prompt: no subagent edits the worktree. Merge findings in Step 4. Count only insertions in the Step 1 reviewable file list toward the threshold; files the preview excludes don't count. If no subagent tool exists in this session, review inline and run the test suites yourself.

Then review for correctness, security, performance, error handling, concurrency, and maintainability. Comment only on changed code (+ lines).

**Gather evidence beyond the diff.** The OCR rules limit what you flag from the text. They don't limit what evidence you collect. Before ranking a finding, check these where they apply:
- **Infrastructure as code** (Terraform, Pulumi, CloudFormation, Helm, k8s manifests): if CI publishes a plan or dry-run, fetch it and treat it as the primary evidence for destroy, replace, and state findings. The diff shows intent; the plan shows effect. Report every environment the plan doesn't cover as unverified.
- **Ownership handoffs:** when the PR removes something because a counterpart PR or repo now owns it, read the counterpart. Check whether it creates its own copy or adopts the existing resource, whether it has merged, and whether it has applied or deployed. If access blocks a check, name the check that's still outstanding.
- **Upstream data invariants:** when a finding depends on what values an upstream table can hold (status combinations, nullability, key uniqueness), find the producing service (`gh repo list <org> --limit 400 --json name --jq '.[].name' | grep -i <name>`), shallow-clone it to scratch, and read its model constraints, migrations, and every writer of the column. A finding whose only evidence is "the consumer doesn't guard against it" ranks no higher than Low until the producer is checked.

### Step 4: Report

Classify each issue by severity:

- **High**: Obvious bugs, security issues, data loss, or clear mistakes with precise fix proposals
- **Medium**: Reasonable concerns, performance suggestions, or fixes requiring manual work
- **Low**: Discard silently (likely false positives, nitpicks, or insufficient context)

Report High and Medium findings with a proposed fix for each. **Never edit or write any file.** Suggest fixes and stop after reporting, even if a fix looks safe and well-defined. Wait for the user to ask before changing anything.
