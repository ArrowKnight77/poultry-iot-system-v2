# Política de retención y objetivos RTO/RPO de respaldos

## 1. Propósito

Definir cuánto tiempo deben conservarse los respaldos PostgreSQL, quién debe
operarlos y qué objetivos de recuperación puede asumir el proyecto
`poultry-iot-system-v2`.

Esta política complementa el flujo 3-2-1 y el
[procedimiento de prueba de restauración](restore-test-procedure.md). No
sustituye la supervisión del servicio ni autoriza por sí sola la eliminación
automática de archivos.

## 2. Alcance

La política cubre:

- la base PostgreSQL ejecutada por Docker Compose en el Droplet;
- los respaldos comprimidos almacenados en `/srv/poultry-backups`;
- las copias cifradas publicadas mediante `poultry-offsite-crypt:`;
- las copias descifradas sincronizadas a `/mnt/e/PoultryBackups` desde WSL;
- la verificación, restauración, evidencia y respuesta ante fallos.

Los archivos `.sql.gz`, `rclone.conf`, tokens OAuth, contraseñas de `crypt`,
salt y archivos de entorno reales deben permanecer fuera de Git.

## 3. Arquitectura de continuidad

| Ubicación | Medio | Generación | Protección principal |
| --- | --- | --- | --- |
| Base productiva | Volumen PostgreSQL del Droplet | Operación continua | Acceso interno mediante Docker |
| Copia local | `/srv/poultry-backups` | Automática y diaria | Permisos Unix `700/600` |
| Copia externa | `poultry-offsite-crypt:` en Google Drive | Automática y diaria | Cifrado de contenido y nombres |
| Copia del host | `/mnt/e/PoultryBackups` / `E:\PoultryBackups` | Sincronización desde WSL | ACL de NTFS y cifrado de disco |

El timer ejecuta el flujo diariamente a las 02:15, según la zona horaria del
servidor, con un retraso aleatorio máximo de 15 minutos. `Persistent=true`
permite ejecutar una tarea omitida cuando el servidor vuelve a estar
disponible.

La copia del host no se genera desde el Droplet ni forma parte de la ejecución
automática diaria. Requiere que el equipo esté encendido, que WSL tenga acceso
al remoto cifrado y que se ejecute `scripts/sync_backups_to_host.sh`.

## 4. Objetivos de recuperación

### 4.1 RPO

El objetivo de punto de recuperación (RPO) es de **24 horas y 15 minutos** en
operación normal. Esta cifra corresponde al intervalo diario y al retraso
aleatorio máximo configurado.

El RPO se mide desde el momento del incidente hasta la fecha UTC del respaldo
más reciente que:

1. exista en el Droplet o pueda recuperarse desde Drive;
2. supere `gzip -t`;
3. pueda restaurarse con el procedimiento documentado.

`Persistent=true` reduce el riesgo de omitir una ejecución después de un
reinicio, pero no garantiza el RPO si el Droplet, PostgreSQL, Google Drive o las
credenciales permanecen indisponibles durante más de un ciclo.

### 4.2 RTO

El objetivo de tiempo de recuperación (RTO) es de **4 horas** desde la
declaración del incidente hasta que:

- PostgreSQL se encuentre restaurado;
- la API pueda conectarse a la base;
- las consultas críticas hayan sido validadas;
- el servicio pueda volver a operación autorizada.

El reloj de RTO comienza cuando el responsable declara que la base productiva
está indisponible, dañada o requiere reversión. Termina después de validar el
servicio, no solamente después de importar el archivo SQL.

El objetivo supone disponibilidad de un operador autorizado, Docker, las
credenciales de infraestructura y al menos una copia válida. Una dependencia
externa indisponible debe registrarse como causa de incumplimiento.

Las pruebas del commit 23 demostraron la restauración técnica de la base desde
el Droplet y Drive, pero no midieron una recuperación productiva completa. El
RTO de cuatro horas deberá cronometrarse y confirmarse durante cada simulacro o
incidente real.

## 5. Política de retención

| Ubicación | Retención mínima | Frecuencia operativa | Finalidad |
| --- | --- | --- | --- |
| Droplet | 7 respaldos diarios válidos | Automática, cada día | Recuperación rápida |
| Host Windows | 30 respaldos válidos | Sincronización al menos semanal | Copia independiente controlada por el operador |
| Drive cifrado | 30 respaldos diarios y 12 cierres mensuales | Diaria; selección mensual | Recuperación externa y conservación histórica |

Para Drive se conservará como cierre mensual el último respaldo válido de cada
mes durante 12 meses. Antes de depurar respaldos diarios, el cierre mensual
debe identificarse y preservarse separadamente en el remoto cifrado.

Independientemente de la antigüedad:

- nunca se eliminarán las tres copias válidas más recientes de una ubicación;
- no se depurará ninguna ubicación si el último respaldo válido supera el RPO;
- no se depurará Drive si no existe al menos una copia reciente y comprobable
  en el Droplet o el host;
- no se eliminará un respaldo seleccionado como evidencia de un incidente;
- los archivos `.partial` no se consideran respaldos válidos.

La retención se aplicará manualmente hasta que exista un control separado de
automatización. Este commit no incorpora comandos de borrado ni tareas de
depuración programadas.

## 6. Responsabilidades

### Responsable de continuidad

- aprobar los objetivos RTO/RPO y sus excepciones;
- autorizar una recuperación sobre el entorno productivo;
- revisar la política después de incidentes o cambios de infraestructura.

### Operador de respaldos

- comprobar el timer, la ejecución diaria y el espacio disponible;
- verificar que la copia local y la copia cifrada tengan fechas coherentes;
- sincronizar el host como mínimo una vez por semana;
- conservar la evidencia permitida sin exponer datos sensibles.

### Custodio de credenciales

- proteger el token OAuth, la contraseña y el salt de `crypt`;
- mantener un método de recuperación de credenciales fuera del repositorio;
- comprobar que las credenciales puedan recuperarse por una persona
  autorizada durante una contingencia.

Una persona puede desempeñar más de un rol en este proyecto, pero cada
actividad debe quedar asociada a un responsable identificable.

## 7. Supervisión y umbrales

Se debe generar una revisión operativa cuando ocurra cualquiera de estas
condiciones:

- no existe un respaldo local y externo válido de las últimas 26 horas;
- la última sincronización comprobada del host supera 7 días;
- el timer está deshabilitado o no tiene una siguiente ejecución;
- `gzip -t` falla;
- la carga a Drive, el descifrado o la prueba de restauración falla;
- no se dispone de las credenciales necesarias para recuperar la copia
  cifrada.

Ante un incumplimiento:

1. suspender cualquier depuración;
2. ejecutar un respaldo manual si PostgreSQL está disponible;
3. restablecer al menos una copia local y una externa;
4. registrar la causa, duración, respaldos afectados y acción correctiva;
5. repetir la prueba de restauración cuando se recupere el flujo.

## 8. Verificación y evidencia

En el Droplet, estas consultas son de solo lectura:

```bash
systemctl list-timers "poultry-backup@$(id -un).timer" --all

sudo journalctl \
  -u "poultry-backup@$(id -un).service" \
  -n 50 \
  --no-pager

ls -lh /srv/poultry-backups/
rclone lsl poultry-offsite-crypt:
```

En WSL:

```bash
./scripts/sync_backups_to_host.sh /mnt/e/PoultryBackups

find /mnt/e/PoultryBackups \
  -maxdepth 1 \
  -type f \
  -name 'poultry_postgres_*.sql.gz'
```

La evidencia puede mostrar fechas, tamaños, nombres lógicos, estado del timer,
resultado de `gzip -t`, tablas validadas y eliminación del contenedor temporal.
No debe mostrar contenido SQL, filas, hashes, secretos MFA, tokens ni
`rclone.conf`.

## 9. Pruebas de restauración

Se realizará:

- una restauración mensual desde una copia recuperada de Drive;
- una validación trimestral de una copia sincronizada al host;
- una prueba adicional después de cambios en PostgreSQL, Docker, el esquema,
  el script de backup o la configuración de cifrado;
- una prueba inmediata después de corregir cualquier fallo de continuidad.

Las pruebas usarán `scripts/test_postgres_restore.sh` y se ejecutarán primero
en un contenedor aislado. Restaurar sobre producción requiere autorización del
responsable de continuidad y un incidente declarado.

## 10. Secuencia de recuperación

1. Declarar el incidente y registrar la hora de inicio del RTO.
2. Detener escrituras si la base dañada todavía está accesible.
3. Identificar el respaldo válido más reciente anterior al incidente.
4. Intentar la recuperación en este orden: Droplet, Drive cifrado y host.
5. Validar el archivo con `gzip -t`.
6. Restaurarlo primero mediante el procedimiento de prueba aislado.
7. Autorizar y ejecutar la recuperación productiva.
8. Validar PostgreSQL, API, autenticación y consultas críticas.
9. Registrar la hora de recuperación, el RPO obtenido y el RTO real.
10. Crear un nuevo respaldo completo después de estabilizar el servicio.

## 11. Revisión de la política

Esta política entra en vigor al integrarse en `dev`. Debe revisarse cada seis
meses y también cuando cambie la frecuencia del timer, el proveedor externo,
el volumen de datos, los responsables, el procedimiento de restauración o los
requisitos del proyecto.

Todo incumplimiento de RTO, RPO o retención debe documentarse con una acción
correctiva. La automatización futura de la depuración deberá implementarse y
probarse en un commit independiente.
