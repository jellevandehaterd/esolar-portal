#!/usr/bin/env bash

set -e

CONFIG_PATH=/data/options.json
CONFIG_INI=/etc/solar.ini

URL=$(jq --raw-output ".url" $CONFIG_PATH)
USERNAME=$(jq --raw-output ".username" $CONFIG_PATH)
PASSWORD=$(jq --raw-output ".password" $CONFIG_PATH)
DELAY=$(jq --raw-output ".delay" $CONFIG_PATH)
VERBOSE=$(jq --raw-output ".verbose" $CONFIG_PATH)

MQTT_BROKER_URL=$(jq --raw-output ".mqtt.broker_url" $CONFIG_PATH)
MQTT_USERNAME=$(jq --raw-output ".mqtt.username" $CONFIG_PATH)
MQTT_PASSWORD=$(jq --raw-output ".mqtt.password" $CONFIG_PATH)
MQTT_TOPIC_PREFIX=$(jq --raw-output ".mqtt.topic_prefix" $CONFIG_PATH)


DEFAULT_CONFIG="
[default]
url = ${URL}
username = ${USERNAME}
password = ${PASSWORD}
delay = ${DELAY}
verbose = ${VERBOSE}
"

MQTT_CONFIG="
[mqtt]
broker_url = ${MQTT_BROKER_URL}
username = ${MQTT_USERNAME}
password = ${MQTT_PASSWORD}
topic_prefix = ${MQTT_TOPIC_PREFIX}
"

echo "${DEFAULT_CONFIG}" >> /etc/solar.ini
echo "${MQTT_CONFIG}" >> /etc/solar.ini

python3 /usr/local/bin/solarportal.py ${CONFIG_INI} --delay ${DELAY} --verbose ${VERBOSE} < /dev/null