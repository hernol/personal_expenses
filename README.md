# Card Expense Analyzer

Backend + dashboard FastAPI para analizar PDFs de resúmenes de tarjeta y decidir qué gastos mantener, downgradear o cancelar.

## Qué hace hoy

- Lee PDFs de resúmenes Visa con texto extraíble.
- Permite subir un PDF o muchos PDFs a la vez.
- Guarda resúmenes y transacciones en SQLite.
- Parsea consumos en ARS y USD, incluso cuando el PDF viene con columnas desordenadas.
- Detecta proveedores de AI: ChatGPT, Cursor, Gomesin IT, Google AI/One.
- Detecta streaming básico: Netflix, Amazon Prime, Disney+, Spotify, YouTube.
- Muestra gráficos con Chart.js: gasto mensual y gasto por categoría.
- Tiene AI Cost Optimizer con uso manual: costo mensual, días usados, costo por día usado y recomendación.
- Incluye What-if simulator para marcar servicios a cancelar y calcular ahorro mensual/anual en USD y ARS.
- Genera recomendaciones proactivas y preguntas pendientes por proveedor.
- Rechaza PDFs escaneados/imagen-only con un error claro: necesitan OCR.

## Correr local

```bash
cd /home/hernol/card-expense-analyzer
uv sync --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Correr con Docker Compose

```bash
docker compose -f compose.yaml up --build
```

La base SQLite queda en el volumen `personal-expenses-data` como `/data/expenses.db`.

## Dashboard

Local:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/dashboard
```

Deploy Coolify:

```text
https://personal-expenses.178.156.165.130.sslip.io/
```

Ahí podés seleccionar múltiples PDFs, ver gráficos, cargar uso manual por proveedor y obtener recomendaciones.

## API

Analizar un PDF:

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze \
  -F "file=@/ruta/a/resumen-visa-2026-04.pdf" | python -m json.tool
```

Analizar muchos PDFs juntos:

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze-batch \
  -F "files=@/ruta/a/tarjeta-visa-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-master-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-visa-2026-05.pdf" | python -m json.tool
```

Ver analytics persistidos:

```bash
curl -s http://127.0.0.1:8000/analytics/summary | python -m json.tool
```

Cargar uso manual:

```bash
curl -s -X POST http://127.0.0.1:8000/usage/manual \
  -H 'Content-Type: application/json' \
  -d '{"provider":"Cursor","days_used_per_month":3,"importance":"low","replacement":"ChatGPT"}' | python -m json.tool
```

Simular ahorro si cancelás providers:

```bash
curl -s -X POST http://127.0.0.1:8000/what-if \
  -H 'Content-Type: application/json' \
  -d '{"cancel_providers":["Cursor","Google AI/One"],"usd_to_ars_rate":1200}' | python -m json.tool
```

## Tests

```bash
uv run pytest -q
```

## Próximos pasos recomendados

1. Agregar conectores read-only para OpenAI/Claude/Cursor cuando estén disponibles.
2. Agregar reglas editables por usuario para normalizar merchants/proveedores.
3. Agregar OCR para PDFs escaneados/imagen-only.
4. Mejorar recurrencia real por proveedor comparando 3+ meses y evitando duplicados.
5. Agregar simulador what-if: ahorro mensual/anual si cancelo X, Y, Z.
