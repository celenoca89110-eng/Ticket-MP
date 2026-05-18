# Déploiement sur Railway

## Prérequis
- Compte Railway (https://railway.app/)
- Compte GitHub (recommandé)

## Étapes de déploiement

### Option 1 : Via GitHub (recommandé)

1. **Créer un repo GitHub**
   - Allez sur https://github.com/new
   - Créez un nouveau repository (public ou privé)
   - Uploadez tous les fichiers du dossier TicketMPbot

2. **Connecter Railway à GitHub**
   - Allez sur https://railway.app/new
   - Cliquez sur "Deploy from GitHub repo"
   - Sélectionnez votre repository
   - Railway détectera automatiquement que c'est un projet Python

3. **Configurer les variables d'environnement**
   - Dans le dashboard Railway, allez dans "Variables"
   - Ajoutez : `DISCORD_TOKEN` = votre token Discord
   - Cliquez sur "Save"

4. **Déployer**
   - Cliquez sur "Deploy"
   - Railway construira et lancera automatiquement le bot

### Option 2 : Via CLI (alternative)

1. **Installer Railway CLI**
```bash
npm install -g @railway/cli
```

2. **Se connecter**
```bash
railway login
```

3. **Initialiser le projet**
```bash
cd "c:\Users\celen\Documents\TicketMP - Copie\TicketMPbot"
railway init
```

4. **Configurer les variables**
```bash
railway variables set DISCORD_TOKEN=VOTRE_TOKEN_ICI
```

5. **Déployer**
```bash
railway up
```

## Mises à jour ultérieures

### Via GitHub
- Faites un `git push` sur votre repo GitHub
- Railway détectera automatiquement les changements et redéploiera

### Via CLI
```bash
railway up
```

## Avantages Railway vs Heroku
- ✅ Pas d'endormissement (bot tourne 24h/24)
- ✅ Déploiement automatique via GitHub
- ✅ Interface web simple
- ✅ Logs en temps réel
- ✅ 5$ / mois pour le plan de base (gratuit pour tester)

## Vérifier les logs
Dans le dashboard Railway, cliquez sur votre projet puis sur "Logs" pour voir les logs du bot en temps réel.

## Redémarrer le bot
Dans le dashboard Railway, cliquez sur "Restart" ou utilisez :
```bash
railway restart
```
