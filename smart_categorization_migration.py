import sqlite3

def migrate_smart_categorization():
    conn = sqlite3.connect('budget_tracker.db')
    
    # Create table to track description patterns
    conn.execute('''
        CREATE TABLE IF NOT EXISTS categorization_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description_pattern TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            usage_count INTEGER DEFAULT 1,
            last_used DATE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories (id),
            UNIQUE(description_pattern, category_id)
        )
    ''')
    
    # Create index for faster pattern lookups
    conn.execute('CREATE INDEX IF NOT EXISTS idx_categorization_patterns_description ON categorization_patterns(description_pattern)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_categorization_patterns_usage ON categorization_patterns(usage_count DESC)')
    
    # Populate initial patterns from existing transactions
    existing_patterns = conn.execute('''
        SELECT LOWER(TRIM(description)) as pattern, category_id, COUNT(*) as count, MAX(date) as last_date
        FROM transactions 
        WHERE description != '' AND category_id IS NOT NULL
        GROUP BY LOWER(TRIM(description)), category_id
        HAVING count >= 2
    ''').fetchall()
    
    for pattern in existing_patterns:
        conn.execute('''
            INSERT OR REPLACE INTO categorization_patterns 
            (description_pattern, category_id, usage_count, last_used)
            VALUES (?, ?, ?, ?)
        ''', (pattern[0], pattern[1], pattern[2], pattern[3]))
    
    conn.commit()
    conn.close()
    print("Smart categorization migration complete!")
    print(f"Added {len(existing_patterns)} initial patterns from existing transactions")

if __name__ == '__main__':
    migrate_smart_categorization()

