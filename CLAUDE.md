# Biamp Tesira Home Assistant Integration

## Project Goal
Build a Home Assistant custom component (`custom_components/biamp_tesira/`)
that integrates with Biamp Tesira DSP hardware using the pytesira library.

## Key Dependency — pytesira fork
We maintain a fork at **https://github.com/we-are-theBorg/pytesira** (local clone at `c:\Dev\pytesira`).
The original upstream is https://github.com/enp6s0/pytesira but we diverge significantly.

`manifest.json` pins pytesira to a specific commit hash. After pushing new commits to the fork:
1. Run `git rev-parse HEAD` in `c:\Dev\pytesira` to get the new hash
2. Update `requirements` in `manifest.json` to `pytesira @ git+https://github.com/we-are-theBorg/pytesira.git@<hash>`
3. Bump `version` in `manifest.json`
4. Copy `custom_components/` to `versions/<new_version>/` as a snapshot
5. Commit and push the HA repo

## Current Version
**0.3.6** — see `versions/` directory for per-release snapshots.

## Test Device
TesiraForte at `172.17.9.1`, credentials `default:default` (SSH port 22).
- Serial: 6255420 | FW: 5.6.1.2
- 105 aliases total; block types present: AEC, AECReference, AudioInput, AudioOutput,
  AudioMeter, AVBPOEAmp, BFMic, BluetoothControlStatus, BluetoothInput, BluetoothOutput,
  Compressor, AudioDelay, LevelControl, LogicGate, MatrixMixer, PassFilter, PeakLim,
  UsbInputEx, UsbOutputEx, UberFilter (unsupported — no pytesira module yet)
- Presets configured: "Main Spaces Only", "Whole Home"

## HA Integration File Map
- `manifest.json` — version + pytesira git requirement
- `__init__.py` — async_setup_entry / async_unload_entry; PLATFORMS list
- `config_flow.py` — ConfigFlow (connection) + OptionsFlow (block types + presets)
- `coordinator.py` — BiampTesiraCoordinator; DSP connection, callbacks, Parlé event firing
- `const.py` — DOMAIN, CONF_* keys, SUPPORTED_BLOCK_TYPES, timing constants
- `entity.py` — BiampTesiraEntity (global listener) + BiampTesiraBlockEntity (per-block listener)
- `number.py` — LevelControl / DanteInput / DanteOutput channels → NumberEntity (dB)
- `switch.py` — per-channel mute (LevelControl, MuteControl, DanteInput, DanteOutput, AudioOutput)
              and block-level mute (SourceSelector) → SwitchEntity
- `select.py` — SourceSelector → SelectEntity
- `sensor.py` — Parlé beam sensors, AudioMeter, Compressor GR, BluetoothControlStatus
- `button.py` — user-configured preset names → ButtonEntity (DEVICE recallPresetByName)

## CRITICAL: Platform Setup Pattern
`async_forward_entry_setups` runs platform `async_setup_entry` functions **before**
`coordinator.async_setup()` starts the background DSP connection. `coordinator.dsp` is
`None` at that point. Every platform must use the background-task pattern:

```python
async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    async def _async_add_when_ready() -> None:
        await coordinator.async_wait_ready()
        entities = [...]
        async_add_entities(entities)

    entry.async_create_background_task(
        hass, _async_add_when_ready(), f"biamp_setup_<platform>_{entry.entry_id}"
    )
```

**Never** access `coordinator.dsp.blocks` directly at setup-entry time.

## Async Constraints
pytesira uses blocking SSH calls internally.
- All DSP method calls from HA must be wrapped in `hass.async_add_executor_job()`
- pytesira callbacks arrive on pytesira threads; dispatch to HA via
  `hass.loop.call_soon_threadsafe()` (coordinator._on_block_update already does this)

## Block Type Discovery (TTP internals)
The DSP sends an intentionally invalid command to each alias to discover its type:
```
"<alias>" get BLOCKTYPE
# Response: -ERR ... <Type>Interface::Attributes
# Extracted: block_type = response.split(" ")[-1].replace("Interface::Attributes", "").strip()
```
The extracted `block_type` string must match a `pytesira/block/<Type>.py` filename exactly.
`UberFilter` appears on the test device but has no pytesira module yet — it is silently skipped.

## TTP Preset Commands — IMPORTANT
Presets are **device-level** commands. They do NOT appear as block instances in
`SESSION get aliases` and cannot be enumerated via TTP. All third-party integrations
(Crestron, Control4, Companion) require manual preset name/ID configuration.

Working commands (confirmed on live hardware):
```
DEVICE recallPreset <id>              # recall by numeric ID → +OK
DEVICE recallPresetByName "<name>"    # recall by exact name → +OK
```
Non-working / unsupported:
```
DEVICE get presetList / presetNameList / numPresets / presetNames  # all -ERR
DEVICE getPresetList / listPresets                                  # parse error
DEVICE savePreset / savePresetByName                               # PRESET_NOT_FOUND
```
`DSP.recall_preset(id)` and `DSP.recall_preset_by_name(name)` are implemented in pytesira.
In HA, preset names are configured in the options flow (comma-separated text field) and
each becomes a `button.*` entity that calls `recall_preset_by_name`.

## Options Flow — Stale Block Type Handling
When loading saved `enabled_block_types` from the config entry, always filter against
`SUPPORTED_BLOCK_TYPES` before passing to the form schema:
```python
current_enabled = [
    t for t in entry.options.get(CONF_ENABLED_BLOCK_TYPES, list(SUPPORTED_BLOCK_TYPES.keys()))
    if t in SUPPORTED_BLOCK_TYPES
]
```
This prevents validation errors when a block type was once in SUPPORTED_BLOCK_TYPES
but has since been removed (e.g. "Preset" was briefly in 0.3.4 then removed in 0.3.5).

## Subscription Rate Floor
All `rate_ms` values passed to `_register_subscription()` must be **≥ 500 ms**.
- BFMic / ParleBeamtracking: 500 ms (raised from 300 ms in 0.3.6)
- AEC meters: 2000 ms
- Standard blocks (levels, mutes): no explicit rate (device default)
The `PARLE_SUB_RATE_MS` constant in const.py mirrors the pytesira default for reference.

## Reconnection & Refresh
- Coordinator background connection retries with exponential backoff: 5 s → 300 s cap
- `_DEVICE_REFRESH_INTERVAL = 30` s passed to DSP; controls subscription renewal cadence
- DSP distributes refresh tasks evenly across the interval (spacing = interval / num_tasks)

## Block Map Caching
pytesira supports `save_block_map()` / `DSP(block_map=...)` for faster restarts.
Cache path defaults to `<config_dir>/biamp_tesira_<entry_id>` (no extension — pytesira
appends `.bmap`). Staleness (e.g. after DSP reprogramming) triggers automatic re-scan.

## HA Coding Conventions
- Use async/await throughout; executor jobs for all pytesira calls
- unique_id pattern: `{serial_number}_{block_id}_{channel}_{attribute}`
- `has_entity_name = True` on all entities
- No selector tricks in options flow schemas — use `str` for plain text fields;
  `SelectSelector(multiple=True, mode=LIST)` for multi-select block-type lists
- Selectors other than SelectSelector can conflict with each other in the same form

## Parlé Beamtracking
### Active Subscriptions (BFMic block, 500 ms rate)
- `audioSources`: azimuth (0–360° CCW from Biamp logo) + intensity (0.0–1.0) per beam
- `segmentsActive`: coarse zone segment active flag

### HA Entities (per channel per BFMic block)
- Azimuth sensor (°, MEASUREMENT) — extra attrs: beams array, elevation, instance_tag
- Intensity sensor (0.0–1.0, MEASUREMENT)
- Zone sensor (string, maps azimuth to named zone via coordinator.zones)
- Talker count sensor (int, beams with intensity > 0.5)

### Custom Event
`biamp_tesira_talker_location` — fired when intensity > 0.5 and azimuth delta > 5°.
Payload: instance_tag, azimuth, intensity, elevation, active_beams, talker_count, zone.

### Zone Mapping
Stored in config entry options as JSON string (CONF_ZONES).
Default: 4 quadrants N/S/E/W. Custom: `{"window": [0, 90], "door": [90, 180], ...}`.
Ranges wrap around 0 when start > end.

## Future: MediaPlayer Platform (not yet implemented)
Combine a LevelControl block + its associated MuteControl into a single
`MediaPlayerEntity` instead of separate number/switch entities. Useful for
rooms where one volume fader + one mute is the natural control surface.

### Planned design
- New platform file: `media_player.py`
- Discovery: look for LevelControl blocks that share a name prefix with a MuteControl
  (e.g. `RoomA-Vol` + `RoomA-Mute`), or configure pairings in options flow
- Entity features: `SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | SUPPORT_VOLUME_STEP`
- `media_player.volume_level` → normalized 0.0–1.0 mapped from dB min/max
- `media_player.is_volume_muted` → channel.muted
- Add `"media_player"` to PLATFORMS in `__init__.py` when ready
- unique_id: `{serial_number}_{block_id}_media_player`

## Future: Voice Pipeline Integration (not yet implemented)
Use Parlé beamtracking to know who was talking when an Assist pipeline run starts,
then surface that zone as context for LLM system prompts.

### Planned design

**Snapshot on pipeline start**
- Listen for `assist_pipeline.run_start` event
- At that moment read the current beam state from each BFMic block
- Store snapshot in a dict keyed by `run_id`: `{run_id: {instance_tag, azimuth, zone, intensity}}`

**Expose current talker zone**
- `input_text.biamp_current_talker_zone` — updated from snapshot dict on each run-start
- Alternatively a `sensor.biamp_current_talker_zone` with state = zone name and
  attributes = full beam snapshot

**Zone-to-room template sensor**
- Template sensor that maps `primary_azimuth` → named zone string
- Zone names and azimuth ranges configurable in options flow (already stored in CONF_ZONES)

**Implementation notes**
- The event listener must be registered in coordinator or a dedicated hass.bus listener
- Beam snapshot should be taken synchronously from the already-cached block state
  (no extra TTP round-trip needed — BFMic subscriptions keep state current)
- run_id is available in the event payload as `event.data["run_id"]`
- Clean up stale run_id entries after a TTL (e.g. 60 s) to avoid memory growth
