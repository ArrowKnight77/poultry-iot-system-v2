
from datetime import datetime
import paho.mqtt.client as mqtt
import requests, json, uuid, time, os

# environment variables
API_URL = os.getenv('API_URL', 'http://localhost:5000/lecturas')
MQTT_BROKER = os.getenv('MQTT_BROKER', 'localhost')
API_INGEST_KEY = os.getenv('API_INGEST_KEY')
MQTT_PORT = int(os.getenv('MQTT_PORT', '1883'))
MQTT_USERNAME = os.getenv('MQTT_USERNAME')
MQTT_PASSWORD = os.getenv('MQTT_PASSWORD')
# Por defecto escuchamos todos los módulos y tanto esquema viejo como nuevo
# - Esquema viejo: sensor/modulo1/temperatura, sensor/modulo1/humedad, etc.
# - Esquema nuevo: sensor/modulo1/data (JSON con todos los valores)
MQTT_TOPIC = os.getenv('MQTT_TOPIC', 'sensor/#')

def build_api_headers():
    headers = {"Content-Type": "application/json"}

    if API_INGEST_KEY:
        headers["X-Ingest-Key"] = API_INGEST_KEY

    return headers


current_readings = {}
last_reading_time = None
def on_connect(client, userdata, flags, rc):
    print(f"Conected to MQTT broker: {rc}")
    if rc == 0:
        client.subscribe(MQTT_TOPIC)
        print("Topic:  ", MQTT_TOPIC)
    else:
        print(f" Error de conexión MQTT: {rc}")

def on_message(client, userdata, message):
    try:
        topic = message.topic
        payload_text = message.payload.decode()

        # ---------------------------------------------
        # 1) Intentar interpretar como JSON (nuevo firmware)
        #    Topic esperado: sensor/moduloX/data
        # ---------------------------------------------
        try:
            data = json.loads(payload_text)

            if isinstance(data, dict):
                parts = topic.split("/")
                if len(parts) < 3:
                    return

                module_raw = parts[1].strip()

                if module_raw.lower().startswith("modulo"):
                    module_num = module_raw[6:]
                elif module_raw.lower().startswith("m"):
                    module_num = module_raw[1:]
                else:
                    module_num = module_raw

                module_id = f"M{module_num}"
                current_time = datetime.now()

                lectura_json = {
                    "id_lectura": data.get("id_lectura") or str(uuid.uuid4()),
                    "modulo": data.get("modulo") or module_id,
                    "hora": data.get("hora") or current_time.isoformat(),
                    "temperatura": data.get("temp", data.get("temperatura")),
                    "humedad": data.get("hum", data.get("humedad")),
                    "co": data.get("co"),
                    "co2": data.get("co2"),
                    "amoniaco": data.get("nh3", data.get("amoniaco"))
                }

                print(f"[JSON] Recibido desde {topic}: {lectura_json}")

                try:
                    response = requests.post(
                        API_URL,
                        json=lectura_json,
                        headers=build_api_headers(),
                        timeout=5
                    )

                    if response.status_code in (200, 201):
                        print(f"Lectura JSON enviada a BD: {lectura_json['id_lectura']}")
                    else:
                        print(f"Error API (HTTP {response.status_code}): {response.text}")

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
                'count': 0  # Contador de sensores recibidos
            }

        # Actualizar el valor correspondiente y aumentar contador
        reading = current_readings[reading_id]
        if sensor_type == 'temperatura':
            reading['temperatura'] = value
            reading['count'] += 1
        elif sensor_type == 'humedad':
            reading['humedad'] = value
            reading['count'] += 1
        elif sensor_type == 'co':
            reading['co'] = value
            reading['count'] += 1
        elif sensor_type == 'nh3':
            reading['amoniaco'] = value
            reading['count'] += 1
        elif sensor_type == 'co2':
            reading['co2'] = value
            reading['count'] += 1

        if reading['count'] >= 5:
            print(f"Sending to API: \n{reading}")
            
            # Enviar a la API via POST
            try:
                response = requests.post(API_URL,json=lectura_json,headers=build_api_headers(),timeout=5)
                if response.status_code == 200:
                    print(f"✅ Lectura ENVIADA EXITOSAMENTE a BD: {reading['id_lectura']}")
                    print(f"   📊 Datos: Temp={reading['temperatura']}°C, Hum={reading['humedad']}%, NH3={reading['amoniaco']}ppm, CO2={reading['co2']}ppm")
                    
                    # ✅ LIMPIAR: Eliminar lectura procesada
                    if reading_id in current_readings:
                        del current_readings[reading_id]
                        print(f"🧹 Lectura {reading_id} eliminada del buffer")
                else:
                    print(f"❌ Error API (HTTP {response.status_code}): {response.text}")
            except requests.exceptions.ConnectionError:
                print("❌ ERROR: No se pudo conectar a la API en http://localhost:5000")
                print("   💡 Verifica que la API esté ejecutándose")
            except requests.exceptions.Timeout:
                print("❌ ERROR: Timeout al conectar con la API")
            except Exception as e:
                print(f"❌ ERROR enviando a API: {e}")

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

