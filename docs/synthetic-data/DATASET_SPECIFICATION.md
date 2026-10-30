# Yashoda SpiceChain AI — Synthetic Dataset Specification

> **MANDATORY DISCLAIMER (Rule 44 & Rule 89)**:
> All records in this dataset package are **100% synthetic, fictional demo data** generated with a fixed pseudorandom seed (`seed=42`). No actual sponsor records, real customer identities, actual sales figures, or proprietary food-safety metrics are contained herein.

---

## 1. Generator Overview & Scientific Assumptions

The synthetic dataset was engineered to evaluate and stress-test the Yashoda SpiceChain AI platform across:
1. **Catalog & Pricing Hierarchy** (Categories, Products, Multi-Pack Variants, Role-Based Pricing).
2. **Production & Traceability** (Raw material receipt, batch codes, quality check releases, public tokens).
3. **FEFO Inventory Engine** (Multi-warehouse stock balances, append-only movement ledger, expiration-sorted allocation).
4. **Milestone M5 Ordering & Fulfillment** (State transitions, idempotency replay, immutable price snapshots, decimal currencies).
5. **Milestone M7 AI Forecasting & Reordering** (24-month chronological demand history, seasonal multipliers, festival spikes).

### 1.1 Demand Generation Mathematical Formulation

Two demand models exist in this project and they are deliberately different. Both are entirely synthetic and both are reproducible from a fixed seed.

#### 1.1.1 Daily demo dataset (`scripts/generate_data.py`)

The 730-day daily sales history shipped in this folder uses a multiplicative model:

$$\text{Demand}(t) = \max\left(0,\ \text{round}\left(\text{Base} \times \text{Season}(m) \times \text{Weekend}(t) \times \text{Noise}(t)\right)\right)$$

Where:
- $\text{Base} \sim U(4, 42)$ units/day, drawn once per product.
- $\text{Season}(m)$ is **per product**. A shared Indian climate curve $s$ (1.22 in Oct–Dec, 1.12 in Mar–Apr, 1.00 otherwise) is scaled by a per-product amplitude $a \sim U(0.55, 1.45)$ and shifted by a per-product phase offset $p \in [0, 11]$ months, giving $\text{Season}(m) = 1 + (s(m - p) - 1)\cdot a$. A single shared seasonal curve would make every forecasting model look better than it really is, because the model would only have to learn one global pattern, so the amplitude and phase vary per product.
- $\text{Weekend}(t) = 1.1$ on Saturday and Sunday, otherwise $1.0$.
- $\text{Noise}(t) \sim U(0.45, 1.65)$, a wide multiplicative spread reflecting lumpy day-to-day ordering in wholesale spice trade.

#### 1.1.2 Monthly series used by the forecast evaluation (`backend/app/ai/forecasting/generator.py`)

The 24-month monthly series that the M7 forecast evaluation scores uses a richer model:

$$\text{Demand}(t) = \max\left(50,\ \text{Base} \times \text{Trend}(t) \times \text{Season}(m) \times \text{Festival}(t) \times \text{Noise}(t)\right)$$

Where:
- $\text{Base}$ is the product's monthly demand (default 500 units).
- $\text{Trend}(t) = 1.0 + 0.15 \times (t/23)$, a $15\%$ growth across 24 months.
- $\text{Season}(m)$ is per product, using the same amplitude $a \sim U(0.55, 1.45)$ and phase offset $p \in [0, 11]$ as above.
- $\text{Festival}(t) = 1 + f$ in October and November, where $f \sim U(0, 0.25)$ is the product's festival strength. The festival stays on the calendar month, because Diwali falls in the same month for every spice.
- $\text{Noise}(t) = 1 + U(-0.08, 0.08)$, a bounded $\pm 8\%$ variation.
- The floor of 50 units is a business rule of the demo dataset only. It is deliberately **not** reproduced inside the forecasting models, because a model must not assume a minimum order size it was never told about.

The monthly noise is deliberately narrower ($\pm 8\%$) than the daily dataset's, because the forecasting evaluation needs a signal a seasonal model can actually learn. The evaluation reports its own error honestly either way, and the honest result is that the seasonal naive baseline still wins; see `docs/forecast-evaluation/EVALUATION_REPORT.md`.

#### 1.1.3 Reproducibility

Both generators derive their random stream from a fixed seed. The monthly generator derives a per-product seed as `CRC32(SKU) + base seed` rather than using Python's `hash()`, because `hash()` is salted per process and would break reproducibility across runs.

---

## 2. Dataset Files & Schema Reference

### 2.1 `products.csv`
Contains 100 spice and masala product definitions.

| Column | Type | Description | Constraints / Examples |
|---|---|---|---|
| `product_id` | String (PK) | External product identifier | `PRD-0001` to `PRD-0100` |
| `sku_family` | String | Common SKU prefix | `YS-0001` |
| `name` | String | Product trade name | Turmeric Powder, Red Chilli Powder, Garam Masala |
| `category` | String | Spice category | Powders, Blended Masalas, Whole Spices |
| `primary_ingredient` | String | Key spice component | Turmeric, Chilli, Coriander, Cumin |
| `description` | String | Product description | Synthetic demo description |
| `is_active` | Boolean | Catalog visibility flag | `true` |

### 2.2 `variants.csv`
Contains 500 sellable SKU variants (5 pack sizes per product: 50g, 100g, 250g, 500g, 1kg).

| Column | Type | Description | Invariant / Constraints |
|---|---|---|---|
| `variant_id` | String (PK) | Unique variant identifier | `VAR-0001-1` |
| `product_id` | String (FK) | Reference to `products.csv` | Foreign Key `PRD-xxxx` |
| `sku` | String (Unique) | Physical barcode / SKU code | `YS-0001-1` |
| `pack_size` | Integer | Numerical pack weight | `50`, `100`, `250`, `500`, `1000` |
| `pack_unit` | String | Unit of measure | `g` |
| `net_weight_g` | Integer | Net weight in grams | `50` |
| `list_price_inr` | Decimal(12,2) | Base MRP in Indian Rupees (B12) | `82.73` |
| `is_active` | Boolean | Sellable status | `true` |

### 2.3 `suppliers.csv`
Contains 12 fictional raw spice agricultural suppliers.

| Column | Type | Description |
|---|---|---|
| `supplier_id` | String (PK) | `SUP-001` to `SUP-012` |
| `name` | String | e.g. Sahyadri Agro Supplies, Deccan Spice Traders |
| `contact_name` | String | Fictional contact person |
| `city` / `state` | String | Nashik, Nagpur, Sangli (Maharashtra) |
| `email` / `phone` | String | Safe `.example.test` domain placeholders |
| `is_synthetic` | Boolean | Always `true` |

### 2.4 `warehouses.csv`
Contains 2 storage and distribution centers.

| Column | Type | Description |
|---|---|---|
| `warehouse_id` | String (PK) | `WH-MH-01`, `WH-MH-02` |
| `code` | String (Unique) | `WH-CENTRAL-01` (Pune Primary), `WH-NAGPUR-01` |
| `name` | String | Central Warehouse Pune, Vidarbha Regional Hub |
| `city` | String | Pune, Nagpur |
| `is_primary` | Boolean | Pune warehouse marked `true` for primary default routing |
| `is_active` | Boolean | `true` |

### 2.5 `production_batches.csv`
Contains 200 manufacturing batches with simulated quality releases and shelf-life dates.

| Column | Type | Invariant Enforced | Description |
|---|---|---|---|
| `batch_id` | String (PK) | - | `BAT-00001` to `BAT-00200` |
| `variant_id` | String (FK) | - | Reference to `variants.csv` |
| `supplier_id` | String (FK) | - | Reference to `suppliers.csv` |
| `warehouse_id` | String (FK) | - | Warehouse location |
| `batch_code` | String (Unique) | - | `YS2608-00001` |
| `manufactured_on` | Date (ISO) | - | Production date |
| `expires_on` | Date (ISO) | B2, B3 | Expiry date (FEFO sort key) |
| `quality_status` | String | B1 | `approved`, `held`, `rejected` |
| `status` | String | B1 | `released`, `hold`, `rejected` |
| `public_token` | String (Unique) | B11 | QR public token `demo-00001-xxxx` |
| `is_synthetic` | Boolean | Rule 44 | `true` |

### 2.6 `inventory.csv` / `inventory_balances.csv`
Contains 151 warehouse batch inventory balances.

| Column | Type | Invariant Enforced | Description |
|---|---|---|---|
| `balance_id` | String (PK) | - | `BAL-00001` |
| `warehouse_id` | String (FK) | - | Warehouse reference |
| `batch_id` | String (FK) | - | Production batch reference |
| `on_hand_qty` | Decimal(14,3) | B4 | Current physical stock ($\ge 0$) |
| `reserved_qty` | Decimal(14,3) | B4 | Active reserved stock ($0 \le \text{reserved} \le \text{on\_hand}$) |
| `unit` | String | - | `packs` |

### 2.7 `stock_movements.csv`
Append-only stock ledger recording every historical receipt, adjustment, reservation, and dispatch.

| Column | Type | Invariant Enforced | Description |
|---|---|---|---|
| `movement_id` | String (PK) | B5 | `MOV-00001` |
| `warehouse_id` | String (FK) | B5 | Warehouse location |
| `batch_id` | String (FK) | B5 | Batch reference |
| `movement_type` | String | B5 | `receipt`, `reserve`, `release`, `dispatch` |
| `quantity` | Decimal(14,3) | B4, B5 | Positive movement quantity |
| `reference_type` | String | - | `initial_opening`, `order`, `adjustment` |
| `reference_id` | String | - | Linked external reference |
| `timestamp` | DateTime (ISO) | - | UTC timestamp |

### 2.8 `dealers.csv`
Contains 150 simulated wholesale partner accounts.

| Column | Type | Description |
|---|---|---|
| `dealer_id` | String (PK) | `DLR-0001` to `DLR-0150` |
| `business_name` | String | Fictional enterprise name |
| `contact_name` | String | Trade contact |
| `city` / `state` | String | Regional distribution zone |
| `tier` | String | `standard`, `silver`, `gold` |
| `is_active` | Boolean | `true` |

### 2.9 `orders.csv` & `order_items.csv`
Contains 10,000 historical dealer orders and 25,101 line items spanning 24 months.

**`orders.csv`**:
- `order_id`: UUID string identifier.
- `order_number`: `ORD-20241001-xxxx`.
- `customer_id`: Link to dealer.
- `order_type`: `sales`.
- `status`: Lifecycle state (`pending`, `reserved`, `packed`, `dispatched`, `delivered`, `cancelled`).
- `idempotency_key`: `SYN-ORD-xxxx` (Rule 48).
- `total_amount`: Total order monetary amount in Decimal(12,2) INR (B12).
- `created_at`: Placement timestamp.

**`order_items.csv`**:
- `order_item_id`: Unique line item identifier.
- `order_id`: Reference to `orders.csv`.
- `variant_id`: Reference to `variants.csv`.
- `quantity`: Decimal pack quantity.
- `unit_price_inr`: **Frozen unit price snapshot at placement** (Invariant B8, B12).
- `discount_percent`: Percentage discount applied.
- `line_total_inr`: Computed line total in INR (Invariant B12).

### 2.10 `sales_history.csv` / `sales_history_daily.csv`
Contains 73,000 observations (100 products $\times$ 730 days) formatted for baseline and ML time-series backtesting (T-070, T-071).

| Column | Type | Description |
|---|---|---|
| `date` | Date (YYYY-MM-DD) | Observation date |
| `product_id` | String (FK) | Reference to `products.csv` |
| `units_sold` | Decimal | Daily aggregated unit volume |
| `revenue_inr` | Decimal(12,2) | Daily gross turnover |
| `source` | String | `synthetic_simulation` |

---

## 3. Mapping to Milestone M5 ORM Models

When mapping synthetic datasets into PostgreSQL:

| Synthetic CSV Column | M5 SQLAlchemy Model (`backend/app/orders/models.py`) | Type in DB |
|---|---|---|
| `orders.order_number` | `Order.order_number` | `String(80)`, Unique |
| `orders.customer_id` | `Order.customer_id` | `UUID`, FK to `users.id` |
| `orders.status` | `Order.status` | `String(30)`, Check Constraint |
| `orders.idempotency_key` | `Order.idempotency_key` | `String(128)`, Unique |
| `orders.total_amount` | `Order.total_amount` | `Numeric(12, 2)` (Decimal) |
| `order_items.quantity` | `OrderItem.quantity` | `Numeric(14, 3)` (Decimal) |
| `order_items.unit_price_inr` | `OrderItem.unit_price_snapshot` | `Numeric(12, 2)` (Decimal B8) |
| `order_items.line_total_inr` | `OrderItem.line_total` | `Numeric(12, 2)` (Decimal B12) |
