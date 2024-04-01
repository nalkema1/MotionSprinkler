import picoweb
import ujson
import utime
from machine import Pin, Timer
import time
from app.timesync import myTime
from app.telemetry import sendTelemetry


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
                sendTelemetry(f"Schedule checked at {formatted_date_time}, and no triggers activated")
    return False

# Function to continuously check the schedule and activate the sprinkler
def schedule_checker(timer):
    config_data = load_config()
    if config_data:
        check_schedule(config_data)

# Set up a timer to periodically check the schedule
timer = Timer(-1)
timer.init(period=30000, mode=Timer.PERIODIC, callback=schedule_checker)  # Check every 30 seconds

@app.route("/")
def index(req, resp):
    yield from picoweb.start_response(resp)
    yield from resp.awrite("""<html><body>
        <h1>Welcome to the Smart Sprinkler System</h1>
        <ul>
            <li><a href="/config">Configure Sprinkler</a></li>
            <li><a href="/telemetry">View Telemetry Data</a></li>
            <li><a href="/config.json">Download Configuration</a></li>
            <li><a href="/telemetry.csv">Download Telemetry Data</a></li>
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
        yield from resp.awrite("""<html><body>
            <form method="POST" action="/config">
                Times (comma-separated, 24hr format, e.g. 06:00,18:00):<br>
                <input type="text" name="times" value="{times}"><br>
                Durations (comma-separated, minutes, e.g. 30,45):<br>
                <input type="text" name="durations" value="{durations}"><br>
                Days (comma-separated, e.g. mon,tue,wed):<br>
                <input type="text" name="days" value="{days}"><br><br>
                <input type="submit" value="Save">
            </form>
        </body></html>""".format(times=config_data.get('times', ''),
                                  durations=config_data.get('durations', ''),
                                  days=','.join(config_data.get('days', []))))

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
        with open(TELEMETRY_FILE, 'r') as f:
            telemetry_data = f.readlines()
            telemetry_data.reverse()  # Reverse the list to show the most recent first
            yield from resp.awrite("<html><head><meta http-equiv='refresh' content='10'></head><body><table>")
            yield from resp.awrite("<tr><th>Date</th><th>Log Message</th></tr>")
            for line in telemetry_data:
                date, log_message = line.strip().split(',', 1)
                yield from resp.awrite(f"<tr><td>{date}</td><td>{log_message}</td></tr>")
            yield from resp.awrite("</table></body></html>")
    except OSError:
        yield from resp.awrite("<html><body><h1>Error: Telemetry file not found</h1></body></html>")

# Add a route to download the telemetry data
@app.route("/telemetry.csv")
def download_telemetry(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    try:
        with open(TELEMETRY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        yield from resp.awrite("Error: Telemetry file not found")

app.run(debug=True, host="0.0.0.0", port=80)