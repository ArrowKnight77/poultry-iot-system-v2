# Cabeceras de seguridad y HSTS en Nginx

Este documento describe los controles del commit 28 de la fase 6. La política
convierte a Nginx en la autoridad de las cabeceras HTTP públicas sin retirar
las defensas locales de Flask.

## Alcance

La configuración versionada conserva las rutas de producción actuales:

- `/api/` y `/lecturas` hacia la API en `127.0.0.1:5000`;
- `/api/security-events` hacia el proxy autenticado del dashboard en
  `127.0.0.1:5001`;
- `/` hacia el dashboard en `127.0.0.1:5001`;
- HTTPS y certificados administrados por Certbot;
- redirección permanente de HTTP a HTTPS.

Los certificados, las llaves privadas y los archivos generados por Certbot no
forman parte del repositorio.

La coincidencia exacta `/api/security-events` preserva la sesión web y MFA del
dashboard. La ruta más específica `/api/security-events/ingest` no coincide con
ella y continúa por `/api/` hacia la API, donde conserva la autenticación
técnica independiente del subscriber MQTT.

## Problemas corregidos

Antes del cambio, Nginx y Flask enviaban simultáneamente
`X-Content-Type-Options` y `X-Frame-Options`. El cliente recibía valores
duplicados. Además, no existía HSTS, se exponía la versión de Nginx y se
conservaba el valor antiguo `X-XSS-Protection: 1; mode=block`.

El snippet oculta las variantes de los servicios upstream con
`proxy_hide_header` y genera una sola copia de cada cabecera en el límite
público. Todas usan `always` para cubrir respuestas exitosas, redirecciones y
errores.

## Política aplicada

| Control | Valor público | Propósito |
| --- | --- | --- |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | Obliga a reutilizar HTTPS durante un año |
| `X-Content-Type-Options` | `nosniff` | Evita inferencia de tipos MIME |
| `X-Frame-Options` | `SAMEORIGIN` | Limita el uso de frames al mismo origen |
| `X-XSS-Protection` | `0` | Desactiva filtros XSS heredados con comportamiento inseguro |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | Reduce información enviada a otros orígenes |
| `Permissions-Policy` | cámara, micrófono, ubicación, pagos y USB deshabilitados | Reduce acceso innecesario a funciones del navegador |
| `Content-Security-Policy` | política compatible inicial | Bloquea plugins, bases, formularios y frames no autorizados |
| `server_tokens` | `off` | Elimina la versión de Nginx del encabezado `Server` |

HSTS se incluye únicamente en el servidor TLS. El bloque de puerto 80 redirige
a HTTPS, pero no publica HSTS porque los navegadores sólo deben aceptarlo sobre
una conexión segura.

## CSP compatible

La política inicial es:

```text
object-src 'none'; base-uri 'self'; frame-ancestors 'self'; form-action 'self'; upgrade-insecure-requests
```

No se restringen todavía `script-src`, `style-src` ni `img-src`. El dashboard
actual utiliza scripts y estilos inline, jsDelivr y URLs configurables para
imágenes de perfil. Endurecer esas fuentes sin migrar primero a nonces, hashes
o archivos locales rompería funciones legítimas.

La migración a una CSP estricta es trabajo independiente. No se debe agregar
`'unsafe-eval'` ni una lista amplia de orígenes para simular una política
estricta.

## Validación local en WSL

Desde `/mnt/e/py/poultry-iot-system-v2`:

```bash
python3.11 -m unittest local_tests.test_nginx_security_headers

NGINX_TEST_IMAGE=nginx:1.24.0-alpine@sha256:77e5d4a6ad906c5d3793764085706577fa705b1dc6f244ea0241c4b5e2155385 \
  bash scripts/validate_nginx_config.sh
```

El segundo comando:

1. adapta temporalmente las referencias externas de Certbot;
2. ejecuta `nginx -t` sobre el virtual host versionado;
3. levanta un Nginx aislado;
4. verifica respuestas 200, 302 y 404;
5. exige exactamente una copia de cada cabecera y oculta la versión.

Los archivos temporales y el contenedor se eliminan al finalizar.

Ejecutar también la regresión del proyecto con la imagen local de la API:

```bash
docker run --rm \
  --entrypoint python \
  -v "$PWD:/src:ro" \
  -w /src \
  poultry-iot-system-v2-api:latest \
  -m unittest discover -s local_tests -p 'test_*.py'
```

Este commit no modifica Docker Compose ni las imágenes de aplicación.

## Validación manual del dashboard

Antes de publicar, iniciar los servicios locales y ejecutar el preview HTTPS:

```bash
docker compose up -d
bash scripts/run_nginx_security_preview.sh
```

El script genera un certificado autofirmado efímero y publica
`https://localhost:8443/login`. La advertencia del navegador es esperada. HSTS
se elimina únicamente de este preview para no almacenar una política de un año
en `localhost`; la sintaxis y el valor HSTS reales se comprueban por separado
en `validate_nginx_config.sh`.

Mientras el script permanece abierto:

1. abrir `/login` y completar el inicio de sesión y MFA;
2. navegar por dashboard, histórico, análisis, reportes y dispositivos;
3. comprobar que Bootstrap, iconos, Flatpickr y las gráficas carguen;
4. comprobar la imagen de perfil configurada;
5. revisar la consola del navegador y confirmar que no existan bloqueos CSP;
6. confirmar que las llamadas de API sigan respondiendo.

Presionar `Ctrl+C` en la terminal del preview para eliminar el contenedor, el
certificado y la configuración temporales.

## Despliegue en el Droplet

El despliegue se realiza únicamente después de incorporar el PR a `dev`.
Primero registrar el SHA desplegado y crear una copia recuperable de la
configuración activa:

```bash
cd /home/admnyc/py/poultry-iot-system-v2
git branch --show-current
git rev-parse --short HEAD

nginx_stamp="$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o root -g root -m 700 /var/backups/poultry-nginx
sudo cp --preserve=mode,ownership,timestamps \
  /etc/nginx/sites-available/poultry-api \
  "/var/backups/poultry-nginx/poultry-api.pre-commit28.$nginx_stamp"
```

Instalar primero el snippet y después el virtual host:

```bash
sudo install -d -o root -g root -m 755 /etc/nginx/snippets
sudo install -o root -g root -m 644 \
  deploy/nginx/snippets/poultry-security-headers.conf \
  /etc/nginx/snippets/poultry-security-headers.conf
sudo install -o root -g root -m 644 \
  deploy/nginx/poultry-api.conf \
  /etc/nginx/sites-available/poultry-api

sudo nginx -t
sudo systemctl reload nginx
sudo systemctl is-active nginx
```

No recargar Nginx si `nginx -t` falla. El cambio no requiere reconstruir ni
recrear contenedores y no modifica PostgreSQL.

## Validación pública

Después de recargar Nginx:

```bash
bash scripts/check_nginx_security_headers.sh \
  https://poultry-system.duckdns.org
```

El script valida `/`, `/login`, `/api/health`, `/health` y una ruta 404. También
comprueba la redirección HTTP, la ausencia de HSTS sobre HTTP y que `Server`
muestre `nginx` sin número de versión.

Revisar el servicio y errores recientes sin imprimir configuraciones o
variables de entorno:

```bash
sudo nginx -t
sudo systemctl is-active nginx
sudo journalctl -u nginx --since='10 minutes ago' --no-pager
docker compose ps
```

## Rollback

Si falla la sintaxis, Nginx no debe recargarse. Si el problema aparece después
de la recarga, restaurar la copia exacta registrada durante el despliegue:

```bash
sudo install -o root -g root -m 644 \
  /var/backups/poultry-nginx/poultry-api.pre-commit28.FECHA_UTC \
  /etc/nginx/sites-available/poultry-api
sudo nginx -t
sudo systemctl reload nginx
```

El snippet puede permanecer en `/etc/nginx/snippets`; la configuración previa
no lo incluye. Restaurar el virtual host no requiere rollback de contenedores
ni de la base de datos.

Los navegadores almacenan HSTS. Si fuera necesario revocar deliberadamente la
política, el servidor HTTPS funcional tendría que responder temporalmente
`Strict-Transport-Security: max-age=0`. Eliminar la cabecera no borra por sí
solo las políticas ya almacenadas.

## Evidencia permitida

Se pueden conservar:

- SHA de Git desplegado;
- resultado resumido de `nginx -t`;
- estado activo de Nginx;
- códigos HTTP y valores de cabeceras públicas;
- ausencia de duplicados y de versión en `Server`;
- resultado de las pruebas automatizadas;
- estado y conteo de reinicios de los contenedores.

No se deben conservar llaves privadas, certificados completos, `.env`, tokens,
contraseñas, salidas completas de procesos ni configuraciones que incorporen
secretos.
