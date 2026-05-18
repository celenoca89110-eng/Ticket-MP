import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from flask import Flask, redirect, render_template, request, session

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]

# Dossier web/ puis racine TicketMPbot/
_WEB_DIR = Path(__file__).resolve().parent
ROOT = str(_WEB_DIR.parent)
if load_dotenv:
    load_dotenv(os.path.join(ROOT, ".env"), encoding="utf-8-sig", override=True)
    load_dotenv(os.path.join(str(_WEB_DIR), ".env"), encoding="utf-8-sig", override=True)
else:
    print(
        "⚠️ Installe python-dotenv pour charger .env : python -m pip install python-dotenv"
    )
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import store

store.init_db()

app = Flask(__name__)

app.secret_key = os.getenv("SESSION_SECRET") or os.urandom(24).hex()

BASE_DIR = os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, "..", "config.json")
ADMIN_FILE = os.path.join(BASE_DIR, "..", "admin.json")

CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "1498556310470524968")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv(
    "DISCORD_REDIRECT_URI",
    "http://localhost:3000/callback",
)

API = "https://discord.com/api/v10"


def load_json(path: str) -> dict:
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read().strip()
                if not text:
                    return {}
                data = json.loads(text)
                return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}
    return {}


def web_admin_allowed() -> bool:
    """Si `web_admins` est absent → tout le monde (OAuth). Liste vide → personne."""
    if "user" not in session:
        return False
    data = load_json(ADMIN_FILE)
    if "web_admins" not in data:
        return True
    raw = data.get("web_admins")
    if raw is None:
        return True
    if not isinstance(raw, list):
        return True
    if len(raw) == 0:
        return False
    uid = str(session["user"]["id"])
    return uid in {str(x) for x in raw}


def load_dashboard_context():
    tickets = store.get_tickets_dict()
    st = store.stats_get()
    return tickets, st["opened"], st["closed"]


@app.route("/")
def home():
    return redirect("/dashboard")


@app.route("/login")
def login():
    q = urlencode(
        {
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify",
        }
    )
    return redirect(f"{API}/oauth2/authorize?{q}")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code provided"
    if not CLIENT_SECRET:
        return (
            "Configurez DISCORD_CLIENT_SECRET (variable d’environnement) "
            "avant OAuth."
        )

    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "scope": "identify",
    }

    r = requests.post(
        f"{API}/oauth2/token", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        token = r.json().get("access_token")
        if not token:
            return f"OAuth: {r.text}"
    except Exception:
        return f"Token error: {r.text}"

    user = requests.get(
        f"{API}/users/@me",
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    session["user"] = user
    return redirect("/dashboard")


@app.route("/dashboard")
def dashboard():
    if "user" not in session:
        return redirect("/login")
    if not web_admin_allowed():
        return (
            "<h1>Accès refusé</h1><p>Ton ID Discord n’est pas dans "
            "<code>admin.json</code> (<code>web_admins</code>). "
            "Demande à l’admin d’ajouter ton ID, ou vide la liste pour mode dev.</p>"
            '<p><a href="/logout">Déconnexion</a></p>'
        ), 403

    tickets, opened, closed = load_dashboard_context()
    guild_cfg = load_json(CONFIG_FILE).get("guilds", {})
    categories_map = {}
    for gid, g in guild_cfg.items():
        cats = (g or {}).get("categories") or {}
        categories_map[str(gid)] = [
            {"key": k, "label": (v or {}).get("label", k)} for k, v in cats.items()
        ]
    guild_categories_json = json.dumps(categories_map, ensure_ascii=False)

    stats = {
        "active": len(tickets),
        "opened_bot": opened,
        "closed_bot": closed,
    }
    return render_template(
        "dashboard.html",
        user=session["user"],
        tickets=tickets,
        stats=stats,
        guild_cfg=guild_cfg,
        guild_categories_json=guild_categories_json,
    )


@app.route("/open_ticket", methods=["POST"])
def open_ticket():
    if "user" not in session:
        return redirect("/login")
    if not web_admin_allowed():
        return redirect("/login")

    ticket_id = f"{int(time.time())}-{os.urandom(2).hex()}"
    store.web_queue_add(
        ticket_id,
        str(session["user"]["id"]),
        request.form["guild_id"],
        request.form["category"],
        request.form.get("message", "Ticket web"),
    )
    return redirect("/dashboard")


@app.route("/close/<uid>")
def close(uid):
    if "user" not in session:
        return redirect("/login")
    if not web_admin_allowed():
        return redirect("/login")

    tickets = store.get_tickets_dict()
    if uid in tickets:
        ch_id = tickets[uid].get("channel_id")
        if ch_id:
            store.close_queue_add(int(ch_id), str(session["user"]["id"]))
        # Le bot supprime l’entrée SQLite après transcript + suppression du salon.

    return redirect("/dashboard")


@app.route("/tickets")
def tickets_page():
    if "user" not in session:
        return redirect("/login")
    if not web_admin_allowed():
        return (
            "<h1>Accès refusé</h1><p>Non autorisé (voir admin.json).</p>",
            403,
        )
    tickets = store.get_tickets_dict()
    return render_template("tickets.html", tickets=tickets, user=session["user"])


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


if __name__ == "__main__":
    port = int(os.getenv("PORT", "3000"))
    app.run(host=os.getenv("FLASK_HOST", "127.0.0.1"), port=port, debug=True)
