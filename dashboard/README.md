# Dashboard Mundial IA

Página web estática que consume `daily_report.json`, `bets_history.json` y `bankroll_state.json` del repo mundial-bot.

## Deploy a Vercel

```bash
cd dashboard
npx vercel --prod
```

O vía web: importa este folder en vercel.com → deploy.

## Cómo funciona

1. Los archivos `*.json` los actualiza el cron de GitHub Actions del mundial-bot
2. Vercel sirve `index.html` que hace fetch a `raw.githubusercontent.com/.../mundial-bot/main/*.json`
3. Refresca cada 1 minuto automáticamente

## Requisito: repo público

El dashboard usa `raw.githubusercontent.com` que solo funciona con repos públicos. Si el repo `mundial-bot` es privado, hay 2 opciones:
- Hacer público el repo (recomendado — el código no tiene secretos, todos están en GitHub Secrets)
- Cambiar `REPO_RAW` en `index.html` por la GitHub API con un token de solo-lectura
