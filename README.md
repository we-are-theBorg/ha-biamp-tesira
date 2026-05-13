# Biamp Tesira — Home Assistant Integration

A custom [Home Assistant](https://www.home-assistant.io/) component that lets you monitor and control [Biamp Tesira](https://www.biamp.com/products/tesira) DSP (Digital Signal Processor) hardware directly from your HA dashboard or automations.

> **Status:** Early / experimental. Tested against pytesira 0.9.0 and Home Assistant 2024.x+.

---

## What this does

Tesira DSPs are rack-mounted audio processors used in conference rooms, auditoriums, and AV installations. They handle things like mixing microphone inputs, routing audio to speakers, and controlling volume zones. Normally you configure them with Biamp's proprietary software — this integration brings basic runtime control (levels, mutes, source selection) into Home Assistant.

Once installed you get:

| HA entity type | What it maps to on the DSP |
|---|---|
| **Number** (slider) | Output level in dB for each channel on a `LevelControl`, `DanteInput`, or `DanteOutput` block |
| **Switch** (on = muted) | Mute state for any channel on a `LevelControl`, `MuteControl`, `DanteInput`, `DanteOutput`, `AudioOutput`, or `SourceSelector` block |
| **Select** (dropdown) | Active source on a `SourceSelector` block |

---

## How it works

### The transport layer

Tesira hardware exposes a text-based command protocol called **TTP (Tesira Text Protocol)** over SSH. Every controllable element on the DSP — a volume fader, a mute button, a source selector — is a named **block** with typed attributes you can get or set by sending lines like:

```
"MyVolumeBlock" set level 1 -12.5
"MyMuteBlock" subscribe mutes "subscription-token"
```

The DSP also sends **subscription notifications** back over the same SSH channel whenever a subscribed value changes, so you get real-time push updates rather than having to poll.

### pytesira handles the SSH session

This integration builds on [pytesira](https://github.com/enp6s0/pytesira), a Python library that wraps all of the above:

- Opens and maintains an SSH connection to the DSP using [Paramiko](https://www.paramiko.org/).
- Discovers every block on the device at startup (or loads a cached "block map" to skip that step).
- Exposes each block as a Python object with typed attributes (`channel.level`, `channel.muted`, `block.selected_source`, etc.).
- Handles re-subscription automatically if the connection drops and reconnects.

### Bridging pytesira to Home Assistant

Home Assistant is fully asynchronous (it runs on Python's `asyncio` event loop). pytesira is fully synchronous (it uses blocking socket I/O and Python threads internally). The integration bridges the two worlds:

```
pytesira worker thread          HA event loop
─────────────────────           ──────────────────────
block subscription fires   →    hass.loop.call_soon_threadsafe()
                                        │
                                entity.async_write_ha_state()
                                        │
                                HA dashboard updates
```

Conversely, when a user moves a slider in HA:

```
HA event loop                   pytesira worker thread
──────────────                  ─────────────────────
async_set_native_value()   →    hass.async_add_executor_job()
                                        │
                                channel.level = value  (blocking TTP command)
```

`async_add_executor_job()` runs a blocking function on a thread pool without blocking the event loop. `call_soon_threadsafe()` schedules a coroutine back onto the event loop from a non-async thread. These two are the standard HA pattern for any integration that talks to hardware over a blocking I/O library.

### Block map caching

Discovering all blocks on startup takes several seconds (one SSH round-trip per block attribute). After the first successful connection the integration saves a `.bmap` cache file to your HA config directory. On subsequent restarts it loads this file instead, cutting startup time significantly. If the DSP is reprogrammed (blocks added or renamed), pytesira detects the mismatch and falls back to a fresh discovery automatically.

### AudioOutput — polling exception

Most Tesira block types support subscriptions, so updates are truly push-based. `AudioOutput` blocks (the DSP's built-in analog outputs) do not. Mute switches for those blocks use HA's standard poll mechanism (`should_poll = True`) and call pytesira's `block.refresh_status()` on each poll cycle to re-query current state.

---

## Installation

### Manual (recommended for development)

```bash
# on your Home Assistant host (or inside the HA container)
cd /config
git clone https://github.com/we-are-theBorg/ha-biamp-tesira.git _biamp_tmp
cp -r _biamp_tmp/custom_components/biamp_tesira custom_components/
rm -rf _biamp_tmp
```

Then restart Home Assistant.

### Via HACS

1. Open HACS → Integrations → three-dot menu → **Custom repositories**.
2. Add `https://github.com/we-are-theBorg/ha-biamp-tesira` with category **Integration**.
3. Search for "Biamp Tesira" and install.
4. Restart Home Assistant.

---

## Configuration

Go to **Settings → Devices & Services → Add Integration** and search for **Biamp Tesira**.

| Field | Required | Default | Notes |
|---|---|---|---|
| Hostname / IP | yes | — | Address of the Tesira server |
| SSH Username | yes | — | Usually `default` |
| SSH Password | yes | — | Set in Tesira software |
| SSH Port | no | `22` | Change only if non-standard |
| Verify SSH Host Key | no | off | Turn on if your network is untrusted |
| Block Map Cache Path | no | auto | Leave blank; auto path is `{config_dir}/biamp_tesira_{entry_id}.bmap` |

The config flow performs a real test-connect when you submit, so bad credentials fail immediately rather than at HA restart.

### SSH host key note

With **Verify SSH Host Key** off (the default), the integration accepts whatever key the DSP presents — suitable for most LAN-only AV setups. Turning it on requires that the DSP's host key already be present in the SSH `known_hosts` of the user running Home Assistant.

### Biamp firmware 4.7.2+ cipher deprecation

Firmware 4.7.2 removed support for some legacy SSH ciphers. If you see SSH negotiation failures on recent firmware, ensure Paramiko (pytesira's SSH library) is up to date (`pip install --upgrade paramiko`). Paramiko 3.x+ prefers modern ciphers (AES-GCM, ChaCha20) that Tesira 4.7.2+ accepts.

---

## Entity naming

Entities are named automatically from DSP block IDs and channel labels you set in Tesira software:

- **Level number:** `{channel label} Level` (e.g., `Main Room Ch1 Level`)
- **Mute switch:** `{channel label} Mute`
- **Source select:** `{block id} Source`

Unique IDs are derived from the DSP's serial number + block ID + channel index, so entities survive renames in HA without losing history.

---

## Project structure

```
custom_components/biamp_tesira/
├── __init__.py          # Entry setup and teardown
├── manifest.json        # HA integration metadata, declares pytesira dependency
├── const.py             # Constants (domain name, config keys)
├── coordinator.py       # Manages the DSP connection; routes push updates to entities
├── config_flow.py       # UI wizard for adding the integration
├── entity.py            # Shared base classes for all entities
├── number.py            # Level entities (dB sliders)
├── switch.py            # Mute entities
├── select.py            # Source selector entities
└── translations/
    └── en.json          # UI strings for the config flow
```

---

## Credits and prior art

This integration would not exist without the following open-source projects:

| Project | What we used it for | License |
|---|---|---|
| [pytesira](https://github.com/enp6s0/pytesira) by enp6s0 | Primary Python library; SSH transport, block discovery, subscription callbacks — this integration is essentially a thin HA wrapper around pytesira | MIT |
| [companion-module-biamp-tesira](https://github.com/bitfocus/companion-module-biamp-tesira) by Bitfocus | Studied for TTP command patterns, subscription handling, and connection lifecycle behaviour | MIT |
| [epi-biamp-tesira](https://github.com/PepperDash/epi-biamp-tesira) by PepperDash | Studied for DSP block architecture patterns and the SSH cipher deprecation issue in firmware 4.7.2+ | MIT |
| [hacs.integration_blueprint](https://github.com/jpawlowski/hacs.integration_blueprint) by jpawlowski | Reference for HA custom component file layout and coordinator/entity patterns | MIT |
| [Home Assistant](https://www.home-assistant.io/) | The platform this runs on | Apache 2.0 |

---

## License

MIT — see [LICENSE](LICENSE).

This project is not affiliated with or endorsed by Biamp Systems.
