"""
Finans Asistanı — Veritabanı Başlatma Modülü
=============================================
Sentetik finans verisi üretir ve SQLite veritabanına kaydeder.
Gerçek bir projede bu dosyanın yerini CSV/Excel yükleyicisi
veya mevcut bir veritabanı bağlantısı alabilir.

Oluşturulan tablolar:
    accounts     → Banka hesapları ve anlık bakiyeler
    categories   → Gelir / gider kategorileri
    transactions → Tüm finansal işlem kayıtları (2024–2025)
    budgets      → Aylık kategori bütçe limitleri
    invoices     → Faturalar ve ödeme durumları

Kullanım:
    python db_init.py
"""

import sqlite3
import random
import os
from datetime import datetime, timedelta

# ── Yapılandırma ───────────────────────────────────────────────────────────────

DB_PATH  = "finance.db"
SEED     = 42                       # Tekrar üretilebilir veri için sabit seed
START_DT = datetime(2024, 1, 1)
END_DT   = datetime(2025, 12, 31)

random.seed(SEED)

# Mevcut veritabanını sil, temiz bir başlangıç yap
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# ── Tablo Tanımları ────────────────────────────────────────────────────────────

cursor.executescript("""
    -- Hesaplar: banka hesabı, kredi kartı, nakit vb.
    CREATE TABLE accounts (
        account_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        account_name TEXT    NOT NULL,
        account_type TEXT    NOT NULL,   -- checking | savings | credit | cash
        balance      REAL    NOT NULL,
        currency     TEXT    DEFAULT 'TRY'
    );

    -- Kategoriler: her işlem bir kategoriye atanır
    CREATE TABLE categories (
        category_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        category_name TEXT NOT NULL,
        category_type TEXT NOT NULL      -- income (gelir) | expense (gider)
    );

    -- İşlemler: tüm gelir ve gider kayıtları
    CREATE TABLE transactions (
        transaction_id   INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_date TEXT    NOT NULL,  -- YYYY-MM-DD formatında
        amount           REAL    NOT NULL,
        transaction_type TEXT    NOT NULL,  -- income | expense | transfer
        category_id      INTEGER REFERENCES categories(category_id),
        account_id       INTEGER REFERENCES accounts(account_id),
        description      TEXT,
        status           TEXT    DEFAULT 'completed'  -- completed | pending | cancelled
    );

    -- Bütçeler: aylık bazda kategori harcama limitleri ve gerçekleşen tutarlar
    CREATE TABLE budgets (
        budget_id    INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id  INTEGER REFERENCES categories(category_id),
        month        INTEGER NOT NULL,   -- 1–12
        year         INTEGER NOT NULL,
        limit_amount REAL    NOT NULL,   -- planlanan limit
        spent_amount REAL    NOT NULL DEFAULT 0  -- gerçekleşen harcama
    );

    -- Faturalar: ödeme durumu ve vade takibi
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

# ── Hesaplar ───────────────────────────────────────────────────────────────────

ACCOUNTS = [
    ("Vadesiz Hesap",  "checking",  45_230.75, "TRY"),
    ("Birikim Hesabı", "savings",  120_000.00, "TRY"),
    ("Kredi Kartı",    "credit",    -8_450.20, "TRY"),
    ("Nakit",          "cash",       2_500.00, "TRY"),
    ("Döviz Hesabı",   "savings",    3_200.00, "USD"),
]

cursor.executemany(
    "INSERT INTO accounts (account_name, account_type, balance, currency) VALUES (?, ?, ?, ?)",
    ACCOUNTS,
)

# ── Kategoriler ────────────────────────────────────────────────────────────────
# Gelir kategorileri ID 1–5, gider kategorileri ID 6–15 olarak sıralanır.
# Bu sıra aşağıdaki işlem ve bütçe verilerinde doğrudan ID olarak kullanılır.

CATEGORIES = [
    # --- Gelir kategorileri ---
    ("Maaş",            "income"),   # ID: 1
    ("Freelance Gelir", "income"),   # ID: 2
    ("Kira Geliri",     "income"),   # ID: 3
    ("Faiz Geliri",     "income"),   # ID: 4
    ("Diğer Gelir",     "income"),   # ID: 5
    # --- Gider kategorileri ---
    ("Kira",            "expense"),  # ID: 6
    ("Market",          "expense"),  # ID: 7
    ("Ulaşım",          "expense"),  # ID: 8
    ("Faturalar",       "expense"),  # ID: 9
    ("Sağlık",          "expense"),  # ID: 10
    ("Eğlence",         "expense"),  # ID: 11
    ("Restoran",        "expense"),  # ID: 12
    ("Eğitim",          "expense"),  # ID: 13
    ("Giyim",           "expense"),  # ID: 14
    ("Diğer Gider",     "expense"),  # ID: 15
]

cursor.executemany(
    "INSERT INTO categories (category_name, category_type) VALUES (?, ?)",
    CATEGORIES,
)

# ── İşlemler ───────────────────────────────────────────────────────────────────
# 2024–2025 boyunca gerçekçi günlük harcama örüntüleri simüle edilir.

# Her gider kategorisi için örnek açıklama metinleri
DESCRIPTIONS = {
    7:  ["Market alışverişi", "Haftalık market", "İndirimli market alışverişi"],
    8:  ["Taksi", "Metro kartı yükleme", "Yakıt", "Otopark ücreti"],
    9:  ["Elektrik faturası", "Doğalgaz faturası", "İnternet faturası", "Su faturası"],
    11: ["Sinema bileti", "Konser", "Netflix aboneliği", "Oyun satın alma"],
    12: ["Öğle yemeği", "Akşam yemeği dışarıda", "Kahve", "Restoran"],
    15: ["Çeşitli harcama", "Online alışveriş", "Hediye"],
}

transactions = []
current_date = START_DT

while current_date <= END_DT:
    day = current_date.day

    # Her ayın 5'inde maaş yatar
    if day == 5:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(18_000, 22_000), 2),
            "income",
            1,   # Maaş kategorisi
            1,   # Vadesiz Hesap
            "Aylık maaş",
            "completed",
        ))

    # Her ayın 1'inde kira ödenir
    if day == 1:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(8_000, 9_000), 2),
            "expense",
            6,   # Kira kategorisi
            1,   # Vadesiz Hesap
            "Aylık kira ödemesi",
            "completed",
        ))

    # Günlük rastgele harcamalar — %60 olasılıkla gerçekleşir
    if random.random() < 0.60:
        cat_id = random.choice([7, 8, 9, 11, 12, 15])
        desc   = random.choice(DESCRIPTIONS.get(cat_id, ["Harcama"]))
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(50, 800), 2),
            "expense",
            cat_id,
            random.choice([1, 3, 4]),   # Vadesiz, Kredi Kartı veya Nakit
            desc,
            "completed",
        ))

    # Freelance geliri — ayda birkaç kez, %5 olasılıkla
    if random.random() < 0.05:
        transactions.append((
            current_date.strftime("%Y-%m-%d"),
            round(random.uniform(2_000, 10_000), 2),
            "income",
            2,   # Freelance Gelir kategorisi
            1,
            "Freelance proje ödemesi",
            "completed",
        ))

    current_date += timedelta(days=1)

cursor.executemany(
    """INSERT INTO transactions
       (transaction_date, amount, transaction_type, category_id, account_id, description, status)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    transactions,
)

# ── Bütçeler ───────────────────────────────────────────────────────────────────
# Her gider kategorisi için aylık bütçe limiti belirlenir.
# Gerçekleşen harcama (spent_amount) bütçeyi bazen aşar — bu gerçekçilik için kasıtlı.

BUDGET_LIMITS = {
    6:  9_000,   # Kira
    7:  3_000,   # Market
    8:  1_500,   # Ulaşım
    9:  1_200,   # Faturalar
    10:   500,   # Sağlık
    11: 1_000,   # Eğlence
    12: 1_500,   # Restoran
    13: 2_000,   # Eğitim
    14: 1_500,   # Giyim
    15:   500,   # Diğer Gider
}

budgets = []
for year in [2024, 2025]:
    for month in range(1, 13):
        for cat_id, limit in BUDGET_LIMITS.items():
            # 0.5× ile 1.3× arasında değişen gerçekleşen harcama
            spent = round(random.uniform(0.5, 1.3) * limit, 2)
            budgets.append((cat_id, month, year, limit, spent))

cursor.executemany(
    "INSERT INTO budgets (category_id, month, year, limit_amount, spent_amount) VALUES (?, ?, ?, ?, ?)",
    budgets,
)

# ── Faturalar ──────────────────────────────────────────────────────────────────
# Düzenli abonelik ve hizmet faturaları simüle edilir.

VENDORS = [
    ("Elektrik Dağıtım A.Ş.", 9),   # Faturalar kategorisi
    ("Doğalgaz A.Ş.",         9),
    ("İnternet Sağlayıcı",    9),
    ("Su İdaresi",            9),
    ("Sigorta Şirketi",      10),   # Sağlık kategorisi
    ("Netflix",              11),   # Eğlence kategorisi
    ("Spotify",              11),
]

invoices = []
TODAY = datetime(2026, 1, 1)   # Referans "bugün" tarihi (vade hesabı için)

for _ in range(200):
    vendor, cat_id = random.choice(VENDORS)
    invoice_date   = START_DT + timedelta(days=random.randint(0, 730))
    due_date       = invoice_date + timedelta(days=30)

    # Vadesi geçmiş faturalar büyük çoğunlukla ödenmiş; az bir kısmı gecikmiş kalmış
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
        None,   # transaction_id — bu örnekte fatura-işlem eşleştirmesi yapılmadı
    ))

cursor.executemany(
    """INSERT INTO invoices
       (vendor_name, invoice_date, due_date, amount, status, category_id, transaction_id)
       VALUES (?, ?, ?, ?, ?, ?, ?)""",
    invoices,
)

# ── Kaydet ve Kapat ────────────────────────────────────────────────────────────

conn.commit()
conn.close()

print(f"\nVeritabanı oluşturuldu: {DB_PATH}")
print(f"  {len(ACCOUNTS):>6}  hesap")
print(f"  {len(CATEGORIES):>6}  kategori")
print(f"  {len(transactions):>6}  işlem")
print(f"  {len(budgets):>6}  bütçe kaydı")
print(f"  {len(invoices):>6}  fatura")
