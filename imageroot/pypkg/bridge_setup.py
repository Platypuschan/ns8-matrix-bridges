#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Settings and generated configuration of a Matrix bridge module.

One repository builds a module image per bridge; etc/bridge names the
bridge of this image and etc/bridges.json describes all of them. The bridge
registers its appservice with the matrix-reworked module on the same node
and gets the homeserver details from it (see bridge_runtime.py).

mautrix bridges own most of their configuration: they complete a small seed
file with their defaults and generated secrets, and update it when a new
version adds options. The module only sets the keys it is responsible for
(addresses, port, database, permissions, double puppeting, ...).
"""

import contextlib
import copy
import fcntl
import json
import os
import re
import secrets

ETC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "..", "etc")
SETTINGS = "bridge.json"
DATA_DIR = "data"
# Set when a file that the bridge reads only at start changed
RESTART_FLAG = "bridge-restart-needed"
# The SQLite database of mautrix bridges, inside the data directory
DATABASE = "bridge.db"
DATABASE_URI = f"file:/data/{DATABASE}?_txlock=immediate"
BACKUP_DATABASE = "bridge-backup.db"
# Address of the host's loopback inside the network namespace of an
# isolated bridge (pasta --map-host-loopback)
HOST_LOOPBACK = "169.254.1.3"
# Image name of the homeserver module (org.nethserver.authorizations)
MATRIX_MODULE = "matrix-reworked"

MXID_PATTERN = re.compile(r"^@[a-z0-9._=/+-]+:[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


class SettingsError(Exception):
    def __init__(self, field, value, error):
        super().__init__(f"{field}: {error}")
        self.field = field
        self.value = value
        self.error = error


def load_bridges(etc=ETC):
    with open(os.path.join(etc, "bridges.json"), "r", encoding="utf-8") as stream:
        return json.load(stream)


def bridge_key(etc=ETC):
    with open(os.path.join(etc, "bridge"), "r", encoding="utf-8") as stream:
        return stream.read().strip()


def bridge_info(etc=ETC):
    key = bridge_key(etc)
    info = dict(load_bridges(etc)[key])
    info["key"] = key
    return info


def write_private(path, content):
    """Atomically write a file readable by the module user only; return
    True if its content changed."""
    try:
        with open(path, "r", encoding="utf-8") as stream:
            if stream.read() == content:
                os.chmod(path, 0o600)
                return False
    except FileNotFoundError:
        pass
    temporary = path + ".tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return True


def write_json(path, data):
    return write_private(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)


def flag_restart(state_dir="."):
    with open(os.path.join(state_dir, RESTART_FLAG), "w", encoding="utf-8"):
        pass


@contextlib.contextmanager
def settings_lock(state_dir="."):
    descriptor = os.open(os.path.join(state_dir, SETTINGS + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def read_settings(state_dir="."):
    try:
        with open(os.path.join(state_dir, SETTINGS), "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


DEFAULTS = {
    "telegram_api_id": "",
    "telegram_api_hash": "",
    "heisenbridge_owner": "",
}


def ensure_settings(info, state_dir="."):
    """Return the settings with defaults and the secrets of this bridge."""
    settings = read_settings(state_dir)
    changed = False
    for key, value in DEFAULTS.items():
        if key not in settings:
            settings[key] = copy.deepcopy(value)
            changed = True
    stored = settings.setdefault("secrets", {})
    if info.get("kind") == "heisenbridge":
        # mautrix bridges generate their own tokens (-g)
        for key in ("as_token", "hs_token"):
            if not stored.get(key):
                stored[key] = secrets.token_urlsafe(48)
                changed = True
    path = os.path.join(state_dir, SETTINGS)
    if changed or not os.path.exists(path):
        write_json(path, settings)
    else:
        os.chmod(path, 0o600)
    return settings


def apply_request(data, info, state_dir=".", write=True):
    """Validate a configure-module request; fields missing from it keep
    their value. Raise SettingsError without changing anything."""
    settings = ensure_settings(info, state_dir)
    new = copy.deepcopy(settings)
    for key in ("telegram_api_id", "heisenbridge_owner"):
        if key in data:
            new[key] = str(data[key] or "").strip()
    if data.get("telegram_api_hash"):
        new["telegram_api_hash"] = str(data["telegram_api_hash"]).strip()
    if info["key"] == "telegram":
        if not new["telegram_api_id"] or not new["telegram_api_id"].isdigit():
            raise SettingsError("telegram_api_id", new["telegram_api_id"], "telegram_api_id_required")
        if not new["telegram_api_hash"]:
            raise SettingsError("telegram_api_hash", "", "telegram_api_hash_required")
    if new["heisenbridge_owner"] and not MXID_PATTERN.fullmatch(new["heisenbridge_owner"]):
        raise SettingsError("heisenbridge_owner", new["heisenbridge_owner"], "invalid_user_id")
    if write:
        write_json(os.path.join(state_dir, SETTINGS), new)
    return new


# --- Bridge configuration ------------------------------------------------------

def set_path(document, path, value):
    node = document
    for key in path[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def homeserver_address(info, matrix):
    """An isolated bridge reaches Synapse through the mapped host loopback."""
    url = matrix["homeserver_url"]
    if info.get("isolated"):
        url = url.replace("://127.0.0.1:", f"://{HOST_LOOPBACK}:", 1)
    return url


def bridge_values(info, settings, matrix, port):
    """The (path, value) pairs of a mautrix bridge configuration owned by the
    module."""
    server_name = matrix["server_name"]
    legacy = info.get("legacy", False)
    isolated = info.get("isolated", False)
    database = {"type": "sqlite3-fk-wal", "uri": DATABASE_URI}
    permissions = {server_name: "user", matrix["admin_user"]: "admin"}
    double_puppet = {server_name: matrix["double_puppet_secret"]}
    values = [
        (("homeserver", "address"), homeserver_address(info, matrix)),
        (("homeserver", "domain"), server_name),
        # Synapse reaches the bridge on the host loopback (published port
        # of an isolated bridge)
        (("appservice", "address"), f"http://127.0.0.1:{port}"),
        (("appservice", "hostname"), "0.0.0.0" if isolated else "127.0.0.1"),
        (("appservice", "port"), port),
        (("bridge", "permissions"), permissions),
        # The journal adds time stamps; no log files in the volume
        (("logging",), {"min_level": "info", "writers": [{"type": "stdout", "format": "pretty"}]}),
    ]
    if legacy:
        values += [
            (("appservice", "database", "type"), database["type"]),
            (("appservice", "database", "uri"), database["uri"]),
            (("bridge", "login_shared_secret_map"), double_puppet),
            (("bridge", "double_puppet_server_map"), {}),
            # Element creates encrypted direct chats; the bot must read them
            (("bridge", "encryption", "allow"), True),
            (("bridge", "federate_rooms"), matrix["federation"]),
        ]
    else:
        values += [
            (("database", "type"), database["type"]),
            (("database", "uri"), database["uri"]),
            (("double_puppet", "secrets"), double_puppet),
            (("double_puppet", "servers"), {}),
            (("encryption", "allow"), True),
            (("matrix", "federate_rooms"), matrix["federation"]),
        ]
    if info["key"] == "steam":
        values += [
            # The image keeps the SteamKit helper next to the bridge
            (("network", "steam_bridge_path"), "/app"),
            (("network", "steam_bridge_address"), "localhost:50051"),
            (("network", "steam_bridge_auto_start"), True),
        ]
    if info["key"] == "telegram":
        values += [
            (("network", "api_id"), int(settings["telegram_api_id"])),
            (("network", "api_hash"), settings["telegram_api_hash"]),
        ]
    return values


def patch_config(document, info, settings, matrix, port):
    document = copy.deepcopy(document) if document else {}
    for path, value in bridge_values(info, settings, matrix, port):
        set_path(document, path, value)
    return document


def patch_registration(document, port):
    """The push URL follows the allocated port (restore, clone)."""
    document = copy.deepcopy(document)
    document["url"] = f"http://127.0.0.1:{port}"
    return document


def heisenbridge_registration(settings, port):
    stored = settings["secrets"]
    return {
        "id": "heisenbridge",
        "url": f"http://127.0.0.1:{port}",
        "as_token": stored["as_token"],
        "hs_token": stored["hs_token"],
        "rate_limited": False,
        "sender_localpart": "heisenbridge",
        # Heisenbridge derives the puppet prefix from this exact form
        "namespaces": {"users": [{"regex": "@hbirc_.*", "exclusive": True}], "aliases": [], "rooms": []},
    }


def unit_env(info, settings, matrix, port):
    """Variables of bridge.service. $BRIDGE_NET and $BRIDGE_ARGS are split
    into words by systemd."""
    if info.get("kind") == "heisenbridge":
        args = ["-c", "/data/registration.yaml", "-l", "127.0.0.1", "-p", str(port)]
        if settings.get("heisenbridge_owner"):
            args += ["-o", settings["heisenbridge_owner"]]
        args.append(matrix["homeserver_url"])
        return {
            "BRIDGE_IMAGE": info["image"],
            "BRIDGE_BINARY": "heisenbridge",
            "BRIDGE_ARGS": " ".join(args),
            "BRIDGE_NET": "--network=host",
        }
    if info.get("isolated"):
        net = f"--network=pasta:--map-host-loopback,{HOST_LOOPBACK} --publish=127.0.0.1:{port}:{port}"
    else:
        net = "--network=host"
    return {
        "BRIDGE_IMAGE": info["image"],
        "BRIDGE_BINARY": info.get("binary", f"/usr/bin/mautrix-{info['key']}"),
        "BRIDGE_ARGS": "-c /data/config.yaml -r /data/registration.yaml",
        "BRIDGE_NET": net,
    }


def configuration(info, settings, state):
    """Output of get-configuration."""
    server_name = state.get("server_name", "")
    return {
        "bridge": info["key"],
        "name": info["name"],
        "bot": f"@{info['bot']}:{server_name}" if server_name else "",
        "matrix_module": state.get("matrix_module", ""),
        "server_name": server_name,
        "registered": bool(state.get("registered")),
        "telegram_api_id": settings.get("telegram_api_id", ""),
        "telegram_api_hash_set": bool(settings.get("telegram_api_hash")),
        "heisenbridge_owner": settings.get("heisenbridge_owner", ""),
    }
