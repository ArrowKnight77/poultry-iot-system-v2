import inspect
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt

from api_avicola import mqtt_subscriber


class MQTTDependencyCompatibilityTests(unittest.TestCase):
    def setUp(self):
        logger_patcher = patch.object(mqtt_subscriber, "logger")
        logger_patcher.start()
        self.addCleanup(logger_patcher.stop)

    def test_on_connect_uses_callback_api_v2_signature(self):
        parameter_names = list(
            inspect.signature(mqtt_subscriber.on_connect).parameters
        )

        self.assertEqual(
            parameter_names,
            [
                "client",
                "userdata",
                "connect_flags",
                "reason_code",
                "properties",
            ],
        )

    def test_start_creates_callback_api_v2_client(self):
        client = Mock()

        with (
            patch.object(
                mqtt_subscriber.mqtt,
                "Client",
                return_value=client,
            ) as client_factory,
            patch.multiple(
                mqtt_subscriber,
                MQTT_TLS_ENABLED=False,
                MQTT_USERNAME=None,
                MQTT_PASSWORD=None,
            ),
            patch.object(
                mqtt_subscriber,
                "set_mqtt_connection_health",
            ),
        ):
            mqtt_subscriber.start()

        client_factory.assert_called_once_with(mqtt.CallbackAPIVersion.VERSION2)
        self.assertIs(client.on_connect, mqtt_subscriber.on_connect)
        self.assertIs(client.on_disconnect, mqtt_subscriber.on_disconnect)
        self.assertIs(client.on_message, mqtt_subscriber.on_message)
        client.connect.assert_called_once_with(
            mqtt_subscriber.MQTT_BROKER,
            mqtt_subscriber.MQTT_PORT,
            60,
        )
        client.loop_forever.assert_called_once_with()

    def test_successful_connection_subscribes_with_qos_one(self):
        client = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "mqtt-connected")
            with patch.object(
                mqtt_subscriber,
                "MQTT_HEALTH_FILE",
                marker_path,
            ):
                mqtt_subscriber.on_connect(client, None, {}, 0, None)
                self.assertTrue(os.path.isfile(marker_path))

        client.subscribe.assert_called_once_with(
            mqtt_subscriber.MQTT_TOPIC,
            qos=1,
        )

    def test_failed_connection_removes_health_marker(self):
        client = Mock()

        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "mqtt-connected")
            with open(marker_path, "w", encoding="utf-8") as marker:
                marker.write("connected\n")

            with patch.object(
                mqtt_subscriber,
                "MQTT_HEALTH_FILE",
                marker_path,
            ):
                mqtt_subscriber.on_connect(client, None, {}, 1, None)

            self.assertFalse(os.path.exists(marker_path))
            client.subscribe.assert_not_called()

    def test_on_disconnect_uses_callback_api_v2_and_removes_marker(self):
        parameter_names = list(
            inspect.signature(mqtt_subscriber.on_disconnect).parameters
        )
        self.assertEqual(
            parameter_names,
            [
                "client",
                "userdata",
                "disconnect_flags",
                "reason_code",
                "properties",
            ],
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            marker_path = os.path.join(temp_dir, "mqtt-connected")
            with open(marker_path, "w", encoding="utf-8") as marker:
                marker.write("connected\n")

            with patch.object(
                mqtt_subscriber,
                "MQTT_HEALTH_FILE",
                marker_path,
            ):
                mqtt_subscriber.on_disconnect(Mock(), None, {}, 0, None)

            self.assertFalse(os.path.exists(marker_path))


if __name__ == "__main__":
    unittest.main()
