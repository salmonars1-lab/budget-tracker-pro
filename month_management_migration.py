import sqlite3
from datetime import datetime

def migrate_month_management():
    conn = sqlite3.connect('budget_tracker.db')
    
    # Add month transition tracking table
    conn.execute('''
        CREATE TABLE IF NOT EXISTS month_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_month INTEGER NOT NULL,
            from_year INTEGER NOT NULL,
            to_month INTEGER NOT NULL,
            to_year INTEGER NOT NULL,
            transition_date DATE NOT NULL,
            sinking_funds_contributed BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Add index for faster lookups
    conn.execute('CREATE INDEX IF NOT EXISTS idx_budget_periods_month_year ON budget_periods(month, year)')
    
    conn.commit()
    conn.close()
    print("Month management migration complete!")

if __name__ == '__main__':
    migrate_month_management()
