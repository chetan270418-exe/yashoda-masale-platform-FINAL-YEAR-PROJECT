# Design System (for Figma and Google Stitch)

Use this file as the single source for look and feel. Give it to Stitch as context, and build the same tokens as Figma variables.

## 1. Principles
1. **Role-first:** each role sees a dashboard built for its job, not a generic admin panel.
2. **Calm and clear:** warm, readable, lots of white space. Data first.
3. **Safe by design:** destructive or stock-changing actions need confirmation and show consequences.
4. **Honest AI:** AI output is labelled, shows sources, and anything that changes data says "Draft, needs approval."
5. **Honest data:** a visible "Demo data" badge while synthetic data is used.

## 2. Colour tokens (start from your presentation palette)
| Token | Hex | Use |
|---|---|---|
| `brand-maroon` | #7A2E1D | Primary buttons, headings, nav active |
| `brand-orange` | #C2501F | Secondary accents, links |
| `brand-gold` | #D4A52A | Highlights, badges, charts |
| `surface-cream` | #FAF5EC | Page background |
| `surface-white` | #FFFFFF | Cards, tables |
| `text-primary` | #2B2118 | Body text |
| `text-muted` | #6B5F52 | Secondary text |
| `success` | #2E7D4F | Released, delivered, in stock |
| `warning` | #B7791F | Expiring soon, low stock, on hold |
| `danger` | #B3261E | Rejected, expired, errors |
| `info` | #1F5A8A | Neutral info, links in tables |
Check every text/background pair for contrast (aim for WCAG AA, 4.5:1 for body text).

## 3. Typography
- Headings: a serif such as Georgia or Playfair Display.
- UI and tables: Inter or a similar clean sans-serif.
- Scale: 12 / 14 / 16 / 20 / 24 / 32. Line height about 1.5 for body.
- Numbers in tables use tabular figures and right alignment.

## 4. Spacing, shape, elevation
- 8-pt spacing grid (4, 8, 12, 16, 24, 32).
- Card radius 12, input radius 8, soft shadow on cards only.
- Max content width about 1200 px on desktop.

## 5. Layout
- **Desktop:** left sidebar (role menu) + top bar (search, notifications, profile) + content.
- **Tablet/mobile:** collapsible menu, tables become card lists.
- Breakpoints: 640, 1024, 1280.
- Store pages use a top navigation with a cart icon.

## 6. Components to design once and reuse
Buttons (primary, secondary, danger, ghost), inputs, selects, date pickers, checkboxes, toggles, tabs, breadcrumbs, modals, drawers, toasts, tables (sortable, filter bar, pagination, empty state), status chips, KPI cards, charts (line, bar, donut), timeline, stepper, file upload, QR display, product card, price block, stock indicator, expiry badge, notification bell, chat panel, skeleton loaders.

## 7. States every screen needs
Loading (skeleton), empty (with next action), error (with retry), success (toast), no permission (403 page), offline/server problem (500 page).

## 8. Status chip colours
- Batch: Draft grey, In production blue, Quality pending amber, Released green, On hold amber, Rejected red.
- Order: Placed grey, Confirmed blue, Reserved blue, Packed amber, Dispatched blue, Delivered green, Cancelled red, Returned amber.
- Stock: In stock green, Low amber, Out red. Expiry: over 90 days none, 31–90 amber, 30 or fewer red.

## 9. AI interface patterns
- Chat panel with source chips under each answer ("Source: Garam Masala product sheet").
- If no source: "I don't have approved information on that."
- Forecast cards show the number, the baseline, and the error metric, plus a "Synthetic data" note.
- Agent drafts appear in a review screen with a clear "Approve / Edit / Reject" bar. Nothing submits automatically.

## 10. Form rules
Labels above inputs. Inline validation messages. Required fields marked. Primary action on the right. Confirm dialogs for cancel, dispatch, stock adjustment and price change.

## 11. Accessibility checklist
Visible focus rings, keyboard navigation, labels for icons, colour is never the only signal (add text or icon), minimum 44 px touch targets, alt text for product images.

## 12. Microcopy
Short, plain English. Buttons say what they do ("Reserve stock", "Approve draft"). Error messages say what to do next. Avoid jargon on consumer pages.

## 13. Figma setup
1. Create a file with pages: Cover, Tokens, Components, Flows, Desktop screens, Mobile screens, Prototype.
2. Create colour, text and spacing styles/variables from sections 2–4.
3. Build components with variants (state, size).
4. Use Auto Layout and a 12-column grid.
5. Name frames `Role / Page / State` (e.g. `Warehouse / Dispatch / Empty`).
6. Prototype the main flows (see `ui-pages.md`).
7. Share a view-only link and a developer handoff note listing tokens and component names.

## 14. Using Google Stitch
Stitch is good for fast first drafts. Paste the master prompt from `ui-pages.md`, generate one screen at a time, then rebuild or tidy the best versions in Figma so tokens and components stay consistent. Always check generated text for wrong data, and replace fake numbers with realistic labelled demo data.
