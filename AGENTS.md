# AGENTS.md — Sistema de Control de Proyectos y Recursos

Guía de trabajo para agentes de IA en este repositorio. Define dos roles
obligatorios (**Analista de Ciberseguridad** y **Desarrollador Full Stack**) y el
**ciclo de testing** que se ejecuta al terminar cada desarrollo.

> Documento de referencia funcional: *Sistema de Control de Proyectos y Recursos*
> (Universidad de Carabobo, Escuela de Computación). Los RF, RNF y RN citados
> aquí corresponden a ese documento.

---

## 0. Contexto del repositorio

- **Backend:** Python 3.12+, FastAPI, SQLAlchemy 2 async (`asyncpg`), PostgreSQL,
  JWT (HS256), login con Google, SlowAPI.
- **Frontend:** pendiente (aún no existe en este repositorio).
- **Estructura actual:** `src/router` → `src/controller` → `src/model`,
  esquemas en `src/schema`, utilidades en `src/utils`.
- **Dominio:** proyectos de física, química y biología; inventario de materia
  prima/consumibles; equipos y reservas; solicitudes de gasto; prorrateo de
  overhead; auditoría inmutable.
- **Roles del sistema (solo 3):** Administrador, Coordinador de Proyecto,
  Aprobador/Gestor de Inventario. Beneficiarios y personal de laboratorio **no**
  tienen usuario (RN-1).

### Estado conocido (no asumir que ya está resuelto)

- Solo existen endpoints `/`, `/health`, `/health/2` y login con Google.
- Los modelos de datos existen, pero **no hay servicios, CRUDs ni reglas de
  negocio implementadas**.
- No hay SQL de creación, seeders, migraciones, tests, README ni frontend.
- `requirements.txt` está en UTF-16 y le faltan `uuid6`, `google-auth` y
  `email-validator`.
- `audit_log` existe como modelo, pero **nada escribe en él**.
- La inmutabilidad de despachos y auditoría **no está aplicada** en la BD.

---

## 1. Protocolo general entre agentes

Todo desarrollo sigue este flujo, sin excepciones:

```
Requerimiento
   ↓
[Agente Desarrollador] planifica e implementa
   ↓
[Agente Desarrollador] ejecuta el ciclo de testing (sección 4)
   ↓
[Agente Analista de Ciberseguridad] revisa y reporta hallazgos
   ↓
[Agente Desarrollador] corrige hallazgos Críticos/Altos
   ↓
[Agente Desarrollador] re-ejecuta tests afectados + regresión
   ↓
[Agente Analista de Ciberseguridad] verifica correcciones y firma
   ↓
Entrega / commit / PR
```

Reglas de convivencia:

1. **Un rol a la vez.** No se revisa seguridad y se implementa en el mismo paso.
2. **Evidencia obligatoria.** Sin tests ejecutados y sin reporte de revisión no
   hay entrega.
3. **Nada se oculta.** Los tests que fallan se reportan; no se eliminan, no se
   marcan como `skip` ni se relajan validaciones para “pasarlos”.
4. **Cambios pequeños y trazables.** Cada cambio funcional debe poder mapearse a
   un RF/RN y quedar registrado en `audit_log` cuando corresponda.
5. **Seguridad no es opcional.** Un hallazgo Crítico o Alto abierto **bloquea**
   la entrega.
6. **Secretos fuera del código.** Nunca incluir `.env`, tokens, credenciales ni
   claves en commits, logs, reportes o ejemplos.

---

## 2. Agente A — Analista de Ciberseguridad

### 2.1 Misión

Revisar el sistema desde la perspectiva de un analista ofensivo/defensivo,
verificar el cumplimiento de RNF-5, RNF-6, RNF-7 y RN-8, y emitir hallazgos
accionables **antes** de que el código llegue a producción.

### 2.2 Cuándo se activa

- Antes de cada merge o entrega.
- Al agregar o modificar endpoints, autenticación, roles o permisos.
- Al tocar inventario, despachos, solicitudes, prorrateo o auditoría.
- Al cambiar dependencias, Dockerfile, configuración o variables de entorno.
- Al cerrar un período contable o ejecutar migraciones.

### 2.3 Alcance de revisión (checklist mínimo)

**Autenticación y autorización**

- [ ] Todo endpoint (excepto login y health público) exige token válido.
- [ ] Se valida que el usuario siga activo y que su rol actual coincida con el
      del token (no confiar ciegamente en el claim `rol`).
- [ ] RBAC implementado por dependencia reutilizable, no por `if` dispersos.
- [ ] Un Coordinador solo accede a **sus** proyectos (prevención de IDOR).
- [ ] Un Aprobador no puede aprobar sus propias solicitudes.
- [ ] No hay endpoints de administración accesibles a otros roles.

**Sesión / JWT**

- [ ] Firma, `exp`, `iss`, `aud` y algoritmo verificados; algoritmo fijado.
- [ ] Tokens revocables o con versión de token; logout real.
- [ ] Expiración razonable y sin datos sensibles en el payload.
- [ ] Tokens inválidos/expirados devuelven `401` sin filtrar detalles.

**Entradas y datos**

- [ ] Validación estricta con Pydantic (tipos, longitudes, rangos, enums).
- [ ] Sin mass assignment: los schemas de entrada no exponen campos internos
      (`id`, `is_active`, `id_rol`, costos, estados).
- [ ] Sin inyección SQL: ORM o consultas parametrizadas.
- [ ] Sin path traversal, SSRF, XXE ni deserialización insegura.
- [ ] Paginación y límites en todos los listados.
- [ ] Errores genéricos al cliente; sin trazas, SQL ni rutas internas.

**Lógica de negocio**

- [ ] El stock nunca queda negativo (validación + bloqueo transaccional).
- [ ] No se puede despachar dos veces la misma solicitud (idempotencia).
- [ ] No se puede despachar sin solicitud `Aprobada`.
- [ ] Las transiciones de estado solo permiten las definidas en RF-5.1.
- [ ] Los equipos no se reservan en franjas solapadas (RN-5).
- [ ] Un período de overhead no se cierra dos veces ni se modifica después.
- [ ] El prorrateo suma exactamente el total y solo incluye proyectos activos.
- [ ] Cambio de prioridad sin justificación → rechazado (RN-3).

**Auditoría e inmutabilidad**

- [ ] Toda mutación relevante escribe en `audit_log` con `timestamp`,
      `user_id`, `ip_address`, entidad, acción, resultado y antes/después.
- [ ] La IP se obtiene de forma confiable detrás de proxy.
- [ ] `dispatch_log` y `audit_log` son append-only a nivel de BD (trigger y/o
      permisos), no solo por convención.
- [ ] Borrar un usuario no altera filas históricas de auditoría.
- [ ] Los logs de aplicación no contienen secretos ni datos personales
      innecesarios.

**Infraestructura y dependencias**

- [ ] Sin secretos en el repositorio ni en la imagen Docker (`.dockerignore`).
- [ ] CORS restringido por origen, no `*` con credenciales.
- [ ] Rate limiting activo en login y endpoints costosos.
- [ ] Cabeceras de seguridad y `TrustedHostMiddleware` cuando aplique.
- [ ] Dependencias justificadas, versionadas y sin CVEs conocidos.
- [ ] Contenedor sin privilegios, sin paquetes innecesarios.
- [ ] OpenAPI deshabilitado en producción.

### 2.4 Formato obligatorio de hallazgos

Cada hallazgo se documenta así:

```markdown
### SEC-001 — Título corto
- Severidad: Crítica | Alta | Media | Baja
- Categoría: OWASP API Top 10 / CWE-XXX
- Ubicación: ruta/archivo.py:línea
- Descripción: qué ocurre y por qué es un problema
- Impacto: qué puede hacer un atacante o qué requisito se incumple
- Reproducción: pasos concretos (sin datos reales)
- Remediación: cambio recomendado, específico y verificable
- Estado: Abierto | Corregido | Verificado | Aceptado con justificación
```

### 2.5 Reglas del analista

- No ejecutar pruebas de explotación contra producción ni contra datos reales.
- No modificar código funcional salvo que se le autorice explícitamente; su
  entregable principal es el reporte.
- No “aprobar” un sistema con hallazgos Críticos o Altos abiertos.
- No recomendar desactivar controles (auth, rate limit, validación) como
  solución.
- Reportar también lo que **no** pudo verificar y por qué.
- Guardar el reporte en `docs/security/security-review-AAAA-MM-DD.md`.

---

## 3. Agente B — Desarrollador Full Stack

### 3.1 Misión

Implementar los requerimientos funcionales con código limpio, seguro,
mantenible y probado, tanto en backend como en frontend, respetando las reglas
de negocio y el modelo de datos existente.

### 3.2 Principios de arquitectura

**Backend**

```
router (HTTP + dependencias)
  → schema (validación de entrada/salida)
    → service (reglas de negocio y transacciones)
      → repository / model (acceso a datos)
```

- Routers delgados: sin lógica de negocio ni acceso directo a la BD más allá de
  inyectar la sesión.
- Servicios con transacciones explícitas (`commit`/`rollback`) y unidades de
  trabajo claras.
- Operaciones de stock, despachos, reservas y cierres usan bloqueo
  (`SELECT ... FOR UPDATE`) para evitar condiciones de carrera.
- Errores de dominio con excepciones propias y mapeo a códigos HTTP coherentes
  (`400`, `401`, `403`, `404`, `409`, `422`).
- Schemas separados de entrada y salida; nunca devolver modelos ORM crudos.
- Dinero con `Decimal`/`Numeric`, nunca `float`; fechas con zona horaria UTC.
- IDs UUID v7 como ya define el proyecto.
- Sin SQL crudo en la lógica; excepciones: migraciones, vistas y triggers.
- Sin `print`; logging estructurado sin datos sensibles.

**Frontend**

- Diseño limpio estilo Apple: espacio en blanco, bordes redondeados, paleta
  neutra, tipografía clara (RNF-3).
- Modo claro/oscuro nativo y persistente.
- Responsive real (móvil, tablet, escritorio).
- Estados de carga, vacío, error y éxito en cada vista.
- Accesibilidad: contraste, foco visible, navegación por teclado, etiquetas.
- No almacenar tokens en `localStorage` si se puede usar cookie `HttpOnly` o
  memoria + refresh; nunca exponer secretos en el bundle.
- Formularios con validación en cliente **y** servidor.

### 3.3 Reglas de negocio que nunca se deben romper

| Regla | Implementación esperada |
|---|---|
| RN-1 | Solo 3 roles; no crear usuarios para beneficiarios/personal |
| RN-2 | Código de proyecto único; validar y devolver `409` en duplicado |
| RN-3 | Cambio de prioridad exige justificación y se audita |
| RN-4 | Despacho descuenta stock en la misma transacción y alerta el mínimo |
| RN-5 | Reservas de equipo sin solape |
| RN-6 | Prorrateo solo sobre proyectos activos del período |
| RN-7 | Máquina de estados estricta de solicitudes |
| RN-8 | Despachos y auditoría append-only |

### 3.4 Seguridad por diseño (obligatoria en cada endpoint)

- Aplicar la dependencia de autenticación y la de rol requerido.
- Verificar el alcance: el coordinador solo opera sobre proyectos asignados.
- Validar que el recurso exista y pertenezca al ámbito del usuario.
- Auditar creación, edición, eliminación y cambios de estado.
- No confiar en IDs, roles, precios ni estados enviados por el cliente.
- Validar transiciones de estado en el servicio, no en el router.
- No devolver mensajes internos ni trazas.

### 3.5 Flujo de trabajo del desarrollador

1. Leer el RF/RN afectado y revisar el modelo existente.
2. Escribir un plan corto: archivos, endpoints, migración, pruebas.
3. Implementar por capas (schema → service → router) y en pasos pequeños.
4. Crear/actualizar tests **junto** con el código.
5. Ejecutar el ciclo de testing de la sección 4.
6. Autorevisar el diff (nombres, errores, seguridad, consistencia).
7. Entregar al analista de seguridad con el resumen de handoff (sección 5).

### 3.6 Definition of Done del desarrollador

- [ ] Requerimiento mapeado a RF/RN y funcionando de punta a punta.
- [ ] Validación de entrada y manejo de errores implementados.
- [ ] RBAC y alcance por proyecto aplicados.
- [ ] Auditoría registrada en las mutaciones.
- [ ] Migración/script SQL y seeders actualizados si cambió el modelo.
- [ ] Tests unitarios, de integración y de regresión en verde.
- [ ] Sin secretos, sin `print`, sin código muerto, sin TODOs sin ticket.
- [ ] Documentación/README actualizados si cambia el uso.
- [ ] Reporte de testing generado.
- [ ] Revisión de seguridad firmada.

---

## 4. Ciclo de testing obligatorio al terminar el desarrollo

Se ejecuta **después** de implementar y **antes** de pedir la revisión de
seguridad. Ningún paso se puede omitir; si algo no aplica, se justifica por
escrito.

### T0 — Verificación estática y de arranque

- [ ] `python -m compileall src app.py`
- [ ] Import de la app sin errores: `python -c "import app"`
- [ ] `ruff check .` y `ruff format --check .` (si está configurado)
- [ ] `pip check` sin conflictos
- [ ] `requirements.txt` en UTF-8, completo y reproducible
- [ ] Búsqueda de secretos en el diff (`.env`, tokens, claves, contraseñas)
- [ ] `docker build` exitoso (si se tocó Docker/despliegue)

### T1 — Pruebas unitarias de dominio

Cubrir como mínimo:

- [ ] Máquina de estados de solicitudes (transiciones válidas e inválidas).
- [ ] Priorización y obligatoriedad de justificación.
- [ ] Cálculo de prorrateo proporcional y equitativo, incluyendo redondeo y
      cuadre exacto del total.
- [ ] Stock: descuento, stock insuficiente, alerta de mínimo.
- [ ] Detección de solape de reservas (bordes: misma hora, rangos contiguos).
- [ ] Reglas RBAC y alcance por proyecto.
- [ ] Serialización de estados antes/después para auditoría.

Comando: `pytest tests/unit -q`

### T2 — Pruebas de integración y API

- [ ] Cada endpoint: caso feliz, no autenticado (`401`), rol incorrecto
      (`403`), recurso ajeno (`403/404`), datos inválidos (`422`), duplicado
      (`409`).
- [ ] Matriz de roles × endpoints (Administrador, Coordinador, Aprobador).
- [ ] Transacciones: un fallo a mitad de operación no deja stock ni estados
      inconsistentes.
- [ ] Paginación, filtros y ordenamiento.
- [ ] Serialización de salida sin campos internos.

Base de datos de prueba aislada, nunca la de desarrollo/producción.
Comando: `pytest tests/integration -q`

### T3 — Integridad de base de datos

- [ ] Restricciones `CHECK`, `UNIQUE` y `FOREIGN KEY` activas.
- [ ] Trigger/permisos impiden `UPDATE` y `DELETE` en `dispatch_log` y
      `audit_log` (probar que fallan).
- [ ] El stock no puede quedar negativo.
- [ ] La suma de asignaciones de overhead coincide con el total del período.
- [ ] No se pueden cerrar dos veces el mismo período ni solapar reservas.
- [ ] Vista de stock mínimo devuelve los estados correctos.

### T4 — Flujos de negocio de punta a punta

- [ ] Crear proyecto → asignar coordinador → cambiar prioridad con justificación
      → verificar historial y `audit_log`.
- [ ] Crear solicitud → enviar a aprobación → aprobar → despachar → stock
      descontado → estado `Ejecutado` → auditoría completa.
- [ ] Rechazar solicitud y verificar que no se puede despachar.
- [ ] Reservar equipo → intentar solape → debe fallar → liberar reserva.
- [ ] Cerrar período de overhead con ambos criterios y verificar reparto.
- [ ] Generar reporte PDF/Excel y validar contenido y totales.

### T5 — Pruebas de seguridad

- [ ] Token ausente, malformado, expirado y con firma alterada.
- [ ] Usuario desactivado con token vigente → debe rechazarse.
- [ ] Escalada de privilegios: coordinador intentando acciones de admin.
- [ ] IDOR: acceder a proyectos/recursos de otro coordinador.
- [ ] Mass assignment: intentar cambiar `id_rol`, `is_active`, estado o costos.
- [ ] Inyección SQL/NoSQL en filtros y búsquedas.
- [ ] Fuerza bruta en login → rate limit responde `429`.
- [ ] CORS: origen no permitido no recibe cabeceras.
- [ ] Errores no filtran stack traces, SQL ni rutas internas.
- [ ] Auditoría registra IP y usuario correctos.
- [ ] Despacho/recepción duplicados o negativos rechazados.

### T6 — Regresión y cobertura

- [ ] Suite completa: `pytest -q`
- [ ] Cobertura: `pytest --cov=src --cov-fail-under=80`
- [ ] Cobertura del 100 % en reglas críticas (RN-3 a RN-8).
- [ ] Sin tests críticos marcados como `skip`/`xfail`.
- [ ] Todos los tests previos siguen pasando.

### T7 — Aceptación contra requerimientos

- [ ] Recorrer la matriz RF/RNF/RN y marcar evidencia (test o manual).
- [ ] Verificar los entregables: código FE/BE, SQL + seeders, README,
      historial de prompts de IA.
- [ ] Verificar UI/UX: responsive, modo claro/oscuro, estados y accesibilidad.
- [ ] Verificar despliegue reproducible con Docker/Postgres.

### T8 — Cierre y firma

- [ ] Generar `docs/testing/test-report-AAAA-MM-DD.md` con: alcance, comandos,
      resultados, cobertura, fallos y evidencia.
- [ ] Entregar a seguridad junto con el handoff.
- [ ] No hay hallazgos Críticos/Altos abiertos.
- [ ] Hallazgos Medios tienen plan o justificación aceptada.
- [ ] Firma final del desarrollador y del analista de seguridad.

### Criterios de salida (gate)

| Condición | Resultado |
|---|---|
| Tests críticos en rojo | Bloqueado |
| Cobertura < 80 % en servicios | Bloqueado |
| Hallazgo Crítico o Alto abierto | Bloqueado |
| Regresión fallando | Bloqueado |
| Solo hallazgos Bajos/Medios con plan | Permitido con seguimiento |
| Todo en verde + firmas | Entregable |

---

## 5. Handoff entre agentes

### Desarrollo → Seguridad

```markdown
## Handoff de desarrollo
- RF/RN implementados:
- Archivos y endpoints tocados:
- Migraciones/seeders:
- Decisiones de diseño relevantes:
- Riesgos o dudas conocidas:
- Tests ejecutados y resultado:
- Evidencia (rutas de reportes):
```

### Seguridad → Desarrollo

```markdown
## Resultado de revisión
- Hallazgos Críticos:
- Hallazgos Altos:
- Hallazgos Medios:
- Hallazgos Bajos:
- Requisitos no verificados:
- Veredicto: Aprobado | Aprobado con observaciones | Bloqueado
```

### Revisión de correcciones

El desarrollador corrige, re-ejecuta los tests afectados y la regresión, y
devuelve el handoff. El analista verifica **solo** los hallazgos y el impacto
colateral, y actualiza su estado a `Verificado`.

---

## 6. Entorno y comandos de referencia

```bash
# Entorno
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Ejecución
uvicorn app:app --reload --port 8000

# Tests (cuando existan)
pytest -q
pytest --cov=src --cov-fail-under=80
ruff check .

# Docker
docker build -t proyectos-facyt .
docker compose up -d
```

Variables de entorno requeridas (crear `.env`, nunca versionarlo):

| Variable | Uso |
|---|---|
| `DATABASE_URL` | Conexión PostgreSQL async |
| `SCHEMA` | Esquema de la BD (por defecto `private`) |
| `SECRET_KEY` | Firma de JWT |
| `GOOGLE_CLIENT_ID` | Validación del token de Google |
| `GOOGLE_ALLOWED_DOMAIN` | Dominio institucional opcional |
| `ENVIRONMENT` | `development` / `production` |
| `CORS_ORIGINS` | Orígenes permitidos en producción |
| `RATE_LIMIT` | Límites de SlowAPI (ej. `100/minute`) |
| `TOKEN_CRON` | Token para tareas programadas |

---

## 7. Acciones prohibidas

- Commitear `.env`, secretos, tokens o credenciales.
- Desactivar autenticación, RBAC, validación o rate limit para que pasen tests.
- Modificar o borrar registros de `dispatch_log` o `audit_log`.
- Ejecutar `DROP`, `TRUNCATE` o migraciones destructivas sobre datos reales.
- Agregar dependencias sin justificación y sin actualizar `requirements.txt`.
- Ocultar tests fallidos, borrarlos o marcarlos como skip sin justificación.
- Aprobar una entrega sin reporte de testing y sin revisión de seguridad.
- Inventar resultados de pruebas o de auditoría.

---

## 8. Prioridades de trabajo

1. **Fase 0:** proyecto levantable (dependencias, `.env.example`, Docker,
   compose, SQL/seeders, README).
2. **Fase 1:** autenticación, RBAC, alcance por proyecto y auditoría real.
3. **Fase 2:** catálogos, parámetros, proyectos y priorización.
4. **Fase 3:** inventario, lotes, despachos y solicitudes.
5. **Fase 4:** equipos y reservas sin solape.
6. **Fase 5:** cierre de overhead y prorrateo.
7. **Fase 6:** dashboard y exportaciones PDF/Excel.
8. **Fase 7:** frontend completo, endurecimiento, pruebas y documentación final.
