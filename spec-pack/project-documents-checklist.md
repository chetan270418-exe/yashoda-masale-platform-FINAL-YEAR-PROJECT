# Documents, Engineering Practices and Testing — What to Prepare

Priority: **M** = must have, **S** = should have, **N** = nice to have.
This is how a small software company would run the project, scaled to a student team.

## 1. Project initiation
| Item | Pri | Owner | When |
|---|---|---|---|
| Sponsor letter / feedback letter | M | M4 | Wk 1–2, and again at the end |
| Project synopsis (title, problem, objectives, scope) | M | All | Wk 1 |
| Feasibility note (technical, time, cost, data) | S | M4 | Wk 1–2 |
| Team charter (roles, meeting rhythm, rules) | S | M4 | Wk 1 |
| Project plan: WBS and Gantt chart (use the 24-week plan) | M | M4 | Wk 2 |
| Risk register (risk, chance, impact, action) | M | M4 | Wk 2, update monthly |
| Stakeholder list | N | M4 | Wk 1 |

## 2. Requirements
| Item | Pri | Notes |
|---|---|---|
| PRD | M | Provided in `prd.md` |
| SRS (IEEE-style: purpose, overall description, functional, non-functional, interfaces) | M | Expand the PRD with numbered requirements |
| User stories with acceptance criteria | M | "As a dealer, I want... so that..." |
| Use-case diagram and use-case descriptions | M | Six roles |
| Requirements Traceability Matrix (RTM) | M | Requirement -> design -> code -> test case |
| Business rules list | S | B1–B12 from `rules.md` |
| Glossary | S | In `memory.md` |

## 3. Design
| Item | Pri | Notes |
|---|---|---|
| Architecture diagram (HLD) | M | `architecture.md` |
| Data flow diagrams (level 0, 1) | M | Already in your report |
| ER diagram and data dictionary | M | Every table and column explained |
| UML: class, sequence (order, FEFO, quality release), state (batch, order), activity (production) | M/S | Sequence and state diagrams impress examiners |
| API specification (OpenAPI from FastAPI) | M | Export as PDF/HTML |
| UI/UX: wireframes, high-fidelity screens, prototype | M | `design.md`, `ui-pages.md` |
| Design system / style guide | S | `design.md` |
| Threat model (STRIDE) with a simple table | S | What can go wrong and how you prevent it |
| Architecture decision records | S | `memory.md` decisions log |

## 4. Implementation
| Item | Pri | Notes |
|---|---|---|
| Git repository with meaningful commits and branches | M | Shows every member's work |
| README (what, setup, run, test) | M | Must work on a clean machine |
| `.env.example` and configuration guide | M | No secrets in Git |
| Coding standards and lint config | S | `rules.md`, ruff, ESLint |
| Pull-request template and review checklist | S | Check B1–B12 |
| Database migrations | M | Alembic |
| Seed and synthetic-data scripts with documented seed | M | Labelled as synthetic |
| Dependency list and licence check | N | |
| Change log and version tags (semantic versioning) | S | v0.1 ... v1.0 |

## 5. Testing (plan these early)
| Item | Pri | Notes |
|---|---|---|
| Test plan (scope, approach, tools, schedule, entry/exit criteria) | M | |
| Test cases (ID, steps, expected, actual, status) linked to requirement IDs | M | Use a spreadsheet |
| Unit tests | M | Pricing, state machines, FEFO, stock maths |
| Integration / API tests | M | Order -> reserve -> dispatch -> stock |
| Permission (authorization) tests | M | Every role against forbidden actions |
| Concurrency test | M | Two orders, one unit |
| End-to-end tests of the demo story | S | Playwright or Cypress |
| Security tests (OWASP Top 10 checklist, ASVS items) | M | Injection, broken access, secrets, file upload |
| Performance test (basic load, response times) | S | Locust or k6; report numbers honestly |
| Usability test with 3–5 real users + sponsor feedback | S | Short task list and feedback form |
| Accessibility check | S | Keyboard, contrast, labels |
| AI evaluation report | M | Dataset method, split, baseline vs model, metrics, limits |
| RAG evaluation | S | Answerable, unanswerable, conflicting-source, prompt-injection questions |
| Regression test run before release | S | Run all tests on release candidate |
| Bug log (ID, severity, steps, status, fix) | M | |
| Test report and UAT sign-off | M | Sponsor or guide signs |

Test types to name in the report: unit, integration, system, regression, smoke, acceptance (UAT), black-box, white-box, boundary-value, equivalence-partition, negative testing, security, performance, usability, compatibility (browsers/mobile).

## 6. Deployment and operations
| Item | Pri | Notes |
|---|---|---|
| Deployment guide | M | Steps to reproduce |
| CI/CD pipeline diagram | S | GitHub Actions |
| Backup and restore procedure, with one tested restore | M | |
| Runbook (how to restart, read logs, rotate secrets) | S | One page |
| Monitoring and logging notes | S | Health check, structured logs |
| Release notes | S | |

## 7. Project management
| Item | Pri | Notes |
|---|---|---|
| Task board (backlog, in progress, review, done) | M | GitHub Projects or Trello |
| Weekly progress report and meeting minutes | M | Guide may ask for these |
| Weekly integration demo record | S | Short note or screenshot |
| Effort and contribution log per member | S | Helps the viva |
| Change request log (scope changes) | S | |

## 8. Professional engineering practices
- **Definition of Ready / Done** for tasks (in `rules.md`).
- **Code review** on every pull request.
- **Branching:** short-lived feature branches, protected `main`.
- **Continuous integration:** tests run automatically.
- **Feature freeze** two weeks before submission.
- **Version control of documents** too (keep docs in the repo).
- **Data privacy:** no real personal data in demo; explain how you would handle it in production.
- **AI governance:** disclose AI tools used for coding and writing, keep human approval for AI actions, log AI tool runs.
- **Responsible claims:** state limits of synthetic data, no false authenticity or legal compliance claims.

## 9. Final submission pack
| Item | Pri |
|---|---|
| Final project report (already drafted) | M |
| Presentation (PPT) | M |
| Demo video (3–5 min, one connected story) and a backup recording | M |
| User manual with screenshots for each role | M |
| Source code on Git with README | M |
| SRS, design documents, test report, AI evaluation report | M |
| Sponsor feedback letter | S |
| Viva Q&A sheet (see below) | S |
| Poster or one-page summary | N |
| Research-paper style short abstract (for a project competition) | N |

## 10. Viva preparation
Expect questions such as:
- Why PostgreSQL? Why a modular monolith instead of microservices?
- How do you stop overselling when two users order at once?
- How does FEFO work and what is excluded?
- How do you stop a dealer from seeing distributor prices?
- What does the QR page prove, and what does it not prove?
- Why is your data synthetic, and what does that mean for the AI results?
- How did you evaluate the forecast? Did the model beat the baseline?
- What can the AI agent do, and what can it not do?
- How did you use AI coding tools, and how did you check the code?
- What would you change to run this in a real factory?
Every member should be able to answer every question, not only their own module.

## 11. Suggested folder structure
```
/docs
  /01-initiation   (synopsis, plan, risks, sponsor letter)
  /02-requirements (PRD, SRS, user stories, RTM)
  /03-design       (architecture, ER, UML, API, UI exports)
  /04-testing      (plan, cases, reports, AI evaluation)
  /05-deployment   (guide, runbook, backup test)
  /06-management   (minutes, progress, contribution log)
  /07-submission   (report, PPT, video, manual)
  prd.md architecture.md design.md ui-pages.md rules.md memory.md task.md implementation.md
```
