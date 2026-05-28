# mundial-bot

Bot autónomo de apuestas para el **Mundial FIFA 2026** (11-jun a 19-jul, 104 partidos, sede USA + México + Canadá).

100% separado del `playdoit-bot`. Bankroll propio, repo propio, workflow propio.

## Filosofía

- **Bayesiano intra-torneo**: cada partido jugado recalibra los priors antes del siguiente.
- **Día a día**: no hay plan multi-día pre-armado. Cada mañana decide en función de lo que pasó ayer.
- **Conservador**: max 2-3 picks/día, Kelly 1/5, edge mínimo +6%. Auto-pausa si pierde 3/3 o cae 15% bankroll.
- **3 capas**: cuantitativo equipos (Poisson + xG) · cuantitativo jugadores (lineups + goleadores) · cualitativo (OpenAI + web_search para clima, motivación, noticias 48h).

## Arquitectura

```
mundial_runner.py          # orquestador del cron diario
├── learning_tracker.py    # actualiza model_state.json con resultados de ayer
├── mundial_data.py        # fetch fixtures, lineups, standings (football-data + api-sports)
├── stats_engine.py        # Poisson normalizado por liga (portado de playdoit-bot)
├── analyze_mundial.py     # pipeline cuantitativo + cualitativo → picks
├── altenar_api.py         # colocación en PlayDoit (portado)
└── event_catalog.py       # catálogo PlayDoit (portado)
```

## Estado

Bankroll inicial: $1,000 MXN · cuenta PlayDoit compartida · cron diario 8 AM CDT (13:00 UTC) · dry runs del 1-9 jun · real desde 11-jun.

## Reglas duras

| Regla | Acción |
|---|---|
| Pierde 3/3 picks del día | Auto-pausa 24h |
| Bankroll cae -15% | Kelly 1/5 → 1/8 |
| Bankroll cae -30% | Pausa 48h |
| Línea PlayDoit cambia >5% antes de colocar | Cancela el pick |
| Edge calculado fuera de [+6%, +35%] | Descarta (sanity check) |
| Sample <5 partidos para un equipo | Skip ese partido |

## WhatsApp

Formato diferenciable del bot regular:

```
🏆 MUNDIAL IA | Jue 11-jun · 3 picks

1. Francia vs Inglaterra (O2.5) @1.90
   Edge +8.2% · Stake $35 · Confianza ALTA
   Veto OpenAI: ✅ (Mbappé titular, sin viento)

2. ...
```

## Variables de entorno

Ver `.env.example`. Todas las llaves duplicadas en GitHub Secrets del repo.
