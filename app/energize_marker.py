# Persistent "is a relay/solenoid energized right now" marker.
#
# Written to flash whenever a zone is switched on or off so that, after an
# unexpected reset, boot can tell whether a relay was energized at the moment
# of the reset. A HARD_RESET *while energized* points at a power brownout from
# the solenoid inrush / back-EMF; a HARD_RESET with nothing energized points at
# a firmware panic (or external reset) instead. See the boot diagnostics in
# app/start.py.
#
# Format: a JSON list of the relay ids currently on, e.g. [2]. File absent or
# "[]" means nothing is energized. Every write is best-effort - a flash error
# here must NEVER stop a zone from switching, so all failures are swallowed.

import ujson

MARKER_FILE = 'energize.flag'


def _read():
    try:
        with open(MARKER_FILE) as f:
            ids = ujson.loads(f.read())
        return ids if isinstance(ids, list) else []
    except Exception:
        return []


def _write(ids):
    try:
        with open(MARKER_FILE, 'w') as f:
            f.write(ujson.dumps(ids))
    except Exception:
        pass


def mark_on(relay_id):
    ids = _read()
    if relay_id not in ids:
        ids.append(relay_id)
        _write(ids)


def mark_off(relay_id):
    ids = _read()
    if relay_id in ids:
        ids.remove(relay_id)
        _write(ids)


def read_and_clear():
    """Return the relay ids recorded as energized, then clear the marker.
    Called once at boot, before any zone is switched."""
    ids = _read()
    _write([])
    return ids
