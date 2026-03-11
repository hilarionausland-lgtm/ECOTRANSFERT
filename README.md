# EcoTransfert — Plateforme de Médiation Financière

Plateforme de transfert d'argent sécurisée (France ↔ Bénin/Afrique) avec système de tiers de confiance.

## 🚀 Déploiement en 5 minutes sur Railway

### 1. Pousser sur GitHub

```bash
git init
git add .
git commit -m "Initial commit - EcoTransfert v1"
git remote add origin https://github.com/TON_USERNAME/ecotransfert.git
git push -u origin main
```

### 2. Déployer sur Railway (gratuit)

1. Allez sur [railway.app](https://railway.app) → **New Project**
2. **Deploy from GitHub repo** → sélectionnez `ecotransfert`
3. Railway détecte automatiquement Python/Flask
4. Dans **Variables d'environnement**, ajoutez :

| Variable | Valeur |
|---|---|
| `SECRET_KEY` | `une-clé-secrète-longue-et-aléatoire` |
| `ADMIN_PASSWORD` | `votre-mot-de-passe-admin` |
| `PORT` | `5000` |

5. Cliquez **Deploy** → votre app est en ligne ! 🎉

---

## 🛠️ Lancer en local

```bash
# Installer les dépendances
pip install -r requirements.txt

# Lancer le serveur de développement
FLASK_ENV=development python app.py
```

Ouvrez [http://localhost:5000](http://localhost:5000)

---

## 📁 Structure du projet

```
ecotransfert/
├── app.py              # Backend Flask + API REST
├── requirements.txt    # Dépendances Python
├── Procfile            # Config Gunicorn (Railway/Render)
├── runtime.txt         # Version Python
├── templates/
│   └── index.html      # Frontend complet (HTML/CSS/JS)
└── static/
    └── uploads/        # Preuves de paiement uploadées
```

---

## 🔑 Comptes par défaut

- **Admin** : login `admin` / mot de passe défini par `ADMIN_PASSWORD` (défaut: `admin123`)
- **Clients** : s'inscrivent directement via l'interface

> ⚠️ **Important** : Changez `ADMIN_PASSWORD` et `SECRET_KEY` avant la mise en production !

---

## ✨ Fonctionnalités

### Espace Client
- ✅ Inscription / connexion sécurisée (mots de passe hashés)
- ✅ Création de demande de transfert EUR → FCFA
- ✅ Calcul automatique avec taux de change + commission 2%
- ✅ Upload de preuve de paiement (JPG/PNG/PDF)
- ✅ Notification WhatsApp automatique à l'admin
- ✅ Historique complet des transactions

### Panneau Admin
- ✅ Dashboard avec statistiques (volume, commissions, nb clients)
- ✅ Liste des transactions en attente de validation
- ✅ Visualisation des preuves en plein écran
- ✅ Validation / rejet en 1 clic
- ✅ Gestion des utilisateurs
- ✅ Paramètres : taux de change, commission, WhatsApp, IBAN

### Sécurité
- ✅ Mots de passe hashés (Werkzeug)
- ✅ Sessions Flask sécurisées
- ✅ Séparation client / admin
- ✅ Base SQLite persistante

---

## 🌍 Alternatives d'hébergement gratuit

| Plateforme | URL | Notes |
|---|---|---|
| [Railway](https://railway.app) | `xxx.railway.app` | Le plus simple, 500h/mois gratuit |
| [Render](https://render.com) | `xxx.onrender.com` | Gratuit, dormance après 15min inactif |
| [PythonAnywhere](https://pythonanywhere.com) | `xxx.pythonanywhere.com` | Très simple, gratuit limité |

---

## 📱 Notification WhatsApp

Configurez votre numéro WhatsApp dans **Admin → Paramètres**.  
Dès qu'un client soumet une preuve, un lien WhatsApp s'ouvre automatiquement avec le message pré-rempli.

---

*EcoTransfert v1.0 — Développé avec Flask + SQLite*
