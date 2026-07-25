# Spotify Track

Tracker personal de tu música: historial de reproducciones y tops (canciones, artistas, álbumes) por periodo.

Stack: Flask + SQLAlchemy (SQLite) + Bootstrap 5. Los datos se traen de la Spotify Web API (endpoint `recently-played`), no de un export manual.

## Configurar tu app de Spotify

1. Ve a [developer.spotify.com/dashboard](https://developer.spotify.com/dashboard) y abre (o crea) tu app.
2. En **Redirect URIs** agrega exactamente: `http://127.0.0.1:5000/callback`
3. El `client_id` y `client_secret` ya están en `.env` (no los subas a git).

> Nota: el endpoint `recently-played` de Spotify solo devuelve tus últimas ~50 reproducciones. El tracker acumula historial real desde el momento en que empiezas a sincronizar hacia adelante; no importa reproducciones pasadas a esa ventana.

## Cómo correr el proyecto

```bash
python -m venv venv
venv\Scripts\activate          # PowerShell: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python run.py
```

Abre http://127.0.0.1:5000, presiona **"Conectar con Spotify"** y autoriza el acceso. Luego usa el botón **"Sincronizar"** cada vez que quieras traer tus reproducciones más recientes (no duplica las que ya tienes guardadas). Cada sync refresca el token de acceso en segundo plano — no vuelve a pedirte login salvo que revoques el acceso desde tu cuenta de Spotify.

La pestaña **"Datos crudos"** muestra el JSON tal cual lo devuelve la API en cada sync, útil para depurar.

La pestaña **"Biblioteca"** muestra tus artistas seguidos y tus playlists (se piden en vivo a Spotify, no se guardan). Si ya habías conectado tu cuenta antes de que existiera esta sección, necesitas presionar **"Reconectar cuenta"** una vez para autorizar los permisos nuevos (`user-follow-read`, `playlist-read-private`, `playlist-read-collaborative`).

## Estructura

- `app/models.py` — modelos `Artist`, `Track`, `PlayEvent`, `SpotifyToken`, `SyncLog`
- `app/spotify_api.py` — flujo OAuth (login/callback), refresco de token, sync de `recently-played`, guardado del JSON crudo, y lectura en vivo de artistas seguidos / playlists
- `app/routes.py` — dashboard, top (con filtros de rango), historial paginado, datos crudos, biblioteca, `/login` `/callback` `/sync`
- `app/templates/` — vistas Bootstrap 5
