import picoweb
import ujson
import utime
from machine import Pin, Timer, reset
import time
from app.timesync import myTime
from app.telemetry import sendTelemetry
import gc
import os

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
    try:
        with open(CONFIG_FILE, 'r') as f:
            config_data_cache = ujson.loads(f.read())
            return config_data_cache
    except OSError:
        return {}

# Helper function to save the configuration to the file
def save_config(config):
    global config_data_cache
    with open(CONFIG_FILE, 'w') as f:
        f.write(ujson.dumps(config))
    config_data_cache = config

# Function to check if the current time matches the schedule
def check_schedule(schedule):
    current_time = myTime()
    current_hour = current_time[3]
    current_minute = current_time[4]
    current_weekday = current_time[6]  # 0 is Monday, 6 is Sunday

    # Convert schedule times to tuples of (hour, minute)
    schedule_times = [(int(t.split(':')[0]), int(t.split(':')[1])) for t in schedule['times'].split(',')]
    schedule_days = schedule['days']  # List of days when the sprinkler should be active
    schedule_durations = [int(d) for d in schedule['durations'].split(',')]  # List of durations in minutes

    # Check if today is a scheduled day
    weekday_str = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    if weekday_str[current_weekday] in schedule_days:
        for i, (hour, minute) in enumerate(schedule_times):
            # Check if the current time matches the schedule time
            if current_hour == hour and current_minute == minute:
                # Turn on the sprinkler for the scheduled duration
                formatted_date_time = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(current_time[2], current_time[1], current_time[0], current_time[3], current_time[4])
                sendTelemetry(f"activating Trigger at: {formatted_date_time}")
                activate_sprinker(schedule_durations[i] * 60)  # Convert minutes to seconds
                return True
            else:
                formatted_date_time = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(current_time[2], current_time[1], current_time[0], current_time[3], current_time[4])
                # sendTelemetry(f"Schedule checked at {formatted_date_time}, and no triggers activated")
    return False

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

@app.route("/config", methods=['GET', 'POST'])
def config(req, resp):
    global config_data_cache
    if req.method == "POST":
        yield from req.read_form_data()
        # Save the configuration to a file
        # Convert the days from a string with commas to a list
        days_str = req.form.get('days', '')
        days_list = [day.strip() for day in days_str.split(',')] if days_str else []
        config_data = {
            'times': req.form.get('times', ''),
            'durations': req.form.get('durations', ''),
            'days': days_list
        }
        save_config(config_data)
        yield from picoweb.start_response(resp)
        yield from resp.awrite("Configuration saved.")
    else:
        # Load the existing configuration
        config_data = load_config()
        yield from picoweb.start_response(resp)
        yield from resp.awrite(f"""<html><head>{CSS_STYLE}</head><body>
            {render_menu_button()}
            <form method="POST" action="/config">
                Times (comma-separated, 24hr format, e.g. 06:00,18:00):<br>
                <input type="text" name="times" value="{config_data.get('times', '')}"><br>
                Durations (comma-separated, minutes, e.g. 30,45):<br>
                <input type="text" name="durations" value="{config_data.get('durations', '')}"><br>
                Days (comma-separated, e.g. mon,tue,wed):<br>
                <input type="text" name="days" value="{','.join(config_data.get('days', []))}"><br><br>
                <input type="submit" value="Save">
            </form>
        </body></html>""")

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
