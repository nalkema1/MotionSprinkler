__all__ = [
    # stdlib re-exports needed by website.py routes
    'ujson', 'utime', 'Pin', 'Timer', 'reset', 'time',
    'myTime', 'sendTelemetry', 'gc', 'os',
    # constants
    'WEEKDAY_STR', 'TELEMETRY_FILE', 'RAIN_HISTORY_FILE',
    # settings
    'load_settings', 'save_settings', 'get_relay_by_id', 'get_current_version',
    # zone timers (underscore names excluded from import * without __all__)
    '_zone_timers', '_zone_clear_timer', 'manual_on_for',
    # config & rain
    'load_config', 'save_config', 'activate_sprinkler',
    'do_rain_check', 'should_skip_for_rain', 'daily_rain_check_if_due',
    # page chrome
    '_head', '_FOOT',
    # HTML rendering helpers
    '_rain_form', '_rain_history', '_schedule_block', '_add_form',
]

import ujson
import utime
from machine import Pin, Timer, reset
import time
from app.timesync import myTime
from app.telemetry import sendTelemetry
import gc
import os
import urequests

CONFIG_VERSION = 3
WEEKDAY_STR = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

# ── Settings ──────────────────────────────────────────────────────────────────
SETTINGS_FILE = 'device_settings.json'
_settings_cache = None

def default_settings():
    return {
        "version": 1,
        "relays": [
            {"id": 1, "gpio": 17, "name": "Zone 1"},
            {"id": 2, "gpio": 19, "name": "Zone 2"},
            {"id": 3, "gpio": 20, "name": "Zone 3"},
            {"id": 4, "gpio": 21, "name": "Zone 4"},
        ]
    }

def load_settings():
    global _settings_cache
    if _settings_cache is not None:
        return _settings_cache
    try:
        with open(SETTINGS_FILE, 'r') as f:
            s = ujson.loads(f.read())
        if not s or 'relays' not in s:
            s = default_settings()
            save_settings(s)
    except OSError:
        s = default_settings()
        save_settings(s)
    _settings_cache = s
    return s

def save_settings(s):
    global _settings_cache
    with open(SETTINGS_FILE, 'w') as f:
        f.write(ujson.dumps(s))
    _settings_cache = s

def get_relay_by_id(relay_id):
    for r in load_settings()['relays']:
        if r['id'] == relay_id:
            return r
    return None

def get_current_version():
    try:
        with open('app/.version', 'r') as f:
            return f.read().strip()
    except Exception:
        return 'unknown'

# ── Per-zone timer state ──────────────────────────────────────────────────────
_zone_timers = {}

def _zone_clear_timer(relay_id):
    if relay_id in _zone_timers:
        try:
            _zone_timers[relay_id]["timer"].deinit()
        except Exception:
            pass
        del _zone_timers[relay_id]

def _make_off_cb(relay_id, gpio):
    def cb(t):
        try:
            Pin(gpio, Pin.OUT).value(0)
            sendTelemetry("Zone {} auto-off".format(relay_id))
        except Exception:
            pass
        _zone_timers.pop(relay_id, None)
    return cb

def manual_on_for(minutes, gpio, relay_id):
    _zone_clear_timer(relay_id)
    Pin(gpio, Pin.OUT).value(1)
    period_ms = int(minutes * 60 * 1000)
    off_at = int(time.time()) + int(minutes * 60)
    t = Timer(-1)
    t.init(period=period_ms, mode=Timer.ONE_SHOT, callback=_make_off_cb(relay_id, gpio))
    _zone_timers[relay_id] = {"timer": t, "off_at": off_at}
    sendTelemetry("Zone {} ON {}min gpio{}".format(relay_id, minutes, gpio))

# ── Config helpers ────────────────────────────────────────────────────────────
CONFIG_FILE = 'sprinkler_config.json'
TELEMETRY_FILE = 'telemetry.csv'
config_data_cache = None

def activate_sprinkler(duration_sec, gpio=17):
    relay = Pin(gpio, Pin.OUT)
    if relay.value() == 0:
        sendTelemetry("Activated gpio{} {}s".format(gpio, duration_sec))
        relay.value(1)
        time.sleep(duration_sec)
        relay.value(0)
    else:
        sendTelemetry("gpio{} already ON".format(gpio))

def empty_config():
    return {
        "version": CONFIG_VERSION,
        "schedules": [],
        "rain_skip": {
            "enabled": False, "threshold_mm": 2.5,
            "latitude": 0.0, "longitude": 0.0,
            "last_check_date": "", "last_check_mm": 0.0,
        },
    }

def load_config():
    global config_data_cache
    if config_data_cache is not None:
        return config_data_cache
    loaded = None
    try:
        with open(CONFIG_FILE, 'r') as f:
            loaded = ujson.loads(f.read())
    except OSError:
        loaded = None
    if not loaded:
        fresh = empty_config()
        save_config(fresh)
        return fresh
    v = loaded.get('version', 1)
    if v < CONFIG_VERSION:
        for s in loaded.get('schedules', []):
            if 'zone' not in s:
                s['zone'] = 1
        loaded['version'] = CONFIG_VERSION
        save_config(loaded)
    rs = loaded.get('rain_skip') or {}
    for k, val in empty_config()['rain_skip'].items():
        if k not in rs:
            rs[k] = val
    loaded['rain_skip'] = rs
    if 'schedules' not in loaded:
        loaded['schedules'] = []
    config_data_cache = loaded
    return config_data_cache

def save_config(config):
    global config_data_cache
    with open(CONFIG_FILE, 'w') as f:
        f.write(ujson.dumps(config))
    config_data_cache = config

# ── Rain helpers ──────────────────────────────────────────────────────────────
RAIN_HISTORY_FILE = 'rain_history.csv'
RAIN_HISTORY_MAX_LINES = 365

def today_str(current_time):
    return "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])

def _append_rain_history(date_str, mm):
    lines = []
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            lines = f.readlines()
    except OSError:
        lines = []
    kept = [ln for ln in lines if not ln.startswith(date_str + ',')]
    kept.append("{},{}\n".format(date_str, mm))
    if len(kept) > RAIN_HISTORY_MAX_LINES:
        kept = kept[-RAIN_HISTORY_MAX_LINES:]
    try:
        with open(RAIN_HISTORY_FILE, 'w') as f:
            for ln in kept:
                f.write(ln)
    except Exception as e:
        sendTelemetry("Rain history err: {}".format(e))

def do_rain_check(config, force=False):
    rs = config.get('rain_skip') or {}
    lat = rs.get('latitude', 0.0)
    lon = rs.get('longitude', 0.0)
    if lat == 0.0 and lon == 0.0:
        return None
    current_time = myTime()
    today = today_str(current_time)
    if not force and rs.get('last_check_date') == today:
        return rs.get('last_check_mm', 0.0)
    try:
        url = ("https://api.open-meteo.com/v1/forecast"
               "?latitude={}&longitude={}&daily=precipitation_sum"
               "&timezone=auto&start_date={}&end_date={}").format(lat, lon, today, today)
        r = urequests.get(url)
        data = r.json()
        r.close()
        mm = float(data['daily']['precipitation_sum'][0] or 0.0)
        rs['last_check_date'] = today
        rs['last_check_mm'] = mm
        config['rain_skip'] = rs
        save_config(config)
        _append_rain_history(today, mm)
        sendTelemetry("Rain {}: {}mm".format(today, mm))
        return mm
    except Exception as e:
        sendTelemetry("Rain check failed: {}".format(e))
        return None

def should_skip_for_rain(config):
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return False
    mm = do_rain_check(config, force=False)
    if mm is None:
        return False
    return mm >= rs.get('threshold_mm', 2.5)

def daily_rain_check_if_due(config):
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return
    if rs.get('latitude', 0.0) == 0.0 and rs.get('longitude', 0.0) == 0.0:
        return
    current_time = myTime()
    if rs.get('last_check_date') == today_str(current_time):
        return
    if current_time[3] < 1:
        return
    do_rain_check(config, force=False)

# ── CSS & page chrome ─────────────────────────────────────────────────────────
CSS = ('<style>'
'*{box-sizing:border-box;margin:0;padding:0}'
'body{font-family:Arial,sans-serif;background:#eef7ee;color:#1b4332}'
'nav{background:#2d6a4f;padding:8px 12px;display:flex;flex-wrap:wrap;gap:4px}'
'.brand{color:#fff;font-weight:bold;font-size:16px;margin-right:8px;text-decoration:none}'
'nav a{color:#d8f3dc;text-decoration:none;padding:4px 9px;border-radius:4px;font-size:14px}'
'nav a:hover{background:rgba(255,255,255,.2)}'
'main{padding:12px;max-width:840px;margin:0 auto}'
'h1,h2,h3{color:#2d6a4f;margin:8px 0 5px}'
'h1{font-size:19px}h2{font-size:15px;border-bottom:2px solid #d8f3dc;padding-bottom:2px}'
'h3{font-size:13px;color:#6b4226}'
'.card{background:#fff;border-radius:8px;padding:12px;margin-bottom:8px;box-shadow:0 1px 3px rgba(0,0,0,.1)}'
'.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(175px,1fr));gap:8px}'
'.on{color:#fff;background:#52b788;padding:2px 8px;border-radius:10px;font-size:12px}'
'.off{color:#fff;background:#999;padding:2px 8px;border-radius:10px;font-size:12px}'
'input[type=text],input[type=number],input[type=time],select{padding:4px 7px;border:1px solid #b0c4b1;border-radius:4px;font-size:13px;max-width:100%}'
'input[type=submit]{padding:6px 12px;border:none;border-radius:5px;cursor:pointer;font-size:13px;color:#fff;background:#52b788;margin:2px}'
'input[type=submit].red{background:#c0392b}input[type=submit].blu{background:#2471a3}'
'table{width:100%;border-collapse:collapse}'
'th,td{border:1px solid #c8e6c9;padding:5px;font-size:13px}th{background:#d8f3dc}'
'.row{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-bottom:5px}'
'label{font-size:13px}small{color:#666;font-size:11px}'
'p,li{margin:4px 0;font-size:13px}ul{list-style:none;padding:0}'
'a{color:#2d6a4f}a:hover{text-decoration:underline}'
'.msg{background:#d4edda;border:1px solid #c3e6cb;padding:7px;border-radius:5px;font-size:13px;margin-bottom:7px}'
'</style>')

META = '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
_FOOT = '</main></body></html>'

def _nav():
    return ('<nav>'
            '<a class="brand" href="/">&#127807; Sprinkler</a>'
            '<a href="/manual">&#9654; Manual</a>'
            '<a href="/config">&#128198; Schedule</a>'
            '<a href="/settings">&#9881; Settings</a>'
            '<a href="/stats">&#128200; Stats</a>'
            '</nav>')

def _head(title):
    return '<html><head>' + META + '<title>' + title + '</title>' + CSS + '</head><body>' + _nav() + '<main>'

# ── HTML rendering helpers ────────────────────────────────────────────────────

def _zone_select(selected, relays, fname='zone'):
    opts = ''
    for r in relays:
        sel = ' selected' if r['id'] == selected else ''
        opts += '<option value="' + str(r['id']) + '"' + sel + '>' + r.get('name', 'Zone ' + str(r['id'])) + '</option>'
    return '<select name="' + fname + '">' + opts + '</select>'

def _schedule_block(s, relays):
    sid = str(s.get('id'))
    name = s.get('name', '')
    t = s.get('time', '06:00')
    dur = str(s.get('duration', 15))
    days = s.get('days', []) or []
    zone = s.get('zone', 1)
    en = ' checked' if s.get('enabled') else ''
    day_html = ''
    for dk, dl in [('mon','Mo'),('tue','Tu'),('wed','We'),('thu','Th'),('fri','Fr'),('sat','Sa'),('sun','Su')]:
        chk = ' checked' if dk in days else ''
        day_html += '<label><input type="checkbox" name="day_' + dk + '"' + chk + '> ' + dl + '</label> '
    return (
        '<div class="card">'
        '<form method="POST" action="/config">'
        '<input type="hidden" name="action" value="update">'
        '<input type="hidden" name="id" value="' + sid + '">'
        '<div class="row">'
        '<label><input type="checkbox" name="enabled"' + en + '> On</label>'
        'Name:<input type="text" name="name" value="' + name + '" style="width:110px">'
        'Time:<input type="time" name="time" value="' + t + '">'
        'Min:<input type="number" name="duration" value="' + dur + '" min="1" max="240" style="width:55px">'
        'Zone:' + _zone_select(zone, relays) +
        '</div><div class="row">' + day_html + '</div>'
        '<input type="submit" value="Save">'
        '</form> '
        '<form method="POST" action="/config" style="display:inline">'
        '<input type="hidden" name="action" value="delete">'
        '<input type="hidden" name="id" value="' + sid + '">'
        '<input type="submit" value="Delete" class="red"></form>'
        '</div>'
    )

def _add_form(relays):
    day_html = ''
    for dk, dl in [('mon','Mo'),('tue','Tu'),('wed','We'),('thu','Th'),('fri','Fr'),('sat','Sa'),('sun','Su')]:
        day_html += '<label><input type="checkbox" name="day_' + dk + '"> ' + dl + '</label> '
    return (
        '<div class="card"><form method="POST" action="/config">'
        '<input type="hidden" name="action" value="add">'
        '<div class="row">'
        'Name:<input type="text" name="name" style="width:110px">'
        'Time:<input type="time" name="time" value="06:00">'
        'Min:<input type="number" name="duration" value="15" min="1" max="240" style="width:55px">'
        'Zone:' + _zone_select(1, relays) +
        '</div><div class="row">' + day_html + '</div>'
        '<input type="submit" value="Add Schedule">'
        '</form></div>'
    )

def _rain_form(rs):
    en = ' checked' if rs.get('enabled') else ''
    th = str(rs.get('threshold_mm', 2.5))
    lat = str(rs.get('latitude', 0.0))
    lon = str(rs.get('longitude', 0.0))
    ld = rs.get('last_check_date', '') or 'never'
    lm = str(rs.get('last_check_mm', 0.0))
    return (
        '<div class="card"><form method="POST" action="/config">'
        '<input type="hidden" name="action" value="rain_config">'
        '<div class="row"><label><input type="checkbox" name="enabled"' + en + '>'
        ' Skip when rained today</label></div>'
        '<div class="row">'
        'mm:<input type="number" name="threshold_mm" step="0.1" value="' + th + '" style="width:70px">'
        'Lat:<input type="number" name="latitude" step="0.0001" value="' + lat + '" style="width:100px">'
        'Lon:<input type="number" name="longitude" step="0.0001" value="' + lon + '" style="width:100px">'
        '</div><small>Last: ' + ld + ' ' + lm + 'mm</small><br>'
        '<input type="submit" value="Save Rain" style="margin-top:6px">'
        '</form>'
        '<form method="POST" action="/config" style="margin-top:6px">'
        '<input type="hidden" name="action" value="rain_check_now">'
        '<input type="submit" value="Check Now" class="blu"></form></div>'
    )

def _rain_history():
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            lines = f.readlines()
    except OSError:
        return '<p><em>No rain history yet.</em></p>'
    if not lines:
        return '<p><em>No rain history yet.</em></p>'
    lines = lines[-14:]
    lines.reverse()
    rows = ''
    for ln in lines:
        parts = ln.strip().split(',')
        if len(parts) >= 2:
            rows += '<tr><td>' + parts[0] + '</td><td>' + parts[1] + 'mm</td></tr>'
    return ('<table><tr><th>Date</th><th>Rain</th></tr>' + rows + '</table>'
            '<p><small><a href="/rain_history.csv">Download</a></small></p>')
