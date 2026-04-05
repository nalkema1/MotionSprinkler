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

def today_str(current_time):
    return "{:04d}-{:02d}-{:02d}".format(current_time[0], current_time[1], current_time[2])

def should_skip_for_rain(config):
    rs = config.get('rain_skip') or {}
    if not rs.get('enabled'):
        return False
    lat = rs.get('latitude', 0.0)
    lon = rs.get('longitude', 0.0)
    if lat == 0.0 and lon == 0.0:
        return False
    threshold = rs.get('threshold_mm', 2.5)
    current_time = myTime()
    today = today_str(current_time)
    mm = rs.get('last_check_mm', 0.0)
    if rs.get('last_check_date') != today:
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
            sendTelemetry("Rain check for {}: {} mm".format(today, mm))
        except Exception as e:
            sendTelemetry("Rain check failed: {}".format(e))
            return False
    return mm >= threshold

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
    ).format(en=en, th=threshold, lat=lat, lon=lon, ld=last_date, lm=last_mm)

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
            relay2.value(1)
            sendTelemetry("Sprinkler manually turned ON")
            message = "Sprinkler turned ON."
        elif action == "off":
            relay2.value(0)
            sendTelemetry("Sprinkler manually turned OFF")
            message = "Sprinkler turned OFF."
    state = "ON" if relay2.value() == 1 else "OFF"
    state_color = "#28a745" if state == "ON" else "#888"
    message_html = "<p><em>" + message + "</em></p>" if message else ""
    yield from picoweb.start_response(resp)
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
        {render_menu_button()}
        <h1>Manual Sprinkler Control</h1>
        <p>Current state: <strong style="color:{state_color};">{state}</strong></p>
        {message_html}
        <form method="POST" action="/manual" style="display:inline-block; margin-right:10px;">
            <input type="hidden" name="action" value="on">
            <input type="submit" value="Turn ON" style="background-color:#28a745;">
        </form>
        <form method="POST" action="/manual" style="display:inline-block;">
            <input type="hidden" name="action" value="off">
            <input type="submit" value="Turn OFF" style="background-color:#dc3545;">
        </form>
    </body></html>""")

@app.route("/api/sprinkler", methods=['GET', 'POST'])
def api_sprinkler(req, resp):
    relay2 = Pin(17, Pin.OUT)
    action = None
    if req.method == "POST":
        try:
            yield from req.read_form_data()
            action = req.form.get('action', '')
        except Exception:
            action = ''
        if not action:
            # Fall back to query string (?action=on|off)
            qs = getattr(req, 'qs', '') or ''
            for part in qs.split('&'):
                if part.startswith('action='):
                    action = part.split('=', 1)[1]
                    break
        if action == "on":
            relay2.value(1)
            sendTelemetry("Sprinkler turned ON via API")
        elif action == "off":
            relay2.value(0)
            sendTelemetry("Sprinkler turned OFF via API")
        else:
            yield from picoweb.start_response(resp, status="400", content_type="application/json")
            yield from resp.awrite(ujson.dumps({"error": "action must be 'on' or 'off'"}))
            return
    state = "on" if relay2.value() == 1 else "off"
    yield from picoweb.start_response(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps({"state": state}))

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
    
    yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
        {render_menu_button()}
        <h1>System Statistics</h1>
        <ul>
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
