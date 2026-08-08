# Guía de despliegue y evidencia de seguridad

## 1. Propósito

Esta guía nació en el commit 29 de la fase 6 y reúne un procedimiento
reproducible para validar, integrar y desplegar los controles de los commits 25
a 30 y el cierre residual posterior. También define qué evidencia puede
conservarse, cómo nombrarla y cómo resolver los errores operativos más comunes
sin exponer secretos.

El commit 29 es exclusivamente documental. Después de integrarlo en `dev`, el
Droplet sólo necesita actualizar su checkout de Git. No requiere reconstruir
imágenes, recrear contenedores, recargar Nginx ni modificar PostgreSQL.

Las guías técnicas especializadas continúan siendo la referencia detallada:

- [auditoría de dependencias](dependency-audit.md);
- [dependencias reproducibles](dependency-pinning.md);
- [hardening básico de contenedores](container-hardening.md);
- [cabeceras de seguridad y HSTS](nginx-security-headers.md);
- [inventario de endpoints y matriz de riesgos](endpoint-inventory-risk-matrix.md);
- [cierre final sin numeración](hardening-final-closure.md);
- [continuidad, retención y RTO/RPO](backup-retention-rto-rpo.md).

## 2. Alcance y criterios de cierre

La guía cubre los siguientes controles:

| Commit | Control | Criterio resumido de cierre |
| --- | --- | --- |
| 25 | Auditoría de vulnerabilidades | Runtime y simulador sin vulnerabilidades conocidas o con mitigación documentada |
| 26 | Dependencias fijadas | Locks válidos, instalación con hashes y builds reproducibles |
| 27 | Hardening de contenedores | Usuario no root, filesystem de solo lectura, privilegios reducidos y healthchecks |
| 28 | Nginx y HSTS | Sintaxis válida, HTTPS con cabeceras únicas y HTTP sin HSTS |
| 29 | Guía y evidencia | Procedimiento ejecutable, capturas seguras, rollback y errores comunes documentados |
| 30 | Inventario y riesgos | Rutas, puertos, tópicos, controles y riesgos residuales trazables |
| Cierre | Remediación residual | Registro administrativo, errores redactados, polling limitado y secretos fail-closed |

El commit 29 se considera listo cuando:

1. los enlaces y comandos de esta guía corresponden a archivos reales;
2. las validaciones locales aplicables terminan correctamente;
3. el DOCX de evidencia contiene los diez espacios numerados solicitados;
4. ninguna captura, documento o salida expone secretos;
5. el PR se integra en `dev` y el checkout del Droplet se actualiza sin reiniciar
   servicios.

## 3. Reglas de evidencia segura

### 3.1 Información permitida

- rama, SHA, fecha y URL de un workflow;
- nombre y conclusión de un check;
- códigos HTTP y cabeceras públicas;
- nombres de paquetes, versiones e identificadores de vulnerabilidad;
- nombre y estado de servicios y contenedores;
- UID/GID, límites de procesos, capacidades eliminadas y estado read-only;
- nombre lógico de un respaldo, fecha, tamaño y resultado de integridad;
- rutas de archivos de configuración versionados y rutas de rollback;
- conteos de eventos sin payloads sensibles.

### 3.2 Información prohibida

No conservar ni mostrar:

- `.env`, variables de entorno completas o `docker compose config` sin
  `--quiet`;
- tokens de GitHub, OAuth, JWT o Telegram;
- contraseñas PostgreSQL o MQTT;
- llaves privadas, certificados completos o secretos MFA/TOTP;
- códigos QR de enrolamiento MFA;
- `rclone.conf`, contraseñas o salt del remoto cifrado;
- contenido SQL, filas productivas o dumps;
- salidas completas de `docker inspect` que incluyan el entorno;
- escritorios, pestañas o fondos donde haya credenciales visibles.

Antes de guardar una captura, recortar la ventana al comando y su resultado,
revisar el fondo y ocultar cualquier dato sensible. No basta con confiar en que
la aplicación enmascare automáticamente los tokens.

### 3.3 Convención de archivos

Las capturas finales se guardan fuera del repositorio mientras contengan datos
operativos. Si una captura ha sido revisada y aprobada para Git, usar PNG y la
siguiente convención:

```text
docs/evidence/commit29/screenshots/E29-01-git-traceability.png
docs/evidence/commit29/screenshots/E29-02-github-actions.png
...
docs/evidence/commit29/screenshots/E29-10-rollback-backup.png
```

No agregar imágenes pendientes ni enlaces rotos. El DOCX incluido en
`docs/evidence/commit29/` funciona como plantilla formal para insertar las
capturas autorizadas.

## 4. Catálogo de evidencias

| ID | Evidencia | Comando o vista principal | Resultado esperado |
| --- | --- | --- | --- |
| E29-01 | Trazabilidad Git | `git branch --show-current`, `git rev-parse --short HEAD`, `git status --short` | Rama correcta, SHA identificable y sólo cambios del commit 29 |
| E29-02 | GitHub Actions | Checks de auditoría, contenedores y Nginx | Ejecuciones aplicables en estado `success` |
| E29-03 | Auditoría de dependencias | `pip-audit` para `requirements.txt` y el simulador | Sin vulnerabilidades conocidas o mitigación documentada |
| E29-04 | Locks y regresión | `poetry check --lock`, pruebas unitarias y `pip check` | Locks válidos, pruebas aprobadas y dependencias consistentes |
| E29-05 | Contenedores endurecidos | `docker compose ps`, `id` e inspección limitada | Servicios saludables, UID 10001 y controles activos |
| E29-06 | Nginx y cabeceras | `validate_nginx_config.sh` y `check_nginx_security_headers.sh` | Sintaxis correcta y cabeceras públicas sin duplicados |
| E29-07 | Login y MFA | Navegador sobre HTTPS | Login administrativo exige contraseña y TOTP válidos |
| E29-08 | Eventos de seguridad | Vista autenticada `/security-events` | Eventos visibles sin romper sesión ni MFA |
| E29-09 | Salud productiva | `docker compose ps`, reinicios y logs recientes | Cinco servicios saludables y sin reinicios nuevos; anotar por separado cualquier contador histórico |
| E29-10 | Rollback y respaldo | Rama de rollback y respaldo Nginx/continuidad | Punto de recuperación identificable sin mostrar secretos |

## 5. Preparación local en WSL

Ejecutar desde el Git local de WSL:

```bash
cd /mnt/e/py/poultry-iot-system-v2

git branch --show-current
git rev-parse --short HEAD
git status --short
docker version
docker compose version
poetry --version
gh auth status
```

`git status --short` debe revisarse manualmente. No usar `git add .`: agregar
solamente los archivos previstos para el commit 29. La evidencia de
`gh auth status` debe limitarse al usuario y al host; nunca debe incluir un
token.

Validar la configuración Compose sin imprimir variables resueltas:

```bash
docker compose config --quiet
echo "Compose config: $?"
```

El código `0` confirma que la configuración es válida. No ejecutar
`docker compose config` sin `--quiet` en una captura porque puede revelar
secretos del archivo `.env`.

## 6. Validaciones de la fase 6

### 6.1 Commit 25: auditoría de dependencias

Si `pip-audit` ya está disponible dentro del entorno Poetry:

```bash
poetry run python -m pip_audit \
  --requirement requirements.txt \
  --vulnerability-service pypi \
  --strict \
  --format markdown \
  --progress-spinner off

poetry run python -m pip_audit \
  . \
  --vulnerability-service pypi \
  --strict \
  --format markdown \
  --progress-spinner off
```

Si el módulo no está instalado, incorporarlo sólo al entorno local de trabajo:

```bash
poetry run python -m pip install "pip-audit==2.10.1"
```

Una salida `No known vulnerabilities found` es aprobatoria. Cuando se ejecuta
una prueba negativa con una dependencia deliberadamente vulnerable, el código
`1` es el resultado esperado y demuestra que el control bloquea la integración.

### 6.2 Commit 26: dependencias reproducibles

```bash
poetry check --lock

docker run --rm \
  --entrypoint python \
  -v "$PWD:/src:ro" \
  -w /src \
  poultry-iot-system-v2-api:latest \
  -m unittest discover -s local_tests -p 'test_*.py'

for image in \
  poultry-iot-system-v2-api:latest \
  poultry-iot-system-v2-dashboard:latest \
  poultry-iot-system-v2-mqtt_subscriber:latest
do
  docker run --rm --entrypoint python "$image" -m pip check
done
```

Para demostrar el enforcement de hashes, una instalación limpia debe usar el
lock versionado:

```bash
docker run --rm \
  -v "$PWD:/src:ro" \
  -w /src \
  python:3.11-slim \
  sh -ec 'python -m pip install --no-cache-dir \
    --require-hashes -r requirements.txt && python -m pip check'
```

No regenerar `requirements.txt` durante una validación de evidencia. La
regeneración sólo corresponde a un cambio deliberado de dependencias.

### 6.3 Commit 27: hardening de contenedores

```bash
docker compose up -d
docker compose ps

for service in api dashboard mqtt_subscriber; do
  printf '%s: ' "$service"
  docker compose exec -T "$service" id
done

for service in api dashboard mqtt_subscriber; do
  container_id="$(docker compose ps -q "$service")"
  docker inspect "$container_id" --format \
    '{{.Name}} readonly={{.HostConfig.ReadonlyRootfs}} pids={{.HostConfig.PidsLimit}} caps={{json .HostConfig.CapDrop}} security={{json .HostConfig.SecurityOpt}}'
done
```

Los tres servicios propios deben mostrar UID/GID `10001`,
`readonly=true`, `pids=128`, `caps=["ALL"]` y
`no-new-privileges:true`.

Comprobar el aislamiento de escritura:

```bash
for service in api dashboard mqtt_subscriber; do
  if docker compose exec -T "$service" touch /app/write-must-fail; then
    echo "ERROR: $service permite escritura en /app"
    exit 1
  fi

  docker compose exec -T "$service" \
    sh -c 'touch /tmp/write-ok && rm /tmp/write-ok'
  echo "OK: $service bloquea /app y permite /tmp"
done
```

El error `Read-only file system` sobre `/app` es el resultado esperado.

### 6.4 Commit 28: Nginx, HTTPS y HSTS

Validación aislada local:

```bash
python -m unittest local_tests.test_nginx_security_headers
bash scripts/validate_nginx_config.sh
```

Validación pública después del despliegue:

```bash
bash scripts/check_nginx_security_headers.sh \
  https://poultry-system.duckdns.org
```

El script debe aprobar `/`, `/login`, `/api/health`, `/health` y una ruta 404.
También exige redirección HTTP sin HSTS, HSTS únicamente sobre HTTPS, una sola
copia de cada cabecera y `Server: nginx` sin versión.

Para la revisión manual local:

```bash
docker compose up -d
bash scripts/run_nginx_security_preview.sh
```

Abrir `https://localhost:8443/login`. La advertencia por certificado
autofirmado es esperada únicamente en este preview. El puerto 8443 evita
interferir con el HTTPS productivo y permite probar la configuración Nginx
aislada sin reemplazar el proxy local normal.

## 7. Validación manual del dashboard

Realizar estas comprobaciones primero en el preview local y, después de la
integración, como smoke test sobre el dominio productivo:

1. abrir `/login` mediante HTTPS;
2. completar usuario, contraseña y TOTP del administrador;
3. recorrer dashboard, históricos, alertas, naves y módulos;
4. comprobar que estilos, iconos, gráficas e imagen de perfil carguen;
5. abrir `/security-events` y aplicar filtros;
6. confirmar que no aparezcan bloqueos CSP en la consola del navegador;
7. cerrar sesión y comprobar que las vistas protegidas ya no sean accesibles.

No capturar la contraseña, el código TOTP, el secreto de enrolamiento ni el
código QR. Para E29-07 basta mostrar la página ya autenticada y el indicador de
MFA activo. Para E29-08 se pueden mostrar conteos y nombres de eventos, pero se
deben ocultar payloads o identificadores sensibles si aparecieran.

## 8. GitHub Actions y documentación-only

Los workflows usan filtros `paths`. Un PR que sólo cambia Markdown y DOCX no
ejecutará automáticamente las auditorías de dependencias, contenedores o
Nginx. La ausencia de checks en el PR del commit 29 no implica un fallo.

Consultar ejecuciones exitosas previas:

```bash
gh repo set-default ArrowKnight77/poultry-iot-system-v2

gh run list --workflow dependency-audit.yml --limit 3
gh run list --workflow container-hardening.yml --limit 3
gh run list --workflow nginx-security-headers.yml --limit 3
```

Cuando sea necesario generar evidencia nueva, los tres workflows admiten
`workflow_dispatch`:

```bash
gh workflow run dependency-audit.yml --ref dev
gh workflow run container-hardening.yml --ref dev
gh workflow run nginx-security-headers.yml --ref dev
```

Esperar la finalización y capturar únicamente nombre, SHA, estado y URL. No
capturar datos de autenticación de GitHub CLI.

## 9. Integración y despliegue del commit 29

### 9.1 Integración

Antes de publicar:

```bash
git diff --check
git status --short
git diff -- docs/deployment-security-evidence-guide.md README.md
```

Revisar el DOCX visualmente en Word o LibreOffice. Agregar al índice sólo los
archivos del commit 29 y comprobar el staged diff antes de crear el commit.

Después del push, abrir un PR hacia `dev`. Como es documentación-only, revisar
manualmente el contenido y los enlaces aunque GitHub no muestre checks de los
workflows filtrados.

### 9.2 Actualización del Droplet

El despliegue del commit 29 sólo actualiza el checkout:

```bash
ssh admnyc@poultry-system.duckdns.org
cd /home/admnyc/py/poultry-iot-system-v2

git branch --show-current
git rev-parse --short HEAD
git status --short

test -z "$(git status --porcelain)" || exit 1

old_sha="$(git rev-parse HEAD)"
old_short="$(git rev-parse --short HEAD)"
git branch "rollback/pre-commit29-$old_short" "$old_sha"

git fetch origin dev
git pull --ff-only origin dev

git rev-parse --short HEAD
git status --short
```

No ejecutar `docker compose build`, `docker compose up`, `systemctl reload
nginx` ni migraciones para desplegar este commit documental.

### 9.3 Smoke test posterior

```bash
docker compose ps

docker compose ps -q | xargs -r docker inspect --format \
  '{{.Name}} restart={{.RestartCount}} status={{.State.Status}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}'

bash scripts/check_nginx_security_headers.sh \
  https://poultry-system.duckdns.org
```

Los cinco servicios deben permanecer saludables y sin reinicios provocados por
el despliegue documental.

## 10. Rollback

Para revertir únicamente el checkout documental:

```bash
cd /home/admnyc/py/poultry-iot-system-v2
git switch rollback/pre-commit29-SHA_ANTERIOR
```

Este rollback no requiere reiniciar contenedores, restaurar PostgreSQL ni
recargar Nginx. Si se necesita volver a `dev`, corregir primero la causa y
actualizar mediante un nuevo commit o volver a cambiar a la rama autorizada.

Los rollbacks de los controles 27 y 28 se ejecutan con sus procedimientos
especializados. En particular, retirar una cabecera HSTS no elimina una
política ya almacenada por los navegadores; una revocación deliberada requiere
servir temporalmente `Strict-Transport-Security: max-age=0` sobre HTTPS.

## 11. Resolución de errores comunes

### `docker: command not found` dentro de WSL

1. iniciar Docker Desktop en Windows;
2. activar **Settings > Resources > WSL Integration** para la distribución;
3. ejecutar `wsl --shutdown` desde PowerShell de Windows, no desde WSL;
4. abrir WSL y validar `docker version`.

### `Cannot connect to the Docker daemon`

Confirmar que Docker Desktop terminó de iniciar y que la integración WSL está
habilitada. No reinstalar paquetes Docker dentro de la distribución hasta
descartar la integración existente.

### `python3.11: command not found`

Usar el Python administrado por Poetry con `poetry run python` o ejecutar la
prueba dentro de `python:3.11-slim`. No construir rutas a un virtualenv que no
se haya creado correctamente.

### `No default remote repository has been set` en `gh`

```bash
gh repo set-default ArrowKnight77/poultry-iot-system-v2
```

No copiar al terminal frases explicativas como si fueran comandos.

### `fatal: unable to get current working directory`

El directorio fue eliminado o dejó de estar disponible. Volver a una ruta
existente:

```bash
cd /mnt/e/py/poultry-iot-system-v2
pwd
git status --short
```

### Certificado inválido en `https://localhost:8443`

Es esperado porque el preview usa un certificado autofirmado efímero. La
advertencia no es aceptable en el dominio productivo, que debe usar el
certificado administrado por Certbot.

### Los eventos de seguridad no cargan

Confirmar que la sesión y MFA sigan vigentes. En Nginx, la coincidencia exacta
`/api/security-events` debe dirigirse al dashboard, mientras
`/api/security-events/ingest` continúa hacia la API. Ejecutar primero
`nginx -t`; no recargar una configuración inválida.

### `sudo` solicita contraseña durante el despliegue

Escribirla únicamente en la sesión SSH interactiva del operador. Nunca pegarla
en el chat, un script, una captura o un archivo del repositorio.

### `nginx -t` falla

No ejecutar reload. Restaurar el virtual host respaldado, repetir `nginx -t` y
recargar sólo cuando la sintaxis sea correcta. Consultar el rollback de
[cabeceras de seguridad y HSTS](nginx-security-headers.md#rollback).

### El PR documental no muestra checks

Es el comportamiento esperado de los filtros por rutas. Revisar ejecuciones
exitosas anteriores o lanzar manualmente los workflows mediante
`workflow_dispatch`.

## 12. Lista final de cierre

- [ ] Rama y SHA registrados.
- [ ] Árbol de trabajo revisado sin archivos ajenos al commit 29.
- [ ] Markdown y DOCX revisados.
- [ ] Enlaces relativos verificados.
- [ ] Validaciones aplicables terminadas correctamente.
- [ ] Diez espacios de captura presentes y numerados E29-01 a E29-10.
- [ ] Capturas recortadas y revisadas para evitar secretos.
- [ ] PR integrado en `dev`.
- [ ] Checkout del Droplet actualizado mediante fast-forward.
- [ ] Contenedores saludables y sin reinicios nuevos.
- [ ] Cabeceras públicas todavía aprobadas.
- [ ] Rama de rollback registrada.

## 13. Resultado esperado del commit 29

El repositorio dispone de una guía única para ejecutar, evidenciar y recuperar
los controles finales de hardening. La documentación permite repetir las
validaciones sin depender del historial del chat y separa claramente la
evidencia permitida de los secretos que nunca deben conservarse.

El cierre global de superficies, roles, puertos y riesgos se conserva en el
[inventario de endpoints y matriz de riesgos](endpoint-inventory-risk-matrix.md).
Ese inventario debe actualizarse cuando cambie una ruta, un método, un puerto,
un tópico MQTT o el estado residual de un riesgo.

## 14. Extensión de cierre posterior al commit 30

La plantilla DOCX continúa usando los espacios E29-01 a E29-10 porque fueron
reservados por la guía original. Para la entrega final deben interpretarse como
evidencia global de Fase 6: E29-01 registra la rama y SHA del cierre; E29-04
incluye las pruebas de autorización, redacción y rate limit; E29-09 confirma el
runtime reconstruido en el Droplet; y E29-10 conserva el SHA de rollback previo
al despliegue final.

A diferencia del commit 29, el cierre residual sí modifica API y dashboard.
Por ello requiere reconstruir `api` y `dashboard`, recrear
`mqtt_subscriber` por su dependencia del API y confirmar los cinco servicios
saludables. Los comandos y respuestas esperadas se conservan en la
[guía de cierre final](hardening-final-closure.md).
