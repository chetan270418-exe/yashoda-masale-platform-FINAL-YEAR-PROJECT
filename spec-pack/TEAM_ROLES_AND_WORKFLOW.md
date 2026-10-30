# Team Responsibilities and GitHub Workflow

## Member 1 --- Vedika Warke: Frontend

**Primary area:** `frontend/` - Implement storefront, catalogue, forms
and role-specific dashboards. - Connect frontend to documented API
endpoints. - Handle loading, empty, error and success states. - Ensure
responsive layouts and accessible interactions. - Coordinate with Sanika
on shared components.

## Member 2 --- Sanika Thale: UI/UX

**Primary area:** `docs/design/` plus design collaboration in
`frontend/` - Define colors, typography, spacing, navigation and
component specifications. - Create wireframes and user flows for
customer, factory, warehouse, dealer and owner screens. - Specify
responsive layouts and interaction states. - Review consistency,
contrast and usability with Vedika.

## Member 3 --- Srushti Bhagat: Database and Domain APIs

**Primary areas:** `database/` and agreed data-focused API modules -
Design ER diagram, schema, relationships, constraints and migrations. -
Work on users/roles, products, suppliers and inventory data
operations. - Prepare synthetic seed data and API request/response
documentation. - Coordinate with Chetan on module boundaries,
transactions and authorization.

## Member 4 --- Chetan Sonawane: Backend Architecture and AI

**Primary areas:** `backend/` and `backend/app/ai/` - Coordinate backend
architecture and integration conventions. - Implement or coordinate
workflow rules and authorization integration. - Build AI interfaces,
forecasting evaluation, grounded assistant and dealer reorder draft
workflow. - Ensure AI uses approved backend functions rather than
arbitrary database access. - Coordinate integration, testing and
deployment.

The AI subsystem lives inside `backend/app/ai/`, not in the top-level
`ai-services/` directory, so it runs in the same process as the business data it
reasons about. `ai-services/README.md` explains why there is no duplicated copy
there.

## Shared responsibility

Everyone reviews pull requests, tests integrated workflows, updates
documentation and helps prepare the demo. Folder ownership is not a
barrier to collaboration.

## Git rules

1.  `main` is the stable branch; do not commit unfinished work directly
    to it.
2.  Create a feature branch for each task.
3.  Pull the latest `main` before beginning.
4.  Make small, focused commits.
5.  Push the branch and open a Pull Request.
6.  Request at least one teammate review.
7.  Run relevant tests before merging.
8.  Never commit `.env`, passwords, API keys or confidential sponsor
    data.
9.  Use `.env.example` for placeholder variable names only.

## Branch examples

-   `feature/ui-design-system`
-   `feature/frontend-catalogue`
-   `feature/database-schema`
-   `feature/product-api`
-   `feature/backend-foundation`
-   `feature/forecasting-baseline`
-   `feature/qr-traceability`

## Standard workflow

``` bash
git switch main
git pull origin main
git switch -c feature/short-task-name

# Make and test changes

git add .
git commit -m "Describe the change"
git push -u origin feature/short-task-name
```

Then create a Pull Request on GitHub and request review.

## API contract agreement

Before frontend and backend work proceeds in parallel, agree on endpoint
path/method, JSON request/response, authentication, permissions,
validation/errors, pagination/filtering and module ownership. Use
FastAPI OpenAPI docs as the API reference once endpoints exist.

## Definition of done

A task is done when implementation works, errors are handled, relevant
tests pass, permissions are checked server-side, a teammate reviews and
merges it, and documentation is updated when needed.
