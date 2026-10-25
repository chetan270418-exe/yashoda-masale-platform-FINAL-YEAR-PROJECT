# Team Playbook — How the Four of Us Build This Together

Written as the team leader's guide. Follow it in order. Do not skip Part 1.

## 0. Roles used in this playbook
| Short name | Person | Owns |
|---|---|---|
| **Lead** | Tech lead | Backend, database, security, Docker, CI, deployment, merging |
| **AI** | Smart teammate | Synthetic data, forecasting, RAG, agent tools, AI evaluation |
| **Front** | Frontend teammate | Web pages, API connection, screen states |
| **Design & Docs** | Video/design teammate | Figma, Stitch, tests log, SRS and diagrams, knowledge-base content, demo video, PPT, user manual |

Mapping to `task.md` owners: M1 = Front, M2 = Lead, M3 = AI, M4 = shared (Lead does CI, Docker, deploy; Design & Docs does test cases, bug log, documentation).

## 1. Leader principles
1. One source of truth: the repo (`/docs`) and the task board. Not chat messages.
2. Small steps, shown working every Friday.
3. Nobody is blocked for more than 24 hours. Say it early.
4. Everyone reviews, everyone can explain everything.
5. Core first, AI second, extras last.

---

# PART 1 — SET UP THE TEAM (Days 1–7)

## Day 1 — Kickoff meeting (60–90 minutes, everyone, in person)
Agenda:
1. Read `README.md`, `prd.md` and the report summary together (15 min).
2. Confirm roles from section 0. Each person says what they are strong in and what they want to learn (10 min).
3. Write the **Team Charter** (20 min). Copy this and fill it in:
   ```
   Meeting times: Mon 30 min, Fri 30 min, mock viva 10 min (fixed day/time)
   Daily update: by 8 pm in the group chat (done / doing / blocked)
   Reply time: within 24 hours for messages about tasks
   Decisions: discuss, then Lead decides if no agreement in 10 minutes
   Pull requests: reviewed within 24 hours by someone else
   Absence: tell the group 1 day before; hand over your open tasks
   Respect: no blaming; fix the process, not the person
   ```
4. Book the first sponsor meeting and guide meeting (10 min).
5. Everyone writes down their weekly available hours. Plan with real numbers.
Output: signed (screenshot) team charter saved in `/docs/06-management`.

## Day 1–2 — Create accounts and shared places
Do these in this order. Lead does steps 1–3, others join.
1. **GitHub:** Lead creates a private repo `yashoda-spicechain-ai`. Settings → Collaborators → add the three others with Write access. Everyone turns on two-factor login.
   - If you are students, check if you qualify for GitHub's student offer, which can unlock more features.
2. **Task board:** in the repo open **Projects** → New project → Board. Columns: `Backlog`, `This week`, `In progress`, `In review`, `Done`. Add labels: `backend`, `frontend`, `ai`, `design`, `docs`, `bug`, `blocked`.
3. **Shared drive:** create a Google Drive folder `Yashoda SpiceChain AI` with subfolders `01 Initiation`, `02 Requirements`, `03 Design`, `04 Testing`, `05 Submission`, `06 Management`, `07 Media`.
4. **Chat:** one WhatsApp (or Discord) group. Rules: no keys or passwords in chat, no big files (use Drive).
5. **Figma:** Design & Docs creates a team/project file. Invite everyone with edit access for Design & Docs and view/comment access for others. Check whether a free student/education plan is available for you.
6. **AI tools:** each person signs in to their coding tool (Codex, Claude Code or Antigravity) and Claude/Gemini chat. Agree which tool each person uses. Keys: each person creates their own and keeps them in their own `.env`.
7. **Calendar:** create recurring events for Monday, Friday and mock viva.

## Day 2–3 — Install tools on every laptop
Everyone installs and checks:
- Git (`git --version`)
- Docker Desktop (`docker --version`; on Windows it needs WSL2, follow the installer)
- Python 3.11 or newer (`python --version`)
- Node.js LTS (`node --version`)
- VS Code or your AI editor
- A database viewer (DBeaver or pgAdmin)
- Postman or the browser docs page for APIs
Set Git identity once:
```
git config --global user.name "Your Name"
git config --global user.email "you@example.com"
```

## Day 3–4 — Lead creates the starting repository
Lead does this and pushes to `main`, then everyone clones.
1. Clone: `git clone <repo-url>`
2. Create folders: `docs/`, `backend/`, `frontend/`.
3. Copy the spec pack into `docs/`. Add a root `AGENTS.md` (or `CLAUDE.md`) containing: "Read docs/rules.md and docs/memory.md before any change."
4. Add `.gitignore` (include `.env`, `node_modules`, `__pycache__`, `.venv`, build folders) and `.env.example`.
5. Add `docker-compose.yml` with `db` (PostgreSQL), `api` (FastAPI), `web` (Next.js).
6. Add a tiny FastAPI `/health` route and a Next.js home page that calls it.
7. Add a CI workflow that runs lint and tests on every pull request.
8. Add `.github/pull_request_template.md`:
   ```
   ## What changed
   ## Task ID
   ## How I tested
   - [ ] Tests added/updated and passing
   - [ ] Rules B1–B12 not broken
   - [ ] States handled (loading, empty, error, no permission)
   - [ ] docs/memory.md updated
   - [ ] I can explain every line in this PR
   ```
9. Turn on branch protection for `main` (require a pull request and one approval) if GitHub allows it on your plan. If not, follow the rule by agreement.
10. Create the first cards on the board from `task.md` milestone M0 and M1.
**Check:** every person can run `docker compose up` and open the home page and `/health`. Fix this before anything else.

## Day 4–5 — Prepare the sponsor meeting (Design & Docs leads the notes)
Ask these questions:
- What are your products, pack sizes and price lists (MRP, dealer, distributor)?
- Which quality checks do you do on each batch, with what limits?
- How do you decide the batch is okay to sell?
- How many warehouses, dealers and distributors do you have?
- How do orders arrive today (call, WhatsApp, paper)?
- How long is shelf life and what minimum remaining shelf life do you accept at dispatch?
- Can you share sample (even anonymised) sales, stock and production data?
- Can you give a short feedback letter at the end?
Write notes in `docs/01-initiation/sponsor-meeting-1.md` the same day.

## Day 6–7 — Guide meeting and first weekly demo
- Show the guide: title, PRD summary, 24-week plan, team charter, the board.
- Record guide comments in the minutes. Update the PRD if scope changes.
- Friday demo: everyone shows `docker compose up` works on their laptop.

---

# PART 2 — HOW WE WORK EVERY DAY AND WEEK

## Daily (5 minutes)
Post in the chat by 8 pm:
```
Done: <task id> <what>
Doing: <task id> <what>
Blocked: <what / who I need>
```

## Weekly cycle
| Day | What happens |
|---|---|
| **Monday (30 min)** | Review last week. Move 3–6 cards to `This week`. Everyone names their cards. Lead checks nobody has too much |
| **Tue–Thu** | Build. Pull requests are opened early, not on Friday night |
| **Friday (30 min)** | Demo: each person shows a working piece. Merge what is ready. Move unfinished work back with a reason |
| **Friday (10 min)** | Mock viva: each person asks another person two questions about a module they did not build |
| **Sunday (5 min)** | Lead updates the board, risk list and `memory.md` |

## Task card format
```
Title: T-041 FEFO selector and reservation
Owner: Lead   Reviewer: AI
Why: requirement FR-INV-03, FR-INV-04
Done when: tests B1–B4 pass, PR merged, memory.md updated
Depends on: T-040
```

## Git workflow (everyone, every task)
```
git checkout main
git pull
git checkout -b feature/T-041-fefo
# work, then:
git add .
git commit -m "feat(inventory): add FEFO selector"
git push -u origin feature/T-041-fefo
```
Then open a Pull Request on GitHub. Reviewer reads it, runs it if needed, comments, approves. Author merges. Then:
```
git checkout main
git pull
git branch -d feature/T-041-fefo
```
Conflicts: pull `main` into your branch (`git pull origin main`), fix the files, commit. If unsure, ask the Lead. Never force-push `main`.

## Code review checklist (reviewer uses this)
- Does it do only what the task says?
- Rules B1–B12 respected? Permission checks present on the server?
- Tests included? Do they test the rule, not just the happy path?
- Loading, empty, error and no-permission states on screens?
- No secrets, no hard-coded colours, no dead code?
- Can I explain it? If not, ask.

## Working with AI coding tools (the loop)
1. Open the task card. Start a **new** session for each task.
2. Give the tool: "Read docs/rules.md and docs/memory.md. Then do task T-0XX. Plan first. Do only this task."
3. Read the plan. Fix it if wrong. Then let it build.
4. Run the tests yourself. Do not trust "it works".
5. Read the whole diff. Ask the tool to explain parts you do not understand.
6. Commit, open a pull request, update `memory.md`.
Never paste a key into a prompt. Never let the tool change many files for a small task. If the tool breaks things, `git restore .` or reset the branch and try again with a smaller task.

## Decision and conflict rules
- Technical choice: discuss 10 minutes, then Lead decides and writes it in `memory.md` (decision log).
- Disagreement on design: Design & Docs decides look; Front decides how it is built.
- Late work: say it before the deadline, not after. Re-plan the card.
- Someone is silent for 3 days: Lead calls them. If the problem continues, tell the guide early, with facts.
- Fair share: use the contribution log and Git history to check balance every month.

---

# PART 3 — PHASE-BY-PHASE GUIDE (24 WEEKS)

For each phase: what each person does, what the Lead checks, and the gate to pass.

## Phase A — Requirements (Weeks 1–2)
- **Lead:** run kickoff, accounts, repo (Part 1). Draft the data model rough list.
- **AI:** write synthetic-data assumptions (products, seasons, festivals, noise, seed).
- **Front:** study the screens in `ui-pages.md`, collect reference designs.
- **Design & Docs:** sponsor notes, user stories, SRS draft, task board, risk list.
- **Gate A:** guide approves PRD and scope. Sponsor questions answered or listed as open.

## Phase B — Design (Weeks 3–4)
- **Design & Docs:** Figma tokens and key flows (factory batch, dealer order, consumer checkout, QR page, owner dashboard). Use Stitch for first drafts, rebuild in Figma.
- **Lead:** ER diagram, data dictionary, architecture, API contract for auth, products, orders, inventory.
- **Front:** review designs, list components, set up the Next.js app shell and tokens.
- **AI:** list AI features with inputs, outputs and metrics; write the knowledge-base content plan.
- **Gate B (end of week 4):** ER diagram, API contract v1 and designs for the demo flows are approved. After this gate, changes to the contract need a change request.

## Phase C — Foundation (Weeks 5–6)
- **Lead:** database migrations, login, roles, permissions, audit log, catalogue and price lists.
- **Front:** login page, role-based menu, product list using the API (or mock data first).
- **AI:** generator script skeleton, project folders for forecasting.
- **Design & Docs:** write permission test matrix (role by route), test plan document.
- **Gate C:** each role logs in and sees only its menu. A dealer cannot read owner data (tested).

## Phase D — Factory records (Weeks 7–9)
- **Lead:** suppliers, raw materials, lots, production plans, batches, quality checks, batch state machine.
- **Front:** factory screens (materials, plans, batch detail, quality form).
- **AI:** generate realistic products, batches and quality values (labelled synthetic).
- **Design & Docs:** test cases for batch release rules; sequence and state diagrams for the report.
- **Gate D:** a batch goes from plan to Released and creates sellable stock. A held batch does not.

## Phase E — Inventory (Weeks 10–12)
- **Lead:** warehouses, balances, ledger, FEFO, reservations, expiry and low-stock queries.
- **Front:** warehouse screens (inventory, ledger, expiry alerts, adjustments).
- **AI:** produce 24 months of sales and stock data; start baseline forecast.
- **Design & Docs:** concurrency and FEFO test cases; screenshots for the manual.
- **Gate E:** concurrency test passes (no overselling). FEFO tests pass.

## Phase F — Ordering (Weeks 13–15)
- **Lead:** orders, price snapshot, state machine, idempotency, reserve/pack/dispatch/cancel/return, invoices, shipments.
- **Front:** store (catalogue, product, cart, checkout, tracking) and partner portal (bulk order, repeat order, invoices).
- **AI:** dealer history tool and sales analytics queries prepared.
- **Design & Docs:** end-to-end test script; user manual first draft.
- **Gate F:** consumer order and dealer order both complete end to end with consistent stock.

## Phase G — QR and dashboards (Week 16)
- **Lead:** batch tokens, batch events, public QR endpoint, scan log, analytics endpoints.
- **Front:** QR page, QR print sheet, role dashboards.
- **AI:** slow-mover and sales summary functions.
- **Design & Docs:** QR test cases (unknown token, private fields hidden); start the demo script.
- **Gate G:** scanning shows correct recorded events; unknown token says "No record found".

## Phase H — Forecast and reorder (Weeks 17–19)
- **AI:** baseline and candidate models, chronological split, metrics, reorder logic, expiry-risk rules.
- **Lead:** endpoints to run forecasts and store results; protect them by role.
- **Front:** forecast, evaluation and recommendation screens.
- **Design & Docs:** AI evaluation report template; screenshots.
- **Gate H:** evaluation shows baseline vs model with honest conclusions.

## Phase I — Assistant and agent (Weeks 20–21)
- **AI:** LLM adapter, knowledge-base ingestion, retrieval, tool whitelist, natural-language analytics, dealer reorder draft.
- **Lead:** secure the AI endpoints, rate limits, logging, approval endpoint.
- **Front:** chat panel, draft review screen, business query screen.
- **Design & Docs:** write the approved knowledge-base content; AI test questions (answerable, unanswerable, prompt injection).
- **Gate I:** unanswerable questions are refused; permission-aware tools; drafts need approval.

## Phase J — Deploy and harden (Weeks 22–23)
- **Lead:** staging deploy, HTTPS, secrets, backups and a tested restore, monitoring, logs.
- **Front:** accessibility and responsive fixes.
- **AI:** final evaluation run, results frozen.
- **Design & Docs:** run all test cases, bug log, test report, traceability matrix, security checklist.
- **Gate J (end of week 23):** all must-have tests pass; feature freeze; release candidate tagged.

## Phase K — Final (Week 24)
- **Everyone:** final report, PPT, demo video, user manual, viva practice.
- **Lead:** release tag and README check on a clean machine.
- **Design & Docs:** assemble the submission pack and sponsor feedback letter.
- **Gate K:** full demo story runs twice without errors, with a backup recording ready.

---

# PART 4 — WHEN THINGS GO WRONG

| Problem | What the Lead does |
|---|---|
| Behind schedule by more than 1 week | Cut optional items first (mobile, voice, vision). Never cut tests or security. Tell the guide |
| Someone is stuck | Pair for 30 minutes. If still stuck, ask a smaller question to the AI tool or split the task |
| Merge conflicts every day | Smaller pull requests, merge more often, agree who edits shared files |
| AI tool produced messy code | Reset the branch, give a smaller task, add the rule to `rules.md` |
| API key limit reached | Use caching and fewer calls, switch to another provider through the adapter, use saved demo answers |
| Sponsor unavailable | Continue with placeholders, mark them "to be confirmed", keep a list of open questions |
| Scope creep | Write it as a change request; add only if something of equal size is removed |
| Unequal work | Look at the board and Git history, reassign tasks calmly, speak privately first |
| Laptop or Wi-Fi failure on demo day | Local Docker demo on a second laptop and a recorded video |

# PART 5 — CHECKLISTS

## Friday demo checklist
- [ ] Each person shows something running.
- [ ] Tests pass on `main`.
- [ ] Board updated; blocked items named.
- [ ] `memory.md` updated.
- [ ] Next week's cards chosen.

## Release checklist (weeks 22–24)
- [ ] Clean-machine setup works from the README.
- [ ] All must-have tests pass.
- [ ] No secrets in the repo (search before tagging).
- [ ] Database backup and restore tested.
- [ ] Demo data labelled; demo accounts for all six roles ready.
- [ ] Report, PPT, manual, video, sponsor letter in the Drive.
- [ ] Mock viva done by every member.

## Demo-day checklist
- [ ] Laptop charged, charger, backup laptop.
- [ ] App running locally and the online version open.
- [ ] Backup video on a pen drive and phone.
- [ ] Demo accounts and the story script printed.
- [ ] Each member knows which part they present and can answer any module's questions.
