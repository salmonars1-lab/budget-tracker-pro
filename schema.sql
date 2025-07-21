-- Budget Tracker Database Schema

-- Main categories (Car Expenses, Food, etc.)
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Subcategories (Insurance under Car Expenses, Groceries under Food)
CREATE TABLE IF NOT EXISTS subcategories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories (id),
    UNIQUE(category_id, name)
);

-- Budget allocations with history tracking
CREATE TABLE IF NOT EXISTS budget_periods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    month INTEGER NOT NULL,
    year INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(month, year)
);

CREATE TABLE IF NOT EXISTS budget_allocations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    budget_period_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    subcategory_id INTEGER NULL,
    budgeted_amount DECIMAL(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (budget_period_id) REFERENCES budget_periods (id),
    FOREIGN KEY (category_id) REFERENCES categories (id),
    FOREIGN KEY (subcategory_id) REFERENCES subcategories (id)
);

-- Sinking funds table
CREATE TABLE IF NOT EXISTS sinking_funds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    target_amount DECIMAL(10,2),
    current_balance DECIMAL(10,2) DEFAULT 0,
    monthly_allocation DECIMAL(10,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Transactions (updated with sinking fund support)
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date DATE NOT NULL,
    description TEXT NOT NULL,
    amount DECIMAL(10,2) NOT NULL,
    category_id INTEGER NOT NULL,
    subcategory_id INTEGER NULL,
    sinking_fund_id INTEGER NULL,
    transaction_type TEXT DEFAULT 'expense', -- 'expense', 'contribution', 'withdrawal'
    notes TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (category_id) REFERENCES categories (id),
    FOREIGN KEY (subcategory_id) REFERENCES subcategories (id),
    FOREIGN KEY (sinking_fund_id) REFERENCES sinking_funds (id)
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_sinking_fund ON transactions(sinking_fund_id);
CREATE INDEX IF NOT EXISTS idx_budget_period ON budget_allocations(budget_period_id);

-- Insert your current categories
INSERT OR IGNORE INTO categories (name) VALUES 
('Car Loan Payment'),
('Car Expenses (Ins. + Maint.)'),
('Prof. Dues + Prime'),
('Convention & Assembly'),
('Emergency Fund Build-Up'),
('Credit Card Minimum Payment'),
('Food'),
('Gas'),
('Utilities & Internet'),
('Online Spending (USD subs + health)'),
('Wife Support'),
('Subscriptions (local & digital)');

-- Example subcategories
INSERT OR IGNORE INTO subcategories (category_id, name) 
SELECT c.id, s.name FROM categories c
JOIN (
    VALUES 
    ('Car Expenses (Ins. + Maint.)', 'Insurance'),
    ('Car Expenses (Ins. + Maint.)', 'Maintenance'),
    ('Car Expenses (Ins. + Maint.)', 'Registration'),
    ('Food', 'Groceries'),
    ('Food', 'Restaurants'),
    ('Food', 'Takeout'),
    ('Utilities & Internet', 'Electricity'),
    ('Utilities & Internet', 'Water'),
    ('Utilities & Internet', 'Internet'),
    ('Utilities & Internet', 'Phone')
) s(category_name, subcategory_name) ON c.name = s.category_name;

-- Insert sinking funds
INSERT OR IGNORE INTO sinking_funds (name, target_amount, monthly_allocation) VALUES 
('Wife/Household', 50000, 20000),
('Vehicle', 100000, 24500),
('Emergency Fund', 200000, 1947),
('Discretionary', 75000, 15000),
('Assembly/Convention', 30000, 9167);
