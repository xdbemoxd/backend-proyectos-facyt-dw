# Reportes exportables (XLSX y PDF)

Reportes formales de solo lectura para análisis de gastos, resumen de prorrateo
y hoja de vida/uso de equipos. Se generan en el backend y el frontend los
descarga a través de sus BFF routes con el Bearer de la sesión.

## Endpoints

| Método | Ruta | Formato | Permisos |
|---|---|---|---|
| `GET` | `/api/reports/expenses.xlsx` | Excel (XLSX) | Administrador, Aprobador, Coordinador |
| `GET` | `/api/reports/expenses.pdf` | PDF | Administrador, Aprobador, Coordinador |
| `GET` | `/api/reports/overhead.xlsx` | Excel (XLSX) | Administrador, Aprobador |
| `GET` | `/api/reports/overhead.pdf` | PDF | Administrador, Aprobador |
| `GET` | `/api/reports/equipment.xlsx` | Excel (XLSX) | Administrador, Aprobador, Coordinador |
| `GET` | `/api/reports/equipment.pdf` | PDF | Administrador, Aprobador, Coordinador |

- Sin token: `401`.
- Coordinador en prorrateo: `403`.
- El coordinador solo recibe filas de los proyectos donde es responsable
  (`Project.id_user`). Un `project_id` ajeno responde `403`; uno inexistente,
  `404`.
- La respuesta es binaria con `Content-Type` y
  `Content-Disposition: attachment; filename="..."`.

## Filtros

Todos los filtros son opcionales y se validan antes de consultar la base:

| Parámetro | Tipo | Uso |
|---|---|---|
| `from_date` | `YYYY-MM-DD` | Inicio del rango (inclusivo) |
| `to_date` | `YYYY-MM-DD` | Fin del rango (inclusivo) |
| `project_id` | UUID | Proyecto concreto |
| `status` | texto | Estado de solicitud (`expenses`) o de equipo (`equipment`) |

Semántica por reporte:

- **Gastos:** el rango se aplica a `created_at`; `status` acepta `draft`,
  `pending`, `approved`, `rejected` o `executed`.
- **Prorrateo:** se incluyen los períodos que se solapan con el rango. Si se
  filtra por `project_id`, se incluyen los períodos completos que tengan una
  asignación a ese proyecto (para no romper el cuadre). `status` no aplica:
  responde `422`.
- **Equipos:** se incluyen las reservas que se solapan con el rango. `status`
  es el nombre del estado de la unidad (`Disponible`, `En uso`,
  `En mantenimiento`), sin distinguir mayúsculas.

Si `from_date > to_date` responde `422`.

## Contenido

### Gastos

Código y nombre del proyecto, descripción, material, cantidad, unidad, estado,
solicitante, fecha de creación y de modificación. Totales: número de
solicitudes, cantidad total y conteo por estado (pendientes, aprobadas,
ejecutadas, rechazadas, borradores).

### Prorrateo

Período, criterio, total del período, proyecto, consumo y monto asignado.
Totales: períodos cerrados, total de períodos, suma asignada y **diferencia**.
La diferencia debe ser `0.00`; si los datos no cuadran se muestra el descuadre
en el archivo y en la hoja XLSX se agrega una nota, sin ocultarlo.

### Equipos

Equipo, modelo, número de serie, proveedor, estado, proyecto, usuario, fecha de
inicio y fin, horas y propósito de cada reserva/uso. Totales: reservas,
unidades distintas, proyectos distintos y horas reservadas.

> El archivo descargado es **una fila por reserva/uso**. La vista previa de la
> pantalla sigue resumiendo por unidad (reservas, horas y último uso), como
> antes de incorporar las descargas reales.

## Formato de los archivos

### XLSX (openpyxl)

- Una hoja por reporte: `Gastos`, `Prorrateo` o `Equipos`.
- Título, fecha/hora de generación (UTC), usuario que exporta y filtros.
- Encabezados con estilo, filtro automático y panel congelado.
- Anchos de columna, formato de moneda/cantidad (`#,##0.00`) y de fechas
  (`yyyy-mm-dd hh:mm`).
- Totales al final.

### PDF (ReportLab)

- Título, fecha/hora de generación, usuario y filtros aplicados.
- Tabla legible en A4 horizontal, con encabezado repetido en cada página,
  filas alternas y totales.
- Pie con número de página.

## Límites

- Máximo **5000** registros por reporte.
- Máximo **500** períodos de prorrateo.
- Al superarlos se responde `422` con un mensaje para refinar los filtros; no
  se genera un archivo sin control de tamaño.

## Auditoría

Cada exportación registra un evento append-only en `audit_log`:

```json
{
  "entity": "Report",
  "action": "export",
  "after_data": {
    "report": "expenses",
    "format": "xlsx",
    "filters": {"from_date": "2026-01-01", "to_date": "2026-12-31", "project_id": null, "status": null},
    "records": 5,
    "filename": "gastos-20261124-153000.xlsx"
  }
}
```

No se guardan tokens, contraseñas ni datos personales innecesarios. La IP y el
usuario se resuelven con la misma infraestructura que el resto de la auditoría.

## Ejemplos

```bash
# Gastos de un proyecto en PDF
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reports/expenses.pdf?project_id=$PROJECT_ID" \
  -o gastos.pdf

# Prorrateo de octubre en Excel
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reports/overhead.xlsx?from_date=2026-10-01&to_date=2026-10-31" \
  -o prorrateo.xlsx

# Uso de equipos en PDF
curl -sS -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/reports/equipment.pdf" \
  -o equipos.pdf
```

## Frontend

- BFF: `app/api/reports/[type]/[format]/route.ts` reenvía el binario con el
  Bearer de la sesión, conserva `Content-Type` y `Content-Disposition` y solo
  acepta los query params permitidos.
- UI: `components/reports/reports-view.tsx` mantiene la vista previa real y
  reemplaza el CSV/print por descargas XLSX/PDF. Los botones se deshabilitan si
  no hay datos, si el rol no puede ver el reporte o mientras se genera el
  archivo, y muestran el error de la API sin romper la vista.
- El reporte de prorrateo se oculta al coordinador.

## Pruebas

```bash
# Unitarias de generación (sin base)
pytest tests/test_report_generation.py -q

# Contrato HTTP (sin base)
pytest tests/test_report_api_contract.py -q

# Live de solo lectura contra la base real (requiere .env y datos semilla)
REPORTS_LIVE_TESTS=1 pytest tests/test_reports_live.py -q
```

Los tests live hacen solo `GET`. La única escritura es el evento de auditoría
`Report/export` que el propio endpoint genera (append-only).
