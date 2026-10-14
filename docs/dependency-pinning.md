# Dependencias Python reproducibles

## Propósito

Los contenedores de la API, el dashboard y el subscriber MQTT instalan un
único lock de dependencias Python. El control evita que un build posterior
resuelva silenciosamente versiones o artefactos distintos.

El alcance es la reproducibilidad del grafo Python. La imagen base, el usuario
del contenedor, los privilegios y los healthchecks corresponden al hardening de
contenedores del commit 27.

## Archivos fuente y generados

| Archivo | Responsabilidad |
| --- | --- |
| `requirements.in` | Dependencias directas compartidas por los tres contenedores Python. |
| `requirements.txt` | Lock generado con versiones transitivas exactas y hashes SHA-256. |
| `pyproject.toml` | Dependencia directa y backend de build del simulador Poetry. |
| `poetry.lock` | Resolución exacta del simulador. |

`requirements.txt` es generado. Nunca debe editarse manualmente ni instalarse
`requirements.in` en producción.

## Toolchain de generación

- Linux y Python 3.11, igual que las imágenes de aplicación;
- `pip-tools` 7.6.0;
- Poetry 2.4.1 y `poetry-core` 2.4.1;
- índice público de PyPI, sin índices privados ni `--trusted-host`.

La resolución depende del sistema operativo y de la versión de Python. Por
eso el lock del runtime debe regenerarse dentro del contenedor Python 3.11 y no
con el Python local de otra versión.

## Regenerar el lock del runtime

Desde WSL, con Docker Desktop iniciado e integrado:

```bash
docker run --rm \
  -v "$PWD:/src" \
  -w /src \
  python:3.11-slim \
  sh -c 'python -m pip install --disable-pip-version-check \
    --no-cache-dir pip-tools==7.6.0 && \
    python -m piptools compile --upgrade \
    --resolver=backtracking \
    --generate-hashes \
    --strip-extras \
    --output-file=requirements.txt \
    requirements.in'
```

Después de regenerar, revisar el diff completo. Una actualización no debe
aprobarse sólo porque resuelva correctamente: también debe pasar auditoría,
pruebas y builds.

## Actualizar el lock del simulador

```bash
poetry lock
poetry check --lock
```

`paho-mqtt` se mantiene en 2.1.0 tanto para el runtime como para el simulador.
El subscriber y el simulador usan Callback API v2.

## Enforcement en builds

Los tres Dockerfiles instalan con:

```bash
python -m pip install --no-cache-dir --require-hashes -r requirements.txt
```

`--require-hashes` hace fallar el build cuando falta un hash, una dependencia
transitiva no está bloqueada o el artefacto descargado no coincide.

## Validación obligatoria

```bash
poetry check --lock
python -m unittest discover -s local_tests -p 'test_*.py'
python -m pip_audit --requirement requirements.txt --strict
python -m pip_audit . --strict
docker compose config --quiet
docker compose build --no-cache api dashboard mqtt_subscriber
```

Además, ejecutar `python -m pip check` dentro de al menos una imagen construida
y confirmar que una segunda compilación del lock no produce diferencias.

## Política de actualización

1. modificar sólo `requirements.in` o `pyproject.toml`;
2. regenerar el lock correspondiente con la toolchain fijada;
3. revisar versiones nuevas, removidas y transitivas;
4. ejecutar `pip-audit`, pruebas y builds limpios;
5. documentar cualquier vulnerabilidad sin corrección antes de integrar;
6. integrar únicamente con los checks de GitHub Actions en verde.
