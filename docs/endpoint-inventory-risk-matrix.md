# Inventario de endpoints, puertos y matriz de riesgos

## 1. Propósito y criterio de autoridad

Este documento cierra la ruta de hardening del Poultry IoT System v2 mediante
un inventario verificable de superficies expuestas, controles de acceso,
riesgos mitigados y riesgos residuales. No sustituye una prueba de penetración
ni afirma que un riesgo está resuelto cuando el código actual no lo demuestra.

La revisión se realizó el 6 de agosto de 2026 sobre `dev` después de integrar
el commit 29. Para resolver diferencias entre documentos históricos se aplica
este orden de autoridad:

1. código y configuración versionados en el repositorio;
2. rutas efectivamente registradas por Flask;
3. reglas de Nginx y puertos publicados por Docker Compose;
4. documentación de cada fase como evidencia histórica;
5. estado del host, que debe comprobarse directamente en el Droplet.

Los números 01 al 30 identifican controles de la ruta de hardening. Los
commits funcionales o correctivos intermedios se conservan por su SHA y asunto,
pero no cambian el número formal de los controles.

## 2. Alcance y límites

El inventario cubre:

- rutas de `api_avicola/api.py` y `dashboard_avicola/dashboard.py`;
- precedencia de `location` en el virtual host Nginx de producción;
- puertos del host, loopback y red interna de Docker;
- autenticación JWT, sesión del dashboard, RBAC, MFA y llaves de servicio;
- autenticación, ACL y TLS de Mosquitto;
- hardening de contenedores, dependencias, respaldos y evidencia operativa;
- riesgos abiertos que requieren un control posterior.

No se versionan direcciones IP del Droplet, valores de `.env`, contraseñas,
tokens, secretos MFA, certificados privados, dumps o salidas completas de
`docker inspect`.

## 3. Arquitectura y fronteras de confianza

```text
Internet
  |
  +-- TCP 80/443 --> Nginx + Certbot
                       |-- /api/security-events --> dashboard:5001
                       |-- /api/*                 --> api:5000
                       |-- /lecturas              --> api:5000
                       `-- /*                      --> dashboard:5001

Host / loopback
  |-- 127.0.0.1:1883 --> Mosquitto autenticado, sin TLS
  |-- 127.0.0.1:8883 --> Mosquitto autenticado, con TLS
  |-- 127.0.0.1:5000 --> API
  `-- 127.0.0.1:5001 --> Dashboard

Red Docker avicola-network
  |-- PostgreSQL:5432, sin publicación al host
  |-- API, dashboard y mqtt_subscriber
  `-- Mosquitto:1883/8883
```

Nginx es la entrada HTTP pública. La publicación en loopback reduce la
exposición directa, pero no convierte en privadas las rutas que Nginx reenvía
a la API.

## 4. Inventario de puertos y servicios

| Puerto / transporte | Alcance esperado | Componente | Control actual | Estado y evidencia |
| --- | --- | --- | --- | --- |
| `22/TCP` | Público restringido por UFW | SSH del Droplet | Autenticación del host y firewall | **Verificar en host** con `ufw status` y `ss`; no se configura en Compose |
| `80/TCP` | Público | Nginx | Redirección canónica a HTTPS; no emite HSTS | **Mitigado**; validar 301/404 y ausencia de HSTS sobre HTTP |
| `443/TCP` | Público | Nginx + Certbot | TLS, HSTS, CSP y cabeceras defensivas | **Mitigado**; validar certificado y una sola instancia de cada cabecera |
| `1883/TCP` | Sólo `127.0.0.1` y red Docker | Mosquitto | Usuario/contraseña y ACL; transporte sin TLS | **Aceptado con monitoreo** para compatibilidad interna; clientes externos deben usar MQTTS |
| `8883/TCP` | Sólo `127.0.0.1` y red Docker | Mosquitto | TLS, certificado de broker, usuario/contraseña y ACL | **Mitigado** para clientes MQTTS autorizados |
| `5000/TCP` | Sólo `127.0.0.1` y red Docker | API Gunicorn | Nginx como proxy; contenedor no root y de sólo lectura | **Mitigado en red**; la autorización depende de cada endpoint |
| `5001/TCP` | Sólo `127.0.0.1` y red Docker | Dashboard Gunicorn | Nginx como proxy; sesión y contenedor endurecido | **Mitigado en red** |
| `5432/TCP` | Sólo red Docker | PostgreSQL | Sin `ports` hacia el host; volumen persistente | **Mitigado en red**; revisar permisos y respaldo del volumen |

Los únicos puertos que deben escuchar en interfaces públicas son los aprobados
por la política del host. El repositorio no puede demostrar UFW ni procesos
ajenos a Compose; esa evidencia se obtiene en el Droplet.

## 5. Precedencia de Nginx

| Regla pública | Destino | Consecuencia de seguridad |
| --- | --- | --- |
| `location = /api/security-events` | Dashboard `127.0.0.1:5001` | La consulta del navegador usa sesión y roles `admin`/`operador` |
| `location /api/` | API `127.0.0.1:5000` | El resto de `/api/*` aplica el control definido directamente en la API |
| `location /lecturas` | API `127.0.0.1:5000` | `POST` requiere `X-Ingest-Key`; `GET` continúa público |
| `location /` | Dashboard `127.0.0.1:5001` | Login, páginas y recursos estáticos usan la sesión del dashboard |

La coincidencia exacta de `/api/security-events` es intencional. El endpoint
más largo `/api/security-events/ingest` continúa hacia la API y exige su llave
de servicio independiente.

## 6. Modelos de autenticación y autorización

| Modelo | Uso | Evidencia del control |
| --- | --- | --- |
| Público | Healthchecks, login, MFA y varios endpoints GET | Debe limitarse a datos aprobados y respuestas sin secretos |
| Llave de ingestión | `POST /lecturas` | `X-Ingest-Key`, comparación constante y validación completa del payload |
| JWT Bearer | API de usuarios y operaciones protegidas | Firma separada, expiración y carga del usuario actual |
| Sesión del dashboard | Vistas y proxies del dashboard | Cookie `HttpOnly`, `SameSite=Lax`, `Secure` en producción y expiración |
| Proxy interno | Dashboard hacia API | `DASHBOARD_PROXY_KEY`, ID de usuario y validación de estado MFA |
| Llave de eventos | `POST /api/security-events/ingest` | Llave distinta de ingestión, comparación constante y campos permitidos |
| MQTT | Publicación y suscripción de nodos | `allow_anonymous false`, password file, ACL por usuario y tópico |

### 6.1 Matriz RBAC

| Capacidad | `visor` | `operador` | `admin` |
| --- | :---: | :---: | :---: |
| Consultar telemetría y páginas generales | Sí | Sí | Sí |
| Consultar eventos de seguridad | No | Sí | Sí |
| Actualizar umbrales y módulos | No | Sí | Sí |
| Gestionar alertas, salvo borrado total | No | Sí | Sí |
| Crear, editar o eliminar granjas y naves | No | No | Sí |
| Inicializar umbrales o borrar todas las alertas | No | No | Sí |
| Modificar su propio perfil | Sí, campos permitidos | Sí, campos permitidos | Sí, campos permitidos |
| Cambiar roles o editar otros usuarios | No | No | Sí |
| Iniciar sesión sin MFA | Sí | Sí | No; TOTP obligatorio |

El backend vuelve a comprobar el rol. Ocultar botones en el dashboard no se
considera un control de autorización.

## 7. Inventario de endpoints de la API

La columna **ingreso público** describe el resultado de las reglas Nginx, no
sólo el `bind` del contenedor.

| Ruta | Métodos | Ingreso público | Control actual | Estado / riesgo asociado |
| --- | --- | --- | --- | --- |
| `/api/health` | GET | Sí, vía `/api/` | Healthcheck mínimo y consulta `SELECT 1` | **Mitigado**; no devuelve versiones ni credenciales |
| `/lecturas` | POST | Sí | `X-Ingest-Key`, esquema, tipos, rangos, duplicados y límite `600/min` | **Mitigado**; R-04/R-17 |
| `/lecturas` | GET | Sí | Sin autenticación; última lectura por módulo y límite `120/min` | **Aceptado con monitoreo**; exposición controlada, R-02/R-17 |
| `/api/live-data` | GET | Sí | Público y límite específico `120/min` | **Aceptado con monitoreo**; R-02, R-17 mitigado |
| `/api/historical` | GET | Sí | Público, filtros, máximo 1500 puntos y límite `30/min` | **Aceptado con monitoreo**; R-02, R-17 mitigado |
| `/api/register` | POST | Sí | JWT `admin`, MFA verificado, límite `5/hour`, hash fuerte y auditoría | **Mitigado**; R-01 |
| `/api/login` | POST | Sí | Límite `10/min`, bloqueo persistente, JWT y MFA admin | **Mitigado**; R-05 |
| `/api/mfa/verify` | POST | Sí | Reto firmado y corto, TOTP, anti-replay y límite `10/min` | **Mitigado**; R-05 |
| `/api/auth/verify` | GET | Sí | Bearer obligatorio, firma, tipo, expiración y usuario | **Mitigado** |
| `/api/user/<int:user_id>` | GET | Sí | JWT o proxy interno; usuario autenticado | **Mitigado con alcance amplio**; cualquier usuario autenticado puede consultar perfiles serializados |
| `/api/user/<int:user_id>` | PUT | Sí | Propio perfil o `admin`; campos permitidos; auditoría | **Mitigado**; R-06 |
| `/api/umbrales` | GET | Sí | Sin autenticación | **Parcial**; R-02 |
| `/api/umbrales` | POST | Sí | JWT/proxy; `admin` o `operador`; orden validado | **Mitigado**; R-06 |
| `/api/umbrales/init` | POST | Sí | JWT/proxy; sólo `admin` | **Mitigado**; R-06 |
| `/api/alerts` | GET | Sí | Sin autenticación, máximo solicitado y límite `60/min` | **Aceptado con monitoreo**; R-02, R-17 mitigado |
| `/api/alerts/stats` | GET | Sí | Sin autenticación y límite `60/min` | **Aceptado con monitoreo**; R-02, R-17 mitigado |
| `/api/alerts/<int:alert_id>` | PUT | Sí | JWT/proxy; `admin` o `operador` | **Mitigado**; R-06 |
| `/api/alerts/mark-all` | PUT | Sí | JWT/proxy; `admin` o `operador` | **Mitigado**; R-06 |
| `/api/alerts/all` | DELETE | Sí | JWT/proxy; sólo `admin`; acción auditada | **Mitigado**; R-06 |
| `/api/alerts/check` | POST | Sí | JWT/proxy; `admin` o `operador` | **Mitigado**; R-06 |
| `/api/security-events` | GET | No por esta aplicación en Nginx | JWT/proxy; `admin` o `operador` | **Mitigado**; la ruta pública exacta va al dashboard |
| `/api/security-events/ingest` | POST | Sí | `X-Security-Events-Key`, campos permitidos y sanitización | **Mitigado**; R-07 |
| `/api/granjas` | GET | Sí | Sin autenticación | **Parcial**; expone nombres y ubicaciones, R-02 |
| `/api/granjas` | POST | Sí | JWT/proxy; sólo `admin`; auditoría | **Mitigado**; R-06 |
| `/api/granjas/<int:granja_id>` | PUT, DELETE | Sí | JWT/proxy; sólo `admin`; auditoría | **Mitigado**; R-06 |
| `/api/naves` | GET | Sí | Sin autenticación | **Parcial**; metadatos operativos, R-02 |
| `/api/naves` | POST | Sí | JWT/proxy; sólo `admin`; auditoría | **Mitigado**; R-06 |
| `/api/naves/<int:nave_id>` | PUT, DELETE | Sí | JWT/proxy; sólo `admin`; auditoría | **Mitigado**; R-06 |
| `/api/modulos` | GET | Sí | Sin autenticación | **Parcial**; estado y última actividad, R-02 |
| `/api/modulos/<string:codigo>` | PUT | Sí | JWT/proxy; `admin` o `operador`; auditoría | **Mitigado**; R-06 |
| `/api/parvada/<string:modulo_codigo>` | GET | Sí | Sin autenticación y límite `60/min` | **Aceptado con monitoreo**; relación módulo/nave/granja, R-02; R-17 mitigado |

## 8. Inventario del dashboard y sus proxies

| Ruta o grupo | Métodos | Control en dashboard | Exposición efectiva por Nginx |
| --- | --- | --- | --- |
| `/health` | GET | Público, respuesta mínima | Pública por `location /`; destinada a healthcheck |
| `/` | GET | Redirección de entrada | Pública |
| `/login` | GET, POST | Límite `10/min`, API de login, MFA y sesión | Pública |
| `/register` | GET, POST | Límite `5/hour`; sólo crea cuando no existe ningún usuario | Pública; riesgo de carrera sólo durante bootstrap, R-01 |
| `/logout` | POST | `login_required`, limpia sesión | Pública sólo con sesión válida |
| `/dashboard` | GET | `login_required` | Pública sólo con sesión válida |
| `/historical` | GET | `login_required` | Pública sólo con sesión válida |
| `/analysis` | GET | `login_required` | Pública sólo con sesión válida |
| `/alerts` | GET | `login_required` | Pública sólo con sesión válida |
| `/devices` | GET | `login_required` | Pública sólo con sesión válida |
| `/ml_models` | GET | `login_required` | Pública sólo con sesión válida |
| `/reports` | GET | `login_required` | Pública sólo con sesión válida |
| `/security-events` | GET | Sesión y rol `admin`/`operador` | Pública sólo con rol permitido |
| `/api/security-events` | GET | Sesión y rol `admin`/`operador`; proxy con llave interna | La coincidencia exacta de Nginx llega al dashboard |
| `/dashboard-api/user/<int:user_id>` | GET, PUT | Sesión; API aplica identidad, campos y rol | Pública por `location /`; proxy de perfil recomendado |
| `/api/user/<int:user_id>` | GET, PUT | Ruta proxy equivalente con sesión | Nginx la envía a la API, no al dashboard |
| `/api/historical` | GET | Sesión y límite `30/min` | Nginx la envía a la API pública con límite equivalente |
| `/api/live-data` | GET | Sesión y límite `120/min` | Nginx la envía a la API pública con límite equivalente |
| `/api/umbrales` | GET, POST | Sesión; escritura `admin`/`operador` | Nginx la envía a la API |
| `/api/alerts` | GET | Sesión y límite `60/min` | Nginx la envía a la API pública con límite equivalente |
| `/api/alerts/stats` | GET | Sesión y límite `60/min` | Nginx la envía a la API pública con límite equivalente |
| `/api/alerts/<int:alert_id>` | PUT | Sesión y rol `admin`/`operador` | Nginx la envía a la API protegida |
| `/api/alerts/mark-all` | PUT | Sesión y rol `admin`/`operador` | Nginx la envía a la API protegida |
| `/api/alerts/all` | DELETE | Sesión y rol `admin` | Nginx la envía a la API protegida |
| `/api/alerts/check` | POST | Sesión y rol `admin`/`operador` | Nginx la envía a la API protegida |
| `/api/granjas` | GET, POST | Sesión; escritura `admin` | Nginx la envía a la API |
| `/api/granjas/<int:granja_id>` | PUT, DELETE | Sesión y rol `admin` | Nginx la envía a la API protegida |
| `/api/naves` | GET, POST | Sesión; escritura `admin` | Nginx la envía a la API |
| `/api/naves/<int:nave_id>` | PUT, DELETE | Sesión y rol `admin` | Nginx la envía a la API protegida |
| `/api/modulos` | GET | Sesión | Nginx la envía a la API pública |
| `/api/modulos/<string:codigo>` | PUT | Sesión y rol `admin`/`operador` | Nginx la envía a la API protegida |
| `/api/parvada/<string:modulo>` | GET | Sesión y límite `60/min` | Nginx la envía a la API pública con límite equivalente |

La existencia de un proxy protegido en el dashboard no protege por sí sola una
ruta homónima que Nginx dirige primero a la API. La columna final documenta esa
diferencia para evitar atribuir una mitigación inexistente.

## 9. Inventario MQTT

| Identidad | Tópico | Permiso | Transporte esperado | Riesgo residual |
| --- | --- | --- | --- | --- |
| `mqtt_sensor_user` | `sensor/+/data` | Publicar | MQTTS 8883 en nodos autorizados | Credencial compartida o compromiso del nodo |
| `mqtt_sensor_user` | `sensor/+/status` | Publicar | MQTTS 8883; mensajes retained cuando corresponda | Suplantación de estado si se roba la credencial |
| `mqtt_sensor_user` | `sensor/+/heartbeat` | Publicar | MQTTS 8883 | Abuso de frecuencia o disponibilidad |
| `mqtt_subscriber_user` | `sensor/+/data` | Suscribirse | Red Docker; autenticado | Acceso interno a telemetría |
| `mqtt_subscriber_user` | `sensor/+/status` | Suscribirse | Red Docker; autenticado | Procesamiento de LWT/estado malicioso |
| `mqtt_subscriber_user` | `sensor/+/heartbeat` | Suscribirse | Red Docker; autenticado | Carga por mensajes excesivos |

Mosquitto tiene `allow_anonymous false`. El listener 1883 conserva
autenticación y ACL, pero no cifra el transporte; debe permanecer restringido a
loopback/red interna. El listener 8883 carga CA, certificado y llave del broker
desde material no versionado.

## 10. Método de valoración

La severidad inicial combina probabilidad e impacto antes de los controles:

- **Crítica:** permite administración no autorizada, ejecución privilegiada,
  pérdida extensa de datos o exposición de secretos principales.
- **Alta:** compromete telemetría, disponibilidad, identidad o datos
  operativos relevantes.
- **Media:** filtra metadatos, amplifica reconocimiento o degrada controles.
- **Baja:** impacto limitado, local o principalmente operativo.

El riesgo residual se valora con los controles que existen en código y
configuración. Los estados son `Mitigado`, `Parcial`, `Aceptado con monitoreo`,
`Abierto` y `Verificar en host`.

## 11. Matriz consolidada de riesgos

| ID | Riesgo / activo | Inicial | Controles comprobables | Residual | Estado y siguiente acción |
| --- | --- | :---: | --- | :---: | --- |
| R-01 | Creación no autorizada de usuarios privilegiados mediante `POST /api/register` | Crítica | JWT `admin`, MFA verificado, límite `5/hour`, validación, hash fuerte y evento privilegiado | **Baja** | **Mitigado:** conservar pruebas 401/403/201 y revisar cada alta en eventos de seguridad |
| R-02 | Exposición pública de telemetría, alertas y estructura de granjas/naves/módulos | Alta | TLS, sólo lectura, sin secretos directos, límites por endpoint y límites de resultados | **Media** | **Aceptado con monitoreo:** la compatibilidad del dashboard actual requiere lecturas públicas; no agregar campos personales o secretos y reevaluar autenticación en una versión mayor |
| R-03 | Divulgación de errores internos mediante detalles de excepciones | Media | Respuestas 500 genéricas en API y dashboard; detalles conservados sólo en logs estructurados del host | **Baja** | **Mitigado:** prueba de regresión impide reintroducir `str(e)` o el secreto de desarrollo |
| R-04 | Inyección o manipulación de telemetría | Alta | `X-Ingest-Key`, comparación constante, JSON/tipos/rangos, duplicados y auditoría | Baja | **Mitigado:** rotar la llave y vigilar rechazos |
| R-05 | Fuerza bruta y apropiación de cuentas | Crítica | Hash scrypt, migración PBKDF2, límites, bloqueo persistente, JWT corto y MFA admin | Baja | **Mitigado:** revisar eventos de login y probar recuperación MFA |
| R-06 | Broken access control en operaciones de escritura | Crítica | JWT/proxy interno, RBAC en backend, campos permitidos y auditoría privilegiada | Baja | **Mitigado:** conservar pruebas por rol y no confiar en controles visuales |
| R-07 | Lectura o falsificación de eventos de seguridad | Alta | Consulta `admin`/`operador`, proxy exacto con sesión, llave de ingestión separada y sanitización | Baja | **Mitigado:** rotar llave y controlar retención |
| R-08 | Cliente MQTT anónimo o acceso a tópicos ajenos | Alta | Acceso anónimo deshabilitado, password file y ACL separada por usuario/dirección | Baja | **Mitigado:** revisar ACL al agregar tópicos o nodos |
| R-09 | Intercepción MQTT en listener 1883 | Alta | Puerto en loopback/red Docker, autenticación y alternativa TLS 8883 | Media | **Aceptado con monitoreo:** impedir exposición pública y migrar clientes externos a 8883 |
| R-10 | Exposición directa de API, dashboard, MQTT o PostgreSQL | Crítica | Binds loopback, PostgreSQL sin puerto host y Nginx como entrada | Baja | **Verificar en host:** confirmar `ss`, UFW y que sólo 22/80/443 sean públicos |
| R-11 | Clickjacking, MIME sniffing, downgrade o inyección en navegador | Alta | HTTPS, HSTS, CSP, `X-Frame-Options`, `nosniff`, Referrer y Permissions Policy | Baja | **Mitigado:** validar cabeceras después de cada cambio Nginx |
| R-12 | Escalada o persistencia dentro de contenedores | Alta | Servicios Python no root, rootfs RO, tmpfs acotado, `cap_drop: ALL`, PID limit y no-new-privileges | Media | **Parcial:** PostgreSQL y Mosquitto conservan excepciones justificadas de sus imágenes oficiales |
| R-13 | Dependencia vulnerable o build no reproducible | Alta | `pip-audit`, lock de Poetry, requirements con versiones/hashes y `pip check` | Baja | **Mitigado con monitoreo:** ejecutar workflows manuales cuando un cambio sólo documental no los active |
| R-14 | Pérdida o corrupción de PostgreSQL | Crítica | Backup diario, flujo 3-2-1 cifrado, restore aislado, RPO 24 h 15 min y RTO 4 h | Media | **Mitigado con ejercicio periódico:** cronometrar recuperación completa y revisar retención |
| R-15 | Exposición de secretos por Git, evidencias o inspección | Crítica | `.gitignore`, ejemplos sin valores, material TLS fuera del repo y reglas de redacción | Media | **Aceptado con monitoreo:** variables siguen presentes en el entorno de contenedores; limitar acceso al host |
| R-16 | Caída silenciosa o arranque desordenado | Alta | Healthchecks, dependencias saludables, `restart: unless-stopped` e indicador MQTT | Baja | **Mitigado:** alertar sobre unhealthy/restarts y probar reconexión MQTT |
| R-17 | Agotamiento por polling o consultas públicas sin límite específico | Alta | Límites explícitos: ingestión `600/min`, live/última lectura `120/min`, alertas/parvada `60/min` e histórico `30/min`; máximo 1500 puntos históricos | Baja | **Mitigado:** ajustar límites sólo con métricas y conservar la prueba que verifica `429` |
| R-18 | Configuración insegura al ejecutar el dashboard fuera de Compose | Alta | API y dashboard fallan al iniciar cuando `SECRET_KEY` falta o tiene menos de 32 caracteres | Baja | **Mitigado:** generar una llave distinta por entorno y nunca recuperarla desde valores por defecto |

### 11.1 Cierre residual posterior al commit 30

El commit de cierre sin numeración corrige R-01, R-03, R-17 y R-18 mediante
autorización administrativa, redacción de errores, límites específicos y
configuración fail-closed. R-02 queda aceptado con monitoreo por compatibilidad
del dashboard actual: únicamente se admiten datos operativos de lectura, bajo
TLS y límites explícitos. Una futura versión que cambie el contrato del
dashboard debe reevaluar la autenticación de estas consultas.

## 12. Trazabilidad de la ruta de hardening

| Fase | Controles formales | Resultado versionado |
| --- | --- | --- |
| 1 | 01-07 | Configuración Flask, loopback/Nginx, validación, ingest key, JWT, RBAC e inventario inicial |
| 2 | 08-12 | Autenticación MQTT, ACL, MQTTS, validación del subscriber y heartbeat/LWT |
| 3 | 13-16 | Logging estructurado, eventos persistentes, bloqueo de login y auditoría privilegiada |
| 4 | 17-20 | Hashes, roles, campos MFA y MFA obligatorio para `admin` |
| 5 | 21-24 | Backup PostgreSQL, 3-2-1 cifrado, restore y retención/RTO/RPO |
| 6 | 25-30 + cierre | Auditoría de dependencias, pinning, contenedores, Nginx, guía de evidencia, inventario final y corrección de riesgos residuales |

La trazabilidad completa se obtiene con `git log --oneline --decorate`. Los
merges explícitos a `dev` conservan el límite entre controles.

## 13. Validación reproducible

### 13.1 Repositorio local en WSL

```bash
cd /mnt/e/py/poultry-iot-system-v2
git branch --show-current
git status --short
git diff --check

rg -n "@app\.(route|get|post|put|patch|delete)" \
  api_avicola/api.py dashboard_avicola/dashboard.py

docker compose config --quiet
bash scripts/validate_nginx_config.sh
```

### 13.2 Servicios locales

```bash
docker compose up -d
docker compose ps

curl -fsS http://127.0.0.1:5000/api/health
curl -fsS http://127.0.0.1:5001/health

curl -sS -o /dev/null -w 'API sin token: %{http_code}\n' \
  http://127.0.0.1:5000/api/user/1

curl -sS -o /dev/null -w 'Ingesta sin llave: %{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://127.0.0.1:5000/lecturas
```

Los dos últimos resultados esperados son `401`. No se debe probar el registro
con un rol privilegiado en producción; R-01 se confirma mediante revisión de
código hasta que exista su corrección.

### 13.3 Producción sin revelar secretos

```bash
curl -fsSI https://poultry-system.duckdns.org/
curl -fsS https://poultry-system.duckdns.org/api/health

sudo nginx -t
sudo ss -lntp
sudo ufw status numbered
docker compose ps
```

La evidencia puede mostrar puertos, códigos HTTP, estado de contenedores y
cabeceras. Debe ocultar IP pública, usuarios SSH, rutas personales, tokens,
contraseñas, variables de entorno y material TLS privado.

## 14. Criterios de aceptación del commit 30

- todas las rutas Flask aparecen en el inventario o en un grupo explícito;
- la precedencia de Nginx coincide con el archivo versionado;
- los puertos publicados coinciden con Docker Compose;
- los roles de escritura coinciden con los decoradores y validaciones del
  backend;
- los tópicos MQTT coinciden con la ACL;
- cada riesgo conserva responsable operativo o siguiente acción;
- los riesgos abiertos no se presentan como mitigados;
- los enlaces relativos y `git diff --check` pasan sin errores;
- no se incorporan secretos ni capturas sin redactar.

## 15. Mantenimiento

Actualizar este documento cuando ocurra cualquiera de estos cambios:

- nueva ruta, método HTTP o `location` de Nginx;
- cambio de rol, autenticación, sesión, llave de servicio o CORS;
- nuevo puerto, servicio Compose, listener o tópico MQTT;
- nueva dependencia, imagen base o excepción de contenedor;
- mitigación, aceptación o cambio de severidad de un riesgo;
- cambio de RTO, RPO, retención o procedimiento de restauración.

El propietario técnico debe revisar el inventario al cerrar cada PR de
seguridad y durante la revisión periódica del hardening.

## 16. Referencias versionadas

- [API Flask](../api_avicola/api.py)
- [Dashboard Flask](../dashboard_avicola/dashboard.py)
- [Docker Compose](../docker-compose.yml)
- [Virtual host Nginx](../deploy/nginx/poultry-api.conf)
- [Cabeceras Nginx](../deploy/nginx/snippets/poultry-security-headers.conf)
- [Configuración Mosquitto](../mosquitto/config/mosquitto.conf)
- [ACL Mosquitto](../mosquitto/config/aclfile)
- [Auditoría de dependencias](dependency-audit.md)
- [Pinning de dependencias](dependency-pinning.md)
- [Hardening de contenedores](container-hardening.md)
- [Cabeceras y HSTS](nginx-security-headers.md)
- [Política de backup y RTO/RPO](backup-retention-rto-rpo.md)
- [Guía de despliegue y evidencia](deployment-security-evidence-guide.md)
