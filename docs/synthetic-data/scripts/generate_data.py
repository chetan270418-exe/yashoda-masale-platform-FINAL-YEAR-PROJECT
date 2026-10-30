import argparse,csv,random,json
from pathlib import Path
from datetime import date,timedelta
AS_OF=date(2026,10,1)
FAMILIES=[("Turmeric Powder","Turmeric","Powders"),("Red Chilli Powder","Chilli","Powders"),("Coriander Powder","Coriander","Powders"),("Cumin Powder","Cumin","Powders"),("Garam Masala","Blend","Masala Blends"),("Kitchen King Masala","Blend","Masala Blends"),("Goda Masala","Blend","Masala Blends"),("Ginger Powder","Ginger","Powders"),("Garlic Powder","Garlic","Powders"),("Black Pepper Powder","Pepper","Powders"),("Cumin Seeds","Cumin","Whole Spices"),("Coriander Seeds","Coriander","Whole Spices"),("Mustard Seeds","Mustard","Whole Spices"),("Fennel Seeds","Fennel","Whole Spices"),("Cardamom","Cardamom","Whole Spices"),("Cinnamon","Cinnamon","Whole Spices"),("Cloves","Clove","Whole Spices"),("Bay Leaf","Bay Leaf","Whole Spices"),("Black Salt","Salt","Seasonings"),("Chaat Masala","Blend","Masala Blends")]
PACKS=[(50,"g"),(100,"g"),(200,"g"),(500,"g"),(1,"kg")]
FIRST=["Aarav","Aditi","Anaya","Arjun","Diya","Ishaan","Kavya","Meera","Neel","Rohan","Saanvi","Sahil","Tanvi","Vedant","Yash","Priya","Om","Sakshi","Mihir","Riya"]
LAST=["Patil","Shinde","Jadhav","Deshmukh","Pawar","Kulkarni","More","Chavan","Joshi","Kadam"]
CITIES=["Pune","Mumbai","Nashik","Kolhapur","Nagpur","Satara","Sangli","Thane","Aurangabad","Solapur"]
SUPPLIER_NAMES=["Sahyadri Agro Supplies","Deccan Spice Traders","Western Ghats Commodities","Konkan Farm Produce","Reliable Food Ingredients","Harvest Basket Trading","Maharashtra Spice Link","Green Field Suppliers","Market Square Commodities","Sunrise Raw Materials","Golden Grain Partners","Pure Origin Traders"]
def write(path,rows,fields):
    with path.open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def generate(out,seed):
    rng=random.Random(seed);out.mkdir(parents=True,exist_ok=True)
    products=[];variants=[]
    for i in range(1,101):
        fam,ing,cat=FAMILIES[(i-1)%len(FAMILIES)]
        if i>len(FAMILIES): fam+=(" Premium" if i%2 else " Classic")
        pid=f"PRD-{i:04d}";products.append(dict(product_id=pid,sku_family=f"YS-{i:04d}",name=fam,category=cat,primary_ingredient=ing,description=f"Synthetic demo product: {fam}; verify specifications before production use.",is_active="true"))
        base=rng.uniform(35,260)
        for j,(amt,unit) in enumerate(PACKS,1):
            grams=amt*1000 if unit=="kg" else amt
            variants.append(dict(variant_id=f"VAR-{i:04d}-{j}",product_id=pid,sku=f"YS-{i:04d}-{j}",pack_size=amt,pack_unit=unit,net_weight_g=grams,list_price_inr=f"{max(18,base*grams/100*rng.uniform(.92,1.12)):.2f}",is_active="true"))
    write(out/"products.csv",products,list(products[0]));write(out/"variants.csv",variants,list(variants[0]))
    suppliers=[dict(supplier_id=f"SUP-{i:03d}",name=SUPPLIER_NAMES[i-1],contact_name=f"{FIRST[i*3%20]} {LAST[i*2%10]}",city=CITIES[i*2%10],state="Maharashtra",email=f"supplier{i:02d}@example.test",phone=f"900000{i:04d}",is_synthetic="true") for i in range(1,13)]
    warehouses=[dict(warehouse_id="WH-MH-01",name="Primary Warehouse",region="Maharashtra",is_primary="true"),dict(warehouse_id="WH-MH-02",name="Dispatch Warehouse",region="Maharashtra",is_primary="false")]
    write(out/"suppliers.csv",suppliers,list(suppliers[0]));write(out/"warehouses.csv",warehouses,list(warehouses[0]))
    batches=[]
    for i,v in enumerate(variants[:200],1):
        made=AS_OF-timedelta(days=rng.randint(15,220));expiry=made+timedelta(days=rng.choice([180,270,365,540]))
        q=rng.choices(["approved","held","rejected"],weights=[80,15,5])[0]
        batches.append(dict(batch_id=f"BAT-{i:05d}",variant_id=v["variant_id"],supplier_id=f"SUP-{rng.randint(1,12):03d}",warehouse_id=warehouses[rng.randrange(2)]["warehouse_id"],batch_code=f"YS{made:%y%m}-{i:05d}",manufactured_on=made.isoformat(),expires_on=expiry.isoformat(),quality_status=q,status="released" if q=="approved" else q,public_token=f"demo-{i:05d}-{rng.getrandbits(32):08x}",is_synthetic="true"))
    write(out/"production_batches.csv",batches,list(batches[0]))
    balances=[];moves=[]
    for b in batches:
        if b["status"]!="released" or date.fromisoformat(b["expires_on"])<=AS_OF: continue
        qty=rng.randint(40,800);bid=f"BAL-{len(balances)+1:05d}"
        balances.append(dict(balance_id=bid,warehouse_id=b["warehouse_id"],batch_id=b["batch_id"],on_hand_qty=qty,reserved_qty=0,unit="packs"))
        moves.append(dict(movement_id=f"MOV-{len(moves)+1:06d}",warehouse_id=b["warehouse_id"],batch_id=b["batch_id"],movement_type="receipt",quantity_delta=qty,reference_type="seed",reference_id=b["batch_id"],occurred_at=b["manufactured_on"]+"T09:00:00",notes="Synthetic opening balance"))
    write(out/"inventory_balances.csv",balances,["balance_id","warehouse_id","batch_id","on_hand_qty","reserved_qty","unit"])
    write(out/"stock_movements.csv",moves,["movement_id","warehouse_id","batch_id","movement_type","quantity_delta","reference_type","reference_id","occurred_at","notes"])
    dealers=[]
    for i in range(1,151):
        dealers.append(dict(dealer_id=f"DLR-{i:04d}",business_name=f"{FIRST[i*7%20]} {LAST[i*3%10]} Trading",city=CITIES[i%10],state="Maharashtra",email=f"dealer{i:04d}@example.test",credit_limit_inr=f"{rng.choice([25000,50000,75000,100000]):.2f}",is_synthetic="true"))
    write(out/"dealers.csv",dealers,list(dealers[0]))
    start=AS_OF-timedelta(days=730);orders=[];items=[];statuses=["delivered","dispatched","confirmed","cancelled"]
    for i in range(1,10001):
        d=start+timedelta(days=rng.randint(0,729));dealer=dealers[rng.randrange(len(dealers))];status=rng.choices(statuses,weights=[62,18,15,5])[0];subtotal=0;selected=rng.sample(variants,rng.randint(1,4))
        for j,v in enumerate(selected,1):
            qty=rng.randint(2,80);price=float(v["list_price_inr"]);disc=rng.choice([0,0,0,2,5,8]);total=round(qty*price*(1-disc/100),2);subtotal+=total
            items.append(dict(order_item_id=f"OI-{i:06d}-{j}",order_id=f"ORD-{i:06d}",variant_id=v["variant_id"],quantity=qty,unit_price_inr=f"{price:.2f}",discount_percent=disc,line_total_inr=f"{total:.2f}"))
        orders.append(dict(order_id=f"ORD-{i:06d}",dealer_id=dealer["dealer_id"],ordered_at=f"{d.isoformat()}T{rng.randint(8,19):02d}:{rng.randint(0,59):02d}:00",status=status,channel=rng.choice(["dealer_portal","staff_entry","agent_draft"]),subtotal_inr=f"{subtotal:.2f}",currency="INR",is_synthetic="true"))
    write(out/"orders.csv",orders,list(orders[0]));write(out/"order_items.csv",items,list(items[0]))
    sales=[]
    for pi,p in enumerate(products,1):
        base=rng.uniform(4,42);price=float(variants[(pi-1)*5]["list_price_inr"])/10
        # Per-product seasonal profile: amplitude scales how strong this
        # product's seasonal swing is, phase shifts its peak month. Without
        # this every product shares one seasonal curve and any forecasting
        # model looks better than it really is.
        amp=rng.uniform(.55,1.45);phase=rng.randint(0,11)
        for offset in range(730):
            d=start+timedelta(days=offset)
            sm=((d.month-1-phase)%12)+1
            base_s=1.22 if sm in (10,11,12) else (1.12 if sm in (3,4) else 1)
            season=1+(base_s-1)*amp;weekend=1.1 if d.weekday()>=5 else 1
            qty=max(0,round(base*season*weekend*rng.uniform(.45,1.65)))
            sales.append(dict(date=d.isoformat(),product_id=p["product_id"],units_sold=qty,revenue_inr=f"{qty*price:.2f}",source="synthetic_simulation"))
    write(out/"sales_history_daily.csv",sales,list(sales[0]))
    write(out/"sales_history.csv",sales,list(sales[0]))
    counts={n:len(rows) for n,rows in [("products",products),("variants",variants),("suppliers",suppliers),("warehouses",warehouses),("production_batches",batches),("inventory_balances",balances),("stock_movements",moves),("dealers",dealers),("orders",orders),("order_items",items),("sales_history_daily",sales)]}
    (out/"manifest.json").write_text(json.dumps({"dataset_type":"synthetic_demo_data","seed":seed,"as_of_date":AS_OF.isoformat(),"warning":"All records are fictional; not actual sponsor data.","record_counts":counts},indent=2),encoding="utf-8")
    return counts
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--output",default="data");ap.add_argument("--seed",type=int,default=42);a=ap.parse_args()
    for k,v in generate(Path(a.output),a.seed).items(): print(f"{k}: {v}")
