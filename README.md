# ns8-matrix-bridges

[NethServer 8](https://github.com/NethServer/ns8-core) modules that bridge a
Matrix server of the
[matrix-reworked](https://github.com/Platypuschan/ns8-matrix-reworked)
module to other chat networks. This repository builds **one module per
bridge**:

| Module (image) | Bridge | Bot |
| --- | --- | --- |
| `ghcr.io/platypuschan/matrix-whatsapp` | [mautrix-whatsapp](https://github.com/mautrix/whatsapp) | `@whatsappbot` |
| `ghcr.io/platypuschan/matrix-signal` | [mautrix-signal](https://github.com/mautrix/signal) | `@signalbot` |
| `ghcr.io/platypuschan/matrix-telegram` | [mautrix-telegram](https://github.com/mautrix/telegram) | `@telegrambot` |
| `ghcr.io/platypuschan/matrix-discord` | [mautrix-discord](https://github.com/mautrix/discord) | `@discordbot` |
| `ghcr.io/platypuschan/matrix-steam` | [matrix-steam-bridge](https://github.com/jasonlaguidice/matrix-steam-bridge) | `@steambot` |
| `ghcr.io/platypuschan/matrix-heisenbridge` | [Heisenbridge](https://github.com/hifi/heisenbridge) (IRC) | `@heisenbridge` |

The bridge versions are pinned in [`bridges.json`](bridges.json).

## Install

Install and configure matrix-reworked first, then a bridge **on the same
node**:

    add-module ghcr.io/platypuschan/matrix-whatsapp:latest 1
    api-cli run module/matrix-whatsapp1/configure-module --data '{}'

`configure-module` finds the matrix-reworked module of the node, reads its
details (`get-matrix-info`), writes the bridge configuration, registers the
bridge's appservice there (`add-appservice`; Synapse restarts) and starts the
bridge. Users of the server then start a chat with the bot and send `login`.

Settings:

| Field | Bridge | Meaning |
| --- | --- | --- |
| `telegram_api_id`, `telegram_api_hash` | Telegram | Required, from <https://my.telegram.org/apps>. An empty hash keeps the stored one. |
| `heisenbridge_owner` | IRC | Matrix ID that administers the bridge; empty: the first local user who talks to it. |

All users of the server may use a bridge; the matrix-reworked admin
(`@matrixadmin`) is bridge admin. mautrix bridges get double puppeting
(your messages from the other network's app appear as you) and allow
end-to-bridge encryption.

## How it works

- Each module runs one container (`bridge.service`) as the module user,
  with its configuration, registration and SQLite database in `state/data`.
  The module sets only the keys it owns in the bridge configuration
  (addresses, port, database, permissions, double puppeting, encryption,
  logging); other options can be edited there and are kept.
- The module image asks for the `appserviceadm` role of matrix-reworked
  (`org.nethserver.authorizations=matrix-reworked@any:appserviceadm`) and
  one TCP port, the appservice listener on 127.0.0.1.
- When matrix-reworked is reconfigured, updated or restored it publishes
  `matrix-changed`; the bridges read its details again and restart if
  needed.
- **Steam**: the SteamKit helper of matrix-steam-bridge always listens on
  port 50051 without authentication, so this bridge runs in its own network
  namespace (pasta, with the host loopback mapped to 169.254.1.3) and only
  its appservice port is published on 127.0.0.1.
- Removing a bridge module removes its registration from matrix-reworked.

## Backup and restore

The backup contains the settings, the bridge configuration and registration
and a consistent SQLite snapshot (`module-dump-state`). Restore matrix-reworked
first; a restored bridge registers again with the matrix-reworked module of
its node.

## Development

`build-images.sh` builds all module images from the shared `imageroot/` and
UI; `etc/bridge` names the bridge of an image. `validate.yml` runs the unit
tests (`tests/test_*.py`); the `Publish images` workflow runs the Robot suite
on NS8 QEMU VMs: it installs matrix-reworked (image in `tests/base-image`)
and all six bridges.
