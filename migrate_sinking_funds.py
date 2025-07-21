import sqlite3

def migrate_database():
    conn = sqlite3.connect('budget_tracker.db')
    
    # Add new columns to transactions table
    try:
        conn.execute('ALTER TABLE transactions ADD COLUMN sinking_fund_id INTEGER')
        print("Added sinking_fund_id column")
    except sqlite3.OperationalError:
        print("sinking_fund_id column already exists")
    
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN transaction_type TEXT DEFAULT 'expense'")
        print("Added transaction_type column")
    except sqlite3.OperationalError:
        print("transaction_type column already exists")
    
    # Create sinking_funds table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sinking_funds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            target_amount DECIMAL(10,2),
            current_balance DECIMAL(10,2) DEFAULT 0,
            monthly_allocation DECIMAL(10,2) DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Insert your sinking funds
    sinking_funds_data = [
        ('Wife/Household', 50000, 20000),
        ('Vehicle', 100000, 24500), 
        ('Emergency Fund', 200000, 1947),
        ('Discretionary', 75000, 15000),
        ('Assembly/Convention', 30000, 9167)
    ]
    
    for name, target, monthly in sinking_funds_data:
        conn.execute(
            'INSERT OR IGNORE INTO sinking_funds (name, target_amount, monthly_allocation) VALUES (?, ?, ?)',
            (name, target, monthly)
        )
    
    conn.commit()
    conn.close()
    print("Migration complete!")

if __name__ == '__main__':
    migrate_database()
