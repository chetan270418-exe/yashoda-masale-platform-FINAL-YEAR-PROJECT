import csv,sys
from pathlib import Path
from datetime import date
def load(p):
    with p.open(newline="",encoding="utf-8-sig") as f:return list(csv.DictReader(f))
def validate(folder):
    errors=[]
    names=["products","variants","suppliers","warehouses","production_batches","inventory_balances","stock_movements","dealers","orders","order_items","sales_history_daily"]
    data={n:load(folder/f"{n}.csv") for n in names}
    def unique(n,k):
        vals=[r[k] for r in data[n]]
        if len(vals)!=len(set(vals)):errors.append(f"{n}: duplicate {k}")
    for n,k in [("products","product_id"),("variants","variant_id"),("production_batches","batch_id"),("inventory_balances","balance_id"),("orders","order_id")]:unique(n,k)
    pids={r["product_id"] for r in data["products"]};vids={r["variant_id"] for r in data["variants"]};bids={r["batch_id"] for r in data["production_batches"]};dids={r["dealer_id"] for r in data["dealers"]};oids={r["order_id"] for r in data["orders"]}
    for r in data["variants"]:
        if r["product_id"] not in pids:errors.append("variant references unknown product")
    for r in data["production_batches"]:
        if r["variant_id"] not in vids:errors.append("batch references unknown variant")
        if date.fromisoformat(r["expires_on"])<=date.fromisoformat(r["manufactured_on"]):errors.append("batch expiry not after manufacture")
    seen=set()
    for r in data["inventory_balances"]:
        key=(r["warehouse_id"],r["batch_id"])
        if key in seen:errors.append("duplicate warehouse/batch balance")
        seen.add(key)
        oh,res=int(r["on_hand_qty"]),int(r["reserved_qty"])
        if oh<0 or res<0 or res>oh:errors.append("inventory invariant failed")
        if r["batch_id"] not in bids:errors.append("balance references unknown batch")
    for r in data["orders"]:
        if r["dealer_id"] not in dids:errors.append("order references unknown dealer")
    for r in data["order_items"]:
        if r["order_id"] not in oids or r["variant_id"] not in vids:errors.append("order item has broken foreign key")
        if int(r["quantity"])<=0:errors.append("order item quantity not positive")
    for r in data["sales_history_daily"]:
        if r["product_id"] not in pids or int(r["units_sold"])<0:errors.append("invalid sales history row")
    expected={"products":100,"variants":500,"orders":10000,"sales_history_daily":73000}
    for n,c in expected.items():
        if len(data[n])!=c:errors.append(f"{n}: expected {c}, found {len(data[n])}")
    if errors:
        print(f"FAILED: {len(errors)} issue(s)")
        for e in errors[:100]:print("-",e)
        return 1
    print("VALIDATION PASSED")
    for n,rows in data.items():print(f"{n}: {len(rows)} rows")
    print("All data is synthetic; it is not sponsor data.")
    return 0
if __name__=="__main__":raise SystemExit(validate(Path(sys.argv[1] if len(sys.argv)>1 else "data")))
