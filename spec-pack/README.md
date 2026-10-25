# Yashoda SpiceChain AI — Spec Pack

Full title: **Yashoda SpiceChain AI: An AI-Enabled Spice Manufacturing, Distribution and E-Commerce Management Platform**

This pack matches your project report (web-first, 6 roles, 24-week plan, synthetic data, human-approved AI).

## Files and what each is for

| File | Purpose | Who reads it |
|---|---|---|
| `prd.md` | What we build and why: requirements with IDs, scope, acceptance criteria | Everyone, and the HOD |
| `architecture.md` | How the system is structured: layers, modules, data flow, deployment | Everyone, and AI coding tools |
| `design.md` | Design system for Figma / Google Stitch: colours, type, components, states | UI/UX owner |
| `ui-pages.md` | Every page to design, by role, plus ready Stitch prompts | UI/UX owner |
| `rules.md` | Hard rules the AI coding tool must follow | AI coding tools (read first) |
| `memory.md` | Decisions, glossary, current status. Updated after every session | AI coding tools and team |
| `task.md` | Milestone task list with owners and "done" checks | Team |
| `implementation.md` | Build guide: schema, state machines, FEFO, AI pipelines, prompt templates | Developers and AI tools |
| `project-documents-checklist.md` | Every document, test artifact and professional practice to prepare | Team |

## How to use with Codex / Claude Code / Antigravity
1. Create the repo and put these files in `/docs`.
2. Add a root file (`AGENTS.md` or `CLAUDE.md`) containing one line: "Read docs/rules.md and docs/memory.md before any change."
3. Give the tool ONE task from `task.md` at a time, and attach the matching section of `implementation.md`.
4. After each session, add what changed to `memory.md`.
5. Review every change yourself before merging. You must be able to explain it in the viva.

## Suggested ownership (matches report Chapter 12)
- Member 1: Frontend and UX
- Member 2: Backend, database and security
- Member 3: AI/ML
- Member 4: Integration, QA, deployment and documentation coordination
Whoever is strongest at design and video should support Member 1 (Figma, Stitch) and own the demo video, PPT and user manual.
