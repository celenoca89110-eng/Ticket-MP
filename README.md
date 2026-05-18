# TicketMP (bot Discord + mini dashboard web)

## Prérequis

- Python 3.10+
- **`discord.py` ≥ 2.4** (menus *salon / rôle* du `/botconfig`)
- Une application Discord (bot + OAuth2 pour le site)

## Fonctionnement ticket (MP ↔ salon)

- Le **demandeur** écrit **uniquement en MP** avec le bot : ses messages sont relayés dans le salon ticket (**embed violet**).
- Dans le salon ticket il a **lecture seule** (il voit le fil mais ne peut pas envoyer dans le salon).
- Le **staff** répond dans le salon : le bot renvoie au demandeur un **embed vert** en MP (texte, pièces jointes, résumé d’embeds).

## Installation

```bash
cd TicketMPbot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Variables d’environnement

Tu peux tout mettre dans un fichier **`.env`** à la racine du dossier `TicketMPbot` (c’est un **fichier texte**, pas un dossier ; sous Windows, crée `TicketMPbot\.env` dans le Bloc-notes puis « Enregistrer sous » Type : *Tous les fichiers*). Le même fichier est lu par `bot.py` et par `web/app.py` (un second fichier optionnel `web/.env` peut surcharger des clés).

**Attention Windows :** si tu as déjà défini `DISCORD_TOKEN` dans les variables d’environnement système ou utilisateur, l’ancienne valeur peut prendre le dessus par défaut. Ce projet utilise `override=True` pour que **le fichier `.env` gagne toujours**.

Exemple `.env` :

```env
DISCORD_TOKEN=ton_token_bot
DISCORD_CLIENT_SECRET=ton_secret_oauth
DISCORD_CLIENT_ID=ton_id_application
DISCORD_REDIRECT_URI=http://localhost:3000/callback
# Optionnel : port du serveur Flask (défaut 3000, doit coller avec l’URL ci-dessus)
# PORT=3000
```

**Bot** (`bot.py`) :

- `DISCORD_TOKEN` — token du bot (obligatoire)

**Site** (`web/app.py`) :

- `DISCORD_CLIENT_SECRET` — secret OAuth (obligatoire pour se connecter)
- `DISCORD_CLIENT_ID` — ID application (sinon valeur par défaut du projet)
- `DISCORD_REDIRECT_URI` — ex. `http://localhost:3000/callback` : **exactement** la même URL que dans le portail Discord → OAuth2 → Redirects (et sans slash en trop)
- `PORT` — port Flask (défaut **3000** pour correspondre à l’URL ci-dessus)
- `FLASK_HOST` — défaut `127.0.0.1`
- `SESSION_SECRET` — chaîne aléatoire longue pour signer les cookies de session (recommandé en prod)

## Fichiers de configuration

- `config.json` — racine : `panel_admin_ids` (optionnel, IDs autorisés pour `/botconfig`).  
  Ensuite `guilds` : `category_id`, `log_channel_id`, `admin_roles`, `categories` du menu ticket, etc.
- `admin.json` — liste `web_admins` (IDs Discord autorisés sur le dashboard).
  - Si la clé **`web_admins` est absente** : tout utilisateur connecté via OAuth peut accéder au dashboard (pratique en dev).
  - Si **`web_admins` est une liste vide** : personne n’a accès (sécurise tout).
  - Si la liste contient des IDs : seuls ces comptes ont accès.

Exemple :

```json
{
  "web_admins": ["123456789012345678"]
}
```

## Lancer le bot

```bash
python bot.py
```

Au premier démarrage, une base **`ticketmp.db`** (SQLite) est créée. Les anciens `tickets.json` / `web_requests.json` / `stats.json` sont importés automatiquement s’ils existent et que la base est vide.

## Lancer le dashboard web

```bash
cd web
python app.py
```

Puis ouvre `http://localhost:3000` (connexion Discord, puis dashboard).

Les fermetures depuis le web passent par une **file d’attente** : le bot supprime le salon, génère un **transcript** `.txt` et l’envoie (avec un embed) dans `log_channel_id` si configuré.

## Commandes slash (Discord)

| Commande | Rôle |
|----------|------|
| `/panel` | Panneau support (bouton MP) |
| `/botconfig` | **Configuration complète** (serveur actuel : catégorie tickets, salon logs, rôles staff, entrées menu, ajout d’un serveur par ID, liste `panel_admin_ids`). Réservé aux **Administrateurs Discord**, au **propriétaire du serveur**, ou aux IDs listés dans `panel_admin_ids`. |
| `/ticket_stats` | Statistiques (permission *Gérer les salons*) |
| `/ticket_close` | Ferme le ticket du salon actuel (staff) |
| `/ticket_reopen` | Rouvre pour un membre (`categorie` optionnelle = dernière fermeture) |

## Dossiers utiles

- `transcripts/` — copies locales des transcripts (également envoyées sur Discord si `log_channel_id` est valide).
