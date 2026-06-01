import picoweb
# Explicit imports - MicroPython v1.18 does not honor __all__ for `import *`,
# and `import *` silently skips underscore-prefixed names regardless.
from app.website_helpers import (
    ujson, utime, Pin, Timer, reset, time,
    myTime, sendTelemetry, gc, os,
    WEEKDAY_STR, TELEMETRY_FILE, RAIN_HISTORY_FILE,
    load_settings, save_settings, get_relay_by_id, get_current_version,
    VALID_PINS, read_pin, _esc,
    _zone_timers, _zone_clear_timer, manual_on_for,
    load_config, save_config, activate_sprinkler,
    do_rain_check, should_skip_for_rain, daily_rain_check_if_due,
    _head, _FOOT,
    _rain_form, _rain_history, _schedule_block, _add_form,
)

app = picoweb.WebApp(__name__)

def _sr(resp, content_type="text/html; charset=utf-8", status="200", headers=None):
    # Wrap picoweb.start_response to always send "Connection: close". Otherwise
    # the browser may reuse a keep-alive socket that this server has already
    # closed; that lost request shows up as an intermittent blank page, and a
    # refresh (on a fresh connection) then works.
    h = {"Connection": "close"}
    if headers:
        try:
            h.update(headers)
        except Exception:
            pass
    return picoweb.start_response(resp, content_type=content_type, status=status, headers=h)

# ── Schedule checker ──────────────────────────────────────────────────────────

# schedule id -> "YYYY-MM-DD HH:MM" it last fired. The 30s checker can match
# the same minute twice; this guarantees one trigger per scheduled minute.
_last_fired = {}

def check_schedule(config):
    schedules = config.get('schedules') or []
    if not schedules:
        return False
    ct = myTime()
    cur_h = ct[3]
    cur_m = ct[4]
    today_name = WEEKDAY_STR[ct[6]]
    minute_key = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(ct[0], ct[1], ct[2], cur_h, cur_m)
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
        sid = s.get('id')
        if _last_fired.get(sid) == minute_key:
            continue  # already handled this schedule during the current minute
        name = s.get('name') or 'schedule {}'.format(sid)
        if should_skip_for_rain(config):
            rs = config.get('rain_skip') or {}
            sendTelemetry("Skip {} rain {}mm".format(name, rs.get('last_check_mm', 0.0)))
            _last_fired[sid] = minute_key
            continue
        duration = int(s.get('duration', 0))
        zone_id = s.get('zone', 1)
        relay_info = get_relay_by_id(zone_id)
        gpio = relay_info['gpio'] if relay_info else 17
        sendTelemetry("Run {} z{} {}min".format(name, zone_id, duration))
        # Non-blocking: turn the zone on now and let the 1 Hz poll timer switch
        # it off after `duration` minutes. The old activate_sprinkler() called
        # time.sleep(duration*60), which froze the entire web server (and every
        # other zone/timer) for the whole watering period - the device answered
        # pings but the UI was dead until watering finished.
        manual_on_for(duration, gpio, zone_id)
        _last_fired[sid] = minute_key
        fired = True
    return fired

_hb_count = 0

def schedule_checker(timer):
    global _hb_count
    try:
        _hb_count += 1
        if _hb_count % 20 == 0:  # ~ every 10 min (20 x 30s) - liveness heartbeat
            try:
                sendTelemetry("alive uptime={}s mem={}".format(utime.time(), gc.mem_free()))
            except Exception:
                pass
        config_data = load_config()
        if config_data:
            # check_schedule first so watering is never delayed/blocked by the
            # once-a-day background rain fetch (the only network call here).
            check_schedule(config_data)
            daily_rain_check_if_due(config_data)
    except Exception as e:
        try:
            sendTelemetry("schedule_checker error: {}".format(e))
        except Exception:
            pass

_sched_timer = Timer(-1)
_sched_timer.init(period=30000, mode=Timer.PERIODIC, callback=schedule_checker)

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index(req, resp):
    relays = load_settings()['relays']
    yield from _sr(resp)
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
        info = _zone_timers.get(rid)
        if info:
            rem = info['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<br><small>Off in {}m{}s</small>'.format(rem // 60, rem % 60)
        yield from resp.awrite(
            '<div class="card"><h3>' + _esc(name) + '</h3>' + badge + timer_html +
            '<br><small>GPIO ' + str(gpio) + '</small></div>'
        )
    # Big buttons are for phones only; wide screens use the top menu.
    yield from resp.awrite('</div>'
                           '<div class="mobile-only">'
                           '<a class="bigbtn" href="/manual">&#9654; Manual Control</a>'
                           '<a class="bigbtn" href="/schedule">&#128198; Schedules</a>'
                           '<a class="bigbtn" href="/settings">&#9881; Settings</a>'
                           '<a class="bigbtn" href="/stats">&#128200; System Stats</a>'
                           '<a class="bigbtn" href="/telemetry">&#128203; Telemetry Log</a>'
                           '<a class="bigbtn" href="/help">&#10067; Help</a>'
                           '</div>'
                           + _FOOT)

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
    yield from _sr(resp)
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
        info = _zone_timers.get(rid)
        if info:
            rem = info['off_at'] - int(time.time())
            if rem > 0:
                timer_html = '<p><small>Off in {}m{}s</small></p>'.format(rem // 60, rem % 60)
        rid_s = str(rid)
        yield from resp.awrite(
            '<div class="card"><h3>' + _esc(name) + '</h3><p>' + badge + '</p>' + timer_html +
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

@app.route("/schedule", methods=['GET', 'POST'])
def schedule_page(req, resp):
    cfg = load_config()
    relays = load_settings()['relays']
    message = ""
    if req.method == "POST":
        yield from req.read_form_data()
        action = req.form.get('action', '')
        if action == 'add':
            new_id = 1
            if cfg['schedules']:
                new_id = max(x.get('id', 0) for x in cfg['schedules']) + 1
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
            message = "Schedule added."
        elif action == 'delete':
            try:
                del_id = int(req.form.get('id', '0'))
                cfg['schedules'] = [x for x in cfg['schedules'] if x.get('id') != del_id]
                save_config(cfg)
                message = "Schedule deleted."
            except Exception:
                pass
        elif action == 'update':
            try:
                up_id = int(req.form.get('id', '0'))
                for sc in cfg['schedules']:
                    if sc.get('id') == up_id:
                        sc['enabled'] = bool(req.form.get('enabled'))
                        sc['name'] = req.form.get('name', sc.get('name', ''))
                        sc['time'] = req.form.get('time', sc.get('time', '06:00'))
                        try:
                            sc['duration'] = int(req.form.get('duration', sc.get('duration', 15)))
                        except Exception:
                            pass
                        sc['days'] = [d for d in WEEKDAY_STR if req.form.get('day_' + d)]
                        try:
                            sc['zone'] = int(req.form.get('zone', sc.get('zone', 1)) or 1)
                        except Exception:
                            pass
                        break
                save_config(cfg)
                message = "Schedule saved."
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
            message = "Rain settings saved."
        # Post/Redirect/Get: reply with a 303 redirect instead of re-rendering
        # the full page inline. The inline re-render ran while the parsed POST
        # form data was still in memory; on a near-full heap that render could
        # fail and return a blank page after Save/Delete even though the change
        # was saved. Redirecting rebuilds the page on a fresh GET with the
        # form-data memory released.
        yield from _sr(resp, status="303", headers={"Location": "/schedule"})
        return

    yield from _sr(resp)
    yield from resp.awrite(_head("Schedule"))
    head = '<h1>&#128198; Schedule</h1>'
    if message:
        head += '<div class="msg">' + message + '</div>'
    head += ('<p><small>Each entry runs one zone at one time. To water a zone at '
             'several times, add a schedule for each time. Schedules are grouped by zone below.</small></p>')
    yield from resp.awrite(head)

    # Schedules grouped by zone. Manual grouping (plain loops) avoids
    # sorted(key=...), which is unreliable on this MicroPython build. Each
    # block is rendered defensively so one bad entry can't blank the page.
    scheds = cfg.get('schedules', [])
    known = [r['id'] for r in relays]
    blocks = ''
    for r in relays:
        for sc in scheds:
            if sc.get('zone', 1) == r['id']:
                try:
                    blocks += _schedule_block(sc, relays)
                except Exception:
                    pass
    for sc in scheds:
        if sc.get('zone', 1) not in known:
            try:
                blocks += _schedule_block(sc, relays)
            except Exception:
                pass
    if not blocks:
        blocks = '<p><em>No schedules yet. Add one below.</em></p>'
    yield from resp.awrite('<h2>Schedules</h2>' + blocks)
    yield from resp.awrite('<h2>Add Schedule</h2>' + _add_form(relays))

    yield from resp.awrite('<h2>Rain Skip</h2>' + _rain_form(cfg.get('rain_skip') or {}))
    yield from resp.awrite('<h2>Rain History (14 days)</h2>' + _rain_history())
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
        message = "Saved. Invalid pins rejected: " + ", ".join(rejected) if rejected else "Settings saved."
        s = load_settings()

    yield from _sr(resp)
    yield from resp.awrite(_head("Settings"))
    head = '<h1>&#9881; Settings</h1>'
    if message:
        head += '<div class="msg">' + message + '</div>'
    zone_rows = ''
    for r in s['relays']:
        rids = str(r['id'])
        zone_rows += ('<tr><td>Zone ' + rids + '</td>'
                      '<td><input type="text" name="name_' + rids + '" value="' + _esc(r.get('name', 'Zone ' + rids)) + '" style="width:120px"></td>'
                      '<td><input type="number" name="gpio_' + rids + '" value="' + str(r['gpio']) + '" min="0" max="39" style="width:65px"></td></tr>')
    head += ('<div class="card"><h2>Zones</h2>'
             '<form method="POST" action="/settings">'
             '<table><tr><th>Zone</th><th>Name</th><th>GPIO</th></tr>' + zone_rows +
             '</table><br><input type="submit" value="Save Zones"></form></div>')
    head += ('<p><small>Set schedules on the <a href="/schedule">Schedule</a> page.</small></p>')
    yield from resp.awrite(head)

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
        yield from _sr(resp, status="400", content_type="application/json")
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
                yield from _sr(resp, status="400", content_type="application/json")
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
            yield from _sr(resp, status="400", content_type="application/json")
            yield from resp.awrite(ujson.dumps({"error": "use ?action=on|off or ?turnonfor=SECONDS"}))
            return
    state = "on" if relay_pin.value() == 1 else "off"
    result = {"state": state, "zone": zone_id, "gpio": gpio}
    info = _zone_timers.get(zone_id)
    if info:
        rem = info['off_at'] - int(time.time())
        if rem > 0:
            result["auto_off_in_seconds"] = rem
    yield from _sr(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps(result))

@app.route("/stats")
def stats(req, resp):
    yield from _sr(resp)
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
    yield from _sr(resp)
    sendTelemetry("Restart initiated.")
    yield from resp.awrite(_head("Restarting"))
    yield from resp.awrite('<h1>Restarting...</h1>' + _FOOT)
    time.sleep(1)
    reset()

@app.route("/config.json")
def config_json(req, resp):
    yield from _sr(resp, content_type="application/json")
    yield from resp.awrite(ujson.dumps(load_config()))

@app.route("/rain_history.csv")
def download_rain_history(req, resp):
    yield from _sr(resp, content_type="text/csv")
    yield from resp.awrite("date,mm\n")
    try:
        with open(RAIN_HISTORY_FILE, 'r') as f:
            yield from resp.awrite(f.read())
    except OSError:
        pass

@app.route("/telemetry")
def telemetry(req, resp):
    yield from _sr(resp)
    try:
        fstat = os.stat(TELEMETRY_FILE)
        lm = time.localtime(fstat[8])
        lm_str = "{:02d}/{:02d}/{:04d} {:02d}:{:02d}".format(lm[2], lm[1], lm[0], lm[3], lm[4])
        yield from resp.awrite(_head("Telemetry"))
        yield from resp.awrite('<h1>&#128203; Telemetry</h1><div class="card">')
        yield from resp.awrite('<p><small>Updated ' + lm_str + ' &mdash; ' + str(fstat[6]) + ' bytes</small></p>'
                               '<p><a href="/telemetry" class="bigbtn" style="display:inline-block;padding:8px 14px">&#8635; Refresh</a> '
                               '<a href="/telemetry.csv" class="bigbtn blu" style="display:inline-block;padding:8px 14px;background:#2471a3">&#8681; Download CSV</a></p>')
        yield from resp.awrite('<table><tr><th>Date</th><th>Message</th></tr>')
        with open(TELEMETRY_FILE, 'r') as f:
            lines = f.readlines()
        # Show only the most recent ~120 entries: bounds memory/response size on
        # this (heaviest) page and keeps the newest events first.
        lines = lines[-120:]
        lines.reverse()
        for line in lines:
            parts = line.strip().split(',', 1)
            if len(parts) == 2:
                yield from resp.awrite('<tr><td>' + parts[0] + '</td><td>' + _esc(parts[1]) + '</td></tr>')
        yield from resp.awrite('</table></div>'
                               '<p><small>Showing the most recent entries. '
                               'Use Download CSV for the full log.</small></p>' + _FOOT)
    except OSError:
        yield from resp.awrite(_head("Telemetry"))
        yield from resp.awrite('<h1>No telemetry file.</h1>' + _FOOT)

@app.route("/telemetry.csv")
def download_telemetry(req, resp):
    yield from _sr(resp, content_type="text/csv")
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
        yield from _sr(resp)
        yield from resp.awrite(_head("Cleared"))
        yield from resp.awrite('<h1>Telemetry cleared.</h1><p><a href="/">Home</a></p>' + _FOOT)
    except OSError:
        yield from _sr(resp)
        yield from resp.awrite(_head("Error"))
        yield from resp.awrite('<h1>Failed to clear telemetry.</h1><p><a href="/">Home</a></p>' + _FOOT)

@app.route("/favicon.ico")
def favicon(req, resp):
    # Answer the browser's favicon request cheaply so it doesn't open a second
    # competing connection (picoweb serves one request at a time).
    yield from _sr(resp, status="204", headers={"Content-Length": "0"})

@app.route("/help")
def help_page(req, resp):
    yield from _sr(resp)
    yield from resp.awrite(_head("Help"))
    yield from resp.awrite('<h1>&#10067; Help</h1>')
    yield from resp.awrite(
        '<div class="card"><h2>Menu</h2><ul>'
        '<li><b>Garden Sprinkler</b> (top-left) &mdash; Home dashboard; live ON/OFF state of every zone.</li>'
        '<li><b>Manual</b> &mdash; Turn each zone on or off by hand, or run a zone for a set number of minutes.</li>'
        '<li><b>Schedule</b> &mdash; Create timed runs. Each entry waters one zone at one time on the days you pick; '
        'add several entries to run a zone at multiple times. Rain Skip settings live here too.</li>'
        '<li><b>Settings</b> &mdash; Name each zone and set its GPIO pin (saved across reboots), plus data downloads.</li>'
        '<li><b>Stats</b> &mdash; Firmware version, free memory, uptime, storage and a Restart button.</li>'
        '<li><b>Telemetry</b> &mdash; Event log you can watch and download as CSV.</li>'
        '</ul></div>')
    yield from resp.awrite(
        '<div class="card"><h2>Remote API</h2>'
        '<p>Control zones over HTTP. Replace &lt;ip&gt; with this device\'s address. '
        'Zone defaults to 1 if omitted.</p>'
        '<table><tr><th>Request</th><th>What it does</th></tr>'
        '<tr><td>GET /api/sprinkler?zone=N</td><td>Return zone N state as JSON</td></tr>'
        '<tr><td>POST /api/sprinkler?zone=N&amp;action=on</td><td>Turn zone N on</td></tr>'
        '<tr><td>POST /api/sprinkler?zone=N&amp;action=off</td><td>Turn zone N off</td></tr>'
        '<tr><td>POST /api/sprinkler?zone=N&amp;turnonfor=SECONDS</td><td>Run zone N for SECONDS, then auto-off (max 14400)</td></tr>'
        '</table>'
        '<p><small>Example: <code>curl -X POST "http://&lt;ip&gt;/api/sprinkler?zone=2&amp;turnonfor=300"</code></small></p>'
        '<p><small>Response: <code>{"state":"on","zone":2,"gpio":19,"auto_off_in_seconds":295}</code></small></p>'
        '<p><small><b>No authentication</b> &mdash; keep this device on a trusted network only.</small></p>'
        '</div>')
    yield from resp.awrite(
        '<div class="card"><h2>About</h2>'
        '<p>MotionSprinkler firmware for ESP32 (MicroPython).</p>'
        '<p>&copy; 2026 Oakridge Technologies &mdash; maintained by nalkema1.</p>'
        '<p>Source &amp; releases: <a href="https://github.com/nalkema1/MotionSprinkler">'
        'github.com/nalkema1/MotionSprinkler</a></p>'
        '<p><small>Firmware version: ' + get_current_version() + '</small></p>'
        '</div>')
    yield from resp.awrite(_FOOT)

# ── Watchdog ──────────────────────────────────────────────────────────────────
# If the main thread ever wedges (e.g. a network call hangs overnight), the
# device used to sit dead-but-pingable until found in the morning. A hardware
# watchdog reboots it automatically instead. A DEDICATED 1 s feed timer (id 2,
# separate from the 30s/60s work timers) gives a wide margin against false
# trips: feeds stop only if the single thread is genuinely blocked, and the
# device resets ~60 s later and recovers on its own. Armed only after OTA (which
# runs earlier in start.py) so a slow update download can't trip it.
try:
    from machine import WDT
    _wdt = WDT(timeout=60000)  # 60 s

    def _feed_wdt(t):
        _wdt.feed()

    _wdt_feeder = Timer(2)
    _wdt_feeder.init(period=1000, mode=Timer.PERIODIC, callback=_feed_wdt)
    sendTelemetry("Watchdog armed (60s)")
except Exception as e:
    sendTelemetry("Watchdog init failed: {}".format(e))

sendTelemetry("Webserver started")
app.run(debug=True, host="0.0.0.0", port=80)
