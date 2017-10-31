import logging
from datetime import timedelta

import asyncio
import aiohttp
import async_timeout
import json
import voluptuous as vol
from bs4 import BeautifulSoup
from collections import OrderedDict
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD, CONF_URL,
    CONF_RESOURCES,
    TEMP_CELSIUS,
    CONF_HOST, CONF_MONITORED_VARIABLES,
    CONF_NAME)
from homeassistant.exceptions import PlatformNotReady
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import location, distance
from urllib.parse import unquote, parse_qsl, urlsplit
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)


REQUEST_TIMEOUT = 5  # In seconds; argument to asyncio.timeout
SCAN_INTERVAL = timedelta(minutes=5)  # Timely, and doesn't suffocate the API

DOMAIN = 'solarportal'

SENSOR_PREFIX = 'Solar '
SENSOR_TYPES = {
    'actualpower': ['Actual Power', 'Watt', 'mdi:weather-sunny'],
    'energytoday': ['Energy Today', 'kWh', 'mdi:flash'],
    'energytotal': ['Energy Total', 'kWh', 'mdi:flash'],
    'incometoday': ['Income Today', 'EUR', 'mdi:cash-100'],
    'incometotal': ['Income Total', 'EUR', 'mdi:cash-100'],
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_URL): cv.url,
    vol.Required(CONF_USERNAME): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
    vol.Required(CONF_RESOURCES, default=[]):
        vol.All(cv.ensure_list, [vol.In(SENSOR_TYPES)]),
})

LOGIN_FIELDS = {
    'viewstate_input': '__VIEWSTATE',
    'password_input': 'ctl00$cc$Login1$Password',
    'username_input': 'ctl00$cc$Login1$UserName',
    'remember_me_input': 'ctl00$cc$Login1$RememberMe',
    'login_value_input': 'ctl00$cc$Login1$LoginButton',
}

if not hasattr(cv, 'positive_float'):
    cv.positive_float = vol.All(vol.Coerce(float), vol.Range(min=0))

ATTR_DATA = 'd'
ATTR_DATAPOINT_LIST = 'dp'
ATTR_DATA_STRING = 'd_string'
ATTR_TYPE = '__type'
ATTR_PASSKEY = 'passkey'
ATTR_FLAT = 'flat'
ATTR_PAGE_ID = 'page'
ATTR_PAGE_SIZE = 'pageSize'
ATTR_PATHS = 'paths'
ATTR_USER_UID = 'useruid'
ATTR_PARAMS = 'para'
ATTR_ADDRESS = "Address"
ATTR_AVERAGE_RUNNING_TIME = "AverageRunningTime"
ATTR_CITY = "City"
ATTR_COMMENT = "Comment"
ATTR_COMMISSION_DATE = "CommissionDate"
ATTR_COUNTRY = "Country"
ATTR_CURRENCY = "Currency"
ATTR_CURRENCY_NAME = "CurrencyName"
ATTR_DISTRIBUTOR_CODE = "DistributorCode"
ATTR_ENERGY_MONTH = "EMonth"
ATTR_ENERGY_TODAY = "EToday"
ATTR_ENERGY_TOTAL = "ETotal"
ATTR_EFFICIENCY = "Efficiency"
ATTR_EXCHANGE_RATE_FOR_CO2 = "ExchangeRateForCo2"
ATTR_EXCHANGE_RATE_FOR_MONEY = "ExchangeRateForMoney"
ATTR_IMAGE_URL = "ImageUrl"
ATTR_INSTALLER = "Installer"
ATTR_INVERTERS_COUNT = "InvertersCount"
ATTR_LAST_ACTION = "LastAction"
ATTR_MAX_COUNT = "MaxCount"
ATTR_MODULE_MANUFACTURE = "ModuleManufacture"
ATTR_ON_LINE_COUNT = "OnLineCount"
ATTR_OWNER = "Owner"
ATTR_PANEL_BRAND = "PanelBrand"
ATTR_PHONE = "Phone"
ATTR_PLANT_ID = "PlantId"
ATTR_PLANT_NAME = "PlantName"
ATTR_POWER = "Power"
ATTR_POWER_EFFICIENCY = "PowerEfficiency"
ATTR_POWER_NOW = "PowerNow"
ATTR_PROJECT_ID = "ProjectId"
ATTR_PROJECT_NAME = "ProjectName"
ATTR_STATUS = "Status"
ATTR_SYS_POWER = "SysPower"
ATTR_TIMESTAMP = "TimeStamp"
ATTR_TIMEZONE = "TimeZone"
ATTR_ZIP_CODE = "ZipCode"
ATTR_CB_EVENTS = "cbEvents"
ATTR_EDT_EMAIL = "edtEmail"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_NEW_Status = "new_Status"
ATTR_REPORT_ENABLE = "reportEnable"
ATTR_REPORT_SETTING = "reportSetting"
ATTR_REPORT_TIME = "reportTime"

USER_ENDPOINT = 'srv/PortalData.svc/GetUserUidAndPath'
USER_REQUEST_PAYLOAD = '{"passkey":""}'
PLANTS_ENDPOINT = 'srv/PortalData.svc/GetManaPlantsOfList'
PLANTS_REQUEST_PAYLOAD = '{"page":1,"pageSize":10,"paths":"{:s}","useruid":"{:s}","para":",,,,0,,0","flat":False}'


USER_SCHEMA_REQUEST = vol.Schema({
    vol.Required(ATTR_PASSKEY): cv.string
})

USER_SCHEMA_RESPONSE = vol.Schema({
    vol.Required(ATTR_DATA): vol.Schema({
        vol.Required(ATTR_TYPE): cv.string,
        vol.Required(ATTR_DATA_STRING):
            vol.All(
                cv.ensure_list,
                vol.Length(min=1),
                [cv.string]
            )
    }, extra=vol.REMOVE_EXTRA)
})

PLANTS_SCHEMA_REQUEST = vol.Schema({
    vol.Required(ATTR_PAGE_ID): cv.positive_int,
    vol.Required(ATTR_PAGE_SIZE): cv.positive_int,
    vol.Required(ATTR_PATHS): cv.string,
    vol.Required(ATTR_USER_UID): cv.string,
    vol.Required(ATTR_PARAMS): cv.string,
    vol.Required(ATTR_FLAT): vol.Boolean(),
})

PLANTS_SCHEMA = vol.Schema({
    vol.Required(ATTR_TYPE): cv.string,
    vol.Required(ATTR_TYPE): cv.string,
    vol.Required(ATTR_ADDRESS): cv.string,
    vol.Required(ATTR_AVERAGE_RUNNING_TIME): cv.string,
    vol.Required(ATTR_CITY): cv.string,
    vol.Optional(ATTR_COMMENT): cv.string,
    vol.Optional(ATTR_COMMISSION_DATE): cv.date,
    vol.Required(ATTR_COUNTRY): cv.string,
    vol.Optional(ATTR_CURRENCY): cv.string,
    vol.Optional(ATTR_CURRENCY_NAME): cv.string,
    vol.Optional(ATTR_DISTRIBUTOR_CODE): cv.string,
    vol.Required(ATTR_ENERGY_MONTH):  cv.positive_float,
    vol.Required(ATTR_ENERGY_TODAY):  cv.positive_float,
    vol.Required(ATTR_ENERGY_TOTAL):  cv.positive_float,
    vol.Required(ATTR_EFFICIENCY):  cv.positive_float,
    vol.Required(ATTR_EXCHANGE_RATE_FOR_CO2):  cv.positive_float,
    vol.Required(ATTR_EXCHANGE_RATE_FOR_MONEY):  cv.positive_float,
    vol.Optional(ATTR_IMAGE_URL): cv.url,
    vol.Optional(ATTR_INSTALLER): cv.string,
    vol.Optional(ATTR_INVERTERS_COUNT): cv.positive_int,
    vol.Optional(ATTR_LAST_ACTION): cv.string,
    vol.Optional(ATTR_MAX_COUNT): cv.positive_int,
    vol.Optional(ATTR_MODULE_MANUFACTURE): cv.string,
    vol.Optional(ATTR_ON_LINE_COUNT): cv.positive_int,
    vol.Optional(ATTR_OWNER): cv.string,
    vol.Optional(ATTR_PANEL_BRAND): cv.string,
    vol.Optional(ATTR_PHONE): cv.string,
    vol.Optional(ATTR_PLANT_ID): cv.string,
    vol.Optional(ATTR_PLANT_NAME): cv.string,
    vol.Optional(ATTR_POWER):  cv.positive_float,
    vol.Optional(ATTR_POWER_EFFICIENCY):  cv.positive_float,
    vol.Optional(ATTR_POWER_NOW):  cv.positive_float,
    vol.Optional(ATTR_PROJECT_ID): cv.string,
    vol.Optional(ATTR_PROJECT_NAME): cv.string,
    vol.Optional(ATTR_STATUS): cv.string,
    vol.Optional(ATTR_SYS_POWER):  cv.positive_float,
    vol.Optional(ATTR_TIMESTAMP): cv.string,
    vol.Optional(ATTR_TIMEZONE): cv.string,
    vol.Optional(ATTR_ZIP_CODE): cv.string,
    vol.Optional(ATTR_CB_EVENTS): cv.boolean,
    vol.Optional(ATTR_EDT_EMAIL): vol.Email,
    vol.Optional(ATTR_LATITUDE): cv.latitude,
    vol.Optional(ATTR_LONGITUDE): cv.longitude,
    vol.Optional(ATTR_NEW_Status): cv.string,
    vol.Optional(ATTR_REPORT_ENABLE): cv.positive_int,
    vol.Optional(ATTR_REPORT_SETTING): cv.positive_int,
    vol.Optional(ATTR_REPORT_TIME): cv.positive_int,
})


PLANTS_SCHEMA_RESPONSE = vol.Schema({
    vol.Required(ATTR_DATA): vol.Schema({
        vol.Required(ATTR_TYPE): cv.string,
        vol.Required(ATTR_DATAPOINT_LIST):
            vol.All(
                cv.ensure_list,
                vol.Length(min=1),
                [PLANTS_SCHEMA]
            )
    }, extra=vol.REMOVE_EXTRA)
}, extra=vol.REMOVE_EXTRA)


class SolarPortalRequestError(Exception):
    """Error to indicate a SolarPortal request has failed."""
    pass


@asyncio.coroutine
def async_portal_request(hass, method, url, **kwargs):
    """Perform a request to CityBikes API endpoint, and parse the response."""
    try:
        session = async_get_clientsession(hass)

        with async_timeout.timeout(REQUEST_TIMEOUT, loop=hass.loop):
            resp = yield from session.request(method, url, **kwargs)

        try:
            json_response = yield from resp.json()
            if 'schema' in kwargs:
                json_response = kwargs['schema'](json_response)
            resp.json = json_response
        except aiohttp.client_exceptions.ClientResponseError as e:
            if 'unexpected mimetype' not in e.message:
                raise
            resp.text = yield from resp.text()

        return resp

    except (asyncio.TimeoutError, aiohttp.ClientError):
        _LOGGER.error("Could not connect to CityBikes API endpoint")
    except ValueError:
        _LOGGER.error("Received non-JSON data from SolarPortal API endpoint")
    except vol.Invalid as err:
        _LOGGER.error("Received unexpected JSON from CityBikes"
                      " API endpoint: %s", err)
    raise SolarPortalRequestError

# pylint: disable=unused-argument
@asyncio.coroutine
def async_setup_platform(hass, config, async_add_devices,
                         discovery_info=None):

    host = config.get(CONF_HOST)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    portal = SolarPortal(hass, host, username, password)
    hass.async_add_job(portal.async_refresh)
    async_track_time_interval(hass, portal.async_refresh,
                              SCAN_INTERVAL)

    yield from portal.ready.wait()

    devices = []

    for resource in config[CONF_RESOURCES]:
        sensor_type = resource.lower()

        if sensor_type not in SENSOR_TYPES:
            SENSOR_TYPES[sensor_type] = [
                sensor_type.title(), '', 'mdi:flash']

        devices.append(SolarPortalSensor(portal, sensor_type))

    async_add_devices(devices, True)


class SolarPortal:
    """Representation of a Solar Portal."""

    LOGIN_PENDING = asyncio.Condition()

    @classmethod
    @asyncio.coroutine
    def login(cls, hass, url, username, password):
        """ Return the required .ASPXAUTH cookie """
        try:
            yield from cls.LOGIN_PENDING.acquire()
            resp = yield from cls.get(cls._url, params=cls._params)
            payload = cls._login_payload(resp)
            return (yield from cls.post(
                cls._url,
                data=aiohttp.FormData(payload)
            ))
        except SolarPortalRequestError:
            raise PlatformNotReady
        finally:
            cls.LOGIN_PENDING.release()

    def __init__(self, hass, url, username, password):
        self.hass = hass

        parts = urlsplit(url)
        self._url = "{scheme}://{netloc}{path}".format(**parts._asdict())
        self._params = dict(parse_qsl(parts.query))

        self._username = username
        self._password = password
        self.ready = asyncio.Event()
        self._session = async_get_clientsession(self.hass)
        self._user_data = None
        self.data = []

    def _payload(self, schema, data, *args):
        return schema(json.loads(data.format(*args)))

    # Payload to be send as Content-Type : application/x-www-form-urlencoded
    def _login_payload(self, resp):
        payload = OrderedDict()

        def get_viewstate(resp):
            soup = BeautifulSoup(
                resp.text, "lxml",
                from_encoding=resp.headers.get('charset')
            )
            return soup.find("input", {"id": "__VIEWSTATE"}).attrs['value']

        payload[LOGIN_FIELDS['viewstate_input']] = get_viewstate(resp)
        payload[LOGIN_FIELDS['username_input']] = self._username
        payload[LOGIN_FIELDS['password_input']] = self._password
        payload[LOGIN_FIELDS['remember_me_input']] = 'on'
        payload[LOGIN_FIELDS['login_value_input']] = 'Login'

        return payload

    @asyncio.coroutine
    def _login(self):
        resp = yield from self.get(self._url, params=self._params)
        payload = self._login_payload(resp)
        return (yield from self.post(
            self._url,
            data=aiohttp.FormData(payload)
        ))


    @asyncio.coroutine
    def _parse(self, resp):
        try:
            if resp.status == 200:
                try:
                    resp.read = yield from resp.read()
                    resp.text = yield from resp.text()
                    resp.json = yield from resp.json()
                except aiohttp.client_exceptions.ClientResponseError:
                    pass
                return resp
            else:
                resp.json = yield from resp.json()
                raise aiohttp.ClientResponseError(
                    history=resp.history,
                    request_info=resp.request_info,
                    code=resp.status,
                    headers=resp.headers,
                    message=resp.json
                )

        finally:
            yield from resp.release()

    @asyncio.coroutine
    def get(self, url, allow_redirects=True, **kwargs):
        resp = yield from self._session.get(url, allow_redirects=allow_redirects, **kwargs)
        return (yield from self._parse(resp))

    @asyncio.coroutine
    def post(self, url, data=None, **kwargs):
        resp = yield from self._session.post(url, data=data, **kwargs)
        return (yield from self._parse(resp))

    @asyncio.coroutine
    def async_refresh(self, now=None):
        filtered = self._session.cookie_jar.filter_cookies(
            self._url
        )
        if '.ASPXAUTH' not in filtered:
            yield from self._login()

        if self._user_data is None:
            resp = yield from self.post(
                url='{url}{endpoint}'.format(self._url, USER_ENDPOINT),
                json=self._payload(USER_SCHEMA_REQUEST, USER_REQUEST_PAYLOAD)
            )

            self._user_data = USER_SCHEMA_RESPONSE(resp.json)['d']['d_string']

        self.data = yield from self.post(
            url='{url}{endpoint}'.format(self._url, PLANTS_ENDPOINT),
            json=self._payload(
                PLANTS_SCHEMA_REQUEST,
                PLANTS_REQUEST_PAYLOAD,
                self._user_data[1],
                self._user_data[0]
            )
        )
        self.ready.set()


class SolarPortalSensor(Entity):
    """Representation of a SolarPortal sensor from the portal."""

    def __init__(self, data, sensor_type):
        """Initialize the sensor."""
        self.data = data
        self.type = sensor_type
        self._name = SENSOR_PREFIX + SENSOR_TYPES[self.type][0]
        self._unit_of_measurement = SENSOR_TYPES[self.type][1]
        self._icon = SENSOR_TYPES[self.type][2]
        self._state = None
        self.update()

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        return self._icon

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._unit_of_measurement

    @asyncio.coroutine
    def async_update(self):
        if self.data.ready.is_set():
            pass

    def update(self):
        """Get the latest data and use it to update our sensor state."""
        self.data.update()

        income = self.data.data.find('income')
        if self.type == 'actualpower':
            self._state = income.find('ActualPower').text
        elif self.type == 'energytoday':
            self._state = income.find('etoday').text
        elif self.type == 'energytotal':
            self._state = income.find('etotal').text
        elif self.type == 'incometoday':
            self._state = income.find('TodayIncome').text
        elif self.type == 'incometotal':
            self._state = income.find('TotalIncome').text