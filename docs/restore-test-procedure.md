# Procedimiento de prueba de restauración

## Objetivo

Demostrar que un respaldo PostgreSQL local o recuperado desde Google Drive
puede restaurarse sin modificar la base productiva. La prueba usa un contenedor
temporal sin puertos publicados ni acceso de red y lo elimina al finalizar.

## Controles aplicados

- Valida la integridad gzip antes de iniciar.
- Usa la misma versión mayor de PostgreSQL que producción.
- Restaura con `ON_ERROR_STOP=1` para detenerse ante cualquier error SQL.
- Exige las nueve tablas principales de la aplicación.
- Verifica las columnas de oxígeno y MFA incorporadas por las migraciones.
- Rechaza roles fuera de `admin`, `operador` y `visor`.
- Comprueba que los secretos MFA persistidos conserven el formato cifrado.
- No imprime hashes, secretos MFA, tokens ni filas de usuarios.
- Elimina el contenedor y su almacenamiento temporal incluso cuando falla.

## Prueba de un respaldo local

En el Droplet, lista los archivos disponibles y elige uno explícitamente:

```bash
ls -lh /srv/poultry-backups/poultry_postgres_*.sql.gz

./scripts/test_postgres_restore.sh \
  /srv/poultry-backups/poultry_postgres_YYYYMMDDTHHMMSSZ.sql.gz
```

El resultado esperado incluye las tablas validadas, sus conteos y:

```text
Restore test completed successfully.
Temporary database removed: poultry-restore-test-...
```

Comprueba que no quede ningún contenedor temporal:

```bash
docker ps -a \
  --filter label=com.poultry-iot.purpose=restore-test
```

## Prueba de una copia cifrada recuperada desde Drive

Lista los nombres lógicos disponibles a través del remoto `crypt`:

```bash
rclone lsl poultry-offsite-crypt:
```

Crea un directorio temporal y descarga un respaldo elegido explícitamente:

```bash
restore_download_dir="$(mktemp -d)"
backup_name=poultry_postgres_YYYYMMDDTHHMMSSZ.sql.gz

rclone copyto \
  "poultry-offsite-crypt:$backup_name" \
  "$restore_download_dir/$backup_name"

./scripts/test_postgres_restore.sh \
  "$restore_download_dir/$backup_name"
```

Elimina únicamente el directorio temporal después de una prueba exitosa:

```bash
rm -r "$restore_download_dir"
```

## Evidencia permitida

Se puede capturar:

- nombre, fecha y tamaño del `.sql.gz`;
- inicio y eliminación del contenedor temporal;
- nombres y conteos de las tablas validadas;
- mensaje final de restauración exitosa;
- consulta de contenedores temporales sin resultados.

No se debe capturar:

- contenido SQL del dump;
- `rclone.conf`, tokens OAuth, contraseña o salt de `crypt`;
- hashes de contraseña o secretos MFA;
- valores completos de las tablas restauradas.

## Alcance

Esta prueba confirma que el archivo puede restaurarse y que el esquema crítico
es coherente. La retención, las responsabilidades, los objetivos RTO/RPO y la
secuencia de recuperación se definen en la
[política de continuidad de respaldos](backup-retention-rto-rpo.md).
