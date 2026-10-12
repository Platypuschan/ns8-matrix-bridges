#
# SPDX-License-Identifier: GPL-3.0-or-later
#

"""Connect a bridge module to the matrix-reworked module and write its files.

sync() is the whole configuration: find the homeserver module on this node,
read its details (get-matrix-info), write the bridge configuration, let a
mautrix bridge generate its registration once, register it with the
homeserver (add-appservice) and flag a restart when anything changed.
configure-module, the matrix-changed event and update-module.d call it.
"""

import json
import os
import shutil
import sqlite3
import subprocess

import bridge_setup as setup

STATE = "matrix.json"


class BridgeError(Exception):
    """A safe-to-show error with a validation code."""

    def __init__(self, error, detail=""):
        super().__init__(f"{error}: {detail}" if detail else error)
        self.error = error


def load_yaml(path):
    import yaml

    try:
        with open(path, "r", encoding="utf-8") as stream:
            return yaml.safe_load(stream) or {}
    except FileNotFoundError:
        return None


def dump_yaml(document):
    import yaml

    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False, allow_unicode=True, width=1000)


def read_state(state_dir="."):
    try:
        with open(os.path.join(state_dir, STATE), "r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return {}


def agent_resolver(name):
    import agent

    return agent.resolve_agent_id(name)


def agent_task(agent_id, action, data=None):
    import agent.tasks

    return agent.tasks.run(agent_id=agent_id, action=action, data=data or {},
                           extra={"isNotificationHidden": True})


def generate_registration(info, data_dir, runner=subprocess.run):
    """Let a mautrix bridge complete its configuration and write its
    registration (new as_token and hs_token)."""
    image = info["image"]
    if runner(("podman", "image", "exists", image)).returncode != 0:
        runner(("podman", "pull", image), check=True)
    runner((
        "podman", "run", "--rm", "--network=none", "--user=0:0",
        "--volume", f"{os.path.abspath(data_dir)}:/data:Z",
        "--entrypoint", info.get("binary", f"/usr/bin/mautrix-{info['key']}"),
        image, "-g", "-c", "/data/config.yaml", "-r", "/data/registration.yaml",
    ), check=True)


def sync(info, settings, port, state_dir=".", resolve=agent_resolver, task=agent_task, runner=subprocess.run):
    """Configure the bridge for the homeserver module of this node and
    register it there. Return the stored state."""
    matrix_module = resolve(f"{setup.MATRIX_MODULE}@node")
    if not matrix_module:
        raise BridgeError("matrix_module_not_found")
    answer = task(matrix_module, "get-matrix-info")
    if answer["exit_code"] != 0:
        raise BridgeError("matrix_module_not_configured", str(answer.get("error", ""))[:200])
    matrix = answer["output"]

    data_dir = os.path.join(state_dir, setup.DATA_DIR)
    setup.private_dir(data_dir)
    if info.get("kind") == "heisenbridge":
        registration = setup.heisenbridge_registration(settings, port)
        if setup.write_private(os.path.join(data_dir, "registration.yaml"), dump_yaml(registration)):
            setup.flag_restart(state_dir)
    else:
        config_path = os.path.join(data_dir, "config.yaml")
        current = load_yaml(config_path)
        patched = setup.patch_config(current, info, settings, matrix, port)
        if patched != current:
            setup.write_private(config_path, dump_yaml(patched))
            setup.flag_restart(state_dir)
        registration_path = os.path.join(data_dir, "registration.yaml")
        if not os.path.exists(registration_path):
            generate_registration(info, data_dir, runner=runner)
            setup.flag_restart(state_dir)
        registration = setup.patch_registration(load_yaml(registration_path), port)
        setup.write_private(registration_path, dump_yaml(registration))

    answer = task(matrix_module, "add-appservice", {"registration": registration})
    if answer["exit_code"] != 0:
        raise BridgeError("appservice_rejected", json.dumps(answer.get("output") or answer.get("error", ""))[:300])

    env = "".join(f'{name}="{value}"\n' for name, value in setup.unit_env(info, settings, matrix, port).items())
    if setup.write_private(os.path.join(state_dir, "unit.env"), env):
        setup.flag_restart(state_dir)

    state = {
        "matrix_module": matrix_module.removeprefix("module/"),
        "server_name": matrix["server_name"],
        "appservice_id": registration["id"],
        "registered": True,
    }
    setup.write_json(os.path.join(state_dir, STATE), state)
    return state


def unregister(state_dir=".", task=agent_task):
    """Remove the registration from the homeserver module (destroy-module)."""
    state = read_state(state_dir)
    if not state.get("matrix_module") or not state.get("appservice_id"):
        return False
    answer = task("module/" + state["matrix_module"], "remove-appservice", {"id": state["appservice_id"]})
    return answer["exit_code"] == 0


# --- Backup ----------------------------------------------------------------------

def make_snapshot(state_dir="."):
    """Consistent copy of the SQLite database while the bridge runs."""
    data_dir = os.path.join(state_dir, setup.DATA_DIR)
    source = os.path.join(data_dir, setup.DATABASE)
    if not os.path.exists(source):
        return False
    target = os.path.join(data_dir, setup.BACKUP_DATABASE)
    temporary = target + ".tmp"
    if os.path.exists(temporary):
        os.remove(temporary)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(temporary)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    os.chmod(temporary, 0o600)
    os.replace(temporary, target)
    return True


def restore_snapshot(state_dir="."):
    """Put the database of a backup in place before the bridge starts."""
    data_dir = os.path.join(state_dir, setup.DATA_DIR)
    backup = os.path.join(data_dir, setup.BACKUP_DATABASE)
    if not os.path.exists(backup):
        return False
    target = os.path.join(data_dir, setup.DATABASE)
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(target + suffix)
        except FileNotFoundError:
            pass
    shutil.move(backup, target)
    return True


def cleanup_snapshot(state_dir="."):
    for name in (setup.BACKUP_DATABASE, setup.BACKUP_DATABASE + ".tmp"):
        try:
            os.remove(os.path.join(state_dir, setup.DATA_DIR, name))
        except FileNotFoundError:
            pass
