import inspect
import unittest
from unittest.mock import Mock, patch

import paho.mqtt.client as mqtt

from api_avicola import mqtt_subscriber


class MQTTDependencyCompatibilityTests(unittest.TestCase):
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
        ):
            mqtt_subscriber.start()

        client_factory.assert_called_once_with(mqtt.CallbackAPIVersion.VERSION2)
        self.assertIs(client.on_connect, mqtt_subscriber.on_connect)
        self.assertIs(client.on_message, mqtt_subscriber.on_message)
        client.connect.assert_called_once_with(
            mqtt_subscriber.MQTT_BROKER,
            mqtt_subscriber.MQTT_PORT,
            60,
        )
        client.loop_forever.assert_called_once_with()

    def test_successful_connection_subscribes_with_qos_one(self):
        client = Mock()

        mqtt_subscriber.on_connect(client, None, {}, 0, None)

        client.subscribe.assert_called_once_with(
            mqtt_subscriber.MQTT_TOPIC,
            qos=1,
        )


if __name__ == "__main__":
    unittest.main()
