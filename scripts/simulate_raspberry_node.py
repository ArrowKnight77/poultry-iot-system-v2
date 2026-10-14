#!/usr/bin/env python3
"""Simulador de un nodo Raspberry Pi con telemetría, heartbeat y LWT."""

import json
import math
import os
import random
import re
import signal
import time
from datetime import datetime, timezone
from threading import Event

import paho.mqtt.client as mqtt


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def positive_float_env(name, default):
    value = float(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_module(raw_module):
    match = re.fullmatch(
        r"(?:modulo|m)?([1-9]\d*)",
        raw_module.strip().lower()
    )

    if not match:
        raise ValueError(
            "SIMULATOR_MODULE debe tener formato modulo1, m1 o 1."
        )

    number = match.group(1)
    return f"modulo{number}", f"M{number}"


MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8883"))
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TLS_ENABLED = env_bool("MQTT_TLS_ENABLED", True)
MQTT_TLS_CA_CERT = os.getenv(
    "MQTT_TLS_CA_CERT",
    "mosquitto/certs/broker/ca.crt"
)

MODULE_TOPIC, MODULE_API = normalize_module(
    os.getenv("SIMULATOR_MODULE", "modulo1")
)

TELEMETRY_INTERVAL = positive_float_env(
    "SIMULATOR_TELEMETRY_INTERVAL_SECONDS",
    10
)
HEARTBEAT_INTERVAL = positive_float_env(
    "SIMULATOR_HEARTBEAT_INTERVAL_SECONDS",
    30
)

BASE_TOPIC = f"sensor/{MODULE_TOPIC}"
DATA_TOPIC = f"{BASE_TOPIC}/data"
STATUS_TOPIC = f"{BASE_TOPIC}/status"
HEARTBEAT_TOPIC = f"{BASE_TOPIC}/heartbeat"

CLIENT_ID = f"raspberry-sim-{MODULE_TOPIC}-{os.getpid()}"

connected = Event()
running = True
started_at = time.monotonic()

client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    clean_session=True,
    protocol=mqtt.MQTTv311,
)


def publish_text(topic, payload, qos=1, retain=False):
    result = client.publish(topic, payload, qos=qos, retain=retain)

    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        print(f"[SIM][ERROR] No se pudo publicar en {topic}. rc={result.rc}")
        return

    print(
        f"[SIM] Publicado topic={topic} payload={payload} "
        f"qos={qos} retain={retain}"
    )


def publish_json(topic, payload, qos=1, retain=False):
    publish_text(
        topic,
        json.dumps(payload, separators=(",", ":")),
        qos=qos,
        retain=retain
    )


def build_telemetry():
    elapsed = time.monotonic() - started_at

    temperature = 28.0 + 0.8 * math.sin(elapsed / 45) + random.uniform(-0.15, 0.15)
    humidity = 65.0 + 3.0 * math.sin(elapsed / 60) + random.uniform(-0.4, 0.4)
    carbon_monoxide = 2.0 + random.uniform(-0.2, 0.2)
    carbon_dioxide = 780.0 + 60.0 * math.sin(elapsed / 50) + random.uniform(-10, 10)
    ammonia = 4.0 + random.uniform(-0.25, 0.25)
    oxygen = 20.8 + random.uniform(-0.15, 0.15)

    return {
        "id_lectura": f"sim-{MODULE_API}-{int(time.time() * 1000)}",
        "hora": utc_now_iso(),
        "temp": round(temperature, 1),
        "hum": round(humidity, 1),
        "co": round(carbon_monoxide, 2),
        "co2": round(carbon_dioxide, 0),
        "nh3": round(ammonia, 2),
        "o2": round(oxygen, 2),
    }


def build_heartbeat():
    return {
        "timestamp": utc_now_iso(),
        "uptime_seconds": round(time.monotonic() - started_at, 1),
    }


def on_connect(mqtt_client, userdata, connect_flags, reason_code, properties):
    if reason_code != 0:
        print(
            f"[SIM][ERROR] Conexión MQTT rechazada. "
            f"reason_code={reason_code}"
        )
        return

    connected.set()
    print(
        f"[SIM] Conectado al broker MQTTS: "
        f"{MQTT_BROKER}:{MQTT_PORT} como {CLIENT_ID}"
    )

    publish_text(STATUS_TOPIC, "online", qos=1, retain=True)


def on_disconnect(
    mqtt_client,
    userdata,
    disconnect_flags,
    reason_code,
    properties
):
    connected.clear()
    print(f"[SIM] Desconectado del broker. reason_code={reason_code}")


def request_shutdown(signum, frame):
    global running
    print(f"[SIM] Señal {signum} recibida. Cerrando nodo de forma controlada.")
    running = False


def main():
    global running

    if not MQTT_USERNAME or not MQTT_PASSWORD:
        raise RuntimeError(
            "MQTT_USERNAME y MQTT_PASSWORD son requeridos para el simulador."
        )

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)

    # El broker publicará offline si este proceso termina abruptamente.
    client.will_set(
        STATUS_TOPIC,
        payload="offline",
        qos=1,
        retain=True
    )

    if MQTT_TLS_ENABLED:
        if not MQTT_TLS_CA_CERT:
            raise RuntimeError(
                "MQTT_TLS_CA_CERT es requerido cuando MQTT_TLS_ENABLED=true."
            )

        client.tls_set(ca_certs=MQTT_TLS_CA_CERT)
        client.tls_insecure_set(False)

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    print("[SIM] Iniciando simulador Raspberry Pi")
    print(f"[SIM] Módulo: {MODULE_API}")
    print(f"[SIM] Data topic: {DATA_TOPIC}")
    print(f"[SIM] Status topic: {STATUS_TOPIC}")
    print(f"[SIM] Heartbeat topic: {HEARTBEAT_TOPIC}")

    client.connect_async(MQTT_BROKER, MQTT_PORT, keepalive=30)
    client.loop_start()

    next_telemetry = 0.0
    next_heartbeat = 0.0

    try:
        while running:
            now = time.monotonic()

            if connected.is_set():
                if now >= next_heartbeat:
                    publish_json(
                        HEARTBEAT_TOPIC,
                        build_heartbeat(),
                        qos=1,
                        retain=False
                    )
                    next_heartbeat = now + HEARTBEAT_INTERVAL

                if now >= next_telemetry:
                    publish_json(
                        DATA_TOPIC,
                        build_telemetry(),
                        qos=1,
                        retain=False
                    )
                    next_telemetry = now + TELEMETRY_INTERVAL

            time.sleep(0.25)

    finally:
        if connected.is_set():
            # En apagado normal se publica offline explícitamente.
            publish_text(STATUS_TOPIC, "offline", qos=1, retain=True)
            time.sleep(0.5)
            client.disconnect()

        client.loop_stop()
        print("[SIM] Simulador detenido.")


if __name__ == "__main__":
    main()
