from flask import Flask, request, jsonify, session, send_from_directory
from flask_cors import CORS
import sqlite3, os, uuid, secrets
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.request, json as _json

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'ecotransfert-secret-v3')
CORS(app, supports_credentials=True)

DB_PATH = os.environ.get('DB_PATH', 'ecotransfert.db')
UPLOAD_FOLDER = 'static/uploads'
BASE_URL = os.environ.get('BASE_URL', 'http://localhost:5000')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ─── MAIL CONFIG (Resend) ────────────────────────────────────
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
MAIL_FROM = os.environ.get('MAIL_FROM', 'EcoTransfert <onboarding@resend.dev>')
MAIL_ENABLED = bool(RESEND_API_KEY)

# ─── DB ──────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            first        TEXT NOT NULL,
            last         TEXT NOT NULL,
            email        TEXT UNIQUE NOT NULL,
            phone        TEXT,
            country      TEXT,
            password     TEXT NOT NULL,
            verified     INTEGER DEFAULT 1,
            verify_token TEXT,
            token_expiry TEXT,
            rating_sum   REAL DEFAULT 0,
            rating_count INTEGER DEFAULT 0,
            created      TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS annonces (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ref          TEXT UNIQUE NOT NULL,
            user_id      INTEGER NOT NULL,
            direction    TEXT NOT NULL,  -- 'EUR_TO_FCFA' or 'FCFA_TO_EUR'
            amount_min   REAL NOT NULL,
            amount_max   REAL NOT NULL,
            amount       REAL NOT NULL,  -- target amount
            currency_give TEXT NOT NULL, -- what user has
            currency_want TEXT NOT NULL, -- what user wants
            rate         REAL,           -- custom rate if any
            note         TEXT,
            status       TEXT DEFAULT 'active', -- active/matched/completed/cancelled/expired
            matched_amount REAL DEFAULT 0,
            expires_at   TEXT,
            created      TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS matches (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ref             TEXT UNIQUE NOT NULL,
            annonce_a_id    INTEGER NOT NULL,  -- EUR sender
            annonce_b_id    INTEGER NOT NULL,  -- FCFA sender
            user_a_id       INTEGER NOT NULL,
            user_b_id       INTEGER NOT NULL,
            amount_eur      REAL NOT NULL,
            amount_fcfa     INTEGER NOT NULL,
            commission_a    REAL NOT NULL,
            commission_b    REAL NOT NULL,
            status          TEXT DEFAULT 'pending', -- pending/proof_a/proof_b/both_proofs/validating/completed/cancelled/disputed
            proof_a         TEXT,  -- EUR sender proof
            proof_b         TEXT,  -- FCFA sender proof
            accepted_a      INTEGER DEFAULT 0,
            accepted_b      INTEGER DEFAULT 0,
            expires_at      TEXT,
            completed_at    TEXT,
            created         TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (annonce_a_id) REFERENCES annonces(id),
            FOREIGN KEY (annonce_b_id) REFERENCES annonces(id)
        );
        CREATE TABLE IF NOT EXISTS ratings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id    INTEGER NOT NULL,
            rater_id    INTEGER NOT NULL,
            rated_id    INTEGER NOT NULL,
            score       INTEGER NOT NULL,  -- 1-5
            comment     TEXT,
            created     TEXT DEFAULT (datetime('now')),
            UNIQUE(match_id, rater_id)
        );
        CREATE TABLE IF NOT EXISTS notifications (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            type       TEXT NOT NULL,
            title      TEXT NOT NULL,
            message    TEXT NOT NULL,
            link       TEXT,
            read       INTEGER DEFAULT 0,
            created    TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT OR IGNORE INTO settings VALUES ('eur_fcfa','655.96');
        INSERT OR IGNORE INTO settings VALUES ('commission','1.0');
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
def load_settings():
    with get_db() as db:
        rows = db.execute("SELECT key, value FROM settings").fetchall()
    return {r['key']: r['value'] for r in rows}

def gen_ref(prefix='AN'):
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

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

def add_notification(user_id, type, title, message, link=None):
    try:
        with get_db() as db:
            db.execute(
                "INSERT INTO notifications (user_id,type,title,message,link) VALUES (?,?,?,?,?)",
                (user_id, type, title, message, link)
            )
    except: pass

def user_to_dict(u, public=False):
    d = {'id': u['id'], 'first': u['first'], 'last': u['last'],
         'country': u['country'], 'rating_sum': u['rating_sum'],
         'rating_count': u['rating_count'],
         'rating': round(u['rating_sum']/u['rating_count'], 1) if u['rating_count'] > 0 else None,
         'created': u['created']}
    if not public:
        d['email'] = u['email']
        d['phone'] = u['phone']
    return d

def annonce_to_dict(a, include_user=False):
    d = dict(a)
    if include_user:
        with get_db() as db:
            u = db.execute("SELECT * FROM users WHERE id=?", (a['user_id'],)).fetchone()
        if u:
            d['user'] = user_to_dict(u, public=True)
    return d

def match_to_dict(m, user_id=None):
    d = dict(m)
    d['proof_a_url'] = f"/static/uploads/{m['proof_a']}" if m['proof_a'] else None
    d['proof_b_url'] = f"/static/uploads/{m['proof_b']}" if m['proof_b'] else None
    with get_db() as db:
        ua = db.execute("SELECT * FROM users WHERE id=?", (m['user_a_id'],)).fetchone()
        ub = db.execute("SELECT * FROM users WHERE id=?", (m['user_b_id'],)).fetchone()
    # Only reveal contact info after match accepted by both
    reveal = m['accepted_a'] and m['accepted_b']
    if ua: d['user_a'] = user_to_dict(ua, public=not reveal)
    if ub: d['user_b'] = user_to_dict(ub, public=not reveal)
    return d

# ─── MATCHING ENGINE ─────────────────────────────────────────
def find_matches(annonce_id):
    """Find compatible annonces and create matches"""
    with get_db() as db:
        ann = db.execute("SELECT * FROM annonces WHERE id=?", (annonce_id,)).fetchone()
        if not ann or ann['status'] != 'active':
            return []

        settings = load_settings()
        rate = float(settings['eur_fcfa'])

        # Find compatible annonces (opposite direction, overlapping amount range)
        if ann['direction'] == 'EUR_TO_FCFA':
            opposite_dir = 'FCFA_TO_EUR'
        else:
            opposite_dir = 'EUR_TO_FCFA'

        candidates = db.execute("""
            SELECT * FROM annonces
            WHERE direction=? AND status='active' AND user_id!=?
            AND amount_max >= ? AND amount_min <= ?
            ORDER BY created ASC
        """, (opposite_dir, ann['user_id'], ann['amount_min'], ann['amount_max'])).fetchall()

        created_matches = []
        remaining = ann['amount'] - ann['matched_amount']

        for candidate in candidates:
            if remaining <= 0:
                break
            cand_remaining = candidate['amount'] - candidate['matched_amount']
            if cand_remaining <= 0:
                continue

            match_amount = min(remaining, cand_remaining)

            # Calculate EUR and FCFA amounts
            if ann['direction'] == 'EUR_TO_FCFA':
                eur_ann, fcfa_ann = ann, candidate
                amount_eur = match_amount
            else:
                eur_ann, fcfa_ann = candidate, ann
                amount_eur = match_amount

            amount_fcfa = round(amount_eur * rate)
            comm = float(settings['commission']) / 100
            comm_a = round(amount_eur * comm, 2)
            comm_b = round(amount_fcfa * comm)

            ref = gen_ref('MX')
            expires = (datetime.now() + timedelta(hours=72)).isoformat()

            db.execute("""
                INSERT INTO matches
                (ref, annonce_a_id, annonce_b_id, user_a_id, user_b_id,
                 amount_eur, amount_fcfa, commission_a, commission_b, expires_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (ref, eur_ann['id'], fcfa_ann['id'],
                  eur_ann['user_id'], fcfa_ann['user_id'],
                  amount_eur, amount_fcfa, comm_a, comm_b, expires))

            match_id = db.execute("SELECT id FROM matches WHERE ref=?", (ref,)).fetchone()['id']

            # Update matched amounts
            db.execute("UPDATE annonces SET matched_amount=matched_amount+? WHERE id=?",
                      (match_amount, ann['id']))
            db.execute("UPDATE annonces SET matched_amount=matched_amount+? WHERE id=?",
                      (match_amount, candidate['id']))

            # Notify both users
            add_notification(eur_ann['user_id'], 'match',
                '🎉 Match trouvé !',
                f'Un partenaire veut échanger {amount_eur}€. Acceptez le match pour continuer.',
                f'/match/{ref}')
            add_notification(fcfa_ann['user_id'], 'match',
                '🎉 Match trouvé !',
                f'Un partenaire veut échanger {amount_eur}€ contre des FCFA. Acceptez le match pour continuer.',
                f'/match/{ref}')

            remaining -= match_amount
            created_matches.append(match_id)

        return created_matches

# ─── FRONTEND ROUTES ─────────────────────────────────────────
@app.route('/')
def index(): return send_from_directory('templates', 'index.html')

@app.route('/dashboard')
def dashboard(): return send_from_directory('templates', 'dashboard.html')

@app.route('/marketplace')
def marketplace(): return send_from_directory('templates', 'marketplace.html')

@app.route('/annonce/new')
def annonce_new(): return send_from_directory('templates', 'annonce.html')

@app.route('/match/<ref>')
def match_detail(ref): return send_from_directory('templates', 'match.html')

@app.route('/admin')
def admin_page(): return send_from_directory('templates', 'admin.html')

@app.route('/verify/<token>')
def verify_email(token):
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE verify_token=?", (token,)).fetchone()
        if not user:
            return "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px'>❌ Lien invalide.</h2>", 400
        db.execute("UPDATE users SET verified=1, verify_token=NULL, token_expiry=NULL WHERE id=?", (user['id'],))
    return send_from_directory('templates', 'verified.html')

# ─── PUBLIC API ───────────────────────────────────────────────
@app.route('/api/settings/public')
def public_settings():
    s = load_settings()
    return jsonify({
        'eur_fcfa': float(s.get('eur_fcfa', 655.96)),
        'commission': float(s.get('commission', 1.0)),
        'iban': s.get('iban',''), 'iban_name': s.get('iban_name','EcoTransfert'),
        'wave_number': s.get('wave_number',''), 'mtn_number': s.get('mtn_number',''),
        'moov_number': s.get('moov_number',''), 'orange_number': s.get('orange_number',''),
        'wa_number': s.get('wa_number',''),
    })

# ─── AUTH ─────────────────────────────────────────────────────
def send_verification_email(email, first, token):
    if not MAIL_ENABLED:
        return False
    try:
        verify_url = f"{BASE_URL}/verify/{token}"
        payload = _json.dumps({
            "from": MAIL_FROM,
            "to": [email],
            "subject": "✅ Confirmez votre compte EcoTransfert",
            "html": f"""<div style="font-family:Georgia,serif;max-width:520px;margin:0 auto;padding:40px 20px;background:#F7F4EF;color:#0D0D0D">
              <div style="text-align:center;margin-bottom:32px">
                <h1 style="color:#B8922A;font-size:28px;margin:0">EcoTransfert</h1>
              </div>
              <div style="background:#fff;border:1px solid #E2DDD6;border-radius:12px;padding:32px">
                <h2 style="font-size:20px;margin:0 0 12px">Bonjour {first},</h2>
                <p style="color:#6B6560;line-height:1.6;margin:0 0 24px">Cliquez sur le bouton ci-dessous pour confirmer votre email et activer votre compte.</p>
                <div style="text-align:center;margin:32px 0">
                  <a href="{verify_url}" style="background:#B8922A;color:#fff;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;display:inline-block">Confirmer mon compte →</a>
                </div>
                <p style="color:#6B6560;font-size:12px;margin:0;text-align:center">Ce lien expire dans 24 heures.</p>
              </div>
            </div>"""
        }).encode('utf-8')
        req = urllib.request.Request(
            'https://api.resend.com/emails',
            data=payload,
            headers={'Authorization': f'Bearer {RESEND_API_KEY}', 'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"Resend error: {e}")
        return False

@app.route('/api/resend-verification', methods=['POST'])
def resend_verification():
    d = request.json
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE email=?", (d.get('email',''),)).fetchone()
    if not user or user['verified']:
        return jsonify({'ok': True})
    token = secrets.token_urlsafe(32)
    expiry = (datetime.now() + timedelta(hours=24)).isoformat()
    with get_db() as db:
        db.execute("UPDATE users SET verify_token=?, token_expiry=? WHERE id=?", (token, expiry, user['id']))
    send_verification_email(user['email'], user['first'], token)
    return jsonify({'ok': True})

@app.route('/api/register', methods=['POST'])
def register():
    d = request.json
    if not all([d.get('first'), d.get('last'), d.get('email'), d.get('password')]):
        return jsonify({'error': 'Champs obligatoires manquants'}), 400
    if len(d['password']) < 6:
        return jsonify({'error': 'Mot de passe trop court (min. 6 caractères)'}), 400
    try:
        token = secrets.token_urlsafe(32)
        expiry = (datetime.now() + timedelta(hours=24)).isoformat()
        verified = 0 if MAIL_ENABLED else 1
        with get_db() as db:
            db.execute(
                "INSERT INTO users (first,last,email,phone,country,password,verified,verify_token,token_expiry) VALUES (?,?,?,?,?,?,?,?,?)",
                (d['first'], d['last'], d['email'], d.get('phone',''), d.get('country',''),
                 generate_password_hash(d['password']), verified,
                 token if MAIL_ENABLED else None,
                 expiry if MAIL_ENABLED else None)
            )
        if MAIL_ENABLED:
            mail_sent = send_verification_email(d['email'], d['first'], token)
            if mail_sent:
                return jsonify({'ok': True, 'mail_sent': True})
            else:
                # Mail failed — auto-verify so user can still login
                with get_db() as db2:
                    db2.execute("UPDATE users SET verified=1, verify_token=NULL WHERE email=?", (d['email'],))
                return jsonify({'ok': True, 'auto_verified': True})
        return jsonify({'ok': True, 'auto_verified': True})
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
    return jsonify(user_to_dict(user))

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

@app.route('/api/me')
@login_required
def me():
    with get_db() as db:
        user = db.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    return jsonify(user_to_dict(user))

@app.route('/api/me', methods=['PUT'])
@login_required
def update_me():
    d = request.json
    with get_db() as db:
        db.execute("UPDATE users SET phone=?, country=? WHERE id=?",
                  (d.get('phone',''), d.get('country',''), session['user_id']))
    return jsonify({'ok': True})

# ─── ANNONCES ─────────────────────────────────────────────────
@app.route('/api/annonces', methods=['GET'])
def list_annonces():
    direction = request.args.get('direction')
    query = "SELECT a.*, u.first, u.last, u.country, u.rating_sum, u.rating_count FROM annonces a JOIN users u ON a.user_id=u.id WHERE a.status='active'"
    params = []
    if direction:
        query += " AND a.direction=?"
        params.append(direction)
    query += " ORDER BY a.created DESC"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['user'] = {'first': r['first'], 'last': r['last'][:1]+'.',
                     'country': r['country'],
                     'rating': round(r['rating_sum']/r['rating_count'],1) if r['rating_count'] > 0 else None,
                     'rating_count': r['rating_count']}
        result.append(d)
    return jsonify(result)

@app.route('/api/annonces', methods=['POST'])
@login_required
def create_annonce():
    d = request.json
    direction = d.get('direction')
    amount = float(d.get('amount', 0))
    amount_min = float(d.get('amount_min', amount * 0.8))
    amount_max = float(d.get('amount_max', amount * 1.2))

    if direction not in ('EUR_TO_FCFA', 'FCFA_TO_EUR'):
        return jsonify({'error': 'Direction invalide'}), 400
    if amount < 10:
        return jsonify({'error': 'Montant minimum 10'}), 400
    if amount_min > amount_max:
        return jsonify({'error': 'Fourchette invalide'}), 400

    currency_give = 'EUR' if direction == 'EUR_TO_FCFA' else 'FCFA'
    currency_want = 'FCFA' if direction == 'EUR_TO_FCFA' else 'EUR'
    expires = (datetime.now() + timedelta(days=7)).isoformat()
    ref = gen_ref('AN')

    with get_db() as db:
        db.execute("""
            INSERT INTO annonces
            (ref, user_id, direction, amount, amount_min, amount_max,
             currency_give, currency_want, note, expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (ref, session['user_id'], direction, amount, amount_min, amount_max,
              currency_give, currency_want, d.get('note',''), expires))
        ann = db.execute("SELECT * FROM annonces WHERE ref=?", (ref,)).fetchone()

    # Try to find matches
    matches = find_matches(ann['id'])

    return jsonify({'annonce': dict(ann), 'matches_found': len(matches)}), 201

@app.route('/api/annonces/mine')
@login_required
def my_annonces():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM annonces WHERE user_id=? ORDER BY created DESC",
            (session['user_id'],)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/annonces/<int:ann_id>', methods=['DELETE'])
@login_required
def cancel_annonce(ann_id):
    with get_db() as db:
        ann = db.execute("SELECT * FROM annonces WHERE id=? AND user_id=?",
                        (ann_id, session['user_id'])).fetchone()
        if not ann:
            return jsonify({'error': 'Non trouvé'}), 404
        db.execute("UPDATE annonces SET status='cancelled' WHERE id=?", (ann_id,))
    return jsonify({'ok': True})

# ─── MATCHES ─────────────────────────────────────────────────
@app.route('/api/matches/mine')
@login_required
def my_matches():
    uid = session['user_id']
    with get_db() as db:
        rows = db.execute("""
            SELECT m.*, 
                   ua.first as a_first, ua.last as a_last, ua.country as a_country,
                   ua.rating_sum as a_rs, ua.rating_count as a_rc,
                   ub.first as b_first, ub.last as b_last, ub.country as b_country,
                   ub.rating_sum as b_rs, ub.rating_count as b_rc
            FROM matches m
            JOIN users ua ON m.user_a_id=ua.id
            JOIN users ub ON m.user_b_id=ub.id
            WHERE m.user_a_id=? OR m.user_b_id=?
            ORDER BY m.created DESC
        """, (uid, uid)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        reveal = r['accepted_a'] and r['accepted_b']
        d['proof_a_url'] = f"/static/uploads/{r['proof_a']}" if r['proof_a'] else None
        d['proof_b_url'] = f"/static/uploads/{r['proof_b']}" if r['proof_b'] else None
        d['is_user_a'] = (r['user_a_id'] == uid)
        # Public info always visible, contact only after both accept
        d['partner'] = {
            'first': r['b_first'] if d['is_user_a'] else r['a_first'],
            'last': (r['b_last'] if d['is_user_a'] else r['a_last'])[:1]+'.' if not reveal else (r['b_last'] if d['is_user_a'] else r['a_last']),
            'country': r['b_country'] if d['is_user_a'] else r['a_country'],
            'rating': round((r['b_rs']/r['b_rc']) if d['is_user_a'] else (r['a_rs']/r['a_rc']), 1) if (r['b_rc'] if d['is_user_a'] else r['a_rc']) > 0 else None,
        }
        result.append(d)
    return jsonify(result)

@app.route('/api/matches/<ref>')
@login_required
def get_match(ref):
    uid = session['user_id']
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
    if not m:
        return jsonify({'error': 'Non trouvé'}), 404
    if m['user_a_id'] != uid and m['user_b_id'] != uid and not session.get('is_admin'):
        return jsonify({'error': 'Accès refusé'}), 403
    return jsonify(match_to_dict(m, uid))

@app.route('/api/matches/<ref>/accept', methods=['POST'])
@login_required
def accept_match(ref):
    uid = session['user_id']
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m:
            return jsonify({'error': 'Non trouvé'}), 404
        if m['status'] not in ('pending',):
            return jsonify({'error': 'Match non modifiable'}), 400

        if m['user_a_id'] == uid:
            db.execute("UPDATE matches SET accepted_a=1 WHERE ref=?", (ref,))
            # Notify partner
            add_notification(m['user_b_id'], 'match_accepted',
                '✅ Match accepté', 'Votre partenaire a accepté le match. Envoyez votre preuve de paiement.', f'/match/{ref}')
        elif m['user_b_id'] == uid:
            db.execute("UPDATE matches SET accepted_b=1 WHERE ref=?", (ref,))
            add_notification(m['user_a_id'], 'match_accepted',
                '✅ Match accepté', 'Votre partenaire a accepté le match. Envoyez votre preuve de paiement.', f'/match/{ref}')
        else:
            return jsonify({'error': 'Non autorisé'}), 403

        # Check if both accepted
        updated = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if updated['accepted_a'] and updated['accepted_b']:
            db.execute("UPDATE matches SET status='proof_pending' WHERE ref=?", (ref,))
            add_notification(m['user_a_id'], 'info', '📤 Envoyez votre preuve',
                'Les deux parties ont accepté. Envoyez maintenant votre preuve de paiement.', f'/match/{ref}')
            add_notification(m['user_b_id'], 'info', '📤 Envoyez votre preuve',
                'Les deux parties ont accepté. Envoyez maintenant votre preuve de paiement.', f'/match/{ref}')

    return jsonify({'ok': True})

@app.route('/api/matches/<ref>/decline', methods=['POST'])
@login_required
def decline_match(ref):
    uid = session['user_id']
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m or (m['user_a_id'] != uid and m['user_b_id'] != uid):
            return jsonify({'error': 'Non autorisé'}), 403
        db.execute("UPDATE matches SET status='cancelled' WHERE ref=?", (ref,))
        # Reactivate annonces
        db.execute("UPDATE annonces SET matched_amount=MAX(0,matched_amount-?) WHERE id=?",
                  (m['amount_eur'], m['annonce_a_id']))
        db.execute("UPDATE annonces SET matched_amount=MAX(0,matched_amount-?) WHERE id=?",
                  (m['amount_eur'], m['annonce_b_id']))
        partner_id = m['user_b_id'] if m['user_a_id'] == uid else m['user_a_id']
        add_notification(partner_id, 'match_declined', '❌ Match refusé',
            'Votre partenaire a refusé le match. Votre annonce est à nouveau active.', '/marketplace')
    return jsonify({'ok': True})

@app.route('/api/matches/<ref>/proof', methods=['POST'])
@login_required
def upload_proof(ref):
    uid = session['user_id']
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m:
            return jsonify({'error': 'Non trouvé'}), 404
        if m['user_a_id'] != uid and m['user_b_id'] != uid:
            return jsonify({'error': 'Non autorisé'}), 403

        proof_path = None
        if 'proof' in request.files:
            file = request.files['proof']
            if file.filename:
                ext = os.path.splitext(file.filename)[1].lower()
                fname = f"{uuid.uuid4().hex}{ext}"
                file.save(os.path.join(UPLOAD_FOLDER, fname))
                proof_path = fname

        if not proof_path:
            return jsonify({'error': 'Fichier manquant'}), 400

        is_a = (m['user_a_id'] == uid)
        if is_a:
            db.execute("UPDATE matches SET proof_a=? WHERE ref=?", (proof_path, ref))
        else:
            db.execute("UPDATE matches SET proof_b=? WHERE ref=?", (proof_path, ref))

        updated = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if updated['proof_a'] and updated['proof_b']:
            db.execute("UPDATE matches SET status='validating' WHERE ref=?", (ref,))
            # Notify admin via settings
            s = load_settings()
            wa = s.get('wa_number','').replace(' ','')
            partner_id = m['user_b_id'] if is_a else m['user_a_id']
            add_notification(partner_id, 'info', '⏳ Validation en cours',
                'Les deux preuves ont été soumises. L\'admin va valider sous 24h.', f'/match/{ref}')
        else:
            partner_id = m['user_b_id'] if is_a else m['user_a_id']
            add_notification(partner_id, 'proof', '📎 Preuve reçue',
                'Votre partenaire a envoyé sa preuve. Envoyez la vôtre pour continuer.', f'/match/{ref}')

    return jsonify({'ok': True})

@app.route('/api/matches/<ref>/rate', methods=['POST'])
@login_required
def rate_match(ref):
    uid = session['user_id']
    d = request.json
    score = int(d.get('score', 0))
    if score < 1 or score > 5:
        return jsonify({'error': 'Note entre 1 et 5'}), 400
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m or m['status'] != 'completed':
            return jsonify({'error': 'Transaction non complétée'}), 400
        rated_id = m['user_b_id'] if m['user_a_id'] == uid else m['user_a_id']
        try:
            db.execute(
                "INSERT INTO ratings (match_id,rater_id,rated_id,score,comment) VALUES (?,?,?,?,?)",
                (m['id'], uid, rated_id, score, d.get('comment',''))
            )
            db.execute(
                "UPDATE users SET rating_sum=rating_sum+?, rating_count=rating_count+1 WHERE id=?",
                (score, rated_id)
            )
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Déjà noté'}), 409
    return jsonify({'ok': True})

# ─── NOTIFICATIONS ────────────────────────────────────────────
@app.route('/api/notifications')
@login_required
def get_notifications():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created DESC LIMIT 20",
            (session['user_id'],)
        ).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/notifications/read', methods=['POST'])
@login_required
def mark_read():
    with get_db() as db:
        db.execute("UPDATE notifications SET read=1 WHERE user_id=?", (session['user_id'],))
    return jsonify({'ok': True})

@app.route('/api/notifications/unread-count')
@login_required
def unread_count():
    with get_db() as db:
        count = db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
            (session['user_id'],)
        ).fetchone()[0]
    return jsonify({'count': count})

# ─── ADMIN ────────────────────────────────────────────────────
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

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    with get_db() as db:
        users = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        annonces = db.execute("SELECT COUNT(*) FROM annonces WHERE status='active'").fetchone()[0]
        matches_total = db.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        matches_validating = db.execute("SELECT COUNT(*) FROM matches WHERE status='validating'").fetchone()[0]
        matches_completed = db.execute("SELECT COUNT(*) FROM matches WHERE status='completed'").fetchone()[0]
        volume = db.execute("SELECT COALESCE(SUM(amount_eur),0) FROM matches WHERE status='completed'").fetchone()[0]
        commission = db.execute("SELECT COALESCE(SUM(commission_a+commission_b),0) FROM matches WHERE status='completed'").fetchone()[0]
    return jsonify({'users': users, 'annonces': annonces, 'matches_total': matches_total,
                    'matches_validating': matches_validating, 'matches_completed': matches_completed,
                    'volume': round(volume,2), 'commission': round(commission,2)})

@app.route('/api/admin/matches')
@admin_required
def admin_matches():
    status = request.args.get('status')
    query = """SELECT m.*, ua.first||' '||ua.last as user_a_name, ub.first||' '||ub.last as user_b_name
               FROM matches m JOIN users ua ON m.user_a_id=ua.id JOIN users ub ON m.user_b_id=ub.id"""
    params = []
    if status:
        query += " WHERE m.status=?"
        params.append(status)
    query += " ORDER BY m.created DESC"
    with get_db() as db:
        rows = db.execute(query, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['proof_a_url'] = f"/static/uploads/{r['proof_a']}" if r['proof_a'] else None
        d['proof_b_url'] = f"/static/uploads/{r['proof_b']}" if r['proof_b'] else None
        result.append(d)
    return jsonify(result)

@app.route('/api/admin/matches/<ref>/validate', methods=['POST'])
@admin_required
def validate_match(ref):
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m:
            return jsonify({'error': 'Non trouvé'}), 404
        db.execute("UPDATE matches SET status='completed', completed_at=? WHERE ref=?",
                  (datetime.now().isoformat(), ref))
        db.execute("UPDATE annonces SET status='completed' WHERE id=? OR id=?",
                  (m['annonce_a_id'], m['annonce_b_id']))
        add_notification(m['user_a_id'], 'completed', '✅ Transaction validée !',
            f'Votre échange de {m["amount_eur"]}€ a été validé. Notez votre partenaire !', f'/match/{ref}')
        add_notification(m['user_b_id'], 'completed', '✅ Transaction validée !',
            f'Votre échange de {m["amount_eur"]}€ a été validé. Notez votre partenaire !', f'/match/{ref}')
    return jsonify({'ok': True})

@app.route('/api/admin/matches/<ref>/reject', methods=['POST'])
@admin_required
def reject_match(ref):
    d = request.json or {}
    with get_db() as db:
        m = db.execute("SELECT * FROM matches WHERE ref=?", (ref,)).fetchone()
        if not m:
            return jsonify({'error': 'Non trouvé'}), 404
        db.execute("UPDATE matches SET status='disputed' WHERE ref=?", (ref,))
        msg = d.get('reason', 'Une preuve a été rejetée. Contactez le support.')
        add_notification(m['user_a_id'], 'dispute', '⚠️ Litige ouvert', msg, f'/match/{ref}')
        add_notification(m['user_b_id'], 'dispute', '⚠️ Litige ouvert', msg, f'/match/{ref}')
    return jsonify({'ok': True})

@app.route('/api/admin/users')
@admin_required
def admin_users():
    with get_db() as db:
        rows = db.execute("""
            SELECT u.*, COUNT(DISTINCT a.id) as annonce_count,
                   COUNT(DISTINCT m.id) as match_count
            FROM users u
            LEFT JOIN annonces a ON a.user_id=u.id
            LEFT JOIN matches m ON m.user_a_id=u.id OR m.user_b_id=u.id
            GROUP BY u.id ORDER BY u.created DESC
        """).fetchall()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/annonces')
@admin_required
def admin_annonces():
    with get_db() as db:
        rows = db.execute("""
            SELECT a.*, u.first||' '||u.last as user_name
            FROM annonces a JOIN users u ON a.user_id=u.id
            ORDER BY a.created DESC
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

@app.route('/api/admin/cleanup-unverified', methods=['GET','POST'])
@admin_required
def cleanup_unverified():
    with get_db() as db:
        users = db.execute("SELECT email FROM users WHERE verified=0").fetchall()
        count = len(users)
        db.execute("DELETE FROM users WHERE verified=0")
    return jsonify({'ok': True, 'deleted': count, 'emails': [u['email'] for u in users]})

if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV')=='development',
            host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
