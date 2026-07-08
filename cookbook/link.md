# Link the Tool into a Skills Directory

## Context
The tool is cloned wherever the developer keeps their repos; a symlink at
`~/.claude/skills/library` is what makes the `/library` skill discoverable to the
agent. `link` creates, repairs, or repoints that symlink. It is re-runnable and
never deletes real files.

## Steps

### 1. Run link

```bash
<tool-dir>/library link                            # link into ~/.claude/skills (default)
<tool-dir>/library link --dir ~/.pi/agent/skills   # link into another harness's skills dir
```

Add `--json` to reason over the result. The user speaks intent ("link this into pi
too", "fix the library link"); you translate to `--dir` / `--force`.

### 2. Interpret the result

| Found at the link path | Behavior |
|---|---|
| nothing | creates the symlink (parent dirs created as needed) |
| symlink to this clone | no-op (`already-linked`) |
| the clone itself (real dir) | no-op (`in-place`) — no link needed |
| dangling symlink (target gone) | repaired automatically |
| symlink to a *different* copy | refuses; `--force` repoints it |
| a real dir/file that isn't this clone | refuses; the user must move it aside — never delete it for them |

For the wrong-target case, confirm with the user before passing `--force` — two
clones on one machine usually means one is stale, and they should pick which
survives.

### 3. Verify

```bash
<tool-dir>/library doctor
```

Doctor's link-health check reports: missing link (warn), dangling link (error),
wrong target or a second copy (warn). `ls -l ~/.claude/skills/library` shows the
target directly.
