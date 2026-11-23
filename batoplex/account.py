"""plex.tv account calls: servers, Plex Home users, switching user. No state here.

Notes:
  - users from /api/v2/home/users (JSON), else /api/home/users (XML, parsed here);
  - a switch returns the user's token; a PIN rejection is told apart from an expired
    account token, so the UI can ask for the PIN again or for a new sign-in.
Tokens never go into a URL. The PIN goes only in the query of the HTTPS switch call, and
plex._request never logs a query.
"""
import urllib.parse
import xml.etree.ElementTree as ET

from . import log, plex

L = log.get("account")


class SwitchError(Exception):
    """kind: "pin" (wrong PIN), "auth" (the account token is no longer valid), "other"."""

    def __init__(self, kind, msg=""):
        super().__init__(msg or kind)
        self.kind = kind


def resources(client_id, token):
    return plex.discover_servers(client_id, token)


def _bool(v):
    return str(v).lower() in ("1", "true", "yes")


def home_users(client_id, token):
    """[{uuid, id, title, protected, admin, restricted, thumb}]. thumb: the avatar URL on
    plex.tv (public; fetched with no token), or None."""
    base = plex._plex_tv()
    h = plex._headers(client_id, token)
    try:
        d = plex._request(base + "/api/v2/home/users", h)
        users = d.get("users") if isinstance(d, dict) else None
        if users is not None:
            return [{"uuid": u.get("uuid"), "id": u.get("id"), "title": u.get("title") or "",
                     "protected": _bool(u.get("protected")), "admin": _bool(u.get("admin")),
                     "restricted": _bool(u.get("restricted")),
                     "thumb": u.get("thumb") or None} for u in users]
    except plex.AuthError:
        raise
    except plex.PlexError as e:
        L.info("home users (v2) failed: %s; trying the XML list", e)
    hx = dict(h, Accept="application/xml")
    raw = plex._request(base + "/api/home/users", hx, raw=True)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise plex.PlexError("home users: unreadable reply (%s)" % e)
    out = []
    for u in root.iter("User"):
        a = u.attrib
        out.append({"uuid": a.get("uuid"), "id": a.get("id"), "title": a.get("title") or "",
                    "protected": _bool(a.get("protected")), "admin": _bool(a.get("admin")),
                    "restricted": _bool(a.get("restricted")),
                    "thumb": a.get("thumb") or None})
    return out


def switch_user(client_id, token, user, pin=None):
    """Returns the user's token. Raises SwitchError."""
    base = plex._plex_tv()
    q = "?" + urllib.parse.urlencode({"pin": pin}) if pin else ""
    uid = user.get("uuid") or user.get("id")      # the XML list can lack the uuid
    url = "%s/api/v2/home/users/%s/switch%s" % (base, urllib.parse.quote(str(uid)), q)
    try:
        d = plex._request(url, plex._headers(client_id, token), method="POST")
    except plex.PlexError as e:
        refused = isinstance(e, plex.AuthError) or "HTTP 403" in str(e)
        if not refused:
            raise SwitchError("other", str(e))
        if not pin:
            raise SwitchError("auth", str(e))
        # A refusal with a PIN: the PIN, or an account token that is no longer valid.
        # The account check tells them apart.
        try:
            whoami(client_id, token)
        except plex.AuthError:
            raise SwitchError("auth", str(e))
        except plex.PlexError:
            pass
        raise SwitchError("pin", str(e))
    tok = d.get("authToken") or d.get("authenticationToken") if isinstance(d, dict) else None
    if not tok:
        raise SwitchError("other", "no token in the switch reply")
    log.add_secret(tok)
    return tok


def whoami(client_id, token):
    d = plex._request(plex._plex_tv() + "/api/v2/user", plex._headers(client_id, token))
    return {"uuid": d.get("uuid"), "title": d.get("title") or d.get("username") or ""} \
        if isinstance(d, dict) else {}
