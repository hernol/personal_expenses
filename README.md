# Card Expense Analyzer

Backend FastAPI para analizar PDFs de resúmenes de tarjeta y encontrar gastos recortables.

## Qué hace hoy

- Lee PDFs de resúmenes Visa con texto extraíble.
- Permite subir un PDF o muchos PDFs a la vez.
- Parsea consumos en ARS y USD, incluso cuando el PDF viene con columnas desordenadas.
- Detecta proveedores de AI: ChatGPT, Cursor, Gomesin IT, Google AI/One.
- Detecta streaming básico: Netflix, Amazon Prime, Disney+, Spotify, YouTube.
- Devuelve categorías, candidatos recurrentes, agregado multi-mes y recomendaciones.
- Rechaza PDFs escaneados/imagen-only con un error claro: necesitan OCR.

## Correr local

```bash
cd /home/hernol/card-expense-analyzer
uv sync --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Analizar un PDF

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze \
  -F "file=@/ruta/a/resumen-visa-2026-04.pdf" | python -m json.tool
```

## Analizar muchos PDFs juntos

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze-batch \
  -F "files=@/ruta/a/tarjeta-visa-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-master-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-visa-2026-05.pdf" | python -m json.tool
```

## UI web

Local:

```text
http://127.0.0.1:8000/
```

Deploy Coolify:

```text
https://personal-expenses.178.156.165.130.sslip.io/
```

Ahí podés seleccionar múltiples PDFs de una vez.

## Tests

```bash
uv run pytest -q
```

## Próximos pasos recomendados

1. Agregar persistencia SQLite/Postgres para comparar varios meses.
2. Agregar endpoint `GET /providers` y reglas editables por usuario.
3. Agregar OCR para PDFs escaneados/imagen-only.
4. Calcular recurrencia real usando 3+ resúmenes, no un solo mes.
5. Añadir UI simple para ranking de recortes.
