# UI Pages to Design

Total: about 70 screens. Design the **bold** ones first (they are needed for the demo story).
Every page also needs loading, empty, error and no-permission states (see `design.md`).

## A. Public and store (everyone, including Consumer)
| # | Page | Route | Key content |
|---|---|---|---|
| 1 | **Home** | `/` | Hero, featured products, categories, "Scan your pack" |
| 2 | **Catalogue** | `/products` | Grid, filters (category, pack size, price), sort, search |
| 3 | **Product detail** | `/products/[slug]` | Images, variants, price, stock, ingredients, reviews |
| 4 | Search results | `/search` | Keyword + smart search results |
| 5 | **Cart** | `/cart` | Items, quantity, totals |
| 6 | **Checkout** | `/checkout` | Address, delivery, simulated payment, summary |
| 7 | Order confirmation | `/orders/[id]/done` | Order number, next steps |
| 8 | **QR traceability** | `/qr/[token]` | Product, batch code, timeline of recorded events, "No record found" state |
| 9 | **Login** | `/login` | Email, password, forgot link |
| 10 | Register | `/register` | Consumer sign-up only |
| 11 | Forgot / reset password | `/forgot`, `/reset` | Two small screens |
| 12 | About, Contact, FAQ | static | Brand story, sponsor, questions |
| 13 | **Yashoda AI chat** | widget + `/assistant` | Chat, source chips, recipe cards |
| 14 | 403, 404, 500 | system | Friendly error pages |

## B. Consumer account
| # | Page | Key content |
|---|---|---|
| 15 | **My orders** | List with status chips |
| 16 | **Order detail and tracking** | Timeline, items, invoice download |
| 17 | Addresses | List, add, edit |
| 18 | Wishlist | Saved products |
| 19 | Write a review | Rating, text |
| 20 | Profile and password | Details, change password |

## C. Dealer and Distributor portal
| # | Page | Key content |
|---|---|---|
| 21 | **Partner dashboard** | Credit, pending orders, outstanding, reorder suggestions |
| 22 | **Partner catalogue** | Products with their own price (dealer or distributor) |
| 23 | **Bulk order builder** | Quick add by SKU, quantity table, totals, stock check |
| 24 | Repeat order | Pick a past order, edit, submit |
| 25 | **AI reorder draft review** | Draft items, reasons, Approve / Edit / Reject |
| 26 | Orders list | Filters, status |
| 27 | Order detail | Items, price snapshot, shipment events |
| 28 | Invoices | List, download |
| 29 | Payments and outstanding | Ledger, status |
| 30 | Profile | Company details |
| 31 | Distributor: my dealers | Assigned dealers list |
| 32 | Distributor: dealer performance | Sales per dealer, charts |

## D. Warehouse
| # | Page | Key content |
|---|---|---|
| 33 | **Warehouse dashboard** | Low stock, expiring soon, pending dispatch |
| 34 | **Inventory by batch** | Product, batch, warehouse, on hand, reserved, expiry |
| 35 | Stock movements ledger | Filterable history |
| 36 | Receive / transfer / adjust stock | Forms with reason |
| 37 | **Expiry alerts** | Batches by days left |
| 38 | **Pick and pack queue** | Orders to fulfil, FEFO suggested batches |
| 39 | **Dispatch form** | Confirm batches, carrier, date |
| 40 | Returns inspection | Decision: restock, quarantine, scrap |
| 41 | Warehouses | List and settings |

## E. Factory
| # | Page | Key content |
|---|---|---|
| 42 | Factory dashboard | Batches by status, quality pending |
| 43 | Suppliers | List, add |
| 44 | Raw materials and lots | Stock, receipt form |
| 45 | Production plans | List, create |
| 46 | **Batch detail** | Stage timeline, materials used, output, status |
| 47 | **Quality check form** | Parameters, measured values, limits, decision |
| 48 | Batch list | Filter by status |
| 49 | QR generate and print | Preview and print label sheet |

## F. Owner / Admin
| # | Page | Key content |
|---|---|---|
| 50 | **Owner dashboard** | Sales, orders, stock health, alerts, AI insights |
| 51 | Users and roles | Create staff, assign roles |
| 52 | Products admin | Create, edit, variants, images |
| 53 | **Price lists** | MRP, dealer, distributor with change history |
| 54 | Orders overview | All orders, filters |
| 55 | **Sales analytics** | Charts by product, dealer, region, month, export |
| 56 | Slow-moving products | Table and chart |
| 57 | **Forecast and evaluation** | Forecast chart, baseline vs model, MAE / RMSE |
| 58 | **Reorder recommendations** | Suggestions with reasons, approve |
| 59 | Expiry-risk flags | Batches at risk, suggested actions |
| 60 | **Ask your business (NL query)** | Question box, chart/table result, period shown |
| 61 | Scan monitor | Scan counts, flagged tokens |
| 62 | Audit log | Who did what, when |
| 63 | AI tool log | Tool runs and results |
| 64 | Settings | Demo-data badge, thresholds, LLM provider status |

## G. Optional
Mobile screens for consumers (shop, track, scan QR, chat). Telegram/WhatsApp flow diagram. Label draft preview.

## Flows to prototype in Figma (use these in the demo)
1. Factory: create batch -> record stages -> quality check -> release.
2. Warehouse: stock appears -> expiry alert.
3. Dealer: bulk order -> reserve -> pick (FEFO) -> dispatch.
4. Consumer: browse -> cart -> checkout -> track -> scan QR.
5. Owner: dashboard -> forecast -> reorder recommendation -> approve.
6. Dealer agent: "reorder my usual" -> draft -> approve.

---

## Google Stitch prompts

### Master prompt (paste first, then one page at a time)
```
Design a responsive web application called "Yashoda SpiceChain AI" for a spice manufacturing and distribution business in India.
Style: warm, clean, professional. Cream background (#FAF5EC), white cards, maroon primary (#7A2E1D), orange accent (#C2501F), gold highlight (#D4A52A).
Headings in a serif font, UI text in a clean sans-serif (Inter). 12px rounded cards, soft shadows, lots of white space.
Layout for staff portals: left sidebar with role menu, top bar with search, notifications and profile.
Use status chips (green released/delivered, amber on hold/expiring, red rejected/expired).
Show a small "Demo data" badge in the top bar.
Use realistic Indian spice product names (Garam Masala 100g, Turmeric Powder 200g, Red Chilli Powder 500g) and Indian rupee formatting.
Do not invent features. Keep text short and clear.
```

### Example page prompts
**Warehouse - Inventory by batch**
```
Warehouse Manager screen "Inventory by Batch". Table columns: Product, Batch code, Warehouse, On hand, Reserved, Available, Expiry date, Status chip. Filter bar (product, warehouse, expiry within 30/60/90 days). Row highlight in amber for expiring within 60 days, red for expired. Summary cards on top: Total SKUs, Low stock, Expiring soon, Held batches. Pagination and CSV export.
```
**Factory - Quality check form**
```
Factory Manager screen "Quality Check for Batch YSM-2026-00125". Left: batch summary card. Right: form with parameter rows (Parameter name, Measured value, Allowed range, Pass/Fail indicator). Bottom: decision radio (Release, Hold, Reject), required remarks box, Submit button with confirmation dialog.
```
**Dealer - AI reorder draft review**
```
Dealer screen "Review Reorder Draft". Banner: "Draft prepared by AI. Nothing is ordered until you approve." Table: Product, Last month qty, Suggested qty, Your price, Stock available, Reason. Totals panel. Buttons: Approve and place order, Edit quantities, Reject. Show a small note listing what data was used.
```
**Public - QR traceability**
```
Mobile-first public page "Batch Traceability". Product name and photo, batch code, manufacture date, expiry date. Vertical timeline of recorded events (Quality released, Dispatched to warehouse, Dispatched to dealer) with dates. Also design the "No record found" state. No private supplier or customer information.
```
**Owner - Forecast and evaluation**
```
Owner screen "Demand Forecast". Line chart: actual vs forecast for the next 3 months for a selected product. Beside it a comparison card: Baseline vs Model with MAE and RMSE. A note: "Based on synthetic demo data". Below, a table of reorder recommendations with reason and Approve button.
```
