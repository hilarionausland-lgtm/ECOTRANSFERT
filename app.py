from flask import Flask, request, jsonify, session, send_from_directory, render_template
from flask_cors import CORS
from flask_mail import Mail, Message
import sqlite3, os, uuid, secrets
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ecotransfert-secret-change-in-prod')
CORS(app, supports_credentials=True)

# ─── MAIL CONFIG ─────────────────────────────────────────────
MAIL_ENABLED = bool(os.environ.get('MAIL_USERNAME') and os.environ.get('MAIL_PASSWORD'))
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME', '')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD', '')
app.config['MAIL_DEFAULT_SENDER'] = os.environ.get('MAIL_USERNAME', 'noreply@ecotransfert.com')
app.config['MAIL_SUPPRESS_SEND'] = not MAIL_ENABLED
mail = Mail(app)

DB_PATH = os.environ.get('DB_PATH', 'ecotransfert.db')
UPLOAD_FOLDER = 'static/uploads'
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── DB INIT ─────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            first         TEXT NOT NULL,
            last          TEXT NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            phone         TEXT,
            country       TEXT,
            password      TEXT NOT NULL,
            verified      INTEGER DEFAULT 0,
            verify_token  TEXT,
            token_expiry  TEXT,
            created       TEXT DEFAULT (datetime('now'))
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
            benef_account TEXT,
            method       TEXT NOT NULL,
            send_method  TEXT,
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
        INSERT OR IGNORE INTO settings VALUES ('wave_number','');
        INSERT OR IGNORE INTO settings VALUES ('mtn_number','');
        INSERT OR IGNORE INTO settings VALUES ('moov_number','');
        INSERT OR IGNORE INTO settings VALUES ('orange_number','');
        """)

init_db()

# ─── HELPERS ─────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
    d = dict(t)
    d['proof_url'] = f"/static/uploads/{t['proof_path']}" if t['proof_path'] else None
    return d

def send_verification_email(email, first, token):
    try:
        verify_url = f"{BASE_URL}/verify/{token}"
        msg = Message(
            subject="✅ Confirmez votre compte EcoTransfert",
            recipients=[email],
            html=f"""
            <div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;padding:40px 20px;background:#0A0E1A;color:#F0EDE8">
              <div style="text-align:center;margin-bottom:32px">
                <h1 style="color:#C9A84C;font-size:28px;margin:0">EcoTransfert</h1>
                <p style="color:#8A9AB5;font-size:13px;margin:8px 0 0">Plateforme de médiation financière sécurisée</p>
              </div>
              <div style="background:#111827;border:1px solid rgba(201,168,76,0.2);border-radius:12px;padding:32px">
                <h2 style="color:#F0EDE8;font-size:20px;margin:0 0 12px">Bonjour {first},</h2>
                <p style="color:#8A9AB5;line-height:1.6;margin:0 0 24px">Merci de vous être inscrit sur EcoTransfert. Cliquez sur le bouton ci-dessous pour confirmer votre adresse email et activer votre compte.</p>
                <div style="text-align:center;margin:32px 0">
                  <a href="{verify_url}" style="background:linear-gradient(135deg,#C9A84C,#9A7A32);color:#0A0E1A;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block">
                    Confirmer mon compte
                  </a>
                </div>
                <p style="color:#8A9AB5;font-size:12px;margin:0;text-align:center">Ce lien expire dans 24 heures.<br>Si vous n'avez pas créé de compte, ignorez cet email.</p>
              </div>
            </div>
            """
        )
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Mail error: {e}")
        return False

# ─── FRONTEND ROUTES ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('templates', 'index.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('templates', 'dashboard.html')

@app.route('/transfer/new')
def transfer_new():
    return send_from_directory('templates', 'transfer.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('templates', 'admin.html')

@app.route('/verify/<token>')
def verify_email(token):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE verify_token=?", (token,)).fetchone()
        if not user:
            return "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px'>❌ Lien invalide ou déjà utilisé.</h2>", 400
        if user['token_expiry'] and datetime.fromisoformat(user['token_expiry']) < datetime.now():
            return "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px'>⏰ Lien expiré. Veuillez vous réinscrire.</h2>", 400
        db.execute("UPDATE users SET verified=1, verify_token=NULL, token_expiry=NULL WHERE id=?", (user['id'],))
    return send_from_directory('templates', 'verified.html')

# ─── PUBLIC API ───────────────────────────────────────────────
@app.route('/api/settings/public')
def public_settings():
    s = load_settings()
    return jsonify({
        'eur_fcfa': float(s.get('eur_fcfa', 655.96)),
        'commission': float(s.get('commission', 2.0)),
        'iban': s.get('iban', ''),
        'iban_name': s.get('iban_name', 'EcoTransfert'),
        'wave_number': s.get('wave_number', ''),
        'mtn_number': s.get('mtn_number', ''),
        'moov_number': s.get('moov_number', ''),
        'orange_number': s.get('orange_number', ''),
    })

# ─── AUTH ─────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    if not d.get('first') or not d.get('last') or not d.get('email') or not d.get('password'):
        return jsonify({'error': 'Champs obligatoires manquants'}), 400
    if len(d['password']) < 6:
        return jsonify({'error': 'Mot de passe trop court (min. 6 caractères)'}), 400

    token = secrets.token_urlsafe(32)
    expiry = (datetime.now() + timedelta(hours=24)).isoformat()

    try:
        verified = 1 if not MAIL_ENABLED else 0
        with get_db() as db:
            db.execute(
                "INSERT INTO users (first,last,email,phone,country,password,verify_token,token_expiry,verified) VALUES (?,?,?,?,?,?,?,?,?)",
                (d['first'], d['last'], d['email'], d.get('phone',''), d.get('country',''),
                 generate_password_hash(d['password']),
                 token if MAIL_ENABLED else None,
                 expiry if MAIL_ENABLED else None,
                 verified)
            )
        if MAIL_ENABLED:
            send_verification_email(d['email'], d['first'], token)
            return jsonify({'ok': True, 'mail_sent': True, 'message': 'Compte créé ! Vérifiez votre email.'})
        else:
            return jsonify({'ok': True, 'mail_sent': False, 'auto_verified': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'Cette adresse email est déjà utilisée'}), 409

@app.route('/api/login', methods=['POST'])
def login():
    d = request.json
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (d.get('email',''),)).fetchone()
    if not user or not check_password_hash(user['password'], d.get('password','')):
        return jsonify({'error': 'Email ou mot de passe incorrect'}), 401
    if not user['verified']:
        return jsonify({'error': 'Veuillez confirmer votre email avant de vous connecter', 'unverified': True}), 403
    session['user_id'] = user['id']
    return jsonify({'id': user['id'], 'first': user['first'], 'last': user['last'], 'email': user['email'], 'phone': user['phone'], 'country': user['country']})

@app.route('/api/resend-verification', methods=['POST'])
def resend_verification():
    d = request.json
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (d.get('email',''),)).fetchone()
    if not user or user['verified']:
        return jsonify({'ok': True})  # Silent for security
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now() + timedelta(hours=24)).isoformat()
    with get_db() as db:
        db.execute("UPDATE users SET verify_token=?, token_expiry=? WHERE id=?", (token, expiry, user['id']))
    send_verification_email(user['email'], user['first'], token)
    return jsonify({'ok': True})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
@login_required
def me():
    with get_db() as db:
        user = db.execute("SELECT id,first,last,email,phone,country,created FROM users WHERE id=?", (session['user_id'],)).fetchone()
    return jsonify(dict(user))

@app.route('/api/me', methods=['PUT'])
@login_required
def update_me():
    d = request.json
    with get_db() as db:
        db.execute("UPDATE users SET phone=?, country=? WHERE id=?", (d.get('phone',''), d.get('country',''), session['user_id']))
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
            (ref,user_id,amount,commission,net,fcfa,beneficiary,benef_phone,benef_account,method,send_method,note,proof_path)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ref, session['user_id'], amount, commission, net, fcfa,
              d.get('beneficiary',''), d.get('benef_phone',''), d.get('benef_account',''),
              d.get('method',''), d.get('send_method',''), d.get('note',''), proof_path))
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

# ─── ADMIN ────────────────────────────────────────────────────
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

@app.route('/api/admin/transactions')
@admin_required
def admin_transactions():
    status = request.args.get('status')
    query = """SELECT t.*, u.first||' '||u.last AS client_name, u.email AS client_email
               FROM transactions t JOIN users u ON t.user_id=u.id"""
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
        users = db.execute("SELECT COUNT(*) FROM users WHERE verified=1").fetchone()[0]
    return jsonify({'total': total, 'pending': pending, 'validated': validated,
                    'volume': round(volume, 2), 'commission': round(commission, 2), 'users': users})

@app.route('/api/admin/users')
@admin_required
def admin_users():
    with get_db() as db:
        rows = db.execute("""
            SELECT u.id, u.first, u.last, u.email, u.phone, u.country, u.verified, u.created,
                   COUNT(t.id) AS tx_count, COALESCE(SUM(t.amount),0) AS volume
            FROM users u LEFT JOIN transactions t ON t.user_id=u.id
            GROUP BY u.id ORDER BY u.created DESC
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/cleanup-unverified', methods=['POST'])
@admin_required
def cleanup_unverified():
    with get_db() as db:
        users = db.execute("SELECT email FROM users WHERE verified=0").fetchall()
        count = len(users)
        db.execute("DELETE FROM users WHERE verified=0")
    return jsonify({'ok': True, 'deleted': count, 'emails': [u['email'] for u in users]})

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

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV')=='development', host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
