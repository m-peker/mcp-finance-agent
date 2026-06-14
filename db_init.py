"""
Finance Assistant — Database Initialization Module
====================================================
Generates synthetic financial data and persists it to a SQLite database.
In a real project, this file would be replaced by a CSV/Excel importer
or a connection to an existing database.

Tables created:
    accounts     → Bank accounts and current balances
    categories   → Income / expense categories
    transactions → All financial transaction records (2024–2025)
    budgets      → Monthly category budget limits
    invoices     → Invoices and payment statuses

Usage:
    python db_init.py
"""

import sqlite3
import random
import os
from datetime import datetime, timedelta

# ── Configuration ────────────────────────────────────────────────────────────

DB_PATH  = "finance.db"
SEED     = 42                       # Fixed seed for reproducible data
START_DT = datetime(2024, 1, 1)
END_DT   = datetime(2025, 12, 31)

random.seed(SEED)

# Remove existing database for a clean start
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# ── Table Definitions ────────────────────────────────────────────────────────

cursor.executescript("""
    -- Accounts: checking, savings, credit card, cash, etc.
    CREATE TABLE accounts (
        account_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT    NOT NULL,
        account_type TEXT    NOT NULL,   -- checking | savings | credit | cash
        balance      REAL    NOT NULL,
        currency     TEXT    DEFAULT 'TRY'
    );

    -- Categories: each transaction belongs to a category
    CREATE TABLE categories (
        category_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL,
        category_type TEXT NOT NULL      -- income | expense
    );

    -- Transactions: all income and expense records
    CREATE TABLE transactions (
        transaction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_date TEXT    NOT NULL,  -- YYYY-MM-DD format
        amount           REAL    NOT NULL,
        transaction_type TEXT    NOT NULL,  -- income | expense | transfer
        category_id      INTEGER REFERENCES categories(category_id),
        account_id       INTEGER REFERENCES accounts(account_id),
        description      TEXT,
        status           TEXT    DEFAULT 'completed'  -- completed | pending | cancelled
    );

    -- Budgets: monthly category spending limits and actual amounts
    CREATE TABLE budgets (
        budget_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id  INTEGER REFERENCES categories(category_id),
        month        INTEGER NOT NULL,   -- 1–12
        year         INTEGER NOT NULL,
        limit_amount REAL    NOT NULL,   -- planned limit
        spent_amount REAL    NOT NULL DEFAULT 0  -- actual spending
    );

    -- Invoices: payment status and due date tracking
    CREATE TABLE invoices (
        invoice_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        vendor_name    TEXT    NOT NULL,
        invoice_date   TEXT    NOT NULL,  -- YYYY-MM-DD
        due_date       TEXT    NOT NULL,  -- YYYY-MM-DD
        amount         REAL    NOT NULL,
        status         TEXT    DEFAULT 'pending',  -- paid | pending | overdue
        category_id    INTEGER REFERENCES categories(category_id),
        transaction_id INTEGER REFERENCES transactions(transaction_id)
    );
""")

# ── Accounts ─────────────────────────────────────────────────────────────────

ACCOUNTS = [
    ("Checking Account",  "checking",  45_230.75, "TRY"),
    ("Savings Account",   "savings",  120_000.00, "TRY"),
    ("Credit Card",       "credit",    -8_450.20, "TRY"),
    ("Cash",              "cash",       2_500.00, "TRY"),
    ("Foreign Currency",  "savings",    3_200.00, "USD"),
]

cursor.executemany(
    "INSERT INTO accounts (account_name, account_type, balance, currency) VALUES (?, ?, ?, ?)",
    ACCOUNTS,
)

# ── Categories ───────────────────────────────────────────────────────────────
# Income categories get IDs 1–5, expense categories get IDs 6–15.
# This ordering is used directly by transactions and budgets below.

CATEGORIES = [
    # --- Income categories ---
    ("Salary",           "income"),   # ID: 1
    ("Freelance Income", "income"),   # ID: 2
    ("Rental Income",    "income"),   # ID: 3
    ("Interest Income",  "income"),   # ID: 4
    ("Other Income",     "income"),   # ID: 5
    # --- Expense categories ---
    ("Rent",             "expense"),  # ID: 6
    ("Groceries",        "expense"),  # ID: 7
    ("Transportation",   "expense"),  # ID: 8
    ("Utilities",        "expense"),  # ID: 9
    ("Healthcare",       "expense"),  # ID: 10
    ("Entertainment",    "expense"),  # ID: 11
    ("Dining Out",       "expense"),  # ID: 12
    ("Education",        "expense"),  # ID: 13
    ("Clothing",         "expense"),  # ID: 14
    ("Miscellaneous",    "expense"),  # ID: 15
]

cursor.executemany(
    "INSERT INTO categories (category_name, category_type) VALUES (?, ?)",
    CATEGORIES,
)

# ── Transactions ─────────────────────────────────────────────────────────────
# Simulates realistic daily spending patterns across 2024–2025.

# Sample description texts per expense category
DESCRIPTIONS = {
    7:  ["Grocery shopping", "Weekly groceries", "Supermarket run"],
    8:  ["Taxi ride", "Metro card top-up", "Fuel", "Parking fee"],
    9:  ["Electricity bill", "Natural gas bill", "Internet bill", "Water bill"],
    11: ["Movie ticket", "Concert ticket", "Netflix subscription", "Game purchase"],
    12: ["Lunch", "Dinner out", "Coffee", "Restaurant"],
    15: ["Miscellaneous expense", "Online shopping", "Gift purchase"],
}

transactions = []
current_date = START_DT

while current_date <= END_DT:
    day = current_date.day

    # Salary deposits on the 5th of each month
    if day == 5:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(18_000, 22_000), 2),
            "income",
            1,   # Salary category
            1,   # Checking Account
            "Monthly salary deposit",
            "completed",
        ))

    # Rent payment on the 1st of each month
    if day == 1:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(8_000, 9_000), 2),
            "expense",
            6,   # Rent category
            1,   # Checking Account
            "Monthly rent payment",
            "completed",
        ))

    # Random daily expenses — 60% chance per day
    if random.random() < 0.60:
        cat_id = random.choice([7, 8, 9, 11, 12, 15])
        desc   = random.choice(DESCRIPTIONS.get(cat_id, ["Expense"]))
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(50, 800), 2),
            "expense",
            cat_id,
            random.choice([1, 3, 4]),   # Checking, Credit Card, or Cash
            desc,
            "completed",
        ))

    # Freelance income — a few times per month, ~5% probability
    if random.random() < 0.05:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(2_000, 10_000), 2),
            "income",
            2,   # Freelance Income category
            1,   # Checking Account
            "Freelance project payment",
            "completed",
        ))

    current_date += timedelta(days=1)

cursor.executemany(
    """INSERT INTO transactions
       (transaction_date, amount, transaction_type, category_id, account_id, description, status)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    transactions,
)

# ── Budgets ──────────────────────────────────────────────────────────────────
# Monthly budget limits for each expense category.
# Actual spending (spent_amount) sometimes exceeds the limit — intentional realism.

BUDGET_LIMITS = {
    6:  9_000,   # Rent
    7:  3_000,   # Groceries
    8:  1_500,   # Transportation
    9:  1_200,   # Utilities
    10:   500,   # Healthcare
    11: 1_000,   # Entertainment
    12: 1_500,   # Dining Out
    13: 2_000,   # Education
    14: 1_500,   # Clothing
    15:   500,   # Miscellaneous
}

budgets = []
for year in [2024, 2025]:
    for month in range(1, 13):
        for cat_id, limit in BUDGET_LIMITS.items():
            # Actual spending varies between 0.5× and 1.3× the limit
            spent = round(random.uniform(0.5, 1.3) * limit, 2)
            budgets.append((cat_id, month, year, limit, spent))

cursor.executemany(
    "INSERT INTO budgets (category_id, month, year, limit_amount, spent_amount) VALUES (?, ?, ?, ?, ?)",
    budgets,
)

# ── Invoices ─────────────────────────────────────────────────────────────────
# Simulates regular subscription and utility invoices.

VENDORS = [
    ("Power Distribution Co.",  9),   # Utilities category
    ("Natural Gas Co.",         9),
    ("Internet Provider",       9),
    ("Water Authority",         9),
    ("Insurance Company",      10),   # Healthcare category
    ("Netflix",                11),   # Entertainment category
    ("Spotify",                11),
]

invoices = []
TODAY = datetime(2026, 1, 1)   # Reference "today" date (for due date calculations)

for _ in range(200):
    vendor, cat_id = random.choice(VENDORS)
    invoice_date   = START_DT + timedelta(days=random.randint(0, 730))
    due_date       = invoice_date + timedelta(days=30)

    # Overdue invoices are mostly paid; a small fraction remain overdue
    if due_date < TODAY:
        status = random.choices(["paid", "overdue"], weights=[75, 25])[0]
    else:
        status = random.choices(["pending", "paid"], weights=[60, 40])[0]

    invoices.append((
        vendor,
        invoice_date.strftime("%Y-%m-%d"),
        due_date.strftime("%Y-%m-%d"),
        round(random.uniform(100, 2_500), 2),
        status,
        cat_id,
        None,   # transaction_id — not mapped in this sample
    ))

cursor.executemany(
    """INSERT INTO invoices
       (vendor_name, invoice_date, due_date, amount, status, category_id, transaction_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    invoices,
)

# ── Save and Close ────────────────────────────────────────────────────────────

conn.commit()
conn.close()

print(f"\n✅ Database created: {DB_PATH}")
print(f"  {len(ACCOUNTS):>6}  accounts")
print(f"  {len(CATEGORIES):>6}  categories")
print(f"  {len(transactions):>6}  transactions")
print(f"  {len(budgets):>6}  budget records")
print(f"  {len(invoices):>6}  invoices")
