# Card Expense Analyzer

Backend FastAPI para analizar resúmenes de tarjeta y encontrar gastos recortables.

## Qué hace hoy

- Lee resúmenes Visa en TXT y PDFs con texto extraíble.
- Permite subir un archivo o muchos archivos a la vez.
- Parsea consumos en ARS y USD, incluso cuando el PDF viene con columnas desordenadas.
- Detecta proveedores de AI: ChatGPT, Cursor, Gomesin IT, Google AI/One.
- Detecta streaming básico: Netflix, Amazon Prime, Disney+, Spotify, YouTube.
- Devuelve categorías, candidatos recurrentes, agregado multi-mes y recomendaciones.

## Correr

```bash
cd /home/hernol/card-expense-analyzer
uv sync --dev
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

## Analizar un resumen TXT/PDF

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze \
  -F "file=@/home/hernol/3d0c3e4c-c901-479a-89a0-ebc2e2a92418.txt" | python -m json.tool
```

## Analizar muchos resúmenes juntos

```bash
curl -s -X POST http://127.0.0.1:8000/statements/analyze-batch \
  -F "files=@/ruta/a/tarjeta-visa-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-master-2026-04.pdf" \
  -F "files=@/ruta/a/tarjeta-visa-2026-05.txt" | python -m json.tool
```

## UI web

Abrí en el navegador:

```text
http://127.0.0.1:8000/
```

Ahí podés seleccionar múltiples archivos de una vez.

## Tests

```bash
uv run pytest -q
```

## Próximos pasos recomendados

1. Agregar persistencia SQLite/Postgres para comparar varios meses.
2. Agregar endpoint `GET /providers` y reglas editables por usuario.
3. Agregar import directo PDF con PyMuPDF.
4. Calcular recurrencia real usando 3+ resúmenes, no un solo mes.
5. Añadir UI simple para ranking de recortes.
