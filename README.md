# NBA Predictor Pro

Predice el ganador, la **diferencia de puntos** y el marcador estimado de los partidos de la NBA.
Solo usa la biblioteca estándar de Python: **no hay que instalar nada** (ni pandas, ni scikit-learn, ni nba_api).

## Uso local (Windows)
- Doble clic en **`NBA_Predictor.pyw`** (sin consola) o **`INICIAR.bat`** (con consola).
- Se abre la interfaz en tu navegador. Primera vez: **Actualizar datos** (≈1 min, necesita internet).
  Si ya tienes `C:\NBA Projecto\nba_historico.csv`, se detecta y se copia a `datos\`.

## Despliegue en Render
1. **Genera los datos en tu PC:** abre la app local, pulsa *Actualizar datos* y confirma que existe `datos/nba_historico.csv`.
2. **Sube el proyecto a GitHub**, incluyendo `datos/nba_historico.csv` (es la semilla del servidor; el `.gitignore` ya lo permite).
3. En Render: **New > Blueprint**, elige el repositorio (lee `render.yaml`). Alternativa manual: *Web Service*,
   Runtime **Python**, Build Command `echo ok`, Start Command `python app.py`, Health Check Path `/salud`,
   y variable `NBA_NUBE=1`.
4. Al abrir la URL pública verás la misma interfaz, sin botones de actualizar ni salir.

**Cómo se comporta en la nube**
- Escucha en `0.0.0.0:$PORT` (Render define `PORT`); `/salud` responde de inmediato mientras el modelo carga.
- Se entrena al arrancar con el CSV del repositorio y calcula la evaluación sola.
- Cada `NBA_REFRESCO_HORAS` horas (por defecto 6) actualiza los datos y reentrena si hay cambios. Fuentes, en orden:
  `stats.nba.com` (datos completos) y, si bloquea al servidor, **ESPN** (marcadores de los partidos nuevos).
- La cartelera usa, en orden: calendario oficial de la NBA, ESPN y `stats.nba.com`. Si todas fallan, la pantalla
  muestra el error de cada una.
- Para saber qué pasó con la última actualización abre `https://TU-APP.onrender.com/api/estado` (campo `actualizacion`)
  o pasa el cursor sobre el indicador de estado de la cabecera.
- Las rutas administrativas (`/api/actualizar`, `/api/evaluar`, `/api/salir`) están bloqueadas para el público.

**Cosas a tener en cuenta**
- El disco del plan gratuito es efímero: lo descargado se pierde al reiniciar; por eso el CSV debe estar en el repositorio.
  Para actualizarlo de forma permanente, repite el paso 1 y haz `git push`.
- El plan gratuito se duerme tras ~15 min sin visitas; el primer acceso tarda mientras entrena.
- `stats.nba.com` suele bloquear IPs de servidores en la nube. Los partidos añadidos por ESPN traen solo el marcador
  (sin tiros ni rebotes), así que su rating ofensivo/defensivo es una aproximación; al volver a descargar la temporada
  completa desde la NBA (por ejemplo, desde tu PC) se reemplazan por los datos completos.

## Pestañas
Cartelera (cualquier fecha) · Partido manual (con ajuste por bajas y línea) · Rendimiento (backtest frente a
referencias simples) · Ranking de equipos.

## Estructura
```
NBA_Predictor.pyw / INICIAR.bat / app.py   arranque local y en Render
render.yaml / requirements.txt / .gitignore  despliegue
nba/   config · datos · features · modelo · evaluacion · predictor · servidor
web/index.html                              interfaz
tests/                                      pruebas con datos sintéticos
datos/                                      CSV (se crea solo en local)
```
Pruebas: `python tests/test_pipeline.py`, `test_datos.py`, `test_nube.py`.

## Límites
El modelo no conoce lesiones ni alineaciones (usa el **ajuste manual**). La NBA es muy ruidosa: ningún modelo
público acierta mucho más de ~65-68 %. Son estimaciones estadísticas, no garantías.
