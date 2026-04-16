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

CONFIG_VERSION = 2
WEEKDAY_STR = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

def get_current_version():
    try:
        with open('app/.version', 'r') as f:
            return f.read().strip()
    except Exception:
        return 'unknown'

# Manual one-shot off timer state
_manual_timer = None
_manual_off_at = 0  # epoch seconds when the timer will expire (0 = no timer)

def _manual_clear_timer():
    global _manual_timer, _manual_off_at
    if _manual_timer is not None:
        try:
            _manual_timer.deinit()
        except Exception:
            pass
    _manual_timer = None
    _manual_off_at = 0

def _manual_off_callback(t):
    global _manual_timer, _manual_off_at
    try:
        Pin(17, Pin.OUT).value(0)
        sendTelemetry("Manual timer expired, sprinkler OFF")
    except Exception as e:
        sendTelemetry("Manual timer callback error: {}".format(e))
    _manual_timer = None
    _manual_off_at = 0

def manual_on_for(minutes):
    global _manual_timer, _manual_off_at
    _manual_clear_timer()
    Pin(17, Pin.OUT).value(1)
    period_ms = int(minutes * 60 * 1000)
    _manual_off_at = int(time.time()) + int(minutes * 60)
    _manual_timer = Timer(1)
    _manual_timer.init(period=period_ms, mode=Timer.ONE_SHOT, callback=_manual_off_callback)
    sendTelemetry("Sprinkler manually turned ON for {} min".format(minutes))

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

app = picoweb.WebApp(__name__)

# Define the path to the configuration file
CONFIG_FILE = 'sprinkler_config.json'
TELEMETRY_FILE = 'telemetry.csv'  # Define the path to the telemetry file
config_data_cache = None

def activate_sprinker(duration_in_sec):
    relay2 = Pin(17, Pin.OUT)
    if relay2.value() == 0:
        sendTelemetry("Sprinkler activated for {} seconds".format(duration_in_sec))
        relay2.value(1)
        time.sleep(duration_in_sec)
        relay2.value(0)
    else:
        sendTelemetry("Sprinkler is already running")

# Helper function to load the configuration from the file
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
    if not loaded or loaded.get('version') != CONFIG_VERSION:
        fresh = empty_config()
        save_config(fresh)
        return fresh
    # Ensure rain_skip block exists with all keys
    rs = loaded.get('rain_skip') or {}
    defaults = empty_config()['rain_skip']
    for k, v in defaults.items():
        if k not in rs:
            rs[k] = v
    loaded['rain_skip'] = rs
    if 'schedules' not in loaded:
        loaded['schedules'] = []
    config_data_cache = loaded
    return config_data_cache

# Helper function to save the configuration to the file
def save_config(config):
    global config_data_cache
    with open(CONFIG_FILE, 'w') as f:
        f.write(ujson.dumps(config))
    config_data_cache = config

RAIN_HISTORY_FILE = 'rain_history.csv'
RAIN_HISTORY_MAX_LINES = 365

def today_str(current_time):
    return "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])

def _append_rain_history(date_str, mm):
    """Append or update today's rain entry. Overwrites the same-date row if present."""
    lines = []
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            lines = f.readlines()
    except OSError:
        lines = []
    # Drop any existing row for this date, then append fresh
    kept = [ln for ln in lines if not ln.startswith(date_str + ',')]
    kept.append("{},{}\n".format(date_str, mm))
    # Trim to max lines (keep newest at tail)
    if len(kept) > RAIN_HISTORY_MAX_LINES:
        kept = kept[-RAIN_HISTORY_MAX_LINES:]
    try:
        with open(RAIN_HISTORY_FILE, 'w') as f:
            for ln in kept:
                f.write(ln)
    except Exception as e:
        sendTelemetry("Rain history write failed: {}".format(e))

def do_rain_check(config, force=False):
    """Fetch today's precipitation from Open-Meteo. Returns mm or None on failure.
    If force=False and today is already cached, returns the cached value without calling the API."""
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
        url = "https://api.open-meteo.com/v1/forecast?latitude={}&longitude={}&daily=precipitation_sum&timezone=auto&start_date={}&end_date={}".format(lat, lon, today, today)
        r = urequests.get(url)
        data = r.json()
        r.close()
        mm = float(data['daily']['precipitation_sum'][0] or 0.0)
        rs['last_check_date'] = today
        rs['last_check_mm'] = mm
        config['rain_skip'] = rs
        save_config(config)
        _append_rain_history(today, mm)
        sendTelemetry("Rain check for {}: {} mm".format(today, mm))
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
    """Called from schedule_checker every 30s. Performs one rain check per day
    once we've rolled past the configured collection hour (default 1 AM local)."""
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return
    lat = rs.get('latitude', 0.0)
    lon = rs.get('longitude', 0.0)
    if lat == 0.0 and lon == 0.0:
        return
    current_time = myTime()
    today = today_str(current_time)
    if rs.get('last_check_date') == today:
        return
    # Only auto-check after 1 AM local — gives Open-Meteo time to finalize yesterday's total
    # while still running at least once per day.
    if current_time[3] < 1:
        return
    do_rain_check(config, force=False)

# Function to check if the current time matches any schedule
def check_schedule(config):
    schedules = config.get('schedules') or []
    if not schedules:
        return False
    current_time = myTime()
    current_hour = current_time[3]
    current_minute = current_time[4]
    current_weekday = current_time[6]
    today_name = WEEKDAY_STR[current_weekday]
    fired = False
    for s in schedules:
        if not s.get('enabled'):
            continue
        if today_name not in s.get('days', []):
            continue
        t = s.get('time', '')
        try:
            h, m = t.split(':')
            h = int(h); m = int(m)
        except Exception:
            continue
        if current_hour != h or current_minute != m:
            continue
        name = s.get('name') or 'schedule {}'.format(s.get('id'))
        if should_skip_for_rain(config):
            rs = config.get('rain_skip') or {}
            sendTelemetry("Skipping {} - rain today: {} mm (threshold {} mm)".format(name, rs.get('last_check_mm', 0.0), rs.get('threshold_mm')))
            continue
        duration = int(s.get('duration', 0))
        sendTelemetry("Activating {} for {} min".format(name, duration))
        activate_sprinker(duration * 60)
        fired = True
    return fired

# Function to continuously check the schedule and activate the sprinkler
def schedule_checker(timer):
    config_data = load_config()
    if config_data:
        daily_rain_check_if_due(config_data)
        check_schedule(config_data)

# Set up a timer to periodically check the schedule
timer = Timer(-1)
timer.init(period=30000, mode=Timer.PERIODIC, callback=schedule_checker)  # Check every 30 seconds

# Define a simple CSS style to improve the UI
CSS_STYLE = """<style>
    body { font-family: Arial, sans-serif; margin: 40px; background-color: #f4f4f4; }
    h1 { color: #333; }
    ul { list-style-type: none; padding: 0; }
    li { margin: 10px 0; }
    a { text-decoration: none; color: #007bff; }
    a:hover { text-decoration: underline; }
    form > input[type=text], form > input[type=submit] { margin: 10px 0; }
    table { width: 100%; border-collapse: collapse; }
    th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
    th { background-color: #f2f2f2; }
    form { background-color: #fff; padding: 20px; border-radius: 8px; }
    input[type=submit] { background-color: #28a745; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; }
    input[type=submit]:hover { background-color: #218838; }
    .menu-button { position: fixed; top: 5px; left: 5px; z-index: 100; background-color: orange; padding: 5px; border-radius: 5px; }
    .menu-button a { font-size:12px; color: white; }
    .menu-button a:hover { color: #ccc; }
</style>"""

# Define a function to render the menu button
def render_menu_button():
    return '<div class="menu-button"><a href="/">Home</a></div>'

@app.route("/")
def index(req, resp):
    yield from picoweb.start_response(resp)
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
        {render_menu_button()}
        <h1>Welcome to the Smart Sprinkler System</h1>
        <ul>
            <li><a href="/config">Configure Sprinkler</a></li>
            <li><a href="/manual">Manual Control</a></li>
            <li><a href="/telemetry">View Telemetry Data</a></li>
            <li><a href="/config.json">Download Configuration</a></li>
            <li><a href="/telemetry.csv">Download Telemetry Data</a></li>
            <li><a href="/clear_telemetry">Clear Telemetry Data</a></li>
            <li><a href="/stats">View System Statistics</a></li>
        </ul>
    </body></html>""")

def render_schedule_block(s):
    sid = s.get('id')
    name = s.get('name', '')
    t = s.get('time', '06:00')
    dur = s.get('duration', 15)
    days = s.get('days', []) or []
    enabled_chk = 'checked' if s.get('enabled') else ''
    day_boxes = ''
    day_labels = [('mon', 'Mon'), ('tue', 'Tue'), ('wed', 'Wed'), ('thu', 'Thu'), ('fri', 'Fri'), ('sat', 'Sat'), ('sun', 'Sun')]
    for dkey, dlabel in day_labels:
        chk = 'checked' if dkey in days else ''
        day_boxes += '<label style="margin-right:8px"><input type="checkbox" name="day_{}" {}> {}</label>'.format(dkey, chk, dlabel)
    return (
        '<div style="background:#fff;padding:12px;border-radius:8px;margin-bottom:10px">'
        '<form method="POST" action="/config" style="display:inline-block;padding:0;background:transparent">'
        '<input type="hidden" name="action" value="update">'
        '<input type="hidden" name="id" value="{sid}">'
        '<label><input type="checkbox" name="enabled" {en}> Enabled</label> &nbsp; '
        'Name: <input type="text" name="name" value="{name}" style="width:140px"> &nbsp; '
        'Time: <input type="time" name="time" value="{t}"> &nbsp; '
        'Duration (min): <input type="number" name="duration" value="{dur}" min="1" max="240" style="width:70px"><br>'
        '{days}<br>'
        '<input type="submit" value="Save" style="padding:4px 10px">'
        '</form>'
        '<form method="POST" action="/config" style="display:inline-block;padding:0;background:transparent;margin-left:8px">'
        '<input type="hidden" name="action" value="delete">'
        '<input type="hidden" name="id" value="{sid}">'
        '<input type="submit" value="Delete" style="background-color:#dc3545;padding:4px 10px">'
        '</form>'
        '</div>'
    ).format(sid=sid, en=enabled_chk, name=name, t=t, dur=dur, days=day_boxes)

def render_add_form():
    day_labels = [('mon', 'Mon'), ('tue', 'Tue'), ('wed', 'Wed'), ('thu', 'Thu'), ('fri', 'Fri'), ('sat', 'Sat'), ('sun', 'Sun')]
    day_boxes = ''
    for dkey, dlabel in day_labels:
        day_boxes += '<label style="margin-right:8px"><input type="checkbox" name="day_{}"> {}</label>'.format(dkey, dlabel)
    return (
        '<form method="POST" action="/config">'
        '<input type="hidden" name="action" value="add">'
        'Name: <input type="text" name="name" value="" style="width:140px"> &nbsp; '
        'Time: <input type="time" name="time" value="06:00"> &nbsp; '
        'Duration (min): <input type="number" name="duration" value="15" min="1" max="240" style="width:70px"><br>'
        + day_boxes +
        '<br><input type="submit" value="Add Schedule">'
        '</form>'
    )

def render_rain_form(rs):
    en = 'checked' if rs.get('enabled') else ''
    threshold = rs.get('threshold_mm', 2.5)
    lat = rs.get('latitude', 0.0)
    lon = rs.get('longitude', 0.0)
    last_date = rs.get('last_check_date', '') or 'never'
    last_mm = rs.get('last_check_mm', 0.0)
    return (
        '<form method="POST" action="/config">'
        '<input type="hidden" name="action" value="rain_config">'
        '<label><input type="checkbox" name="enabled" {en}> Skip schedules when it has rained today</label><br>'
        'Threshold (mm): <input type="number" name="threshold_mm" step="0.1" value="{th}" style="width:80px"> &nbsp; '
        'Latitude: <input type="number" name="latitude" step="0.0001" value="{lat}" style="width:120px"> &nbsp; '
        'Longitude: <input type="number" name="longitude" step="0.0001" value="{lon}" style="width:120px"><br>'
        '<small>Last check: {ld} &mdash; {lm} mm</small><br>'
        '<input type="submit" value="Save Rain Settings">'
        '</form>'
        '<form method="POST" action="/config" style="margin-top:6px">'
        '<input type="hidden" name="action" value="rain_check_now">'
        '<input type="submit" value="Check Rain Now" style="background-color:#007bff">'
        '</form>'
    ).format(en=en, th=threshold, lat=lat, lon=lon, ld=last_date, lm=last_mm)

def render_rain_history():
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            lines = f.readlines()
    except OSError:
        return '<p><em>No rain history recorded yet.</em></p>'
    if not lines:
        return '<p><em>No rain history recorded yet.</em></p>'
    lines = lines[-14:]  # last 14 days
    lines.reverse()
    rows = ''
    for ln in lines:
        parts = ln.strip().split(',')
        if len(parts) >= 2:
            rows += '<tr><td>{}</td><td>{} mm</td></tr>'.format(parts[0], parts[1])
    return (
        '<table style="width:auto"><tr><th>Date</th><th>Rain</th></tr>'
        + rows + '</table>'
        '<p><small><a href="/rain_history.csv">Download full history</a></small></p>'
    )

@app.route("/config", methods=['GET', 'POST'])
def config(req, resp):
    cfg = load_config()
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
            cfg['schedules'].append({
                'id': new_id,
                'name': req.form.get('name', '') or 'Schedule {}'.format(new_id),
                'time': req.form.get('time', '06:00') or '06:00',
                'duration': duration,
                'days': days,
                'enabled': True,
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
        # re-load (cache is up to date) and fall through to render
        cfg = load_config()
    # Render page
    schedules_html = ''
    for s in cfg.get('schedules', []):
        schedules_html += render_schedule_block(s)
    if not schedules_html:
        schedules_html = '<p><em>No schedules yet.</em></p>'
    rain_html = render_rain_form(cfg.get('rain_skip') or {})
    add_html = render_add_form()
    yield from picoweb.start_response(resp)
    yield from resp.awrite('<html><head>' + CSS_STYLE + '</head><body>')
    yield from resp.awrite(render_menu_button())
    yield from resp.awrite('<h1>Sprinkler Configuration</h1>')
    yield from resp.awrite('<h2>Rain Skip</h2>')
    yield from resp.awrite(rain_html)
    yield from resp.awrite('<h3>Rain History (last 14 days)</h3>')
    yield from resp.awrite(render_rain_history())
    yield from resp.awrite('<h2>Schedules</h2>')
    yield from resp.awrite(schedules_html)
    yield from resp.awrite('<h2>Add New Schedule</h2>')
    yield from resp.awrite(add_html)
    yield from resp.awrite('</body></html>')

@app.route("/manual", methods=['GET', 'POST'])
def manual(req, resp):
    relay2 = Pin(17, Pin.OUT)
    message = ""
    if req.method == "POST":
        yield from req.read_form_data()
        action = req.form.get('action', '')
        if action == "on":
            _manual_clear_timer()
            relay2.value(1)
            sendTelemetry("Sprinkler manually turned ON")
            message = "Sprinkler turned ON."
        elif action == "on_timed":
            try:
                minutes = float(req.form.get('minutes', '5') or 5)
            except Exception:
                minutes = 5
            if minutes <= 0:
                minutes = 1
            if minutes > 240:
                minutes = 240
            manual_on_for(minutes)
            message = "Sprinkler turned ON for {} min.".format(minutes)
        elif action == "off":
            _manual_clear_timer()
            relay2.value(0)
            sendTelemetry("Sprinkler manually turned OFF")
            message = "Sprinkler turned OFF."
    state = "ON" if relay2.value() == 1 else "OFF"
    state_color = "#28a745" if state == "ON" else "#888"
    message_html = "<p><em>" + message + "</em></p>" if message else ""
    timer_html = ""
    if _manual_off_at:
        remaining = _manual_off_at - int(time.time())
        if remaining > 0:
            mins = remaining // 60
            secs = remaining % 60
            timer_html = "<p>Auto-off in: <strong>{}m {}s</strong></p>".format(mins, secs)
    yield from picoweb.start_response(resp)
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
        {render_menu_button()}
        <h1>Manual Sprinkler Control</h1>
        <p>Current state: <strong style="color:{state_color};">{state}</strong></p>
        {timer_html}
        {message_html}
        <form method="POST" action="/manual" style="display:inline-block; margin-right:10px;">
            <input type="hidden" name="action" value="on">
            <input type="submit" value="Turn ON" style="background-color:#28a745;">
        </form>
        <form method="POST" action="/manual" style="display:inline-block; margin-right:10px;">
            <input type="hidden" name="action" value="off">
            <input type="submit" value="Turn OFF" style="background-color:#dc3545;">
        </form>
        <form method="POST" action="/manual" style="display:inline-block;">
            <input type="hidden" name="action" value="on_timed">
            Minutes: <input type="number" name="minutes" value="5" min="1" max="240" step="1" style="width:70px">
            <input type="submit" value="Turn ON for N min" style="background-color:#007bff;">
        </form>
    </body></html>""")

@app.route("/api/sprinkler", methods=['GET', 'POST'])
def api_sprinkler(req, resp):
    relay2 = Pin(17, Pin.OUT)
    if req.method == "POST":
        qs = getattr(req, 'qs', '') or ''
        # Check for ?turnonfor=N
        turnonfor_str = ''
        for part in qs.split('&'):
            if part.startswith('turnonfor='):
                turnonfor_str = part.split('=', 1)[1]
                break
        if turnonfor_str:
            try:
                seconds = int(turnonfor_str)
            except Exception:
                seconds = 0
            if seconds <= 0:
                yield from picoweb.start_response(resp, status="400", content_type="application/json")
                yield from resp.awrite(ujson.dumps({"error": "turnonfor must be a positive integer (seconds)"}))
                return
            if seconds > 14400:
                seconds = 14400
            manual_on_for(seconds / 60.0)
        else:
            # Check for ?action=on|off
            action = ''
            for part in qs.split('&'):
                if part.startswith('action='):
                    action = part.split('=', 1)[1]
                    break
            if action == "on":
                _manual_clear_timer()
                relay2.value(1)
                sendTelemetry("Sprinkler turned ON via API")
            elif action == "off":
                _manual_clear_timer()
                relay2.value(0)
                sendTelemetry("Sprinkler turned OFF via API")
            else:
                yield from picoweb.start_response(resp, status="400", content_type="application/json")
                yield from resp.awrite(ujson.dumps({"error": "use ?action=on|off or ?turnonfor=SECONDS"}))
                return
    state = "on" if relay2.value() == 1 else "off"
    result = {"state": state}
    if _manual_off_at:
        remaining = _manual_off_at - int(time.time())
        if remaining > 0:
            result["auto_off_in_seconds"] = remaining
    yield from picoweb.start_response(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps(result))

@app.route("/stats")
def stats(req, resp):
    yield from picoweb.start_response(resp)
    # Get system statistics
    mem_free = gc.mem_free()
    uptime = utime.time()
    # Format uptime into a more readable format
    uptime_days = uptime // (24 * 3600)
    uptime_hours = (uptime % (24 * 3600)) // 3600
    uptime_minutes = (uptime % 3600) // 60
    uptime_seconds = uptime % 60
    formatted_uptime = "{} days, {} hours, {} minutes, {} seconds".format(uptime_days, uptime_hours, uptime_minutes, uptime_seconds)
    # Get the last reboot time
    last_reboot_time = myTime()
    formatted_reboot_time = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(last_reboot_time[2], last_reboot_time[1], last_reboot_time[0], last_reboot_time[3], last_reboot_time[4])
    
    # Get storage statistics
    fs_stat = os.statvfs('/')
    fs_size = fs_stat[0] * fs_stat[2]
    fs_free = fs_stat[0] * fs_stat[3]
    
    current_version = get_current_version()
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
        {render_menu_button()}
        <h1>System Statistics</h1>
        <ul>
            <li>Firmware Version: {current_version}</li>
            <li>Free Memory: {mem_free} bytes</li>
            <li>Uptime: {formatted_uptime}</li>
            <li>Last Reboot: {formatted_reboot_time}</li>
            <li>Total Storage: {fs_size} bytes</li>
            <li>Free Storage: {fs_free} bytes</li>
        </ul>
        <form method="POST" action="/restart">
            <input type="submit" value="Restart System">
        </form>
    </body></html>""")

@app.route("/restart", methods=['POST'])
def restart(req, resp):
    yield from picoweb.start_response(resp)
    sendTelemetry("System restart initiated.")
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>{render_menu_button()}<h1>Restarting system...</h1></body></html>""")
    time.sleep(1)  # Sleep for a short while to allow the response to be sent before restarting
    reset()

# Add a route to serve the configuration file
@app.route("/config.json")
def config_json(req, resp):
    yield from picoweb.start_response(resp, content_type="application/json")
    config_data = load_config()
    yield from resp.awrite(ujson.dumps(config_data))

# Add a route to download the rain history
@app.route("/rain_history.csv")
def download_rain_history(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    yield from resp.awrite("date,mm\n")
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        pass

# Add a route to serve the telemetry data
@app.route("/telemetry")
def telemetry(req, resp):
    yield from picoweb.start_response(resp)
    try:
        telemetry_file_stats = os.stat(TELEMETRY_FILE)
        last_modified = time.localtime(telemetry_file_stats[8])
        formatted_last_modified = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(last_modified[2], last_modified[1], last_modified[0], last_modified[3], last_modified[4])
        file_size = telemetry_file_stats[6]
        yield from resp.awrite(f"""<html><head>{CSS_STYLE}<meta http-equiv='refresh' content='10'></head><body>
            {render_menu_button()}
            <h2>Telemetry File: {TELEMETRY_FILE}</h2>
            <p>Last Updated: {formatted_last_modified}</p>
            <p>File Size: {file_size} bytes</p>
            <table>
                <tr><th>Date</th><th>Log Message</th></tr>""")
        with open(TELEMETRY_FILE, 'r') as f:
            telemetry_data = f.readlines()
            telemetry_data.reverse()  # Reverse the list to show the most recent first
            for line in telemetry_data:
                date, log_message = line.strip().split(',', 1)
                yield from resp.awrite(f"<tr><td>{date}</td><td>{log_message}</td></tr>")
        yield from resp.awrite("</table></body></html>")
    except OSError:
        yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>{render_menu_button()}<h1>Error: Telemetry file not found</h1></body></html>""")
# Add a route to download the telemetry data
@app.route("/telemetry.csv")
def download_telemetry(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    try:
        with open(TELEMETRY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        yield from resp.awrite("Error: Telemetry file not found")

# Add a route to clear the telemetry data
@app.route("/clear_telemetry", methods=['POST'])
def clear_telemetry(req, resp):
    try:
        os.remove(TELEMETRY_FILE)  # Delete the file
        sendTelemetry("Telemetry data cleared.")
        yield from picoweb.start_response(resp)
        yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>{render_menu_button()}<h1>Telemetry data cleared.</h1></body></html>""")
    except OSError:
        sendTelemetry("Failed to clear telemetry data.")
        yield from picoweb.start_response(resp)
        yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>{render_menu_button()}<h1>Error: Failed to clear telemetry data.</h1></body></html>""")

sendTelemetry("Webserver started")
app.run(debug=True, host="0.0.0.0", port=80)
