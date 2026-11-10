# rv

A Claude Code skill for report-only code review. It reviews your working tree, a commit, a ref range, or a GitHub PR, then reports High and Medium findings with proposed fixes. It never edits files.

File selection and per-language checklists come from [Open Code Review](https://github.com/alibaba/open-code-review) (OCR) v1.13.6. A stdlib Python script reads OCR's data files directly, so you don't need the `ocr` binary. Claude does the review itself.

## Install

As a plugin:

```
/plugin marketplace add brandon-kaplan/rv-skill
/plugin install rv@rv-skill
```

Run it as `/rv:review`. Claude Code always prefixes plugin skills with the plugin name.

As a plain skill, which gives you `/rv`:

```bash
git clone https://github.com/brandon-kaplan/rv-skill
cp -R rv-skill/skills/review ~/.claude/skills/rv
```

Either way, Claude also picks the skill up when you ask for a report-only review in plain words.

Requires `python3` and `git`. PR reviews also use `gh`.

## Usage

Examples use `/rv`. With the plugin install, type `/rv:review` instead.

```
/rv                                  # staged, unstaged, and untracked changes
/rv --commit abc123                  # one commit
/rv --from origin/main --to HEAD     # a ref range
/rv PR 42                            # a GitHub PR, fetched into a review worktree
/rv --exclude 'docs/**,*.lock'       # skip paths
```

Reviews over 5 files or 1,000 inserted lines fan out to read-only subagents, one per file group, plus one subagent that runs the tests.

## Check the script

```bash
python3 skills/review/ocr_delegate.py selftest
```

## What it doesn't mirror from OCR

- `.opencodereview/rule.json` project and global rule layers. If a repo has one, Claude reads it and applies it by hand.
- The `.m` MATLAB/Objective-C content sniff.
- The token-size gate.

## License

Apache-2.0. `skills/review/rules/` is copied from alibaba/open-code-review v1.13.6 (commit 891df1b) under Apache-2.0. See `skills/review/rules/SOURCE` for how to refresh it.
