
from datetime import datetime, timezone
import math
import re
import paho.mqtt.client as mqtt
import requests, json, uuid, time, os
import logging
import sys

# environment variables
API_URL = os.getenv('API_URL', 'http://localhost:5000/lecturas')
MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
API_INGEST_KEY = os.getenv('API_INGEST_KEY')
SECURITY_EVENTS_API_URL = os.getenv(
    'SECURITY_EVENTS_API_URL',
    'http://localhost:5000/api/security-events/ingest',
)
SECURITY_EVENTS_API_KEY = os.getenv('SECURITY_EVENTS_API_KEY')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
MQTT_TLS_ENABLED = os.getenv("MQTT_TLS_ENABLED", "false").lower() == "true"
MQTT_TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT")
MQTT_HEALTH_FILE = os.getenv("MQTT_HEALTH_FILE", "/tmp/mqtt-connected")
# Por defecto escuchamos todos los módulos y tanto esquema viejo como nuevo
# - Esquema viejo: sensor/modulo1/temperatura, sensor/modulo1/humedad, etc.
# - Esquema nuevo: sensor/modulo1/data (JSON con todos los valores)
MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'sensor/#')


class UTCLogFormatter(logging.Formatter):
    """Formatear logs del subscriber en UTC para correlacionarlos con Docker."""
    converter = time.gmtime


def configure_component_logger(name):
    log = logging.getLogger(name)

    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            UTCLogFormatter(
                "%(asctime)sZ %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        log.addHandler(handler)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log.setLevel(getattr(logging, level_name, logging.INFO))
    log.propagate = False
    return log


logger = configure_component_logger("avicola.mqtt")
logger.info(
    "event=logging_initialized component=mqtt_subscriber level=%s",
    logging.getLevelName(logger.level),
)


def set_mqtt_connection_health(connected):
    """Mantener un indicador local que Docker pueda consultar sin credenciales."""
    try:
        if connected:
            with open(MQTT_HEALTH_FILE, "w", encoding="utf-8") as marker:
                marker.write("connected\n")
            return

        try:
            os.remove(MQTT_HEALTH_FILE)
        except FileNotFoundError:
            pass
    except OSError as exc:
        logger.error(
            "event=mqtt_health_marker_failed connected=%s error_type=%s",
            int(bool(connected)),
            type(exc).__name__,
        )


def safe_log_value(value, fallback="-", limit=150):
    """Normalizar valores externos para logs key=value sin saltos de línea."""
    if value is None:
        return fallback

    normalized = " ".join(str(value).split()).replace("=", "_")
    return normalized[:limit] or fallback


SENSOR_LIMITS = {
    "temperatura": (-40.0, 125.0),
    "humedad": (0.0, 100.0),
    "co": (0.0, 500.0),
    "co2": (400.0, 5000.0),
    "amoniaco": (0.0, 100.0),
    "oxigeno": (0.0, 25.0),
}


def normalize_module_from_topic(topic, expected_channel=None):
    """Obtener un módulo M<n> seguro desde sensor/<modulo>/<canal>."""
    parts = topic.split("/")

    if len(parts) != 3 or parts[0] != "sensor":
        return None, "Tópico inválido. Se esperaba sensor/<modulo>/<canal>."

    channel = parts[2]
    if expected_channel and channel != expected_channel:
        return None, (
            f"Canal MQTT inválido. Se esperaba '{expected_channel}' "
            f"y se recibió '{channel}'."
        )

    module_raw = parts[1].strip()
    module_lower = module_raw.lower()

    if module_lower.startswith("modulo"):
        module_num = module_raw[6:]
    elif module_lower.startswith("m"):
        module_num = module_raw[1:]
    else:
        module_num = module_raw

    if not module_num.isdigit() or int(module_num) < 1:
        return None, "Módulo inválido en el tópico MQTT."

    return f"M{int(module_num)}", None


def validate_reading_before_forwarding(reading):
    """Validar y normalizar telemetría antes de enviarla a la API."""
    errors = []
    normalized = {}

    id_lectura = reading.get("id_lectura")
    if not isinstance(id_lectura, str) or not id_lectura.strip():
        errors.append("id_lectura debe ser texto no vacío.")
    elif len(id_lectura.strip()) > 100:
        errors.append("id_lectura no debe exceder 100 caracteres.")
    else:
        normalized["id_lectura"] = id_lectura.strip()

    modulo = reading.get("modulo")
    if not isinstance(modulo, str) or not re.fullmatch(r"M[1-9]\d*", modulo):
        errors.append("modulo debe tener el formato M<n>, por ejemplo M1.")
    else:
        normalized["modulo"] = modulo

    hora = reading.get("hora")
    if not isinstance(hora, str) or not hora.strip():
        errors.append("hora debe ser una fecha ISO 8601 no vacía.")
    else:
        try:
            datetime.fromisoformat(hora.replace("Z", "+00:00"))
            normalized["hora"] = hora
        except ValueError:
            errors.append("hora debe tener formato ISO 8601 válido.")

    for field_name, (min_value, max_value) in SENSOR_LIMITS.items():
        value = reading.get(field_name)

        if isinstance(value, bool):
            errors.append(f"{field_name} debe ser numérico, no booleano.")
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            errors.append(f"{field_name} debe ser numérico.")
            continue

        if not math.isfinite(numeric_value):
            errors.append(f"{field_name} debe ser un número finito.")
            continue

        if numeric_value < min_value or numeric_value > max_value:
            errors.append(
                f"{field_name} fuera de rango permitido "
                f"({min_value} a {max_value})."
            )
            continue

        normalized[field_name] = numeric_value

    if errors:
        return None, errors

    return normalized, []

def build_api_headers():
    headers = {"Content-Type": "application/json"}

    if API_INGEST_KEY:
        headers["X-Ingest-Key"] = API_INGEST_KEY

    return headers


def build_security_events_headers():
    return {
        "Content-Type": "application/json",
        "X-Security-Events-Key": SECURITY_EVENTS_API_KEY or "",
    }


def report_security_event(
    event_type,
    reason,
    module_id=None,
    topic=None,
    severity="warning",
):
    """Enviar un evento técnico a la API sin interrumpir telemetría."""

    if not SECURITY_EVENTS_API_URL or not SECURITY_EVENTS_API_KEY:
        logger.error(
            "event=security_event_report_failed reason=missing_configuration "
            "security_event_type=%s",
            safe_log_value(event_type, limit=100),
        )
        return False

    event_payload = {
        "event_type": safe_log_value(event_type, limit=100),
        "severity": safe_log_value(severity, limit=20).lower(),
        "module": safe_log_value(module_id, limit=50) if module_id else None,
        "reason": safe_log_value(reason, limit=100),
        "topic": safe_log_value(topic, limit=150) if topic else None,
    }

    try:
        response = requests.post(
            SECURITY_EVENTS_API_URL,
            json=event_payload,
            headers=build_security_events_headers(),
            timeout=3,
        )

        if response.status_code == 201:
            logger.debug(
                "event=security_event_reported security_event_type=%s",
                event_payload["event_type"],
            )
            return True

        logger.warning(
            "event=security_event_report_failed reason=api_http_error "
            "security_event_type=%s http_status=%s",
            event_payload["event_type"],
            response.status_code,
        )
        return False

    except requests.exceptions.ConnectionError:
        logger.warning(
            "event=security_event_report_failed reason=api_connection "
            "security_event_type=%s",
            event_payload["event_type"],
        )
        return False

    except requests.exceptions.Timeout:
        logger.warning(
            "event=security_event_report_failed reason=api_timeout "
            "security_event_type=%s",
            event_payload["event_type"],
        )
        return False

    except Exception:
        logger.exception(
            "event=security_event_report_failed reason=unexpected_error "
            "security_event_type=%s",
            event_payload["event_type"],
        )
        return False


current_readings = {}
last_reading_time = None
node_states = {}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def log_mqtt_rejection(event, reason, topic, module_id=None):
    logger.warning(
        "event=%s reason=%s module=%s topic=%s",
        safe_log_value(event, limit=64),
        safe_log_value(reason, limit=80),
        safe_log_value(module_id, limit=32),
        safe_log_value(topic, limit=150),
    )
    report_security_event(
        event_type=event,
        reason=reason,
        module_id=module_id,
        topic=topic,
        severity="warning",
    )


def handle_node_status(topic, payload_text, retained):
    module_id, topic_error = normalize_module_from_topic(topic, "status")

    if topic_error:
        log_mqtt_rejection("node_status_rejected", "invalid_topic", topic)
        return

    status = payload_text.strip().lower()
    if status not in {"online", "offline"}:
        log_mqtt_rejection(
            "node_status_rejected",
            "invalid_status",
            topic,
            module_id,
        )
        return

    observed_at = utc_now_iso()
    node = node_states.setdefault(module_id, {})
    node.update({
        "status": status,
        "last_status": observed_at,
        "last_seen": observed_at,
    })

    logger.info(
        "event=node_status_changed module=%s status=%s retained=%s",
        safe_log_value(module_id, limit=32),
        safe_log_value(status, limit=16),
        int(bool(retained)),
    )


def handle_node_heartbeat(topic, payload_text):
    module_id, topic_error = normalize_module_from_topic(topic, "heartbeat")

    if topic_error:
        log_mqtt_rejection("node_heartbeat_rejected", "invalid_topic", topic)
        return

    try:
        heartbeat = json.loads(payload_text)
    except json.JSONDecodeError:
        log_mqtt_rejection(
            "node_heartbeat_rejected",
            "invalid_json",
            topic,
            module_id,
        )
        return

    if not isinstance(heartbeat, dict):
        log_mqtt_rejection(
            "node_heartbeat_rejected",
            "invalid_shape",
            topic,
            module_id,
        )
        return

    timestamp = heartbeat.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        log_mqtt_rejection(
            "node_heartbeat_rejected",
            "missing_timestamp",
            topic,
            module_id,
        )
        return

    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        log_mqtt_rejection(
            "node_heartbeat_rejected",
            "invalid_timestamp",
            topic,
            module_id,
        )
        return

    uptime_seconds = heartbeat.get("uptime_seconds")
    if uptime_seconds is not None:
        if isinstance(uptime_seconds, bool):
            log_mqtt_rejection(
                "node_heartbeat_rejected",
                "invalid_uptime_type",
                topic,
                module_id,
            )
            return

        try:
            uptime_seconds = float(uptime_seconds)
        except (TypeError, ValueError):
            log_mqtt_rejection(
                "node_heartbeat_rejected",
                "invalid_uptime_type",
                topic,
                module_id,
            )
            return

        if not math.isfinite(uptime_seconds) or uptime_seconds < 0:
            log_mqtt_rejection(
                "node_heartbeat_rejected",
                "invalid_uptime",
                topic,
                module_id,
            )
            return

    observed_at = utc_now_iso()
    node = node_states.setdefault(module_id, {})
    node.update({
        "last_heartbeat": observed_at,
        "last_seen": observed_at,
    })

    logger.info(
        "event=node_heartbeat module=%s uptime_seconds=%s",
        safe_log_value(module_id, limit=32),
        safe_log_value(uptime_seconds, limit=32),
    )


def on_connect(client, userdata, connect_flags, reason_code, properties):
    if reason_code == 0:
        client.subscribe(MQTT_TOPIC, qos=1)
        set_mqtt_connection_health(True)
        logger.info(
            "event=mqtt_connected broker=%s port=%s topic=%s tls_enabled=%s",
            safe_log_value(MQTT_BROKER, limit=100),
            MQTT_PORT,
            safe_log_value(MQTT_TOPIC, limit=150),
            MQTT_TLS_ENABLED,
        )
    else:
        set_mqtt_connection_health(False)
        logger.error(
            "event=mqtt_connection_failed result_code=%s broker=%s port=%s",
            reason_code,
            safe_log_value(MQTT_BROKER, limit=100),
            MQTT_PORT,
        )


def on_disconnect(
    client,
    userdata,
    disconnect_flags,
    reason_code,
    properties,
):
    set_mqtt_connection_health(False)
    logger.warning(
        "event=mqtt_disconnected result_code=%s broker=%s port=%s",
        reason_code,
        safe_log_value(MQTT_BROKER, limit=100),
        MQTT_PORT,
    )


def on_message(client, userdata, message):
    try:
        topic = message.topic
        payload_text = message.payload.decode(errors="replace")
        if topic.endswith("/status"):
            handle_node_status(topic, payload_text, message.retain)
            return

        if topic.endswith("/heartbeat"):
            handle_node_heartbeat(topic, payload_text)
            return
        # ---------------------------------------------
        # 1) Intentar interpretar como JSON (nuevo firmware)
        #    Topic esperado: sensor/moduloX/data
        # ---------------------------------------------
        try:
            data = json.loads(payload_text)

            if isinstance(data, dict):
                module_id, topic_error = normalize_module_from_topic(topic, "data")

                if topic_error:
                    log_mqtt_rejection(
                        "telemetry_rejected",
                        "invalid_topic",
                        topic,
                    )
                    return

                current_time = datetime.now()

                lectura_json = {
                    "id_lectura": data.get("id_lectura") or str(uuid.uuid4()),
                    "modulo": module_id,
                    "hora": data.get("hora") or current_time.isoformat(),
                    "temperatura": data.get("temp", data.get("temperatura")),
                    "humedad": data.get("hum", data.get("humedad")),
                    "co": data.get("co"),
                    "co2": data.get("co2"),
                    "amoniaco": data.get("nh3", data.get("amoniaco")),
                    "oxigeno": data.get("o2", data.get("oxigeno")),
                }

                validated_reading, validation_errors = (
                    validate_reading_before_forwarding(lectura_json)
                )

                if validation_errors:
                    logger.warning(
                        "event=telemetry_rejected source=json reason=validation_failed "
                        "module=%s reading_id=%s error_count=%s topic=%s",
                        safe_log_value(module_id, limit=32),
                        safe_log_value(lectura_json.get("id_lectura")),
                        len(validation_errors),
                        safe_log_value(topic, limit=150),
                    )
                    report_security_event(
                        event_type="telemetry_rejected",
                        reason="validation_failed",
                        module_id=module_id,
                        topic=topic,
                        severity="warning",
                    )
                    return

                logger.info(
                    "event=telemetry_validated source=json module=%s reading_id=%s topic=%s",
                    safe_log_value(validated_reading["modulo"], limit=32),
                    safe_log_value(validated_reading["id_lectura"]),
                    safe_log_value(topic, limit=150),
                )

                try:
                    response = requests.post(
                        API_URL,
                        json=validated_reading,
                        headers=build_api_headers(),
                        timeout=5
                    )

                    if response.status_code in (200, 201):
                        logger.info(
                            "event=telemetry_forwarded source=json module=%s "
                            "reading_id=%s http_status=%s",
                            safe_log_value(validated_reading["modulo"], limit=32),
                            safe_log_value(validated_reading["id_lectura"]),
                            response.status_code,
                        )
                    else:
                        logger.error(
                            "event=telemetry_forward_failed source=json reason=api_http_error "
                            "module=%s reading_id=%s http_status=%s",
                            safe_log_value(validated_reading["modulo"], limit=32),
                            safe_log_value(validated_reading["id_lectura"]),
                            response.status_code,
                        )

                except requests.exceptions.ConnectionError:
                    logger.error(
                        "event=telemetry_forward_failed source=json reason=api_connection "
                        "module=%s reading_id=%s",
                        safe_log_value(validated_reading["modulo"], limit=32),
                        safe_log_value(validated_reading["id_lectura"]),
                    )

                except requests.exceptions.Timeout:
                    logger.error(
                        "event=telemetry_forward_failed source=json reason=api_timeout "
                        "module=%s reading_id=%s",
                        safe_log_value(validated_reading["modulo"], limit=32),
                        safe_log_value(validated_reading["id_lectura"]),
                    )

                except Exception:
                    logger.exception(
                        "event=telemetry_forward_failed source=json reason=unexpected_error "
                        "module=%s reading_id=%s",
                        safe_log_value(validated_reading["modulo"], limit=32),
                        safe_log_value(validated_reading["id_lectura"]),
                    )

                return

        except json.JSONDecodeError:
            pass

        # ---------------------------------------------
        # 2) Flujo antiguo: payload numérico por sensor
        #    Topic: sensor/modulo1/temperatura, etc.
        # ---------------------------------------------
        try:
            value = float(payload_text)
        except ValueError:
            log_mqtt_rejection(
                "telemetry_rejected",
                "unsupported_payload",
                topic,
            )
            return

        parts = topic.split("/")
        if len(parts) < 3:
            log_mqtt_rejection(
                "telemetry_rejected",
                "invalid_legacy_topic",
                topic,
            )
            return

        module_raw = parts[1]                      # 'modulo1'
        sensor_type = parts[2]
        module_num = module_raw.replace('modulo', '')  # '1'
        module_id = f'M{module_num}'  # Formato M1, M2, etc.

        current_time = datetime.now()
        # ID único que incluye módulo y timestamp para evitar conflictos
        reading_id = f"{module_id}_{int(current_time.timestamp()) // 1}"

        if reading_id not in current_readings:
            current_readings[reading_id] = {
                'id_lectura': str(uuid.uuid4()),
                'modulo': module_id,  # Usar el módulo extraído del topic
                'hora': current_time.isoformat(),
                'temperatura': 0.0,
                'humedad': 0.0,
                'co': 0.0,
                'co2': 0.0,
                'amoniaco': 0.0,
                'oxigeno': None,
                'received_sensors': set()  # Tipos de sensor recibidos
            }

        # Actualizar el valor correspondiente y aumentar contador
        reading = current_readings[reading_id]
        if sensor_type == 'temperatura':
            reading['temperatura'] = value
            reading['received_sensors'].add(sensor_type)
        elif sensor_type == 'humedad':
            reading['humedad'] = value
            reading['received_sensors'].add(sensor_type)
        elif sensor_type == 'co':
            reading['co'] = value
            reading['received_sensors'].add(sensor_type)
        elif sensor_type == 'nh3':
            reading['amoniaco'] = value
            reading['received_sensors'].add(sensor_type)
        elif sensor_type == 'co2':
            reading['co2'] = value
            reading['received_sensors'].add(sensor_type)
        elif sensor_type == "o2":
            reading["oxigeno"] = value
            reading["received_sensors"].add(sensor_type)
        if len(reading["received_sensors"]) == 6:
            payload_to_validate = {
                key: reading[key]
                for key in (
                    "id_lectura",
                    "modulo",
                    "hora",
                    "temperatura",
                    "humedad",
                    "co",
                    "co2",
                    "amoniaco",
                    "oxigeno",
                )
            }

            validated_reading, validation_errors = (
                validate_reading_before_forwarding(payload_to_validate)
            )

            if validation_errors:
                logger.warning(
                    "event=telemetry_rejected source=legacy reason=validation_failed "
                    "module=%s reading_id=%s error_count=%s topic=%s",
                    safe_log_value(reading.get("modulo"), limit=32),
                    safe_log_value(reading.get("id_lectura")),
                    len(validation_errors),
                    safe_log_value(topic, limit=150),
                )
                report_security_event(
                    event_type="telemetry_rejected",
                    reason="validation_failed",
                    module_id=reading.get("modulo"),
                    topic=topic,
                    severity="warning",
                )

                if reading_id in current_readings:
                    del current_readings[reading_id]

                return

            logger.info(
                "event=telemetry_validated source=legacy module=%s reading_id=%s topic=%s",
                safe_log_value(validated_reading["modulo"], limit=32),
                safe_log_value(validated_reading["id_lectura"]),
                safe_log_value(topic, limit=150),
            )

            try:
                response = requests.post(
                    API_URL,
                    json=validated_reading,
                    headers=build_api_headers(),
                    timeout=5,
                )

                if response.status_code in (200, 201):
                    logger.info(
                        "event=telemetry_forwarded source=legacy module=%s "
                        "reading_id=%s http_status=%s",
                        safe_log_value(validated_reading["modulo"], limit=32),
                        safe_log_value(validated_reading["id_lectura"]),
                        response.status_code,
                    )

                    if reading_id in current_readings:
                        del current_readings[reading_id]
                        logger.info(
                            "event=legacy_buffer_entry_removed reading_id=%s reason=forwarded",
                            safe_log_value(reading_id),
                        )
                else:
                    logger.error(
                        "event=telemetry_forward_failed source=legacy reason=api_http_error "
                        "module=%s reading_id=%s http_status=%s",
                        safe_log_value(validated_reading["modulo"], limit=32),
                        safe_log_value(validated_reading["id_lectura"]),
                        response.status_code,
                    )

            except requests.exceptions.ConnectionError:
                logger.error(
                    "event=telemetry_forward_failed source=legacy reason=api_connection "
                    "module=%s reading_id=%s",
                    safe_log_value(validated_reading["modulo"], limit=32),
                    safe_log_value(validated_reading["id_lectura"]),
                )

            except requests.exceptions.Timeout:
                logger.error(
                    "event=telemetry_forward_failed source=legacy reason=api_timeout "
                    "module=%s reading_id=%s",
                    safe_log_value(validated_reading["modulo"], limit=32),
                    safe_log_value(validated_reading["id_lectura"]),
                )

            except Exception:
                logger.exception(
                    "event=telemetry_forward_failed source=legacy reason=unexpected_error "
                    "module=%s reading_id=%s",
                    safe_log_value(validated_reading["modulo"], limit=32),
                    safe_log_value(validated_reading["id_lectura"]),
                )

        # ✅ MEJORADO: Limpiar lecturas antiguas (> 30 segundos)
        cleanup_old_readings()

    except Exception:
        logger.exception(
            "event=mqtt_message_processing_failed topic=%s",
            safe_log_value(locals().get("topic"), limit=150),
        )

def cleanup_old_readings():
    """Clear old readings from buffer"""
    current_time = datetime.now().timestamp()
    keys_to_delete = []
    
    for reading_id, reading_data in current_readings.items():
        # El reading_id está basado en timestamp, calcular antigüedad
        reading_timestamp = int(reading_id.split('_')[1])
        if current_time - reading_timestamp > 30:  # Más de 30 segundos
            keys_to_delete.append(reading_id)
    
    for key in keys_to_delete:
        del current_readings[key]
        logger.info(
            "event=legacy_buffer_entry_removed reading_id=%s reason=expired",
            safe_log_value(key),
        )

def start():
    set_mqtt_connection_health(False)
    logger.info(
        "event=service_started component=mqtt_subscriber broker=%s port=%s "
        "topic=%s tls_enabled=%s api_url_configured=%s",
        safe_log_value(MQTT_BROKER, limit=100),
        MQTT_PORT,
        safe_log_value(MQTT_TOPIC, limit=150),
        MQTT_TLS_ENABLED,
        bool(API_URL),
    )

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        if MQTT_USERNAME and MQTT_PASSWORD:
            client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

        if MQTT_TLS_ENABLED:
            if not MQTT_TLS_CA_CERT:
                raise RuntimeError(
                    "MQTT_TLS_CA_CERT no está definida para conexión MQTTS."
                )

            client.tls_set(ca_certs=MQTT_TLS_CA_CERT)
            client.tls_insecure_set(False)

        client.on_connect = on_connect
        client.on_disconnect = on_disconnect
        client.on_message = on_message

        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()

    except ConnectionRefusedError:
        logger.error(
            "event=mqtt_connection_refused broker=%s port=%s",
            safe_log_value(MQTT_BROKER, limit=100),
            MQTT_PORT,
        )
    except Exception:
        logger.exception(
            "event=mqtt_startup_failed broker=%s port=%s",
            safe_log_value(MQTT_BROKER, limit=100),
            MQTT_PORT,
        )
    finally:
        set_mqtt_connection_health(False)


def stop():
    set_mqtt_connection_health(False)
    logger.info("event=service_stopping component=mqtt_subscriber")

if __name__ == "__main__":
    start()
