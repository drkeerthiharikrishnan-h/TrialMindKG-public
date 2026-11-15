# Agents working on TrialMindKG

Use `bin/agent-cli` first: prefer it over re-reading files or guessing commands (`bin/agent-cli --help`,
`bin/agent-cli start` at the start of a session, `bin/agent-cli <subcommand> --help`; add `--json` for JSON).
Continuity: `.claude/continuity.yaml` lists every memory file, skill, agent, hook, mod and connector and the start/end
sequences; `bin/agent-cli handoff` shows the last session's snapshot, `bin/agent-cli end` writes the next one.
The rules, layout and workflow are in CLAUDE.md and .claude/skills/trialmindkg/SKILL.md; the ordered to-do list
in docs/NEXT_STEPS.md; the one live plan is docs/MASTER_PLAN.md.

Two agents share this repo since 2026-11-02: Claude Code (cloud) and Claude Science (owner's Mac, has the data).
Roles, working rules and the work packets to Nov 17: docs/MASTER_PLAN.md (sections 5, 6, 9). Git main is the only channel between them.
