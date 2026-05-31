import picoweb
# Explicit imports - MicroPython v1.18 does not honor __all__ for `import *`,
# and `import *` silently skips underscore-prefixed names regardless.
from app.website_helpers import (
    ujson, utime, Pin, Timer, reset, time,
    myTime, sendTelemetry, gc, os,
    WEEKDAY_STR, TELEMETRY_FILE, RAIN_HISTORY_FILE,
    load_settings, save_settings, get_relay_by_id, get_current_version,
    VALID_PINS, read_pin,
    _zone_timers, _zone_clear_timer, manual_on_for,
    load_config, save_config, activate_sprinkler,
    do_rain_check, should_skip_for_rain, daily_rain_check_if_due,
    _head, _FOOT,
    _rain_form, _rain_history, _schedule_block, _add_form,
)

app = picoweb.WebApp(__name__)

# ── Schedule checker ──────────────────────────────────────────────────────────

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
            sendTelemetry("Skip {} rain {}mm".format(name, rs.get('last_check_mm', 0.0)))
            continue
        duration = int(s.get('duration', 0))
        zone_id = s.get('zone', 1)
        relay_info = get_relay_by_id(zone_id)
        gpio = relay_info['gpio'] if relay_info else 17
        sendTelemetry("Run {} z{} {}min".format(name, zone_id, duration))
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
        state = read_pin(gpio)
        if state is None:
            badge = '<span class="off">BAD PIN</span>'
        elif state:
            badge = '<span class="on">ON</span>'
        else:
            badge = '<span class="off">OFF</span>'
        timer_html = ''
        if rid in _zone_timers:
            rem = _zone_timers[rid]['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<br><small>Off in {}m{}s</small>'.format(rem // 60, rem % 60)
        yield from resp.awrite(
            '<div class="card"><h3>' + name + '</h3>' + badge + timer_html +
            '<br><small>GPIO ' + str(gpio) + '</small></div>'
        )
    yield from resp.awrite('</div><div class="card"><ul>'
                           '<li><a href="/manual">&#9654; Manual Control</a></li>'
                           '<li><a href="/config">&#128198; Schedules</a></li>'
                           '<li><a href="/telemetry">&#128203; Telemetry</a></li>'
                           '<li><a href="/stats">&#128200; Stats</a></li>'
                           '</ul></div>')
    yield from resp.awrite(_FOOT)

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
                sendTelemetry("{} ON".format(zname))
                message = "{} turned ON.".format(zname)
            elif action == "off":
                _zone_clear_timer(zone_id)
                Pin(gpio, Pin.OUT).value(0)
                sendTelemetry("{} OFF".format(zname))
                message = "{} turned OFF.".format(zname)
            elif action == "on_timed":
                try:
                    minutes = float(req.form.get('minutes', '5') or 5)
                except Exception:
                    minutes = 5
                minutes = max(1.0, min(240.0, minutes))
                manual_on_for(minutes, gpio, zone_id)
                message = "{} ON for {}min.".format(zname, minutes)
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Manual"))
    yield from resp.awrite('<h1>&#9654; Manual Control</h1>')
    if message:
        yield from resp.awrite('<div class="msg">' + message + '</div>')
    yield from resp.awrite('<div class="grid">')
    for r in relays:
        gpio = r['gpio']
        rid = r['id']
        name = r.get('name', 'Zone {}'.format(rid))
        state = read_pin(gpio)
        if state is None:
            badge = '<span class="off">BAD PIN</span>'
        elif state:
            badge = '<span class="on">ON</span>'
        else:
            badge = '<span class="off">OFF</span>'
        timer_html = ''
        if rid in _zone_timers:
            rem = _zone_timers[rid]['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<p><small>Off in {}m{}s</small></p>'.format(rem // 60, rem % 60)
        rid_s = str(rid)
        yield from resp.awrite(
            '<div class="card"><h3>' + name + '</h3><p>' + badge + '</p>' + timer_html +
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
            '<input type="number" name="minutes" value="5" min="1" max="240" style="width:55px">min '
            '<input type="submit" value="Timed ON" class="blu"></form>'
            '</div>'
        )
    yield from resp.awrite('</div>' + _FOOT)

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
                'duration': duration, 'days': days, 'enabled': True, 'zone': zone,
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
    yield from resp.awrite('<h2>Rain History (14 days)</h2>')
    yield from resp.awrite(_rain_history())
    yield from resp.awrite('<h2>Schedules</h2>')
    for s in cfg.get('schedules', []):
        yield from resp.awrite(_schedule_block(s, relays))
    if not cfg.get('schedules'):
        yield from resp.awrite('<p><em>No schedules yet.</em></p>')
    yield from resp.awrite('<h2>Add Schedule</h2>')
    yield from resp.awrite(_add_form(relays))
    yield from resp.awrite(_FOOT)

@app.route("/settings", methods=['GET', 'POST'])
def settings_page(req, resp):
    s = load_settings()
    message = ""
    if req.method == "POST":
        yield from req.read_form_data()
        updated = []
        rejected = []
        for r in s['relays']:
            rid = r['id']
            rids = str(rid)
            name = req.form.get('name_' + rids, r.get('name', 'Zone ' + rids)) or 'Zone ' + rids
            try:
                gpio = int(req.form.get('gpio_' + rids, str(r['gpio'])) or r['gpio'])
            except Exception:
                gpio = r['gpio']
            if gpio not in VALID_PINS:
                rejected.append("Zone {} GPIO {} (kept {})".format(rid, gpio, r['gpio']))
                gpio = r['gpio']
            updated.append({"id": rid, "gpio": gpio, "name": name})
        s['relays'] = updated
        save_settings(s)
        if rejected:
            message = "Saved. Invalid pins rejected: " + ", ".join(rejected)
        else:
            message = "Settings saved."
    yield from picoweb.start_response(resp)
    yield from resp.awrite(_head("Settings"))
    yield from resp.awrite('<h1>&#9881; Settings</h1>')
    if message:
        yield from resp.awrite('<div class="msg">' + message + '</div>')
    yield from resp.awrite('<div class="card"><h2>Zone GPIO</h2>'
                           '<form method="POST" action="/settings">'
                           '<table><tr><th>Zone</th><th>Name</th><th>GPIO</th></tr>')
    for r in s['relays']:
        rid = r['id']
        rids = str(rid)
        yield from resp.awrite(
            '<tr><td>Zone ' + rids + '</td>'
            '<td><input type="text" name="name_' + rids + '" value="' + r.get('name', 'Zone ' + rids) + '" style="width:120px"></td>'
            '<td><input type="number" name="gpio_' + rids + '" value="' + str(r['gpio']) + '" min="0" max="39" style="width:65px"></td>'
            '</tr>'
        )
    yield from resp.awrite('</table><br><input type="submit" value="Save Settings"></form></div>')
    yield from resp.awrite('<div class="card"><h2>Data</h2><ul>'
                           '<li><a href="/config.json">&#8681; sprinkler_config.json</a></li>'
                           '<li><a href="/telemetry.csv">&#8681; telemetry.csv</a></li>'
                           '<li><a href="/rain_history.csv">&#8681; rain_history.csv</a></li>'
                           '</ul>'
                           '<form method="POST" action="/clear_telemetry" style="margin-top:8px">'
                           '<input type="submit" value="Clear Telemetry" class="red"></form></div>')
    yield from resp.awrite(_FOOT)

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
            manual_on_for(min(seconds, 14400) / 60.0, gpio, zone_id)
        elif action == "on":
            _zone_clear_timer(zone_id)
            relay_pin.value(1)
            sendTelemetry("Zone {} ON API".format(zone_id))
        elif action == "off":
            _zone_clear_timer(zone_id)
            relay_pin.value(0)
            sendTelemetry("Zone {} OFF API".format(zone_id))
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
    ver = get_current_version()
    yield from resp.awrite(_head("Stats"))
    yield from resp.awrite('<h1>&#128200; Stats</h1><div class="card"><table>')
    yield from resp.awrite('<tr><th>Firmware</th><td>' + ver + '</td></tr>')
    yield from resp.awrite('<tr><th>Free RAM</th><td>' + str(mem_free) + ' bytes</td></tr>')
    yield from resp.awrite('<tr><th>Uptime</th><td>{}d {}h {}m {}s</td></tr>'.format(ud, uh, um, us))
    yield from resp.awrite('<tr><th>Last Reboot</th><td>' + reboot_str + '</td></tr>')
    yield from resp.awrite('<tr><th>Storage</th><td>{} / {} bytes free</td></tr>'.format(
        fs[0] * fs[3], fs[0] * fs[2]))
    yield from resp.awrite('</table></div>')
    yield from resp.awrite('<form method="POST" action="/restart">'
                           '<input type="submit" value="Restart" class="red"></form>')
    yield from resp.awrite(_FOOT)

@app.route("/restart", methods=['POST'])
def restart(req, resp):
    yield from picoweb.start_response(resp)
    sendTelemetry("Restart initiated.")
    yield from resp.awrite(_head("Restarting"))
    yield from resp.awrite('<h1>Restarting...</h1>' + _FOOT)
    time.sleep(1)
    reset()

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
        yield from resp.awrite(_head("Telemetry"))
        yield from resp.awrite('<h1>&#128203; Telemetry</h1><div class="card">')
        yield from resp.awrite('<p>' + lm_str + ' &mdash; ' + str(fstat[6]) + ' bytes &mdash; '
                               '<a href="/telemetry.csv">Download</a></p>')
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
        yield from resp.awrite('<h1>No telemetry file.</h1>' + _FOOT)

@app.route("/telemetry.csv")
def download_telemetry(req, resp):
    yield from picoweb.start_response(resp, content_type="text/csv")
    try:
        with open(TELEMETRY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        yield from resp.awrite("Error: file not found")

@app.route("/clear_telemetry", methods=['POST'])
def clear_telemetry(req, resp):
    try:
        os.remove(TELEMETRY_FILE)
        sendTelemetry("Telemetry cleared.")
        yield from picoweb.start_response(resp)
        yield from resp.awrite(_head("Cleared"))
        yield from resp.awrite('<h1>Telemetry cleared.</h1><p><a href="/">Home</a></p>' + _FOOT)
    except OSError:
        yield from picoweb.start_response(resp)
        yield from resp.awrite(_head("Error"))
        yield from resp.awrite('<h1>Failed to clear telemetry.</h1><p><a href="/">Home</a></p>' + _FOOT)

sendTelemetry("Webserver started")
app.run(debug=True, host="0.0.0.0", port=80)
