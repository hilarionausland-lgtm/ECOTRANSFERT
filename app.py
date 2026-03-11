from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import sqlite3, os, base64, uuid
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ecotransfert-secret-change-in-prod')
CORS(app, supports_credentials=True)

DB_PATH = os.environ.get('DB_PATH', 'ecotransfert.db')
UPLOAD_FOLDER = 'static/uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

RATE = {'eur_fcfa': 655.96, 'commission': 2.0}

# ─── DB INIT ─────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            first    TEXT NOT NULL,
            last     TEXT NOT NULL,
            email    TEXT UNIQUE NOT NULL,
            phone    TEXT,
            password TEXT NOT NULL,
            created  TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS transactions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ref          TEXT UNIQUE NOT NULL,
            user_id      INTEGER NOT NULL,
            amount       REAL NOT NULL,
            commission   REAL NOT NULL,
            net          REAL NOT NULL,
            fcfa         INTEGER NOT NULL,
            beneficiary  TEXT NOT NULL,
            benef_phone  TEXT,
            method       TEXT NOT NULL,
            note         TEXT,
            proof_path   TEXT,
            status       TEXT DEFAULT 'pending',
            created      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT OR IGNORE INTO settings VALUES ('eur_fcfa','655.96');
        INSERT OR IGNORE INTO settings VALUES ('commission','2.0');
        INSERT OR IGNORE INTO settings VALUES ('wa_number','+33600000000');
        INSERT OR IGNORE INTO settings VALUES ('iban','');
        INSERT OR IGNORE INTO settings VALUES ('iban_name','EcoTransfert');
        """)

init_db()

# ─── HELPERS ─────────────────────────────────────────────────
def gen_ref():
    with get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    return f"TX-{str(count+1).zfill(4)}-{uuid.uuid4().hex[:4].upper()}"

def load_settings():
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r['key']: r['value'] for r in rows}

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Non authentifié'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'error': 'Accès refusé'}), 403
        return f(*args, **kwargs)
    return decorated

def tx_to_dict(t):
    return {
        'id': t['id'], 'ref': t['ref'], 'user_id': t['user_id'],
        'amount': t['amount'], 'commission': t['commission'],
        'net': t['net'], 'fcfa': t['fcfa'],
        'beneficiary': t['beneficiary'], 'benef_phone': t['benef_phone'],
        'method': t['method'], 'note': t['note'],
        'proof_url': f"/static/uploads/{t['proof_path']}" if t['proof_path'] else None,
        'status': t['status'], 'created': t['created'],
    }

# ─── AUTH ─────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO users (first,last,email,phone,password) VALUES (?,?,?,?,?)",
                (d['first'], d['last'], d['email'], d.get('phone',''),
                 generate_password_hash(d['password']))
            )
            user = db.execute("SELECT * FROM users WHERE email=?", (d['email'],)).fetchone()
        session['user_id'] = user['id']
        session['user_name'] = user['first']
        return jsonify({'id': user['id'], 'first': user['first'], 'last': user['last'], 'email': user['email']})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Email déjà utilisé'}), 409

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (d['email'],)).fetchone()
    if not user or not check_password_hash(user['password'], d['password']):
        return jsonify({'error': 'Identifiants incorrects'}), 401
    session['user_id'] = user['id']
    session['user_name'] = user['first']
    return jsonify({'id': user['id'], 'first': user['first'], 'last': user['last'], 'email': user['email'], 'phone': user['phone']})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
@login_required
def me():
    with get_db() as db:
        user = db.execute("SELECT id,first,last,email,phone,created FROM users WHERE id=?", (session['user_id'],)).fetchone()
    return jsonify(dict(user))

@app.route('/api/me', methods=['PUT'])
@login_required
def update_me():
    d = request.json
    with get_db() as db:
        db.execute("UPDATE users SET phone=? WHERE id=?", (d.get('phone',''), session['user_id']))
    return jsonify({'ok': True})

# ─── ADMIN AUTH ───────────────────────────────────────────────
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    d = request.json
    if d.get('username') == 'admin' and d.get('password') == ADMIN_PASSWORD:
        session['is_admin'] = True
        return jsonify({'ok': True})
    return jsonify({'error': 'Identifiants incorrects'}), 401

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    return jsonify({'ok': True})

# ─── TRANSACTIONS ─────────────────────────────────────────────
@app.route('/api/transactions', methods=['POST'])
@login_required
def create_transaction():
    d = request.form
    settings = load_settings()
    rate = float(settings['eur_fcfa'])
    comm_pct = float(settings['commission'])

    amount = float(d.get('amount', 0))
    if amount < 10:
        return jsonify({'error': 'Montant minimum 10€'}), 400

    commission = round(amount * comm_pct / 100, 2)
    net = round(amount - commission, 2)
    fcfa = round(net * rate)

    proof_path = None
    if 'proof' in request.files:
        file = request.files['proof']
        if file.filename:
            ext = os.path.splitext(file.filename)[1].lower()
            fname = f"{uuid.uuid4().hex}{ext}"
            file.save(os.path.join(UPLOAD_FOLDER, fname))
            proof_path = fname

    ref = gen_ref()
    with get_db() as db:
        db.execute("""
            INSERT INTO transactions
            (ref,user_id,amount,commission,net,fcfa,beneficiary,benef_phone,method,note,proof_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (ref, session['user_id'], amount, commission, net, fcfa,
              d.get('beneficiary',''), d.get('benef_phone',''),
              d.get('method',''), d.get('note',''), proof_path))
        tx = db.execute("SELECT * FROM transactions WHERE ref=?", (ref,)).fetchone()

    return jsonify(tx_to_dict(tx)), 201

@app.route('/api/transactions/mine')
@login_required
def my_transactions():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM transactions WHERE user_id=? ORDER BY created DESC",
            (session['user_id'],)
        ).fetchall()
    return jsonify([tx_to_dict(r) for r in rows])

# ─── ADMIN ROUTES ─────────────────────────────────────────────
@app.route('/api/admin/transactions')
@admin_required
def admin_transactions():
    status = request.args.get('status')
    query = "SELECT t.*, u.first||' '||u.last AS client_name, u.email AS client_email FROM transactions t JOIN users u ON t.user_id=u.id"
    params = []
    if status:
        query += " WHERE t.status=?"
        params.append(status)
    query += " ORDER BY t.created DESC"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = tx_to_dict(r)
        d['client_name'] = r['client_name']
        d['client_email'] = r['client_email']
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/transactions/<int:tx_id>', methods=['PATCH'])
@admin_required
def update_transaction(tx_id):
    d = request.json
    status = d.get('status')
    if status not in ('pending','review','validated','rejected'):
        return jsonify({'error': 'Statut invalide'}), 400
    with get_db() as db:
        db.execute("UPDATE transactions SET status=? WHERE id=?", (status, tx_id))
        tx = db.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
    return jsonify(tx_to_dict(tx))

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    with get_db() as db:
        total = db.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        pending = db.execute("SELECT COUNT(*) FROM transactions WHERE status='pending'").fetchone()[0]
        validated = db.execute("SELECT COUNT(*) FROM transactions WHERE status='validated'").fetchone()[0]
        volume = db.execute("SELECT COALESCE(SUM(amount),0) FROM transactions").fetchone()[0]
        commission = db.execute("SELECT COALESCE(SUM(commission),0) FROM transactions").fetchone()[0]
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    return jsonify({
        'total': total, 'pending': pending, 'validated': validated,
        'volume': round(volume, 2), 'commission': round(commission, 2),
        'users': users
    })

@app.route('/api/admin/users')
@admin_required
def admin_users():
    with get_db() as db:
        rows = db.execute("""
            SELECT u.id, u.first, u.last, u.email, u.phone, u.created,
                   COUNT(t.id) AS tx_count,
                   COALESCE(SUM(t.amount),0) AS volume
            FROM users u
            LEFT JOIN transactions t ON t.user_id = u.id
            GROUP BY u.id ORDER BY u.created DESC
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/settings', methods=['GET'])
@admin_required
def get_settings():
    return jsonify(load_settings())

@app.route('/api/admin/settings', methods=['PUT'])
@admin_required
def update_settings():
    d = request.json
    with get_db() as db:
        for k, v in d.items():
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, str(v)))
    return jsonify({'ok': True})

# ─── STATIC / FRONTEND ────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/api/settings/public')
def public_settings():
    s = load_settings()
    return jsonify({'eur_fcfa': float(s['eur_fcfa']), 'commission': float(s['commission'])})

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV') == 'development', host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
