# Déploiement sur Heroku

## Prérequis
- Compte Heroku gratuit (https://signup.heroku.com/)
- Git installé
- Heroku CLI installé (https://devcenter.heroku.com/articles/heroku-cli)

## Étapes de déploiement

### 1. Initialiser Git
```bash
cd "c:\Users\celen\Documents\TicketMP - Copie\TicketMPbot"
git init
git add .
git commit -m "Initial commit"
```

### 2. Créer l'app Heroku
```bash
heroku login
heroku create
```

### 3. Configurer les variables d'environnement
```bash
heroku config:set DISCORD_TOKEN=VOTRE_TOKEN_ICI
```

**Important :** Remplacez `VOTRE_TOKEN_ICI` par votre vrai token Discord.

### 4. Déployer
```bash
git push heroku main
```

### 5. Vérifier que le bot tourne
```bash
heroku logs --tail
```

## Mises à jour ultérieures
Pour mettre à jour le bot après des modifications :
```bash
git add .
git commit -m "Description des changements"
git push heroku main
```

## Redémarrer le bot
```bash
heroku restart
```

## Limites du plan gratuit Heroku
- Le bot s'endort après 30 minutes d'inactivité
- Il se réveille automatiquement quand il reçoit un événement Discord
- Maximum 550-1000 heures par mois

## Pour éviter l'endormissement (optionnel)
Utilisez un service comme UptimeRobot pour envoyer un ping toutes les 5 minutes à votre bot Heroku.
