"""Logging with secret redaction. Every registered secret is replaced before a line is written."""
import logging
import logging.handlers
import os
import re

_SECRETS = set()
_PAT = re.compile(r"(X-Plex-Token[=:]\s*|authToken\"?:\s*\"?|accessToken\"?:\s*\"?)[A-Za-z0-9_\-\.]+")


def add_secret(s):
    if s and len(s) >= 6:
        _SECRETS.add(s)


def redact(text):
    text = _PAT.sub(r"\1<redacted>", str(text))
    for s in _SECRETS:
        text = text.replace(s, "<redacted>")
    return text


class _Redact(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        if record.exc_info:
            import traceback
            msg += "\n" + "".join(traceback.format_exception(*record.exc_info))
            record.exc_info = None
            record.exc_text = None
        record.msg = redact(msg)
        record.args = ()
        return True


def setup(data_dir, verbose=False):
    log = logging.getLogger("batoplex")
    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = os.path.join(data_dir, "batoplex.log")
    # Rotates while running too (batoplex.log.1 is the one backup): a long session stays small.
    for h in (logging.handlers.RotatingFileHandler(path, maxBytes=512 * 1024, backupCount=1),
              logging.StreamHandler()):
        h.setFormatter(fmt)
        h.addFilter(_Redact())
        log.addHandler(h)
    import threading
    threading.excepthook = lambda a: log.error("thread error", exc_info=(a.exc_type, a.exc_value, a.exc_traceback))
    return log


def get(name):
    return logging.getLogger("batoplex." + name)
