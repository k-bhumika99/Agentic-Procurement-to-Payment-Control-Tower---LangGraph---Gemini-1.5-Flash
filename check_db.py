import sqlite3
db = sqlite3.connect('procurement.db')
tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("Tables:", tables)
for t in tables:
    cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})").fetchall()]
    print(f"  {t}: {cols}")
