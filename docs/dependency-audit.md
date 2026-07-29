# Auditoría de vulnerabilidades de dependencias Python

## 1. Propósito

El workflow `Dependency vulnerability audit` detecta vulnerabilidades públicas
conocidas en las dependencias Python antes de integrar cambios y mediante una
revisión semanal. El control informa el paquete, la versión afectada, el
identificador de vulnerabilidad y las versiones corregidas conocidas.

El escaneo utiliza `pip-audit` 2.10.1 y el servicio de vulnerabilidades de PyPI.
Las acciones de preparación del runner están fijadas por hash de commit para
evitar depender de etiquetas mutables.

## 2. Alcance

La auditoría mantiene dos comprobaciones independientes:

| Comprobación | Fuente | Uso en el proyecto |
| --- | --- | --- |
| Runtime de la aplicación | `requirements.txt` | API, dashboard y subscriber MQTT construidos por Docker |
| Simulador IoT | `pyproject.toml` en la raíz | Simulador y herramientas MQTT administradas como proyecto Python |

`requirements.txt` y `pyproject.toml` expresan actualmente contratos distintos
para `paho-mqtt`. El workflow conserva ambos alcances para hacer visible esa
diferencia. La reconciliación y fijación de versiones corresponde al commit 26;
el commit 25 no modifica dependencias de producción.

## 3. Cuándo se ejecuta

El workflow se ejecuta:

- en pull requests hacia `dev` o `main` cuando cambia una fuente de
  dependencias o el propio workflow;
- en pushes a `dev` o `main` con esos mismos cambios;
- cada lunes a las 07:17 UTC;
- manualmente mediante `workflow_dispatch`.

La ejecución programada permite detectar nuevas vulnerabilidades publicadas
aunque el repositorio no haya cambiado.

## 4. Política de resultado

`pip-audit` devuelve un código distinto de cero cuando encuentra una o más
vulnerabilidades conocidas. El workflow conserva ese comportamiento y no usa
`continue-on-error`, arreglos automáticos ni una opción global para permitir
fallos.

Un resultado fallido debe tratarse de una de estas formas:

1. actualizar la dependencia a una versión corregida y ejecutar las pruebas de
   regresión;
2. si no existe corrección, documentar el identificador, la exposición real,
   los controles compensatorios, el responsable y la fecha de revisión;
3. si se demuestra un falso positivo, justificar por escrito cualquier
   exclusión por identificador y asignarle una fecha de caducidad.

Nunca debe ignorarse una vulnerabilidad únicamente para obtener un workflow
verde.

## 5. Reproducción local

Ejemplo para Linux o WSL con Python 3.11:

```bash
python3.11 -m venv /tmp/poultry-dependency-audit
/tmp/poultry-dependency-audit/bin/python -m pip install \
  "pip-audit==2.10.1"

/tmp/poultry-dependency-audit/bin/python -m pip_audit \
  --requirement requirements.txt \
  --vulnerability-service pypi \
  --strict \
  --format markdown \
  --progress-spinner off

/tmp/poultry-dependency-audit/bin/python -m pip_audit \
  . \
  --vulnerability-service pypi \
  --strict \
  --format markdown \
  --progress-spinner off
```

La consulta requiere acceso a PyPI y a su base de vulnerabilidades. Auditar un
archivo de requisitos puede resolver paquetes de manera comparable a una
instalación; no deben añadirse índices o paquetes que no sean confiables.

## 6. Evidencia segura

La evidencia mínima permitida es:

- URL y fecha de la ejecución de GitHub Actions;
- SHA auditado;
- nombre de la comprobación;
- paquetes y versiones afectados;
- identificadores de vulnerabilidad y versiones de corrección;
- decisión de mitigación cuando corresponda.

No deben incluirse archivos `.env`, credenciales de índices, tokens de GitHub,
salidas de `docker compose config`, dumps, configuraciones `rclone` ni secretos
de MQTT, JWT o MFA.

## 7. Criterios de cierre del control

El commit 25 queda validado cuando:

- GitHub reconoce y ejecuta el workflow;
- las dos comprobaciones llegan a una conclusión auditable;
- cualquier vulnerabilidad tiene corrección o plan de mitigación documentado;
- el workflow conserva permisos de sólo lectura;
- las dependencias no son modificadas automáticamente;
- la evidencia no expone secretos.

## 8. Resultado inicial

El 29 de julio de 2026 se ejecutaron localmente las dos comprobaciones con
`pip-audit` 2.10.1 y el servicio de vulnerabilidades de PyPI:

| Comprobación | Resultado |
| --- | --- |
| `requirements.txt` | No se encontraron vulnerabilidades conocidas |
| Proyecto del simulador (`pyproject.toml`) | No se encontraron vulnerabilidades conocidas |

Este resultado es una observación puntual. La ejecución programada debe
continuar porque una vulnerabilidad puede publicarse después sin que cambie el
repositorio.
