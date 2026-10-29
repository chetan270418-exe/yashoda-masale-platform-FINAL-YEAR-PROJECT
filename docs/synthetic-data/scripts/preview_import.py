import csv,json
from pathlib import Path
data=Path(__file__).resolve().parents[1]/"data"
manifest=json.loads((data/"manifest.json").read_text(encoding="utf-8"))
print("Dataset:",manifest["dataset_type"],"| seed:",manifest["seed"])
print("Read-only preview. This script does not connect to PostgreSQL.")
for p in sorted(data.glob("*.csv")):
    with p.open(newline="",encoding="utf-8-sig") as f:n=sum(1 for _ in csv.DictReader(f))
    print(f"{p.name:28} {n:>7} rows")
print("Map columns to your real SQLAlchemy models before adding any DB writes.")
