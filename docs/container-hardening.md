# Hardening básico de contenedores

Este documento describe los controles incorporados en la fase 6, commit 27,
para reducir el impacto de una posible ejecución de código dentro de los
contenedores del Poultry IoT System.

## Alcance

Los controles estrictos se aplican a las imágenes desarrolladas por el
proyecto:

- `api`;
- `dashboard`;
- `mqtt_subscriber`.

PostgreSQL y Mosquitto conservan sus usuarios y sistemas de archivos definidos
por las imágenes oficiales. Sus entrypoints necesitan preparar permisos y
volúmenes persistentes; forzar el mismo UID o un root filesystem de solo
lectura podría impedir su inicialización. PostgreSQL mantiene su healthcheck y
Mosquitto incorpora una comprobación TCP local sin credenciales.

## Controles implementados

| Control | Aplicación |
| --- | --- |
| Usuario no root | UID/GID fijo `10001:10001` en las tres imágenes Python |
| Root filesystem | `read_only: true` |
| Escritura temporal | `tmpfs` limitado a 16 MiB en `/tmp` |
| Capacidades Linux | `cap_drop: ALL` |
| Escalación | `no-new-privileges:true` |
| Procesos | `pids_limit: 128` |
| Recolección de procesos | `init: true` |
| Dependencias | Instalación con versiones y hashes obligatorios |
| Paquetes de construcción | `gcc` y `curl` ausentes de las imágenes finales |

El código y el home pertenecen al usuario `app`, pero el contenedor sólo puede
escribir en el `tmpfs`. Python no genera bytecode en tiempo de ejecución.

Gunicorn atiende la API y el dashboard. Su socket de control está deshabilitado
y los latidos de los workers utilizan `/tmp`, evitando escrituras dentro del
home o de `/app`.

## Healthchecks

| Servicio | Comprobación | Resultado saludable |
| --- | --- | --- |
| `db` | `pg_isready` | PostgreSQL acepta conexiones |
| `mqtt` | Conexión TCP local al puerto 1883 | Broker escuchando |
| `api` | `GET /api/health` y `SELECT 1` | HTTP 200 con `{"status":"ok"}` |
| `dashboard` | `GET /health` | HTTP 200 con `{"status":"ok"}` |
| `mqtt_subscriber` | `/tmp/mqtt-connected` | Sesión MQTT conectada |

Los endpoints no incluyen versiones, nombres de host, credenciales ni detalles
de excepciones. La API devuelve HTTP 503 cuando la consulta mínima a la base de
datos falla.

El subscriber elimina su indicador al iniciar, desconectarse o detenerse. Lo
crea únicamente después de una conexión MQTT exitosa. Esto permite detectar
una pérdida real de conectividad con el broker.

## Dependencias de arranque

Compose espera estados saludables:

1. PostgreSQL y Mosquitto inician y pasan sus healthchecks.
2. La API inicia cuando ambos están saludables.
3. El dashboard inicia después de que la API esté saludable.
4. El subscriber inicia después de que API, PostgreSQL y Mosquitto estén
   saludables.

El orden reduce falsos errores de arranque, pero los servicios siguen
gestionando desconexiones posteriores.

## Validación local en WSL

Ejecutar desde `/mnt/e/py/poultry-iot-system-v2`:

```bash
docker compose config --quiet
docker compose build --no-cache api dashboard mqtt_subscriber
docker compose up -d
docker compose ps
```

Todos los servicios deben terminar en estado `healthy`. Confirmar el usuario de
los contenedores propios:

```bash
for service in api dashboard mqtt_subscriber; do
  printf '%s: ' "$service"
  docker compose exec -T "$service" id
done
```

El resultado debe mostrar UID y GID `10001`.

Comprobar los controles configurados por Docker:

```bash
for service in api dashboard mqtt_subscriber; do
  container_id="$(docker compose ps -q "$service")"
  docker inspect "$container_id" --format \
    '{{.Name}} readonly={{.HostConfig.ReadonlyRootfs}} pids={{.HostConfig.PidsLimit}} caps={{json .HostConfig.CapDrop}} security={{json .HostConfig.SecurityOpt}}'
done
```

Comprobar el aislamiento de escritura:

```bash
if docker compose exec -T api touch /app/write-must-fail; then
  echo 'ERROR: /app permite escritura'
  exit 1
else
  echo 'OK: /app es de solo lectura'
fi

docker compose exec -T api sh -c 'touch /tmp/write-ok && rm /tmp/write-ok'
```

Validar endpoints y dependencias instaladas:

```bash
curl -fsS http://127.0.0.1:5000/api/health
curl -fsS http://127.0.0.1:5001/health

for service in api dashboard mqtt_subscriber; do
  docker compose exec -T "$service" python -m pip check
done
```

Las pruebas unitarias normalmente se ejecutan con un bind mount temporal o
desde el checkout, porque `local_tests` no forma parte de las imágenes de
producción:

```bash
docker run --rm \
  --entrypoint python \
  -v "$PWD:/src:ro" \
  -w /src \
  poultry-iot-system-v2-api:latest \
  -m unittest discover -s local_tests -p 'test_*.py'
```

Revisar que no existan reinicios ni errores:

```bash
docker compose ps
docker compose logs --since=10m api dashboard mqtt_subscriber
```

## Prueba de recuperación MQTT local

Esta prueba interrumpe temporalmente el broker y sólo debe ejecutarse en el
entorno local:

```bash
docker compose stop mqtt
sleep 40
docker inspect "$(docker compose ps -q mqtt_subscriber)" \
  --format '{{.State.Health.Status}}'

docker compose start mqtt
sleep 40
docker inspect "$(docker compose ps -q mqtt_subscriber)" \
  --format '{{.State.Health.Status}}'
```

El subscriber debe pasar a `unhealthy` durante la desconexión y regresar a
`healthy` después de reconectarse.

## Despliegue y rollback

Antes de desplegar:

1. crear un respaldo PostgreSQL nuevo y comprobar su archivo gzip;
2. crear una rama de rollback en el commit desplegado;
3. validar `docker compose config --quiet`;
4. reconstruir las tres imágenes Python sin caché;
5. recrear Mosquitto y los servicios Python en una ventana controlada;
6. conservar PostgreSQL y su volumen sin recrearlos;
7. comprobar healthchecks, usuarios, restricciones, logs y HTTPS externo.

Si un servicio no permanece saludable, volver al commit de la rama de rollback,
reconstruir las imágenes anteriores y recrear los servicios. El respaldo sólo
se restaura si existe una pérdida o modificación de datos; un rollback de
contenedores no requiere restaurar PostgreSQL.

## Evidencia permitida

Se pueden conservar:

- SHA desplegado;
- salida resumida de `docker compose ps`;
- UID/GID y metadatos de aislamiento;
- resultados de pruebas y `pip check`;
- códigos HTTP de los healthchecks;
- nombres de servicios y conteos de reinicios.

No se deben conservar `.env`, valores de variables, contraseñas MQTT, tokens,
certificados privados, dumps de base de datos ni salidas completas de
`docker inspect` que incluyan el entorno.
