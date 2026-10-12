#
# SPDX-License-Identifier: GPL-3.0-or-later
#

import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "imageroot" / "pypkg"))
import bridge_runtime  # noqa: E402
import bridge_setup  # noqa: E402

BRIDGES = json.loads((ROOT / "bridges.json").read_text())
MATRIX = {
    "api_version": 1,
    "server_name": "example.org",
    "homeserver_url": "http://127.0.0.1:20001",
    "public_url": "https://matrix.example.org",
    "federation": True,
    "admin_user": "@matrixadmin:example.org",
    "double_puppet_secret": "as_token:dp-secret",
}


def info(key):
    return {**BRIDGES[key], "key": key}


class StateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def mode(self, *parts):
        return stat.S_IMODE(os.stat(os.path.join(self.state, *parts)).st_mode)


class CatalogTest(unittest.TestCase):
    def test_bridges(self):
        self.assertEqual(set(BRIDGES), {"whatsapp", "signal", "telegram", "discord", "steam", "heisenbridge"})
        for key, item in BRIDGES.items():
            with self.subTest(key=key):
                self.assertRegex(item["image"], r"^[a-z0-9.-]+/[a-z0-9/_.-]+:[A-Za-z0-9._-]+$")
                self.assertTrue(item["bot"])
                self.assertTrue(item["description"].endswith("."))

    def test_etc_loader(self):
        with tempfile.TemporaryDirectory() as etc:
            Path(etc, "bridges.json").write_text(json.dumps(BRIDGES))
            Path(etc, "bridge").write_text("signal\n")
            loaded = bridge_setup.bridge_info(etc)
            self.assertEqual(loaded["key"], "signal")
            self.assertEqual(loaded["bot"], "signalbot")

    def test_build_labels(self):
        build = (ROOT / "build-images.sh").read_text()
        self.assertIn("org.nethserver.authorizations=matrix-reworked@any:appserviceadm", build)
        self.assertIn("org.nethserver.tcp-ports-demand=1", build)

    def test_output_schema(self):
        schema = json.loads((ROOT / "imageroot/actions/get-configuration/validate-output.json").read_text())
        output = bridge_setup.configuration(info("whatsapp"), {}, {})
        self.assertEqual(set(schema["required"]), set(output))


class SettingsTest(StateTest):
    def test_telegram_needs_credentials(self):
        with self.assertRaises(bridge_setup.SettingsError) as raised:
            bridge_setup.apply_request({}, info("telegram"), self.state)
        self.assertEqual(raised.exception.error, "telegram_api_id_required")
        with self.assertRaises(bridge_setup.SettingsError) as raised:
            bridge_setup.apply_request({"telegram_api_id": "123"}, info("telegram"), self.state)
        self.assertEqual(raised.exception.error, "telegram_api_hash_required")
        settings = bridge_setup.apply_request({"telegram_api_id": "123", "telegram_api_hash": "h"}, info("telegram"), self.state)
        self.assertEqual(settings["telegram_api_hash"], "h")
        # An empty hash keeps the stored one (restore sends {})
        self.assertEqual(bridge_setup.apply_request({}, info("telegram"), self.state)["telegram_api_hash"], "h")
        self.assertEqual(self.mode(bridge_setup.SETTINGS), 0o600)

    def test_other_bridges_need_nothing(self):
        self.assertEqual(bridge_setup.apply_request({}, info("whatsapp"), self.state)["secrets"], {})

    def test_heisenbridge(self):
        settings = bridge_setup.apply_request({"heisenbridge_owner": "@alice:example.org"}, info("heisenbridge"), self.state)
        self.assertEqual(len(settings["secrets"]["as_token"]), 64)
        self.assertNotEqual(settings["secrets"]["as_token"], settings["secrets"]["hs_token"])
        again = bridge_setup.apply_request({}, info("heisenbridge"), self.state)
        self.assertEqual(again["secrets"], settings["secrets"])
        with self.assertRaises(bridge_setup.SettingsError) as raised:
            bridge_setup.apply_request({"heisenbridge_owner": "alice"}, info("heisenbridge"), self.state)
        self.assertEqual(raised.exception.error, "invalid_user_id")


class ConfigTest(unittest.TestCase):
    def test_bridgev2(self):
        original = {"appservice": {"as_token": "keep", "bot": {"username": "whatsappbot"}}, "network": {"x": 1}}
        config = bridge_setup.patch_config(original, info("whatsapp"), {}, MATRIX, 20101)
        self.assertEqual(original["appservice"], {"as_token": "keep", "bot": {"username": "whatsappbot"}})
        self.assertEqual(config["appservice"]["as_token"], "keep")
        self.assertEqual(config["network"], {"x": 1})
        self.assertEqual(config["homeserver"], {"address": "http://127.0.0.1:20001", "domain": "example.org"})
        self.assertEqual(config["appservice"]["address"], "http://127.0.0.1:20101")
        self.assertEqual(config["appservice"]["hostname"], "127.0.0.1")
        self.assertEqual(config["database"], {"type": "sqlite3-fk-wal", "uri": "file:/data/bridge.db?_txlock=immediate"})
        self.assertEqual(config["bridge"]["permissions"], {"example.org": "user", "@matrixadmin:example.org": "admin"})
        self.assertEqual(config["double_puppet"]["secrets"], {"example.org": "as_token:dp-secret"})
        self.assertTrue(config["encryption"]["allow"])
        self.assertTrue(config["matrix"]["federate_rooms"])

    def test_legacy_discord(self):
        config = bridge_setup.patch_config(None, info("discord"), {}, {**MATRIX, "federation": False}, 20101)
        self.assertNotIn("database", config)
        self.assertEqual(config["appservice"]["database"]["type"], "sqlite3-fk-wal")
        self.assertEqual(config["bridge"]["login_shared_secret_map"], {"example.org": "as_token:dp-secret"})
        self.assertFalse(config["bridge"]["federate_rooms"])

    def test_telegram(self):
        settings = {"telegram_api_id": "42", "telegram_api_hash": "h"}
        config = bridge_setup.patch_config({}, info("telegram"), settings, MATRIX, 20101)
        self.assertEqual(config["network"]["api_id"], 42)
        self.assertEqual(config["network"]["api_hash"], "h")

    def test_steam_is_isolated(self):
        config = bridge_setup.patch_config({}, info("steam"), {}, MATRIX, 20101)
        self.assertEqual(config["homeserver"]["address"], "http://169.254.1.3:20001")
        self.assertEqual(config["appservice"]["hostname"], "0.0.0.0")
        self.assertEqual(config["appservice"]["address"], "http://127.0.0.1:20101")
        self.assertEqual(config["network"]["steam_bridge_path"], "/app")
        env = bridge_setup.unit_env(info("steam"), {}, MATRIX, 20101)
        self.assertEqual(env["BRIDGE_NET"], "--network=pasta:--map-host-loopback,169.254.1.3 --publish=127.0.0.1:20101:20101")
        self.assertEqual(env["BRIDGE_BINARY"], "/app/steam")
        self.assertEqual(bridge_setup.unit_env(info("signal"), {}, MATRIX, 20101)["BRIDGE_NET"], "--network=host")

    def test_heisenbridge_unit(self):
        settings = {"heisenbridge_owner": "@alice:example.org", "secrets": {"as_token": "a" * 20, "hs_token": "b" * 20}}
        env = bridge_setup.unit_env(info("heisenbridge"), settings, MATRIX, 20101)
        self.assertEqual(env["BRIDGE_ARGS"], "-c /data/registration.yaml -l 127.0.0.1 -p 20101 "
                         "-o @alice:example.org http://127.0.0.1:20001")
        registration = bridge_setup.heisenbridge_registration(settings, 20101)
        self.assertEqual(registration["url"], "http://127.0.0.1:20101")
        self.assertEqual(registration["namespaces"]["users"], [{"regex": "@hbirc_.*", "exclusive": True}])


class SyncTest(StateTest):
    def fakes(self, registered, matrix_ok=True):
        tasks = []

        def resolve(name):
            self.assertEqual(name, "matrix-reworked@node")
            return "module/matrix-reworked1"

        def task(agent_id, action, data=None):
            tasks.append((agent_id, action, data))
            if action == "get-matrix-info":
                return {"exit_code": 0 if matrix_ok else 2, "output": MATRIX if matrix_ok else None, "error": "x"}
            if action == "add-appservice":
                registered.append(data["registration"])
                return {"exit_code": 0, "output": MATRIX}
            if action == "remove-appservice":
                return {"exit_code": 0, "output": {"removed": True}}
            raise AssertionError(action)

        def runner(args, **kwargs):
            class Result:
                returncode = 0
            if args[:2] == ("podman", "run"):
                self.assertIn("--user=0:0", args)
                data = args[args.index("--volume") + 1].split(":")[0]
                Path(data, "registration.yaml").write_text(
                    "id: whatsapp\nurl: http://localhost:29318\nas_token: t\nhs_token: u\n"
                    "sender_localpart: x\nnamespaces: {users: []}\n")
            return Result()

        return resolve, task, runner, tasks

    def test_mautrix(self):
        registered = []
        resolve, task, runner, tasks = self.fakes(registered)
        settings = bridge_setup.ensure_settings(info("whatsapp"), self.state)
        state = bridge_runtime.sync(info("whatsapp"), settings, 20101, self.state, resolve, task, runner)
        self.assertEqual(state["matrix_module"], "matrix-reworked1")
        self.assertEqual(registered[0]["url"], "http://127.0.0.1:20101")
        self.assertEqual([t[1] for t in tasks], ["get-matrix-info", "add-appservice"])
        self.assertEqual(self.mode("data", "config.yaml"), 0o600)
        self.assertEqual(self.mode("data"), 0o700)
        self.assertTrue(Path(self.state, bridge_setup.RESTART_FLAG).exists())
        self.assertIn('BRIDGE_NET="--network=host"', Path(self.state, "unit.env").read_text())
        # Unchanged on the next run: no restart, no new registration tokens
        os.remove(Path(self.state, bridge_setup.RESTART_FLAG))
        bridge_runtime.sync(info("whatsapp"), settings, 20101, self.state, resolve, task, runner)
        self.assertFalse(Path(self.state, bridge_setup.RESTART_FLAG).exists())
        self.assertEqual(registered[1]["as_token"], "t")
        # A new port (restore) changes config and registration
        bridge_runtime.sync(info("whatsapp"), settings, 20201, self.state, resolve, task, runner)
        self.assertEqual(registered[2]["url"], "http://127.0.0.1:20201")
        self.assertTrue(Path(self.state, bridge_setup.RESTART_FLAG).exists())
        self.assertTrue(bridge_runtime.unregister(self.state, task))
        self.assertEqual(tasks[-1], ("module/matrix-reworked1", "remove-appservice", {"id": "whatsapp"}))

    def test_heisenbridge_needs_no_container(self):
        registered = []
        resolve, task, runner, tasks = self.fakes(registered)
        settings = bridge_setup.ensure_settings(info("heisenbridge"), self.state)
        bridge_runtime.sync(info("heisenbridge"), settings, 20101, self.state, resolve, task,
                            lambda *a, **k: self.fail("no container expected"))
        self.assertEqual(registered[0]["as_token"], settings["secrets"]["as_token"])

    def test_errors(self):
        registered = []
        resolve, task, runner, tasks = self.fakes(registered, matrix_ok=False)
        settings = bridge_setup.ensure_settings(info("signal"), self.state)
        with self.assertRaises(bridge_runtime.BridgeError) as raised:
            bridge_runtime.sync(info("signal"), settings, 20101, self.state, resolve, task, runner)
        self.assertEqual(raised.exception.error, "matrix_module_not_configured")
        with self.assertRaises(bridge_runtime.BridgeError) as raised:
            bridge_runtime.sync(info("signal"), settings, 20101, self.state, lambda name: None, task, runner)
        self.assertEqual(raised.exception.error, "matrix_module_not_found")


class BackupTest(StateTest):
    def test_snapshot_and_restore(self):
        data = Path(self.state, "data")
        data.mkdir()
        db = sqlite3.connect(data / "bridge.db")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE t (x)")
        db.execute("INSERT INTO t VALUES (42)")
        db.commit()
        self.assertTrue(bridge_runtime.make_snapshot(self.state))
        self.assertEqual(self.mode("data", "bridge-backup.db"), 0o600)
        db.execute("INSERT INTO t VALUES (43)")
        db.commit()
        db.close()
        self.assertTrue(bridge_runtime.restore_snapshot(self.state))
        restored = sqlite3.connect(data / "bridge.db")
        self.assertEqual(restored.execute("SELECT x FROM t").fetchall(), [(42,)])
        restored.close()
        self.assertFalse((data / "bridge-backup.db").exists())
        bridge_runtime.cleanup_snapshot(self.state)
        self.assertFalse(bridge_runtime.make_snapshot(tempfile.mkdtemp(dir=self.state)))


if __name__ == "__main__":
    unittest.main()
