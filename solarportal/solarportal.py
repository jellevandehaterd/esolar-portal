#!/usr/bin/env python
import aiohttp
import asyncio
import async_timeout
import json
import os
import click
from datetime import timedelta, datetime
import sys
import copy
from concurrent import futures
import configparser

import math
from bs4 import BeautifulSoup
from urllib.parse import parse_qsl, urlsplit
import voluptuous as vol
import logging
import signal
from aiohttp.hdrs import USER_AGENT
from hbmqtt.client import MQTTClient, ConnectException
from hbmqtt.mqtt.constants import QOS_0

LOGGER_FORMAT = "%(asctime)-12s %(levelname)-8s %(message)s"
logging.basicConfig(format=LOGGER_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('asyncio')

MAJOR_VERSION = 0
MINOR_VERSION = 9
PATCH_VERSION = '0.rc1'
__short_version__ = '{}.{}'.format(MAJOR_VERSION, MINOR_VERSION)
__version__ = '{}.{}'.format(__short_version__, PATCH_VERSION)


class ConfigValidation:
    @property
    def boolean(self):
        return vol.Boolean()

    @property
    def date(self):
        return vol.Date()

    @staticmethod
    def string(value):
        """Coerce value to string, except for None."""
        if value is not None:
            return str(value)
        raise vol.Invalid('string value is None')

    @property
    def positive_int(self):
        return vol.All(vol.Coerce(int), vol.Range(min=0))

    @property
    def positive_float(self):
        return vol.All(vol.Coerce(float), vol.Range(min=0))

    @property
    def url(self):
        return vol.Url()

    @property
    def email(self):
        return vol.Email()

    @property
    def latitude(self):
        return vol.All(vol.Coerce(float), vol.Range(min=-90, max=90),
                       msg='invalid latitude')

    @property
    def longitude(self):
        return vol.All(vol.Coerce(float), vol.Range(min=-180, max=180),
                       msg='invalid longitude')

    @staticmethod
    def ensure_list(value):
        """Wrap value in list if it is not one."""
        if value is None:
            return []
        return value if isinstance(value, list) else [value]


cv = ConfigValidation()
sys.modules[__name__] = cv

REQUEST_TIMEOUT = 30  # In seconds; argument to asyncio.timeout
SCAN_INTERVAL = timedelta(minutes=5)  # Timely, and doesn't suffocate the API

SERVER_SOFTWARE = 'SolarPortal/{0} aiohttp/{1} Python/{2[0]}.{2[1]}'.format(
    __version__, aiohttp.__version__, sys.version_info)

DOMAIN = 'solarportal'

LOGIN_FIELDS = {
    'viewstate_input': '__VIEWSTATE',
    'password_input': 'ctl00$cc$Login1$Password',
    'username_input': 'ctl00$cc$Login1$UserName',
    'remember_me_input': 'ctl00$cc$Login1$RememberMe',
    'login_value_input': 'ctl00$cc$Login1$LoginButton',
}

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
ATTR_IS_DUPLICATE_DIVICE = "isDuplicateDevice"
ATTR_IS_DUPLICATE_NAME = "isDuplicateName"
ATTR_IS_TIMER_ERROR = "isTimerError"
ATTR_LATITUDE = "latitude"
ATTR_LONGITUDE = "longitude"
ATTR_NEW_STATUS = "new_Status"
ATTR_OFFDAY = "offday"
ATTR_REPORT_ENABLE = "reportEnable"
ATTR_REPORT_SETTING = "reportSetting"
ATTR_REPORT_TIME = "reportTime"

USER_DATA = 'user-data'
DEFAULT_DELAY = 600
USER_ENDPOINT = 'srv/PortalData.svc/GetUserUidAndPath'
USER_REQUEST_PAYLOAD = '{{"passkey":""}}'
OVERVIEW = 'overview.html'
PLANTS_ENDPOINT = 'srv/PortalData.svc/GetManaPlantsOfList'
PLANTS_REQUEST_PAYLOAD = '{{"page":1,"pageSize":10,"paths":"{:s}","useruid":"{:s}","para":",,,,0,,0,0,0,0","flat": false}}'

SOLAR_SENSOR = {
    'status': ATTR_STATUS,
    'actual_power': ATTR_POWER_NOW,
    'energy_today': ATTR_ENERGY_TODAY,
    'energy_total': ATTR_ENERGY_TOTAL,
    'income_today': lambda x: round(x[ATTR_ENERGY_TODAY] * x[ATTR_EXCHANGE_RATE_FOR_MONEY], 2),
    'income_total': lambda x: round(x[ATTR_ENERGY_TOTAL] * x[ATTR_EXCHANGE_RATE_FOR_MONEY], 2)
}

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
    vol.Required(ATTR_ENERGY_MONTH): cv.positive_float,
    vol.Required(ATTR_ENERGY_TODAY): cv.positive_float,
    vol.Required(ATTR_ENERGY_TOTAL): cv.positive_float,
    vol.Required(ATTR_EFFICIENCY): cv.positive_float,
    vol.Required(ATTR_EXCHANGE_RATE_FOR_CO2): cv.positive_float,
    vol.Required(ATTR_EXCHANGE_RATE_FOR_MONEY): cv.positive_float,
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
    vol.Optional(ATTR_POWER): cv.positive_float,
    vol.Optional(ATTR_POWER_EFFICIENCY): cv.positive_float,
    vol.Optional(ATTR_POWER_NOW): cv.positive_float,
    vol.Optional(ATTR_PROJECT_ID): cv.string,
    vol.Optional(ATTR_PROJECT_NAME): cv.string,
    vol.Optional(ATTR_STATUS): cv.string,
    vol.Optional(ATTR_SYS_POWER): cv.positive_float,
    vol.Optional(ATTR_TIMESTAMP): cv.string,
    vol.Optional(ATTR_TIMEZONE): cv.string,
    vol.Optional(ATTR_ZIP_CODE): cv.string,
    vol.Optional(ATTR_CB_EVENTS): cv.boolean,
    vol.Optional(ATTR_EDT_EMAIL): cv.email,
    vol.Optional(ATTR_IS_DUPLICATE_DIVICE): cv.positive_int,
    vol.Optional(ATTR_IS_DUPLICATE_NAME): cv.positive_int,
    vol.Optional(ATTR_IS_TIMER_ERROR):  cv.positive_int,
    vol.Optional(ATTR_LATITUDE): cv.latitude,
    vol.Optional(ATTR_LONGITUDE): cv.longitude,
    vol.Optional(ATTR_NEW_STATUS): cv.string,
    vol.Optional(ATTR_OFFDAY): cv.positive_int,
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

PUBLISH_SCHEMA = vol.Schema({
    vol.Optional(ATTR_POWER_NOW): cv.positive_float,
    vol.Required(ATTR_ENERGY_MONTH): cv.positive_float,
    vol.Required(ATTR_ENERGY_TODAY): cv.positive_float,
    vol.Required(ATTR_ENERGY_TOTAL): cv.positive_float,
    vol.Optional(ATTR_STATUS): cv.string,
    vol.Optional(ATTR_SYS_POWER): cv.positive_float,
}, extra=vol.REMOVE_EXTRA)


class PlatformNotReady(Exception):
    """Error to indicate that platform is not ready."""
    pass


class SolarPortalRequestError(Exception):
    """Error to indicate a SolarPortal request has failed."""
    pass


@asyncio.coroutine
def async_portal_request(portal, method, url, **kwargs):
    """Perform a request to API endpoint, and parse the response."""
    schema = kwargs.pop('schema', None)
    logger.debug("%s: %s", method, url)
    try:

        with async_timeout.timeout(REQUEST_TIMEOUT, loop=portal.loop):
            resp = yield from portal.session.request(method, url, **kwargs)

        try:
            resp.json = yield from resp.json()
            if schema:
                resp.json = schema(resp.json)

        except aiohttp.client_exceptions.ClientResponseError as err:
            if 'unexpected mimetype' not in err.message:
                raise

        resp.text = yield from resp.text()
        return resp

    except (asyncio.TimeoutError, aiohttp.ClientError) as err:
        logger.error("Could not connect to SolarPortal API endpoint: %s", err)
    except (vol.SchemaError, vol.Invalid) as err:
        logger.error("Received unexpected JSON from SolarPortal"
                     "API endpoint: %s", err)
    except ValueError:
        logger.error("Received non-JSON data from SolarPortal API endpoint")

    raise SolarPortalRequestError


class SolarPortal:

    def __init__(self, config):
        logger.info('Initializing Portal')

        self.config = config
        parts = urlsplit(self.config['default']['url'])
        self.url = "{scheme}://{netloc}{path}".format(**parts._asdict())

        self.params = dict(parse_qsl(parts.query))
        self.loop = asyncio.get_event_loop()
        self.executor = futures.ThreadPoolExecutor(5)
        self.loop.set_default_executor(self.executor)
        self.data = {}
        self.user_data = []
        self.session = aiohttp.ClientSession(
            loop=self.loop,
            headers={USER_AGENT: SERVER_SOFTWARE}
        )
        self.exit_code = None

    def start(self):
        logger.info("Starting SolarPortal core loop")
        try:
            asyncio.ensure_future(self.async_start())
            self.loop.run_forever()
            return self.exit_code

        except KeyboardInterrupt:
            self.loop.call_soon_threadsafe(
                self.loop.create_task, self.async_stop())
            self.loop.run_forever()

        finally:
            self.executor.shutdown(wait=True)
            self.loop.close()

    @asyncio.coroutine
    def async_start(self):
        tasks = [
            asyncio.ensure_future(self.async_login()),
            asyncio.ensure_future(self.async_refresh())
        ]
        yield from asyncio.wait(tasks)

    def stop(self, exit_code=0):
        logger.setLevel(logging.INFO)
        logger.info("Cancelling tasks")
        for task in asyncio.Task.all_tasks():
            task.cancel()
        asyncio.ensure_future(self.async_stop(exit_code))

    @asyncio.coroutine
    def async_stop(self, exit_code=0):
        self.session.close()
        self.exit_code = exit_code
        self.loop.stop()
        logger.info("Stopped")

    @asyncio.coroutine
    def async_login(self):
        """ Return the required .ASPXAUTH cookie """

        def get_viewstate(response=None):
            soup = BeautifulSoup(
                response.text, "lxml",
                from_encoding=response.headers.get('charset')
            )
            return soup.find("input", {"id": "__VIEWSTATE"}).attrs['value']

        try:
            logger.debug("Requesting login page")
            payload = {
                LOGIN_FIELDS['username_input']: self.config['default']['username'],
                LOGIN_FIELDS['password_input']: self.config['default']['password'],
                LOGIN_FIELDS['remember_me_input']: 'on',
                LOGIN_FIELDS['login_value_input']: 'Login',
                LOGIN_FIELDS['viewstate_input']: get_viewstate(
                    (yield from async_portal_request(self, 'get', self.url, params=self.params))
                )
            }

            logger.debug("Try Login")
            # Payload to be send as Content-Type : application/x-www-form-urlencoded
            yield from async_portal_request(
                self,
                'post',
                self.url,
                params=self.params,
                data=aiohttp.FormData(payload)
            )

            logger.debug("Request userdata")
            resp = yield from async_portal_request(
                self,
                'post',
                url='{url}{endpoint}'.format(url=self.url, endpoint=USER_ENDPOINT),
                json=self._payload(USER_SCHEMA_REQUEST, USER_REQUEST_PAYLOAD),
                schema=USER_SCHEMA_RESPONSE
            )
            logger.debug("Userdata received")

            self.loop.call_later(delay=30, callback=self.session_refresh)

            self.user_data = resp.json['d']['d_string']

        except SolarPortalRequestError:
            raise PlatformNotReady
        finally:
            logger.info("Login done")

    def session_refresh(self):
        for cookie in self.session.cookie_jar:
            if 'paaswaaaw' in cookie.key and 'expires' in cookie:
                if cookie['expires']:
                    logger.info(
                        "Session valid until %s",
                        datetime.strptime(cookie['expires'], '%a, %d-%b-%Y %H:%M:%S %Z').strftime('%H:%M')
                    )
        asyncio.ensure_future(self.async_session_refresh())

    @asyncio.coroutine
    def async_session_refresh(self):
        logger.debug('Async session refresh')
        try:
            url = '{url}{endpoint}'.format(url=self.url, endpoint=OVERVIEW)
            yield from async_portal_request(self, 'get', url, params=self.params)

        except SolarPortalRequestError as err:
            logger.error("SolarPortal API endpoint error: %s", err)
            raise PlatformNotReady

        expiration = now = datetime.now()
        for cookie in self.session.cookie_jar:
            if 'paaswaaaw' in cookie.key:
                expiration = datetime.strptime(cookie['expires'], '%a, %d-%b-%Y %H:%M:%S %Z')

        logger.debug('Session expiring at %s', expiration.strftime('%H:%M'))
        if expiration.timestamp() > now.timestamp():
            # Scheduling refresh 30 min before session expiration
            expiration -= timedelta(minutes=30)
            logger.info("Scheduling session refresh at %s", expiration.strftime('%H:%M'))
            when = self.loop.time() + (expiration.timestamp() - now.timestamp())
            self.loop.call_at(when=when, callback=self.session_refresh)
        else:
            logger.warning("Session expired at %s, login required", expiration.strftime('%H:%M'))
            yield from self.async_login()

    def refresh(self, delay=None):
        asyncio.ensure_future(self.async_refresh(delay))

    @asyncio.coroutine
    def async_refresh(self, delay=None):
        if not self.user_data:
            self.loop.call_later(delay=10, callback=self.refresh)
            return

        if delay is None:
            delay = DEFAULT_DELAY
            if 'delay' in self.config['default']:
                delay = int(self.config['default']['delay'])

        try:
            logger.info('Updating data')
            future = asyncio.ensure_future(
                async_portal_request(
                    self, 'post',
                    url='{url}{endpoint}'.format(url=self.url, endpoint=PLANTS_ENDPOINT),
                    json=self._payload(
                        PLANTS_SCHEMA_REQUEST,
                        PLANTS_REQUEST_PAYLOAD,
                        self.user_data[1],
                        self.user_data[0]
                    ),
                    schema=PLANTS_SCHEMA_RESPONSE
                )
            )

            def callback(fut):
                logger.info('Updating done')
                self.data = fut.result().json
                asyncio.ensure_future(self.async_publish(PUBLISH_SCHEMA))
                logger.debug("%s", self.data['d']['dp'][0])

            future.add_done_callback(callback)

        except IndexError:
            logger.warning("Userdata not set, rescheduling refresh task in %s seconds", delay)
        except SolarPortalRequestError as err:
            logger.error("SolarPortal API endpoint error: %s", err)
            raise PlatformNotReady

        _when = ceil_dt(datetime.now(), timedelta(seconds=delay))
        logger.info("Scheduling refresh at %s", _when.strftime('%H:%M'))
        when = self.loop.time() + (_when.timestamp() - datetime.now().timestamp())
        self.loop.call_at(when=when, callback=self.refresh)

    @asyncio.coroutine
    def async_publish(self, schema):
        client = MQTTClient(client_id=os.uname().nodename)
        try:
            mqtt = self.config['mqtt']
            logger.debug('mqtt client connecting to: %s', mqtt['broker_url'])
            url = mqtt['broker_url'].format(username=mqtt['username'], password=mqtt['password'])

            yield from client.connect(url)
            topic = "{prefix}/{component}".format(
                prefix=mqtt['topic_prefix'],
                component='data'
            )
            data = copy.copy(SOLAR_SENSOR)
            data_set = schema(self.data['d']['dp'][0])
            for key, value_from in data.items():
                data[key] = value_from(self.data['d']['dp'][0]) if callable(value_from) else data_set[value_from]

            message = json.dumps(data).encode()
            yield from client.publish(topic, message, qos=QOS_0, retain=True)
            logger.info("Published at topic %s: %s", topic, message)

        except ConnectException as ce:
            logger.error("Connection failed: %s" % ce)
        finally:
            logger.debug('mqtt client disconnecting')
            yield from client.disconnect()

    @staticmethod
    def _payload(schema, data, *args):
        return schema(json.loads(data.format(*args)))


def read_config(path):
    cfg = os.path.join(path)
    parser = configparser.ConfigParser(interpolation=configparser.ExtendedInterpolation())
    parser.read([cfg])
    rv = {}
    for section in parser.sections():
        rv[section] = {}
        for key, value in parser.items(section):
            rv[section][key] = value
    return rv


def ceil_dt(dt, delta):
    return datetime.min + math.ceil((dt - datetime.min) / delta) * delta


@click.command()
@click.argument('configfile', type=click.Path(exists=True))
@click.option('--delay', default=None)
@click.option('--verbose', type=click.Choice(['v', 'vv', 'vvv']))
def main(configfile, verbose, delay):
    logger.setLevel(logging.INFO)
    logger.info("Version: %s", __version__)
    logger.info('Reading config from %s', configfile)
    config = read_config(configfile)

    if delay:
        config['default']['delay'] = delay
    logger.info("Starting script")

    if verbose == 'v':
        logger.setLevel(logging.WARNING)
    elif verbose == 'vv':
        logger.setLevel(logging.INFO)
    elif verbose == 'vvv':
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.ERROR)
    portal = SolarPortal(config)

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            portal.loop.add_signal_handler(sig, portal.stop)

        portal.start()
    except:
        logger.critical("Caught unhandled exception, shutting down")
        portal.stop(exit_code=1)


if __name__ == '__main__':
    main()
