"""Константы интеграции 3i Robot Vacuum."""

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME  # noqa: F401  (re-export)

DOMAIN = "three_i"

# --- Поля конфигурации -------------------------------------------------------
CONF_DEVICE_ID = "device_id"
CONF_TENANT_ID = "tenant_id"
CONF_PRODUCT_ID = "product_id"
CONF_PROJECT = "project"

# --- Параметры облака 3i (Picea / 3irobotix) ---------------------------------
# Это глобальные идентификаторы экосистемы 3i, одинаковые для всех аккаунтов
# и всех моделей. Менять их пользователю не нужно.
TENANT_ID = "1528911334443982848"
PRODUCT_ID = "1829367890298044416"
PROJECT = "android_iot.3i"

APP_HOST = "cn-appaiot.3irobotix.net"
LOGIN_PATH = "/user-center/auth/login"
MQTT_HOST = "cn-ipamqttaiot.3irobotix.net"
MQTT_PORT = 8883

# Параметры клиента, как их отправляет официальное приложение.
APP_VERSION_CODE = 10004
APP_VERSION_NAME = "1.0.4"
PHONE_BRAND = "android"

DEFAULT_NAME = "3i P10 Ultra"

# --- Протокол MQTT -----------------------------------------------------------
PROTOCOL_VERSION = "1.0.1"

# Свойства для активного опроса (робот сам присылает только дельты).
STATUS_PROPERTIES = [
    "status", "fault", "mode", "work_mode", "clean_progress", "quantity",
    "sweep_type", "wind", "water", "cloth_state", "back_to_wash", "back_to_dust",
    "mop_route", "charge_state", "cleaning_area", "cleaning_time",
    "current_map_id", "dust_action", "station_act", "quiet_status",
    "firmware", "detergent", "hot_water_switch", "net_status",
    "auto_airdry_switch", "dust_auto_state", "work_time", "water_tank",
]

CONSUMABLE_PROPERTIES = ["main_brush", "side_brush", "hypa", "mop_life"]

# Ресурс деталей в часах: робот сообщает отработанные часы,
# остаток в процентах считается от этих значений.
CONSUMABLE_MAX_HOURS = {
    "main_brush": 360,
    "side_brush": 180,
    "hypa": 180,
    "mop_life": 300,
}

# Служебные поля оболочки протокола, не идут в атрибуты состояния.
IGNORED_KEYS = {
    "msgId", "version", "tenantId", "method",
    "params", "gtid", "code", "data", "result",
}

STATUS_TEXT = {
    0: "Сон / ожидание",
    1: "Ожидание",
    2: "Пауза",
    3: "Уборка",
    4: "На базе (готов)",
    5: "Уборка",
    6: "Уборка",
    7: "Промывка мопов",
    8: "Возврат на базу",
    9: "Возврат на базу",
    10: "На базе (зарядка)",
    11: "Поиск базы",
    12: "Ошибка",
    14: "Промывка мопов",
    15: "Сушка мопов",
    16: "Самоочистка пылесборника",
}

# Период полного опроса свойств, сек.
POLL_INTERVAL = 300
