
from datetime import datetime, timezone
import math
import re
import paho.mqtt.client as mqtt
import requests, json, uuid, time, os

# environment variables
API_URL = os.getenv('API_URL', 'http://localhost:5000/lecturas')
MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
API_INGEST_KEY = os.getenv('API_INGEST_KEY')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
MQTT_TLS_ENABLED = os.getenv("MQTT_TLS_ENABLED", "false").lower() == "true"
MQTT_TLS_CA_CERT = os.getenv("MQTT_TLS_CA_CERT")
# Por defecto escuchamos todos los módulos y tanto esquema viejo como nuevo
# - Esquema viejo: sensor/modulo1/temperatura, sensor/modulo1/humedad, etc.
# - Esquema nuevo: sensor/modulo1/data (JSON con todos los valores)
MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'sensor/#')


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


current_readings = {}
last_reading_time = None
node_states = {}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def handle_node_status(topic, payload_text, retained):
    module_id, topic_error = normalize_module_from_topic(topic, "status")

    if topic_error:
        print(f"[DROP] {topic_error} Topic: {topic}")
        return

    status = payload_text.strip().lower()
    if status not in {"online", "offline"}:
        print(
            f"[DROP] Estado inválido en {topic}. "
            "Solo se permite online u offline."
        )
        return

    observed_at = utc_now_iso()
    node = node_states.setdefault(module_id, {})
    node.update({
        "status": status,
        "last_status": observed_at,
        "last_seen": observed_at,
    })

    print(
        f"[NODE] {module_id} status={status} "
        f"retained={retained} observed_at={observed_at}"
    )


def handle_node_heartbeat(topic, payload_text):
    module_id, topic_error = normalize_module_from_topic(topic, "heartbeat")

    if topic_error:
        print(f"[DROP] {topic_error} Topic: {topic}")
        return

    try:
        heartbeat = json.loads(payload_text)
    except json.JSONDecodeError:
        print(f"[DROP] Heartbeat inválido en {topic}: JSON no válido.")
        return

    if not isinstance(heartbeat, dict):
        print(f"[DROP] Heartbeat inválido en {topic}: se esperaba un objeto JSON.")
        return

    timestamp = heartbeat.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp.strip():
        print(f"[DROP] Heartbeat inválido en {topic}: timestamp requerido.")
        return

    try:
        datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        print(f"[DROP] Heartbeat inválido en {topic}: timestamp ISO 8601 inválido.")
        return

    uptime_seconds = heartbeat.get("uptime_seconds")
    if uptime_seconds is not None:
        if isinstance(uptime_seconds, bool):
            print(f"[DROP] Heartbeat inválido en {topic}: uptime_seconds no puede ser booleano.")
            return

        try:
            uptime_seconds = float(uptime_seconds)
        except (TypeError, ValueError):
            print(f"[DROP] Heartbeat inválido en {topic}: uptime_seconds debe ser numérico.")
            return

        if not math.isfinite(uptime_seconds) or uptime_seconds < 0:
            print(f"[DROP] Heartbeat inválido en {topic}: uptime_seconds inválido.")
            return

    observed_at = utc_now_iso()
    node = node_states.setdefault(module_id, {})
    node.update({
        "last_heartbeat": observed_at,
        "last_seen": observed_at,
    })

    print(
        f"[HEARTBEAT] {module_id} timestamp={timestamp} "
        f"uptime_seconds={uptime_seconds} observed_at={observed_at}"
    )

def on_connect(client, userdata, flags, rc):
    print(f"Conected to MQTT broker: {rc}")
    if rc == 0:
        client.subscribe(MQTT_TOPIC, qos=1)
        print("Topic:  ", MQTT_TOPIC)
    else:
        print(f" Error de conexión MQTT: {rc}")

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
                    print(f"[DROP] {topic_error} Topic: {topic}")
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
                    print(
                        f"[DROP] Payload rechazado en {topic}: "
                        f"{'; '.join(validation_errors)}"
                    )
                    return

                print(f"[JSON] Payload validado desde {topic}: {validated_reading}")

                try:
                    response = requests.post(
                        API_URL,
                        json=validated_reading,
                        headers=build_api_headers(),
                        timeout=5
                    )

                    if response.status_code in (200, 201):
                        print(
                            "Lectura JSON enviada a BD: "
                            f"{validated_reading['id_lectura']}"
                        )
                    else:
                        print(
                            f"Error API (HTTP {response.status_code}): "
                            f"{response.text}"
                        )

                except requests.exceptions.ConnectionError:
                    print("ERROR: No se pudo conectar a la API en http://localhost:5000")

                except requests.exceptions.Timeout:
                    print("ERROR: Timeout al conectar con la API")

                except Exception as e:
                    print(f"ERROR enviando a API (JSON): {e}")

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
            # Ni JSON ni número válido
            return

        parts = topic.split("/")
        if len(parts) < 3:
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
                print(
                    f"[DROP] Lectura antigua rechazada en {topic}: "
                    f"{'; '.join(validation_errors)}"
                )

                if reading_id in current_readings:
                    del current_readings[reading_id]

                return

            print(f"[LEGACY] Payload validado: {validated_reading}")

            try:
                response = requests.post(
                    API_URL,
                    json=validated_reading,
                    headers=build_api_headers(),
                    timeout=5
                )

                if response.status_code in (200, 201):
                    print(
                        "Lectura antigua enviada a BD: "
                        f"{validated_reading['id_lectura']}"
                    )

                    if reading_id in current_readings:
                        del current_readings[reading_id]
                        print(f"Lectura {reading_id} eliminada del buffer")
                else:
                    print(
                        f"Error API (HTTP {response.status_code}): "
                        f"{response.text}"
                    )

            except requests.exceptions.ConnectionError:
                print("ERROR: No se pudo conectar a la API en http://localhost:5000")

            except requests.exceptions.Timeout:
                print("ERROR: Timeout al conectar con la API")

            except Exception as e:
                print(f"ERROR enviando a API: {e}")

        # ✅ MEJORADO: Limpiar lecturas antiguas (> 30 segundos)
        cleanup_old_readings()

    except Exception:
        # Cualquier error inesperado se ignora para no tumbar el subscriber
        pass

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
        print(f"Deleting old values: {key}")
        del current_readings[key]

def start():
    print(" STARTING MQTT SUBSCRIBER MODULE")
    print(" MQTT CONFIGURATION:")
    print(f"    API_URL: {API_URL}")
    print(f"    MQTT_BROKER: {MQTT_BROKER}")
    print(f"    MQTT_PORT: {MQTT_PORT}")
    print(f"    MQTT_TOPIC: {MQTT_TOPIC}")

    
    try:
        client = mqtt.Client()
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
        client.on_message = on_message

        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        client.loop_forever()
        
    except ConnectionRefusedError:
        print("ERROR: MQTT connection refused")
    except Exception as e:
        print(f"ERROR: MQTT configuration error {e}")

def stop():
    print("🛑 Deteniendo suscriptor MQTT...")

if __name__ == "__main__":
    start()

