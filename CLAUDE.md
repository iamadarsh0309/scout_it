# Project instructions for Claude

This is `scout_it` — an AI football scouting platform. `ProjectPlan.md` is the full original vision; `ARCHITECTURE.md` tracks actual build progress against it (HLD + master todo list); `docs/LLD.md` is the file-level code architecture.

## Always update ARCHITECTURE.md after every commit

After creating any git commit in this repo (whether you made it directly or via a subagent), check whether `ARCHITECTURE.md`'s master todo list still reflects reality:

- Mark newly-completed items `[x]`, add new items discovered during the work (bugs found, new modules built, new debts incurred), and update `[~]` in-progress items if their state changed.
- If the commit changed how major modules depend on each other (new adapter, new pipeline stage, restructured imports), update `docs/LLD.md`'s dependency graph and file-responsibilities section too — this one doesn't need touching for every commit, only when the file-level architecture actually changed.
- Keep the HLD diagram in `ARCHITECTURE.md` in sync with reality — if a "planned" (dashed) component becomes real, move it to "built" (solid) styling in the Mermaid diagram, don't just leave it stale.
- This update can go in the same commit as the work, or a small immediate follow-up commit — don't let it drift more than one commit behind.
- If a commit is purely trivial (typo fix, formatting) and genuinely changes nothing tracked in either doc, it's fine to skip — use judgment, the goal is the docs staying trustworthy, not mechanical busywork on every commit.
