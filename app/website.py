import picoweb
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

# ── Settings (zone / GPIO config) ─────────────────────────────────────────────
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

# ── Per-zone manual timer state ───────────────────────────────────────────────
_zone_timers = {}  # {relay_id: {"timer": Timer, "off_at": int}}

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
            sendTelemetry("Zone {} auto-off gpio{}".format(relay_id, gpio))
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
    sendTelemetry("Zone {} ON for {} min (gpio{})".format(relay_id, minutes, gpio))

# ── Sprinkler config ──────────────────────────────────────────────────────────
app = picoweb.WebApp(__name__)
CONFIG_FILE = 'sprinkler_config.json'
TELEMETRY_FILE = 'telemetry.csv'
config_data_cache = None

def activate_sprinkler(duration_sec, gpio=17):
    relay = Pin(gpio, Pin.OUT)
    if relay.value() == 0:
        sendTelemetry("Activated gpio{} for {}s".format(gpio, duration_sec))
        relay.value(1)
        time.sleep(duration_sec)
        relay.value(0)
    else:
        sendTelemetry("gpio{} already running".format(gpio))

def empty_config():
    return {
        "version": CONFIG_VERSION,
        "schedules": [],
        "rain_skip": {
            "enabled": False,
            "threshold_mm": 2.5,
            "latitude": 0.0,
            "longitude": 0.0,
            "last_check_date": "",
            "last_check_mm": 0.0,
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
        # Migrate v1/v2 → v3: add zone=1 to existing schedules
        for s in loaded.get('schedules', []):
            if 'zone' not in s:
                s['zone'] = 1
        loaded['version'] = CONFIG_VERSION
        save_config(loaded)
    # Ensure rain_skip has all keys
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
        sendTelemetry("Rain history write failed: {}".format(e))

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
        sendTelemetry("Rain check {}: {} mm".format(today, mm))
        return mm
    except Exception as e:
        sendTelemetry("Rain check failed: {}".format(e))
        return None

def should_skip_for_rain(config):
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return False
    threshold = rs.get('threshold_mm', 2.5)
    mm = do_rain_check(config, force=False)
    if mm is None:
        return False
    return mm >= threshold

def daily_rain_check_if_due(config):
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return
    if rs.get('latitude', 0.0) == 0.0 and rs.get('longitude', 0.0) == 0.0:
        return
    current_time = myTime()
    today = today_str(current_time)
    if rs.get('last_check_date') == today:
        return
    if current_time[3] < 1:
        return
    do_rain_check(config, force=False)

def check_schedule(config):
    schedules = config.get('schedules') or []
    if not schedules:
        return False
    current_time = myTime()
    cur_h = current_time[3]
    cur_m = current_time[4]
    today_name = WEEKDAY_STR[current_time[6]]
    fired = False
    for s in schedules:
        if not s.get('enabled'):
            continue
        if today_name not in s.get('days', []):
            continue
        t = s.get('time', '')
        try:
            sh, sm = t.split(':')
            sh = int(sh); sm = int(sm)
        except Exception:
            continue
        if cur_h != sh or cur_m != sm:
            continue
        name = s.get('name') or 'schedule {}'.format(s.get('id'))
        if should_skip_for_rain(config):
            rs = config.get('rain_skip') or {}
            sendTelemetry("Skip {} - rain {} mm".format(name, rs.get('last_check_mm', 0.0)))
            continue
        duration = int(s.get('duration', 0))
        zone_id = s.get('zone', 1)
        relay_info = get_relay_by_id(zone_id)
        gpio = relay_info['gpio'] if relay_info else 17
        sendTelemetry("Run {} zone{} for {}min".format(name, zone_id, duration))
        activate_sprinkler(duration * 60, gpio)
        fired = True
    return fired

def schedule_checker(timer):
    config_data = load_config()
    if config_data:
        daily_rain_check_if_due(config_data)
        check_schedule(config_data)

_sched_timer = Timer(-1)
_sched_timer.init(period=30000, mode=Timer.PERIODIC, callback=schedule_checker)

# ── CSS & page chrome ─────────────────────────────────────────────────────────
CSS = """<style>
:root{--gd:#2d6a4f;--gm:#52b788;--gl:#d8f3dc;--earth:#6b4226;--bg:#eef7ee;--w:#fff;--txt:#1b4332;--red:#c0392b;--blu:#2471a3}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,Arial,sans-serif;background:var(--bg);color:var(--txt)}
nav{background:var(--gd);padding:10px 14px;display:flex;flex-wrap:wrap;gap:4px;align-items:center}
.brand{color:#fff;font-weight:700;font-size:17px;margin-right:8px;text-decoration:none}
nav a{color:#d8f3dc;text-decoration:none;padding:5px 10px;border-radius:4px;font-size:14px}
nav a:hover{background:rgba(255,255,255,.15)}
main{padding:14px;max-width:860px;margin:0 auto}
h1{font-size:20px;margin:12px 0 10px;color:var(--gd)}
h2{font-size:16px;margin:12px 0 6px;color:var(--gd);border-bottom:2px solid var(--gl);padding-bottom:3px}
h3{font-size:14px;margin:0 0 6px;color:var(--earth);font-weight:600}
.card{background:var(--w);border-radius:10px;padding:14px;margin-bottom:10px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(185px,1fr));gap:8px}
.badge-on{color:#fff;background:var(--gm);padding:2px 10px;border-radius:12px;font-size:13px;font-weight:600;display:inline-block}
.badge-off{color:#fff;background:#9e9e9e;padding:2px 10px;border-radius:12px;font-size:13px;display:inline-block}
input[type=text],input[type=number],input[type=time],select{padding:5px 8px;border:1px solid #b0c4b1;border-radius:5px;font-size:13px;background:#fff;max-width:100%}
input[type=submit]{padding:7px 13px;border:none;border-radius:6px;cursor:pointer;font-size:13px;color:#fff;background:var(--gm);margin:2px}
input[type=submit]:hover{opacity:.85}
input[type=submit].red{background:var(--red)}
input[type=submit].blu{background:var(--blu)}
table{width:100%;border-collapse:collapse;margin-top:4px}
th,td{border:1px solid #c8e6c9;padding:6px 8px;text-align:left;font-size:13px}
th{background:var(--gl)}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px}
label{font-size:13px;display:inline-flex;align-items:center;gap:3px}
small{color:#666;font-size:12px}
p{margin:5px 0;font-size:14px}
ul{list-style:none;padding:0}
li{margin:5px 0;font-size:14px}
a{color:var(--gd)}
a:hover{text-decoration:underline}
.msg{background:#d4edda;border:1px solid #c3e6cb;padding:8px 12px;border-radius:6px;font-size:13px;margin-bottom:8px}
</style>"""

META = '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'

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

_FOOT = '</main></body></html>'

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index(req, resp):
    relays = load_settings()['relays']
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Dashboard"))
    yield from resp.awrite('<h1>&#127807; Dashboard</h1><div class="grid">')
    for r in relays:
        gpio = r['gpio']
        rid = r['id']
        name = r.get('name', 'Zone {}'.format(rid))
        state = Pin(gpio, Pin.OUT).value()
        badge = '<span class="badge-on">ON</span>' if state else '<span class="badge-off">OFF</span>'
        timer_html = ''
        if rid in _zone_timers:
            rem = _zone_timers[rid]['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<br><small>Auto-off {}m {}s</small>'.format(rem // 60, rem % 60)
        yield from resp.awrite(
            '<div class="card"><h3>' + name + '</h3>' +
            badge + timer_html +
            '<br><small>GPIO ' + str(gpio) + '</small></div>'
        )
    yield from resp.awrite('</div>')
    yield from resp.awrite('<div class="card"><ul>'
                           '<li><a href="/manual">&#9654; Manual Control</a></li>'
                           '<li><a href="/config">&#128198; Schedules &amp; Rain Skip</a></li>'
                           '<li><a href="/telemetry">&#128203; Telemetry Log</a></li>'
                           '<li><a href="/stats">&#128200; System Stats</a></li>'
                           '</ul></div>')
    yield from resp.awrite(_FOOT)

# ── Manual control ────────────────────────────────────────────────────────────

@app.route("/manual", methods=['GET', 'POST'])
def manual(req, resp):
    relays = load_settings()['relays']
    message = ""
    if req.method == "POST":
        yield from req.read_form_data()
        action = req.form.get('action', '')
        try:
            zone_id = int(req.form.get('zone_id', '1') or 1)
        except Exception:
            zone_id = 1
        relay = get_relay_by_id(zone_id)
        if relay:
            gpio = relay['gpio']
            zname = relay.get('name', 'Zone {}'.format(zone_id))
            if action == "on":
                _zone_clear_timer(zone_id)
                Pin(gpio, Pin.OUT).value(1)
                sendTelemetry("{} manually ON".format(zname))
                message = "{} turned ON.".format(zname)
            elif action == "off":
                _zone_clear_timer(zone_id)
                Pin(gpio, Pin.OUT).value(0)
                sendTelemetry("{} manually OFF".format(zname))
                message = "{} turned OFF.".format(zname)
            elif action == "on_timed":
                try:
                    minutes = float(req.form.get('minutes', '5') or 5)
                except Exception:
                    minutes = 5
                minutes = max(1.0, min(240.0, minutes))
                manual_on_for(minutes, gpio, zone_id)
                message = "{} ON for {} min.".format(zname, minutes)
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Manual Control"))
    yield from resp.awrite('<h1>&#9654; Manual Control</h1>')
    if message:
        yield from resp.awrite('<div class="msg">' + message + '</div>')
    yield from resp.awrite('<div class="grid">')
    for r in relays:
        gpio = r['gpio']
        rid = r['id']
        name = r.get('name', 'Zone {}'.format(rid))
        state = Pin(gpio, Pin.OUT).value()
        badge = '<span class="badge-on">ON</span>' if state else '<span class="badge-off">OFF</span>'
        timer_html = ''
        if rid in _zone_timers:
            rem = _zone_timers[rid]['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<p><small>Auto-off in {}m {}s</small></p>'.format(rem // 60, rem % 60)
        rid_s = str(rid)
        yield from resp.awrite(
            '<div class="card"><h3>' + name + '</h3>'
            '<p>' + badge + '</p>' + timer_html +
            '<form method="POST" style="display:inline">'
            '<input type="hidden" name="zone_id" value="' + rid_s + '">'
            '<input type="hidden" name="action" value="on">'
            '<input type="submit" value="ON"></form> '
            '<form method="POST" style="display:inline">'
            '<input type="hidden" name="zone_id" value="' + rid_s + '">'
            '<input type="hidden" name="action" value="off">'
            '<input type="submit" value="OFF" class="red"></form>'
            '<form method="POST" class="row" style="margin-top:8px">'
            '<input type="hidden" name="zone_id" value="' + rid_s + '">'
            '<input type="hidden" name="action" value="on_timed">'
            '<input type="number" name="minutes" value="5" min="1" max="240" style="width:60px"> min '
            '<input type="submit" value="Timed ON" class="blu"></form>'
            '</div>'
        )
    yield from resp.awrite('</div>' + _FOOT)

# ── Schedule / Config ─────────────────────────────────────────────────────────

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
        '<label><input type="checkbox" name="enabled"' + en + '> Enabled</label>'
        'Name: <input type="text" name="name" value="' + name + '" style="width:120px">'
        'Time: <input type="time" name="time" value="' + t + '">'
        'Min: <input type="number" name="duration" value="' + dur + '" min="1" max="240" style="width:60px">'
        'Zone: ' + _zone_select(zone, relays) +
        '</div>'
        '<div class="row">' + day_html + '</div>'
        '<input type="submit" value="Save">'
        '</form> '
        '<form method="POST" action="/config" style="display:inline">'
        '<input type="hidden" name="action" value="delete">'
        '<input type="hidden" name="id" value="' + sid + '">'
        '<input type="submit" value="Delete" class="red">'
        '</form>'
        '</div>'
    )

def _add_form(relays):
    day_html = ''
    for dk, dl in [('mon','Mo'),('tue','Tu'),('wed','We'),('thu','Th'),('fri','Fr'),('sat','Sa'),('sun','Su')]:
        day_html += '<label><input type="checkbox" name="day_' + dk + '"> ' + dl + '</label> '
    return (
        '<div class="card">'
        '<form method="POST" action="/config">'
        '<input type="hidden" name="action" value="add">'
        '<div class="row">'
        'Name: <input type="text" name="name" style="width:120px">'
        'Time: <input type="time" name="time" value="06:00">'
        'Min: <input type="number" name="duration" value="15" min="1" max="240" style="width:60px">'
        'Zone: ' + _zone_select(1, relays) +
        '</div>'
        '<div class="row">' + day_html + '</div>'
        '<input type="submit" value="Add Schedule">'
        '</form>'
        '</div>'
    )

def _rain_form(rs):
    en = ' checked' if rs.get('enabled') else ''
    th = str(rs.get('threshold_mm', 2.5))
    lat = str(rs.get('latitude', 0.0))
    lon = str(rs.get('longitude', 0.0))
    ld = rs.get('last_check_date', '') or 'never'
    lm = str(rs.get('last_check_mm', 0.0))
    return (
        '<div class="card">'
        '<form method="POST" action="/config">'
        '<input type="hidden" name="action" value="rain_config">'
        '<div class="row"><label><input type="checkbox" name="enabled"' + en + '> Skip schedules when it has rained today</label></div>'
        '<div class="row">'
        'Threshold (mm): <input type="number" name="threshold_mm" step="0.1" value="' + th + '" style="width:80px">'
        'Lat: <input type="number" name="latitude" step="0.0001" value="' + lat + '" style="width:110px">'
        'Lon: <input type="number" name="longitude" step="0.0001" value="' + lon + '" style="width:110px">'
        '</div>'
        '<small>Last check: ' + ld + ' &mdash; ' + lm + ' mm</small><br>'
        '<input type="submit" value="Save Rain Settings" style="margin-top:6px">'
        '</form>'
        '<form method="POST" action="/config" style="margin-top:6px">'
        '<input type="hidden" name="action" value="rain_check_now">'
        '<input type="submit" value="Check Rain Now" class="blu">'
        '</form>'
        '</div>'
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
            rows += '<tr><td>' + parts[0] + '</td><td>' + parts[1] + ' mm</td></tr>'
    return ('<table><tr><th>Date</th><th>Rain</th></tr>' + rows + '</table>'
            '<p><small><a href="/rain_history.csv">Download full history</a></small></p>')

@app.route("/config", methods=['GET', 'POST'])
def config(req, resp):
    cfg = load_config()
    relays = load_settings()['relays']
    if req.method == "POST":
        yield from req.read_form_data()
        action = req.form.get('action', '')
        if action == 'add':
            new_id = 1
            if cfg['schedules']:
                new_id = max(s.get('id', 0) for s in cfg['schedules']) + 1
            days = [d for d in WEEKDAY_STR if req.form.get('day_' + d)]
            try:
                duration = int(req.form.get('duration', '15') or 15)
            except Exception:
                duration = 15
            try:
                zone = int(req.form.get('zone', '1') or 1)
            except Exception:
                zone = 1
            cfg['schedules'].append({
                'id': new_id,
                'name': req.form.get('name', '') or 'Schedule {}'.format(new_id),
                'time': req.form.get('time', '06:00') or '06:00',
                'duration': duration,
                'days': days,
                'enabled': True,
                'zone': zone,
            })
            save_config(cfg)
        elif action == 'delete':
            try:
                del_id = int(req.form.get('id', '0'))
                cfg['schedules'] = [s for s in cfg['schedules'] if s.get('id') != del_id]
                save_config(cfg)
            except Exception:
                pass
        elif action == 'update':
            try:
                up_id = int(req.form.get('id', '0'))
                for s in cfg['schedules']:
                    if s.get('id') == up_id:
                        s['enabled'] = bool(req.form.get('enabled'))
                        s['name'] = req.form.get('name', s.get('name', ''))
                        s['time'] = req.form.get('time', s.get('time', '06:00'))
                        try:
                            s['duration'] = int(req.form.get('duration', s.get('duration', 15)))
                        except Exception:
                            pass
                        s['days'] = [d for d in WEEKDAY_STR if req.form.get('day_' + d)]
                        try:
                            s['zone'] = int(req.form.get('zone', s.get('zone', 1)) or 1)
                        except Exception:
                            pass
                        break
                save_config(cfg)
            except Exception:
                pass
        elif action == 'rain_check_now':
            do_rain_check(cfg, force=True)
        elif action == 'rain_config':
            rs = cfg.get('rain_skip') or {}
            rs['enabled'] = bool(req.form.get('enabled'))
            try:
                rs['threshold_mm'] = float(req.form.get('threshold_mm', '2.5') or 2.5)
            except Exception:
                pass
            try:
                rs['latitude'] = float(req.form.get('latitude', '0') or 0)
                rs['longitude'] = float(req.form.get('longitude', '0') or 0)
            except Exception:
                pass
            rs['last_check_date'] = ''
            rs['last_check_mm'] = 0.0
            cfg['rain_skip'] = rs
            save_config(cfg)
        cfg = load_config()
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Schedule"))
    yield from resp.awrite('<h1>&#128198; Schedule</h1>')
    yield from resp.awrite('<h2>Rain Skip</h2>')
    yield from resp.awrite(_rain_form(cfg.get('rain_skip') or {}))
    yield from resp.awrite('<h2>Rain History (last 14 days)</h2>')
    yield from resp.awrite(_rain_history())
    yield from resp.awrite('<h2>Schedules</h2>')
    for s in cfg.get('schedules', []):
        yield from resp.awrite(_schedule_block(s, relays))
    if not cfg.get('schedules'):
        yield from resp.awrite('<p><em>No schedules configured yet.</em></p>')
    yield from resp.awrite('<h2>Add New Schedule</h2>')
    yield from resp.awrite(_add_form(relays))
    yield from resp.awrite(_FOOT)

# ── Settings ──────────────────────────────────────────────────────────────────

@app.route("/settings", methods=['GET', 'POST'])
def settings_page(req, resp):
    s = load_settings()
    message = ""
    if req.method == "POST":
        yield from req.read_form_data()
        updated = []
        for r in s['relays']:
            rid = r['id']
            rids = str(rid)
            name = req.form.get('name_' + rids, r.get('name', 'Zone ' + rids)) or 'Zone ' + rids
            try:
                gpio = int(req.form.get('gpio_' + rids, str(r['gpio'])) or r['gpio'])
            except Exception:
                gpio = r['gpio']
            updated.append({"id": rid, "gpio": gpio, "name": name})
        s['relays'] = updated
        save_settings(s)
        message = "Settings saved."
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Settings"))
    yield from resp.awrite('<h1>&#9881; Settings</h1>')
    if message:
        yield from resp.awrite('<div class="msg">' + message + '</div>')
    yield from resp.awrite('<div class="card">'
                           '<h2>Zone GPIO Configuration</h2>'
                           '<form method="POST" action="/settings">'
                           '<table>'
                           '<tr><th>Zone</th><th>Name</th><th>GPIO Pin</th></tr>')
    for r in s['relays']:
        rid = r['id']
        rids = str(rid)
        name = r.get('name', 'Zone ' + rids)
        gpio = str(r['gpio'])
        yield from resp.awrite(
            '<tr><td>Zone ' + rids + '</td>'
            '<td><input type="text" name="name_' + rids + '" value="' + name + '" style="width:130px"></td>'
            '<td><input type="number" name="gpio_' + rids + '" value="' + gpio + '" min="0" max="39" style="width:70px"></td>'
            '</tr>'
        )
    yield from resp.awrite('</table><br>'
                           '<input type="submit" value="Save Settings">'
                           '</form></div>')
    yield from resp.awrite('<div class="card"><h2>Data Management</h2><ul>'
                           '<li><a href="/config.json">&#8681; Download sprinkler_config.json</a></li>'
                           '<li><a href="/telemetry.csv">&#8681; Download telemetry.csv</a></li>'
                           '<li><a href="/rain_history.csv">&#8681; Download rain_history.csv</a></li>'
                           '</ul>'
                           '<form method="POST" action="/clear_telemetry" style="margin-top:10px">'
                           '<input type="submit" value="Clear Telemetry Data" class="red">'
                           '</form></div>')
    yield from resp.awrite(_FOOT)

# ── JSON API ──────────────────────────────────────────────────────────────────

@app.route("/api/sprinkler", methods=['GET', 'POST'])
def api_sprinkler(req, resp):
    qs = getattr(req, 'qs', '') or ''
    zone_id = 1
    for part in qs.split('&'):
        if part.startswith('zone='):
            try:
                zone_id = int(part.split('=', 1)[1])
            except Exception:
                pass
            break
    relay = get_relay_by_id(zone_id)
    if relay is None:
        yield from picoweb.start_response(resp, status="400", content_type="application/json")
        yield from resp.awrite(ujson.dumps({"error": "unknown zone"}))
        return
    gpio = relay['gpio']
    relay_pin = Pin(gpio, Pin.OUT)
    if req.method == "POST":
        turnonfor_str = ''
        action = ''
        for part in qs.split('&'):
            if part.startswith('turnonfor='):
                turnonfor_str = part.split('=', 1)[1]
            elif part.startswith('action='):
                action = part.split('=', 1)[1]
        if turnonfor_str:
            try:
                seconds = int(turnonfor_str)
            except Exception:
                seconds = 0
            if seconds <= 0:
                yield from picoweb.start_response(resp, status="400", content_type="application/json")
                yield from resp.awrite(ujson.dumps({"error": "turnonfor must be positive"}))
                return
            seconds = min(seconds, 14400)
            manual_on_for(seconds / 60.0, gpio, zone_id)
        elif action == "on":
            _zone_clear_timer(zone_id)
            relay_pin.value(1)
            sendTelemetry("Zone {} ON via API".format(zone_id))
        elif action == "off":
            _zone_clear_timer(zone_id)
            relay_pin.value(0)
            sendTelemetry("Zone {} OFF via API".format(zone_id))
        else:
            yield from picoweb.start_response(resp, status="400", content_type="application/json")
            yield from resp.awrite(ujson.dumps({"error": "use ?action=on|off or ?turnonfor=SECONDS"}))
            return
    state = "on" if relay_pin.value() == 1 else "off"
    result = {"state": state, "zone": zone_id, "gpio": gpio}
    if zone_id in _zone_timers:
        rem = _zone_timers[zone_id]['off_at'] - int(time.time())
        if rem > 0:
            result["auto_off_in_seconds"] = rem
    yield from picoweb.start_response(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps(result))

# ── Stats ─────────────────────────────────────────────────────────────────────

@app.route("/stats")
def stats(req, resp):
    yield from picoweb.start_response(resp)
    mem_free = gc.mem_free()
    uptime = utime.time()
    ud = uptime // 86400
    uh = (uptime % 86400) // 3600
    um = (uptime % 3600) // 60
    us = uptime % 60
    t = myTime()
    reboot_str = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(t[2], t[1], t[0], t[3], t[4])
    fs = os.statvfs('/')
    fs_size = fs[0] * fs[2]
    fs_free = fs[0] * fs[3]
    ver = get_current_version()
    yield from resp.awrite(_head("Stats"))
    yield from resp.awrite('<h1>&#128200; System Stats</h1><div class="card"><table>')
    yield from resp.awrite('<tr><th>Firmware</th><td>' + ver + '</td></tr>')
    yield from resp.awrite('<tr><th>Free Memory</th><td>' + str(mem_free) + ' bytes</td></tr>')
    yield from resp.awrite('<tr><th>Uptime</th><td>{}d {}h {}m {}s</td></tr>'.format(ud, uh, um, us))
    yield from resp.awrite('<tr><th>Last Reboot</th><td>' + reboot_str + '</td></tr>')
    yield from resp.awrite('<tr><th>Storage Total</th><td>' + str(fs_size) + ' bytes</td></tr>')
    yield from resp.awrite('<tr><th>Storage Free</th><td>' + str(fs_free) + ' bytes</td></tr>')
    yield from resp.awrite('</table></div>')
    yield from resp.awrite('<form method="POST" action="/restart">'
                           '<input type="submit" value="Restart System" class="red">'
                           '</form>')
    yield from resp.awrite(_FOOT)

@app.route("/restart", methods=['POST'])
def restart(req, resp):
    yield from picoweb.start_response(resp)
    sendTelemetry("System restart initiated.")
    yield from resp.awrite(_head("Restarting"))
    yield from resp.awrite('<h1>Restarting system...</h1>' + _FOOT)
    time.sleep(1)
    reset()

# ── File downloads & telemetry ────────────────────────────────────────────────

@app.route("/config.json")
def config_json(req, resp):
    yield from picoweb.start_response(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps(load_config()))

@app.route("/rain_history.csv")
def download_rain_history(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    yield from resp.awrite("date,mm\n")
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        pass

@app.route("/telemetry")
def telemetry(req, resp):
    yield from picoweb.start_response(resp)
    try:
        fstat = os.stat(TELEMETRY_FILE)
        lm = time.localtime(fstat[8])
        lm_str = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(lm[2], lm[1], lm[0], lm[3], lm[4])
        fsize = str(fstat[6])
        yield from resp.awrite(_head("Telemetry"))
        yield from resp.awrite('<h1>&#128203; Telemetry</h1><div class="card">')
        yield from resp.awrite('<p>Updated: ' + lm_str + ' &mdash; ' + fsize + ' bytes &mdash; '
                               '<a href="/telemetry.csv">Download CSV</a></p>')
        yield from resp.awrite('<table><tr><th>Date</th><th>Message</th></tr>')
        with open(TELEMETRY_FILE, 'r') as f:
            lines = f.readlines()
        lines.reverse()
        for line in lines:
            date, msg = line.strip().split(',', 1)
            yield from resp.awrite('<tr><td>' + date + '</td><td>' + msg + '</td></tr>')
        yield from resp.awrite('</table></div>' + _FOOT)
    except OSError:
        yield from resp.awrite(_head("Telemetry"))
        yield from resp.awrite('<h1>No telemetry file found.</h1>' + _FOOT)

@app.route("/telemetry.csv")
def download_telemetry(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    try:
        with open(TELEMETRY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        yield from resp.awrite("Error: Telemetry file not found")

@app.route("/clear_telemetry", methods=['POST'])
def clear_telemetry(req, resp):
    try:
        os.remove(TELEMETRY_FILE)
        sendTelemetry("Telemetry cleared.")
        yield from picoweb.start_response(resp)
        yield from resp.awrite(_head("Cleared"))
        yield from resp.awrite('<h1>Telemetry cleared.</h1><p><a href="/">Return home</a></p>' + _FOOT)
    except OSError:
        yield from picoweb.start_response(resp)
        yield from resp.awrite(_head("Error"))
        yield from resp.awrite('<h1>Failed to clear telemetry.</h1><p><a href="/">Return home</a></p>' + _FOOT)

sendTelemetry("Webserver started")
app.run(debug=True, host="0.0.0.0", port=80)
