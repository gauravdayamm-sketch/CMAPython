import sqlite3

conn = sqlite3.connect('db/cma.sqlite')

tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'"
).fetchall()

print('Tables found:')
for t in tables:
    print(' -', t[0])

conn.close()
print('Database connection OK')