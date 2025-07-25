import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
import sqlite3
import os
from datetime import datetime, date
from decimal import Decimal

app = Flask(__name__)

# Database configuration
DATABASE = 'budget_tracker.db'

def get_db_connection():
    import time
    max_retries = 3
    
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DATABASE, timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            conn.execute('PRAGMA journal_mode=WAL;')
            conn.execute('PRAGMA synchronous=NORMAL;')
            conn.execute('PRAGMA cache_size=1000;')
            conn.execute('PRAGMA temp_store=memory;')
            return conn
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.1 * (attempt + 1))  # Short exponential backoff
                continue
            raise
    raise sqlite3.OperationalError("Could not connect to database after retries")


def init_db():
    """Initialize the database with tables and sample data"""
    conn = get_db_connection()
    
    # Read and execute the schema
    with open('schema.sql', 'r') as f:
        conn.executescript(f.read())
    
    conn.close()

def init_db_if_needed():
    """Initialize database if tables don't exist"""
    conn = get_db_connection()
    
    try:
        # Check if main tables exist
        result = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='categories'"
        ).fetchone()
        
        if not result:
            print("Database tables not found. Initializing database...")
            # Tables don't exist, initialize database
            with open('schema.sql', 'r') as f:
                conn.executescript(f.read())
            conn.commit()
            print("Database initialized successfully!")
        else:
            print("Database tables found. Skipping initialization.")
    
    except Exception as e:
        print(f"Error checking/initializing database: {e}")
    finally:
        conn.close()

def learn_categorization_pattern(description, category_id):
    """Learn from user's categorization choice"""
    if not description or not category_id:
        return
    
    pattern = description.lower().strip()
    conn = get_db_connection()
    
    # Check if pattern already exists
    existing = conn.execute(
        'SELECT id, usage_count FROM categorization_patterns WHERE description_pattern = ? AND category_id = ?',
        (pattern, category_id)
    ).fetchone()
    
    if existing:
        # Increment usage count
        conn.execute(
            'UPDATE categorization_patterns SET usage_count = usage_count + 1, last_used = DATE("now") WHERE id = ?',
            (existing['id'],)
        )
    else:
        # Create new pattern
        conn.execute(
            'INSERT INTO categorization_patterns (description_pattern, category_id, last_used) VALUES (?, ?, DATE("now"))',
            (pattern, category_id)
        )
    
    conn.commit()
    conn.close()

def get_category_suggestions(description):
    """Get category suggestions based on description"""
    if not description or len(description.strip()) < 3:
        return []
    
    pattern = description.lower().strip()
    conn = get_db_connection()
    
    # Look for exact matches first
    exact_matches = conn.execute('''
        SELECT cp.category_id, c.name, cp.usage_count
        FROM categorization_patterns cp
        JOIN categories c ON cp.category_id = c.id
        WHERE cp.description_pattern = ?
        ORDER BY cp.usage_count DESC, cp.last_used DESC
        LIMIT 3
    ''', (pattern,)).fetchall()
    
    if exact_matches:
        conn.close()
        return [{'id': row['category_id'], 'name': row['name'], 'confidence': 'high'} for row in exact_matches]
    
    # Look for partial matches (words in common)
    words = [word for word in pattern.split() if len(word) > 2]
    suggestions = []
    
    if words:
        # Find patterns that contain any of the words
        word_conditions = ' OR '.join(['cp.description_pattern LIKE ?' for _ in words])
        word_params = [f'%{word}%' for word in words]
        
        partial_matches = conn.execute(f'''
            SELECT cp.category_id, c.name, cp.usage_count, cp.description_pattern
            FROM categorization_patterns cp
            JOIN categories c ON cp.category_id = c.id
            WHERE {word_conditions}
            ORDER BY cp.usage_count DESC, cp.last_used DESC
            LIMIT 5
        ''', word_params).fetchall()
        
        # Score matches based on word overlap
        for match in partial_matches:
            pattern_words = set(match['description_pattern'].split())
            input_words = set(words)
            overlap = len(pattern_words.intersection(input_words))
            
            if overlap > 0:
                confidence = 'medium' if overlap >= 2 else 'low'
                suggestions.append({
                    'id': match['category_id'], 
                    'name': match['name'], 
                    'confidence': confidence
                })
    
    conn.close()
    
    # Remove duplicates and return top 3
    seen = set()
    unique_suggestions = []
    for suggestion in suggestions:
        if suggestion['id'] not in seen:
            seen.add(suggestion['id'])
            unique_suggestions.append(suggestion)
            if len(unique_suggestions) >= 3:
                break
    
    return unique_suggestions

@app.route('/api/category-suggestions')
def category_suggestions():
    """Get category suggestions based on description (AJAX endpoint)"""
    description = request.args.get('description', '')
    suggestions = get_category_suggestions(description)
    return jsonify(suggestions)

# Add custom template filter for currency formatting
@app.template_filter('currency')
def currency_filter(amount):
    """Format amount as currency"""
    if amount is None:
        amount = 0
    return f"${float(amount):,.2f}"

def ensure_current_budget_period():
    """Ensure current month budget period exists, create if needed"""
    import time
    max_retries = 3
    
    now = datetime.now()
    current_month = now.month
    current_year = now.year
    
    for attempt in range(max_retries):
        try:
            conn = get_db_connection()
            
            # Check if current budget period exists
            budget_period = conn.execute(
                'SELECT id FROM budget_periods WHERE month = ? AND year = ?',
                (current_month, current_year)
            ).fetchone()
            
            if not budget_period:
                # Use INSERT OR IGNORE to handle race conditions
                conn.execute(
                    'INSERT OR IGNORE INTO budget_periods (month, year) VALUES (?, ?)',
                    (current_month, current_year)
                )
                conn.commit()
                
                # Get the budget period ID
                budget_period = conn.execute(
                    'SELECT id FROM budget_periods WHERE month = ? AND year = ?',
                    (current_month, current_year)
                ).fetchone()
                budget_period_id = budget_period['id']
                
                # Copy budget allocations from previous month (with error handling)
                try:
                    prev_month = current_month - 1 if current_month > 1 else 12
                    prev_year = current_year if current_month > 1 else current_year - 1
                    
                    prev_budget_period = conn.execute(
                        'SELECT id FROM budget_periods WHERE month = ? AND year = ?',
                        (prev_month, prev_year)
                    ).fetchone()
                    
                    if prev_budget_period:
                        prev_allocations = conn.execute(
                            'SELECT category_id, subcategory_id, budgeted_amount FROM budget_allocations WHERE budget_period_id = ?',
                            (prev_budget_period['id'],)
                        ).fetchall()
                        
                        for allocation in prev_allocations:
                            conn.execute(
                                'INSERT OR IGNORE INTO budget_allocations (budget_period_id, category_id, subcategory_id, budgeted_amount) VALUES (?, ?, ?, ?)',
                                (budget_period_id, allocation['category_id'], allocation['subcategory_id'], allocation['budgeted_amount'])
                            )
                        
                        # Record the transition
                        conn.execute(
                            'INSERT OR IGNORE INTO month_transitions (from_month, from_year, to_month, to_year, transition_date) VALUES (?, ?, ?, ?, ?)',
                            (prev_month, prev_year, current_month, current_year, now.date())
                        )
                except Exception as e:
                    print(f"Warning: Could not copy previous month's budget: {e}")
                
                conn.commit()
            else:
                budget_period_id = budget_period['id']
            
            conn.close()
            return budget_period_id
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) and attempt < max_retries - 1:
                time.sleep(0.2 * (attempt + 1))
                continue
            else:
                print(f"Database error in ensure_current_budget_period: {e}")
                raise
        except Exception as e:
            print(f"Unexpected error in ensure_current_budget_period: {e}")
            raise
    
    raise sqlite3.OperationalError("Could not access database after retries")

# Fixed Analytics Route for Budget Tracker Pro
# This solves the data type mismatch issue in budget vs actual chart

@app.route('/analytics')
def analytics():
    try:
        from datetime import datetime
        conn = get_db_connection()
        now = datetime.now()
        current_month = now.month
        current_year = now.year

        # --- Category Spending Breakdown (already works) ---
        category_spending = conn.execute('''
            SELECT 
                c.name AS category,
                SUM(t.amount) AS total_spent
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE strftime('%m', t.date) = ? 
              AND strftime('%Y', t.date) = ?
              AND t.sinking_fund_id IS NULL
            GROUP BY c.id, c.name
            ORDER BY total_spent DESC
        ''', (f'{current_month:02d}', str(current_year))).fetchall()

        # --- Budget vs Actual Calculation ---
        budget_vs_actual = []

        # Get current budget period
        budget_period = conn.execute('''
            SELECT id FROM budget_periods 
            WHERE month = ? AND year = ?
        ''', (current_month, current_year)).fetchone()

        if budget_period:
            budget_period_id = budget_period['id']

            # 1. Fetch budgeted allocations
            budget_allocations = conn.execute('''
                SELECT c.name AS category, ba.budgeted_amount
                FROM budget_allocations ba
                JOIN categories c ON ba.category_id = c.id
                WHERE ba.budget_period_id = ?
            ''', (budget_period_id,)).fetchall()

            # 2. Fetch actual spending
            spending_data = conn.execute('''
                SELECT c.name AS category, SUM(t.amount) AS spent
                FROM transactions t
                JOIN categories c ON t.category_id = c.id
                WHERE strftime('%m', t.date) = ? 
                  AND strftime('%Y', t.date) = ?
                  AND t.sinking_fund_id IS NULL
                GROUP BY c.name
            ''', (f'{current_month:02d}', str(current_year))).fetchall()

            # 3. Convert spending to lookup dictionary
            category_spending_dict = {
                row['category']: float(row['spent']) if row['spent'] else 0.0
                for row in spending_data
            }

            # 4. Combine budgeted and spent data
            for allocation in budget_allocations:
                try:
                    category_name = allocation['category']
                    budgeted = allocation['budgeted_amount']
                    if budgeted is None:
                        continue

                    # Force cast to float
                    budgeted_float = float(budgeted)
                    if budgeted_float <= 0:
                        continue

                    spent_float = category_spending_dict.get(category_name, 0.0)

                    budget_vs_actual.append({
                        'category': category_name,
                        'budgeted': budgeted_float,
                        'spent': spent_float
                    })

                except (ValueError, TypeError) as e:
                    print(f"Skipping invalid budget data: {allocation} -> {e}")
                    continue

        # --- Sinking Fund Progress ---
        raw_sinking_funds = conn.execute('''
            SELECT 
                sf.name,
                sf.current_balance,
                sf.target_amount,
                sf.monthly_allocation
            FROM sinking_funds sf
            WHERE sf.is_active = 1
            ORDER BY sf.name
        ''').fetchall()

        processed_sinking_funds = []
        for fund in raw_sinking_funds:
            try:
                current_balance = float(fund['current_balance'] or 0)
                target_amount = float(fund['target_amount'] or 0)
                monthly_allocation = float(fund['monthly_allocation'] or 0)

                progress_percent = (
                    round((current_balance / target_amount) * 100.0, 1)
                    if target_amount > 0 else 0.0
                )

                processed_sinking_funds.append({
                    'name': fund['name'],
                    'current_balance': current_balance,
                    'target_amount': target_amount,
                    'monthly_allocation': monthly_allocation,
                    'progress_percent': progress_percent
                })
            except (ValueError, TypeError) as e:
                print(f"Skipping invalid sinking fund data: {fund} -> {e}")
                continue

        conn.close()

        return render_template('analytics.html',
            category_spending=category_spending,
            budget_vs_actual=budget_vs_actual,
            monthly_trends=[],  # Future enhancement
            sinking_fund_progress=processed_sinking_funds,
            current_month_name=now.strftime('%B %Y')
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Analytics Error: {str(e)}"

        
@app.route('/debug-budget-data')
def debug_budget_data():
    """Temporary debug route to inspect budget data types"""
    try:
        conn = get_db()
        
        # Get sample budget allocation data with type information
        debug_cursor = conn.execute('''
            SELECT 
                c.name as category_name,
                ba.budgeted_amount,
                typeof(ba.budgeted_amount) as amount_type,
                bp.month,
                bp.year
            FROM budget_allocations ba
            JOIN categories c ON ba.category_id = c.id
            JOIN budget_periods bp ON ba.budget_period_id = bp.id
            ORDER BY bp.year DESC, bp.month DESC
            LIMIT 10
        ''')
        
        debug_data = debug_cursor.fetchall()
        
        # Also check the schema of budget_allocations table
        schema_cursor = conn.execute("PRAGMA table_info(budget_allocations)")
        schema_info = schema_cursor.fetchall()
        
        conn.close()
        
        return f"""
        <h2>Budget Allocations Debug Info</h2>
        <h3>Table Schema:</h3>
        <pre>{schema_info}</pre>
        
        <h3>Sample Data with Types:</h3>
        <table border="1">
            <tr><th>Category</th><th>Budget Amount</th><th>Data Type</th><th>Month</th><th>Year</th></tr>
            {"".join([f"<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td><td>{row[4]}</td></tr>" for row in debug_data])}
        </table>
        
        <p><a href="/analytics">Back to Analytics</a></p>
        """
        
    except Exception as e:
        return f"Debug error: {str(e)}"

# Add this temporary debug route to your app.py to see what's happening

@app.route('/debug-detailed')
def debug_detailed():
    """Detailed debugging to find why budget vs actual shows no data"""
    current_date = datetime.now()
    current_month = current_date.month
    current_year = current_date.year
    
    try:
        conn = sqlite3.connect('budget_tracker.db', timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        
        debug_info = []
        debug_info.append(f"<h2>Debugging Budget vs Actual for {current_month}/{current_year}</h2>")
        
        # 1. Check if budget_periods exists for current month
        debug_info.append("<h3>1. Budget Periods Check:</h3>")
        periods_cursor = conn.execute('''
            SELECT id, month, year FROM budget_periods 
            WHERE month = ? AND year = ?
        ''', (current_month, current_year))
        periods = periods_cursor.fetchall()
        
        if periods:
            period_id = periods[0][0]
            debug_info.append(f"✓ Found budget period: ID {period_id} for {current_month}/{current_year}")
        else:
            debug_info.append(f"✗ No budget period found for {current_month}/{current_year}")
            # Check what periods DO exist
            all_periods_cursor = conn.execute('SELECT id, month, year FROM budget_periods ORDER BY year DESC, month DESC LIMIT 5')
            all_periods = all_periods_cursor.fetchall()
            debug_info.append(f"Available periods: {all_periods}")
            
        # 2. Check budget_allocations for current period
        debug_info.append("<h3>2. Budget Allocations Check:</h3>")
        if periods:
            allocations_cursor = conn.execute('''
                SELECT ba.id, c.name, ba.budgeted_amount, typeof(ba.budgeted_amount)
                FROM budget_allocations ba
                JOIN categories c ON ba.category_id = c.id
                WHERE ba.budget_period_id = ?
            ''', (period_id,))
            allocations = allocations_cursor.fetchall()
            
            if allocations:
                debug_info.append(f"✓ Found {len(allocations)} budget allocations:")
                for alloc in allocations:
                    debug_info.append(f"  - {alloc[1]}: {alloc[2]} (type: {alloc[3]})")
            else:
                debug_info.append("✗ No budget allocations found for this period")
        
        # 3. Check transactions for current month
        debug_info.append("<h3>3. Transactions Check:</h3>")
        trans_cursor = conn.execute('''
            SELECT t.id, t.description, t.amount, c.name, t.date
            FROM transactions t
            JOIN categories c ON t.category_id = c.id
            WHERE strftime('%m', t.date) = ? AND strftime('%Y', t.date) = ?
            LIMIT 10
        ''', (str(current_month).zfill(2), str(current_year)))
        transactions = trans_cursor.fetchall()
        
        if transactions:
            debug_info.append(f"✓ Found {len(transactions)} transactions this month:")
            for trans in transactions[:5]:  # Show first 5
                debug_info.append(f"  - {trans[1]}: ${trans[2]} ({trans[3]}) on {trans[4]}")
        else:
            debug_info.append("✗ No transactions found for current month")
            
        # 4. Test the exact query from analytics
        debug_info.append("<h3>4. Testing Analytics Query:</h3>")
        try:
            analytics_cursor = conn.execute('''
                SELECT 
                    c.name as category_name,
                    ba.budgeted_amount,
                    COALESCE(SUM(t.amount), 0) as actual_spent
                FROM budget_allocations ba
                JOIN categories c ON ba.category_id = c.id
                JOIN budget_periods bp ON ba.budget_period_id = bp.id
                LEFT JOIN transactions t ON t.category_id = ba.category_id 
                    AND strftime('%m', t.date) = printf('%02d', bp.month)
                    AND strftime('%Y', t.date) = CAST(bp.year AS TEXT)
                WHERE bp.month = ? AND bp.year = ?
                GROUP BY c.name, ba.budgeted_amount
            ''', (current_month, current_year))
            
            analytics_results = analytics_cursor.fetchall()
            
            if analytics_results:
                debug_info.append(f"✓ Analytics query returned {len(analytics_results)} results:")
                for result in analytics_results:
                    debug_info.append(f"  - {result[0]}: Budget ${result[1]}, Spent ${result[2]}")
            else:
                debug_info.append("✗ Analytics query returned no results")
                
        except Exception as e:
            debug_info.append(f"✗ Analytics query failed: {str(e)}")
            
        # 5. Check date formats in transactions
        debug_info.append("<h3>5. Transaction Date Format Check:</h3>")
        date_cursor = conn.execute('''
            SELECT DISTINCT 
                strftime('%m', date) as month_part,
                strftime('%Y', date) as year_part,
                date as original_date
            FROM transactions 
            LIMIT 10
        ''')
        date_formats = date_cursor.fetchall()
        
        debug_info.append("Transaction date formats:")
        for date_format in date_formats:
            debug_info.append(f"  - Original: {date_format[2]}, Month: {date_format[0]}, Year: {date_format[1]}")
            
        conn.close()
        
        return "<br>".join(debug_info) + '<br><br><a href="/analytics">Back to Analytics</a>'
        
    except Exception as e:
        return f"Debug error: {str(e)}"

# ALTERNATIVE APPROACH: Fix the database data types
def fix_budget_data_types():
    """One-time function to fix budget amount data types in database"""
    conn = get_db()
    
    try:
        # Get all budget allocations
        cursor = conn.execute('SELECT id, budgeted_amount FROM budget_allocations')
        allocations = cursor.fetchall()
        
        # Update each record to ensure proper numeric storage
        for allocation_id, amount in allocations:
            try:
                # Convert to float and back to ensure numeric storage
                if isinstance(amount, str):
                    numeric_amount = float(amount.replace('$', '').replace(',', '').strip())
                else:
                    numeric_amount = float(amount)
                
                # Update the record with proper numeric value
                conn.execute(
                    'UPDATE budget_allocations SET budgeted_amount = ? WHERE id = ?',
                    (numeric_amount, allocation_id)
                )
                
            except (ValueError, TypeError):
                print(f"Could not convert amount for allocation {allocation_id}: {amount}")
        
        conn.commit()
        conn.close()
        print("Budget data types fixed successfully!")
        
    except Exception as e:
        conn.rollback()
        conn.close()
        print(f"Error fixing budget data types: {str(e)}")

# Call this function once to fix existing data:
# fix_budget_data_types()@app.route('/debug-routes')
def debug_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        routes.append(f"{rule.rule} -> {rule.endpoint}")
    return "<br>".join(routes)

@app.route('/')
def index():
    """Main dashboard"""
    # Ensure current budget period exists
    budget_period_id = ensure_current_budget_period()
    
    conn = get_db_connection()
    
    # Get current month/year
    now = datetime.now()
    current_month = now.month
    current_year = now.year
    
    # Get budget vs actual data (excluding sinking fund transactions)
    dashboard_data = conn.execute('''
        SELECT 
            c.name as category_name,
            COALESCE(ba.budgeted_amount, 0) as budgeted,
            COALESCE(SUM(CASE WHEN t.sinking_fund_id IS NULL THEN t.amount ELSE 0 END), 0) as spent
        FROM categories c
        LEFT JOIN budget_allocations ba ON c.id = ba.category_id AND ba.budget_period_id = ?
        LEFT JOIN transactions t ON c.id = t.category_id 
            AND strftime('%m', t.date) = ? 
            AND strftime('%Y', t.date) = ?
        GROUP BY c.id, c.name, ba.budgeted_amount
        ORDER BY c.name
    ''', (budget_period_id, f'{current_month:02d}', str(current_year))).fetchall()
    
    # Get recent transactions (excluding sinking fund transactions for budget tab)
    recent_transactions = conn.execute('''
        SELECT t.*, c.name as category_name, sc.name as subcategory_name,
               sf.name as sinking_fund_name
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        LEFT JOIN subcategories sc ON t.subcategory_id = sc.id
        LEFT JOIN sinking_funds sf ON t.sinking_fund_id = sf.id
        WHERE t.sinking_fund_id IS NULL
        ORDER BY t.date DESC, t.created_at DESC
        LIMIT 10
    ''').fetchall()
    
    # Get sinking funds data
    sinking_funds = conn.execute('''
        SELECT 
            sf.*,
            COALESCE(SUM(CASE WHEN t.transaction_type = 'contribution' THEN t.amount ELSE 0 END), 0) -
            COALESCE(SUM(CASE WHEN t.transaction_type = 'withdrawal' THEN t.amount ELSE 0 END), 0) as calculated_balance
        FROM sinking_funds sf
        LEFT JOIN transactions t ON sf.id = t.sinking_fund_id
        WHERE sf.is_active = 1
        GROUP BY sf.id, sf.name, sf.target_amount, sf.current_balance, sf.monthly_allocation
        ORDER BY sf.name
    ''').fetchall()
    
    # Update current balances based on transactions
    for fund in sinking_funds:
        if fund['calculated_balance'] != fund['current_balance']:
            conn.execute(
                'UPDATE sinking_funds SET current_balance = ? WHERE id = ?',
                (fund['calculated_balance'], fund['id'])
            )
    conn.commit()
    
    # Check if monthly sinking fund contributions have been made this month
    monthly_contributions_made = conn.execute('''
        SELECT COUNT(*) as count FROM month_transitions 
        WHERE to_month = ? AND to_year = ? AND sinking_funds_contributed = 1
    ''', (current_month, current_year)).fetchone()
    
    show_contribute_button = monthly_contributions_made['count'] == 0
    
    conn.close()
    
    return render_template('dashboard.html', 
                         dashboard_data=dashboard_data, 
                         recent_transactions=recent_transactions,
                         sinking_funds=sinking_funds,
                         show_contribute_button=show_contribute_button,
                         current_month_name=now.strftime('%B %Y'))

@app.route('/contribute-monthly-allocations', methods=['POST'])
def contribute_monthly_allocations():
    """Auto-contribute monthly allocations to all sinking funds"""
    try:
        conn = get_db_connection()
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        
        # Check if already contributed this month
        existing = conn.execute('''
            SELECT COUNT(*) as count FROM month_transitions 
            WHERE to_month = ? AND to_year = ? AND sinking_funds_contributed = 1
        ''', (current_month, current_year)).fetchone()
        
        if existing['count'] > 0:
            conn.close()
            return "Monthly contributions already made", 400
        
        # Get all active sinking funds with monthly allocations
        sinking_funds = conn.execute('''
            SELECT * FROM sinking_funds 
            WHERE is_active = 1 AND monthly_allocation > 0
            ORDER BY name
        ''').fetchall()
        
        total_contributed = 0
        contributions_made = 0
        
        for fund in sinking_funds:
            if fund['monthly_allocation'] > 0:
                # Map sinking funds to their appropriate categories
                category_mapping = {
                    1: 11,  # Wife/Household -> Wife Support  
                    2: 2,   # Vehicle -> Car Expenses
                    3: 5,   # Emergency Fund -> Emergency Fund Build-Up
                    4: 3,   # Discretionary -> Prof. Dues + Prime
                    5: 4    # Assembly/Convention -> Convention & Assembly
                }
                
                category_id = category_mapping.get(fund['id'], 5)
                
                # Create transaction for the contribution
                description = f"Monthly allocation to {fund['name']}"
                
                conn.execute('''
                    INSERT INTO transactions (date, description, amount, category_id, sinking_fund_id, transaction_type)
                    VALUES (?, ?, ?, ?, ?, 'contribution')
                ''', (now.date(), description, fund['monthly_allocation'], category_id, fund['id']))
                
                # Update sinking fund balance
                new_balance = fund['current_balance'] + fund['monthly_allocation']
                conn.execute(
                    'UPDATE sinking_funds SET current_balance = ? WHERE id = ?',
                    (new_balance, fund['id'])
                )
                
                total_contributed += fund['monthly_allocation']
                contributions_made += 1
        
        # Mark contributions as made for this month
        cursor = conn.execute('''
            UPDATE month_transitions 
            SET sinking_funds_contributed = 1 
            WHERE to_month = ? AND to_year = ?
        ''', (current_month, current_year))
        
        # If no transition record exists, create one
        if cursor.rowcount == 0:
            conn.execute('''
                INSERT INTO month_transitions (from_month, from_year, to_month, to_year, transition_date, sinking_funds_contributed)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (current_month-1 if current_month > 1 else 12, 
                  current_year if current_month > 1 else current_year-1,
                  current_month, current_year, now.date()))
        
        conn.commit()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error contributing to sinking funds: {str(e)}", 400

@app.route('/api/sinking-funds')
def get_sinking_funds():
    """Get all sinking funds (AJAX endpoint)"""
    conn = get_db_connection()
    sinking_funds = conn.execute('SELECT * FROM sinking_funds WHERE is_active = 1 ORDER BY name').fetchall()
    conn.close()
    
    return jsonify([dict(row) for row in sinking_funds])

@app.route('/sinking-fund/<int:fund_id>/transaction', methods=['POST'])
def sinking_fund_transaction(fund_id):
    """Add contribution or withdrawal to sinking fund"""
    try:
        transaction_type = request.form['transaction_type']  # 'contribution' or 'withdrawal'
        amount = float(request.form['amount'])
        description = request.form.get('description', '')
        date_str = request.form.get('date', datetime.now().date().isoformat())
        
        conn = get_db_connection()
        
        # Get sinking fund details
        fund = conn.execute('SELECT * FROM sinking_funds WHERE id = ?', (fund_id,)).fetchone()
        if not fund:
            conn.close()
            return "Sinking fund not found", 404
        
        # For withdrawals, check if sufficient balance
        if transaction_type == 'withdrawal' and fund['current_balance'] < amount:
            conn.close()
            return "Insufficient balance in sinking fund", 400
        
        # Create a general transaction record
        # Map sinking funds to their appropriate categories
        category_mapping = {
            1: 11,  # Wife/Household -> Wife Support  
            2: 2,   # Vehicle -> Car Expenses
            3: 5,   # Emergency Fund -> Emergency Fund Build-Up
            4: 3,   # Discretionary -> Prof. Dues + Prime (or create new)
            5: 4    # Assembly/Convention -> Convention & Assembly
        }
        
        category_id = category_mapping.get(fund_id, 5)  # Default to Emergency Fund Build-Up
        
        # Insert transaction
        conn.execute('''
            INSERT INTO transactions (date, description, amount, category_id, sinking_fund_id, transaction_type)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (date_str, description, amount, category_id, fund_id, transaction_type))
        
        # Update sinking fund balance
        if transaction_type == 'contribution':
            new_balance = fund['current_balance'] + amount
        else:  # withdrawal
            new_balance = fund['current_balance'] - amount
            
        conn.execute(
            'UPDATE sinking_funds SET current_balance = ? WHERE id = ?',
            (new_balance, fund_id)
        )
        
        conn.commit()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error processing sinking fund transaction: {str(e)}", 400

@app.route('/sinking-fund/<int:fund_id>/edit', methods=['POST'])
def edit_sinking_fund(fund_id):
    """Edit sinking fund target and monthly allocation"""
    try:
        target_amount = float(request.form.get('target_amount', 0))
        monthly_allocation = float(request.form.get('monthly_allocation', 0))
        
        conn = get_db_connection()
        conn.execute(
            'UPDATE sinking_funds SET target_amount = ?, monthly_allocation = ? WHERE id = ?',
            (target_amount, monthly_allocation, fund_id)
        )
        conn.commit()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error updating sinking fund: {str(e)}", 400


@app.route('/budget-setup')
def budget_setup():
    """Show budget setup form"""
    conn = get_db_connection()
    
    # Get current month/year
    now = datetime.now()
    current_month = now.month
    current_year = now.year
    
    # Get or create current budget period
    budget_period = conn.execute(
        'SELECT id FROM budget_periods WHERE month = ? AND year = ?',
        (current_month, current_year)
    ).fetchone()
    
    if not budget_period:
        conn.execute(
            'INSERT INTO budget_periods (month, year) VALUES (?, ?)',
            (current_month, current_year)
        )
        conn.commit()
        budget_period_id = conn.lastrowid
    else:
        budget_period_id = budget_period['id']
    
    # Get categories with current budget amounts
    budget_data = conn.execute('''
        SELECT 
            c.id,
            c.name as category_name,
            COALESCE(ba.budgeted_amount, 0) as budgeted_amount,
            COALESCE(ba.id, 0) as allocation_id
        FROM categories c
        LEFT JOIN budget_allocations ba ON c.id = ba.category_id AND ba.budget_period_id = ?
        ORDER BY c.name
    ''', (budget_period_id,)).fetchall()
    
    # Get sinking funds data
    sinking_funds_data = conn.execute('''
        SELECT * FROM sinking_funds WHERE is_active = 1 ORDER BY name
    ''').fetchall()
    
    conn.close()
    
    return render_template('budget_setup.html', 
                         budget_data=budget_data,
                         sinking_funds_data=sinking_funds_data,
                         current_month=current_month,
                         current_year=current_year)

@app.route('/budget-setup', methods=['POST'])
def save_budget():
    """Save budget allocations and sinking fund settings"""
    try:
        conn = get_db_connection()
        
        # Get current month/year
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        
        # Get budget period
        budget_period = conn.execute(
            'SELECT id FROM budget_periods WHERE month = ? AND year = ?',
            (current_month, current_year)
        ).fetchone()
        
        budget_period_id = budget_period['id']
        
        # Process each category budget
        for key, value in request.form.items():
            if key.startswith('budget_'):
                category_id = int(key.replace('budget_', ''))
                amount = float(value) if value else 0
                
                # Check if allocation already exists
                existing = conn.execute(
                    'SELECT id FROM budget_allocations WHERE budget_period_id = ? AND category_id = ?',
                    (budget_period_id, category_id)
                ).fetchone()
                
                if existing:
                    # Update existing allocation
                    conn.execute(
                        'UPDATE budget_allocations SET budgeted_amount = ? WHERE id = ?',
                        (amount, existing['id'])
                    )
                else:
                    # Create new allocation
                    conn.execute(
                        'INSERT INTO budget_allocations (budget_period_id, category_id, budgeted_amount) VALUES (?, ?, ?)',
                        (budget_period_id, category_id, amount)
                    )
            
            # Process sinking fund settings
            elif key.startswith('sf_target_'):
                fund_id = int(key.replace('sf_target_', ''))
                target_amount = float(value) if value else 0
                
                conn.execute(
                    'UPDATE sinking_funds SET target_amount = ? WHERE id = ?',
                    (target_amount, fund_id)
                )
            
            elif key.startswith('sf_monthly_'):
                fund_id = int(key.replace('sf_monthly_', ''))
                monthly_allocation = float(value) if value else 0
                
                conn.execute(
                    'UPDATE sinking_funds SET monthly_allocation = ? WHERE id = ?',
                    (monthly_allocation, fund_id)
                )
        
        conn.commit()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error saving budget: {str(e)}", 400

@app.route('/transaction/<int:transaction_id>')
def view_transaction(transaction_id):
    """View single transaction details"""
    conn = get_db_connection()
    
    transaction = conn.execute('''
        SELECT t.*, c.name as category_name, sc.name as subcategory_name
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        LEFT JOIN subcategories sc ON t.subcategory_id = sc.id
        WHERE t.id = ?
    ''', (transaction_id,)).fetchone()
    
    if not transaction:
        conn.close()
        return "Transaction not found", 404
    
    categories = conn.execute('SELECT * FROM categories ORDER BY name').fetchall()
    subcategories = conn.execute('SELECT * FROM subcategories WHERE category_id = ? ORDER BY name', 
                                (transaction['category_id'],)).fetchall()
    
    conn.close()
    
    return render_template('transaction_detail.html', 
                         transaction=transaction, 
                         categories=categories,
                         subcategories=subcategories)

@app.route('/transaction/<int:transaction_id>/edit')
def edit_transaction(transaction_id):
    """Show edit transaction form"""
    conn = get_db_connection()
    
    transaction = conn.execute('''
        SELECT t.*, c.name as category_name, sc.name as subcategory_name
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        LEFT JOIN subcategories sc ON t.subcategory_id = sc.id
        WHERE t.id = ?
    ''', (transaction_id,)).fetchone()
    
    if not transaction:
        conn.close()
        return "Transaction not found", 404
    
    categories = conn.execute('SELECT * FROM categories ORDER BY name').fetchall()
    
    conn.close()
    
    return render_template('edit_transaction.html', 
                         transaction=transaction, 
                         categories=categories)

@app.route('/transaction/<int:transaction_id>/edit', methods=['POST'])
def update_transaction(transaction_id):
    """Update transaction"""
    try:
        # Get form data
        date_str = request.form['date']
        description = request.form['description']
        amount = float(request.form['amount'])
        category_id = int(request.form['category_id'])
        subcategory_id = request.form.get('subcategory_id')
        notes = request.form.get('notes', '')
        
        # Convert subcategory_id to int or None
        subcategory_id = int(subcategory_id) if subcategory_id else None
        
        conn = get_db_connection()
        
        # Check if transaction exists
        existing = conn.execute('SELECT id FROM transactions WHERE id = ?', (transaction_id,)).fetchone()
        if not existing:
            conn.close()
            return "Transaction not found", 404
        
        # Update transaction
        conn.execute('''
            UPDATE transactions 
            SET date = ?, description = ?, amount = ?, category_id = ?, subcategory_id = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        ''', (date_str, description, amount, category_id, subcategory_id, notes, transaction_id))
        
        conn.commit()
        conn.close()
        
        # Learn from this categorization choice (updates are also learning opportunities)
        learn_categorization_pattern(description, category_id)
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error updating transaction: {str(e)}", 400

@app.route('/transaction/<int:transaction_id>/delete', methods=['POST'])
def delete_transaction(transaction_id):
    """Delete transaction"""
    try:
        conn = get_db_connection()
        
        # Check if transaction exists
        existing = conn.execute('SELECT id FROM transactions WHERE id = ?', (transaction_id,)).fetchone()
        if not existing:
            conn.close()
            return "Transaction not found", 404
        
        # Delete transaction
        conn.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))
        conn.commit()
        conn.close()
        
        return redirect(url_for('index'))
        
    except Exception as e:
        return f"Error deleting transaction: {str(e)}", 400

@app.route('/transactions')
def all_transactions():
    """Show all transactions with search/filter"""
    # Get filter parameters
    search = request.args.get('search', '')
    category_filter = request.args.get('category', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    conn = get_db_connection()
    
    # Build query with filters
    query = '''
        SELECT t.*, c.name as category_name, sc.name as subcategory_name,
               sf.name as sinking_fund_name, t.transaction_type
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        LEFT JOIN subcategories sc ON t.subcategory_id = sc.id
        LEFT JOIN sinking_funds sf ON t.sinking_fund_id = sf.id
        WHERE 1=1
    '''
    params = []
    
    if search:
        query += ' AND (t.description LIKE ? OR t.notes LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    
    if category_filter:
        query += ' AND t.category_id = ?'
        params.append(category_filter)
    
    if date_from:
        query += ' AND t.date >= ?'
        params.append(date_from)
    
    if date_to:
        query += ' AND t.date <= ?'
        params.append(date_to)
    
    query += ' ORDER BY t.date DESC, t.created_at DESC'
    
    transactions = conn.execute(query, params).fetchall()
    categories = conn.execute('SELECT * FROM categories ORDER BY name').fetchall()
    
    conn.close()
    
    return render_template('all_transactions.html', 
                         transactions=transactions,
                         categories=categories,
                         filters={
                             'search': search,
                             'category': category_filter,
                             'date_from': date_from,
                             'date_to': date_to
                         })

@app.route('/add-transaction')
def add_transaction_form():
    """Show transaction entry form"""
    conn = get_db_connection()
    categories = conn.execute('SELECT * FROM categories ORDER BY name').fetchall()
    conn.close()
    
    return render_template('add_transaction.html', categories=categories)

@app.route('/api/subcategories/<int:category_id>')
def get_subcategories(category_id):
    """Get subcategories for a category (AJAX endpoint)"""
    conn = get_db_connection()
    subcategories = conn.execute(
        'SELECT * FROM subcategories WHERE category_id = ? ORDER BY name',
        (category_id,)
    ).fetchall()
    conn.close()
    
    return jsonify([dict(row) for row in subcategories])

@app.route('/add-transaction', methods=['POST'])
def add_transaction():
    """Process new transaction"""
    try:
        # Get form data
        date_str = request.form['date']
        description = request.form['description']
        amount = float(request.form['amount'])
        category_id = int(request.form['category_id'])
        subcategory_id = request.form.get('subcategory_id')
        notes = request.form.get('notes', '')
        
        # Convert subcategory_id to int or None
        subcategory_id = int(subcategory_id) if subcategory_id else None
        
        conn = get_db_connection()
        conn.execute('''
            INSERT INTO transactions (date, description, amount, category_id, subcategory_id, notes)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (date_str, description, amount, category_id, subcategory_id, notes))
        conn.commit()
        conn.close()
        
        # Learn from this categorization choice
        learn_categorization_pattern(description, category_id)
        
        return redirect(url_for('index'))
        
    except Exception as e:
        # In production, you'd want better error handling
        return f"Error: {str(e)}", 400

@app.route('/api/categories', methods=['POST'])
def add_category():
    """Add a new category"""
    try:
        name = request.form.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Category name is required'}), 400
        
        conn = get_db_connection()
        
        # Check if category already exists
        existing = conn.execute('SELECT id FROM categories WHERE name = ?', (name,)).fetchone()
        if existing:
            conn.close()
            return jsonify({'error': 'Category already exists'}), 400
        
        # Add new category
        cursor = conn.execute('INSERT INTO categories (name) VALUES (?)', (name,))
        category_id = cursor.lastrowid
        
        conn.commit()
        conn.close()
        
        return jsonify({'id': category_id, 'name': name, 'message': 'Category added successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/categories/<int:category_id>', methods=['PUT'])
def update_category(category_id):
    """Update category name"""
    try:
        name = request.form.get('name', '').strip()
        if not name:
            return jsonify({'error': 'Category name is required'}), 400
        
        conn = get_db_connection()
        
        # Check if category exists
        existing = conn.execute('SELECT id FROM categories WHERE id = ?', (category_id,)).fetchone()
        if not existing:
            conn.close()
            return jsonify({'error': 'Category not found'}), 404
        
        # Check if new name conflicts with existing category
        conflict = conn.execute('SELECT id FROM categories WHERE name = ? AND id != ?', (name, category_id)).fetchone()
        if conflict:
            conn.close()
            return jsonify({'error': 'Category name already exists'}), 400
        
        # Update category
        conn.execute('UPDATE categories SET name = ? WHERE id = ?', (name, category_id))
        conn.commit()
        conn.close()
        
        return jsonify({'message': 'Category updated successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/categories/<int:category_id>', methods=['DELETE'])
def delete_category(category_id):
    """Delete category (only if no transactions use it and no active budgets)"""
    try:
        conn = get_db_connection()
        
        # Check if category has transactions
        transactions_count = conn.execute(
            'SELECT COUNT(*) as count FROM transactions WHERE category_id = ?', 
            (category_id,)
        ).fetchone()
        
        if transactions_count['count'] > 0:
            conn.close()
            return jsonify({'error': f'Cannot delete category. It has {transactions_count["count"]} transactions.'}), 400
        
        # Check if category has budget allocations > 0
        budget_count = conn.execute(
            'SELECT COUNT(*) as count FROM budget_allocations WHERE category_id = ? AND budgeted_amount > 0', 
            (category_id,)
        ).fetchone()
        
        if budget_count['count'] > 0:
            conn.close()
            return jsonify({'error': 'Cannot delete category. It has active budget allocations.'}), 400
        
        # Delete category and any $0 budget allocations
        conn.execute('DELETE FROM budget_allocations WHERE category_id = ?', (category_id,))
        conn.execute('DELETE FROM categories WHERE id = ?', (category_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'message': 'Category deleted successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/manage-categories')
def manage_categories():
    """Category management page"""
    conn = get_db_connection()
    
    # Get all categories with transaction counts (only count budget allocations > 0)
    categories_data = []
    categories = conn.execute('SELECT id, name FROM categories ORDER BY name').fetchall()
    
    for category in categories:
        # Count transactions for this category
        transaction_count = conn.execute(
            'SELECT COUNT(*) as count FROM transactions WHERE category_id = ?',
            (category['id'],)
        ).fetchone()['count']
        
        # Count budget allocations > 0 for this category
        budget_count = conn.execute(
            'SELECT COUNT(*) as count FROM budget_allocations WHERE category_id = ? AND budgeted_amount > 0',
            (category['id'],)
        ).fetchone()['count']
        
        categories_data.append({
            'id': category['id'],
            'name': category['name'],
            'transaction_count': transaction_count,
            'budget_count': budget_count
        })
    
    conn.close()
    
    return render_template('manage_categories.html', categories=categories_data)

if __name__ == '__main__':
    # Initialize database if it doesn't exist
    if not os.path.exists(DATABASE):
        init_db()
    
    # Always check if tables exist (for cloud deployments)
    init_db_if_needed()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
