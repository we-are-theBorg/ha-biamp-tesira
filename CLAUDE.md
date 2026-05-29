# Biamp Tesira Home Assistant Integration

## Project Goal
Build a Home Assistant custom component (`custom_components/biamp_tesira/`) 
that integrates with Biamp Tesira DSP hardware using the pytesira library.

## Key dependency
pytesira (https://github.com/enp6s0/pytesira) — MIT licensed Python library
that connects to Tesira DSPs via SSH using the Tesira Text Protocol.
Install via: pip install pytesira

## HA Integration Architecture
- `manifest.json` — declares pytesira as a dependency
- `__init__.py` — async_setup_entry, async_unload_entry
- `config_flow.py` — ConfigFlow: hostname, SSH username, SSH password, 
  optional block map cache path
- `coordinator.py` — DataUpdateCoordinator wrapping DSP; uses pytesira 
  callbacks for push updates rather than polling
- `const.py` — DOMAIN = "biamp_tesira", constants
- `number.py` — LevelControl channels → NumberEntity (dB, min/max from DSP)
- `switch.py` — MuteControl, AudioOutput mute, DanteInput/Output mute → SwitchEntity
- `select.py` — SourceSelector → SelectEntity (source list as options)
- `media_player.py` — Optional: combined LevelControl+MuteControl → MediaPlayerEntity

## Async constraints
pytesira uses blocking SSH calls internally. All connection calls must be 
wrapped in hass.async_add_executor_job(). Callbacks from pytesira must 
dispatch back to the HA event loop via hass.loop.call_soon_threadsafe().

## Block map caching
pytesira supports save_block_map() / DSP(block_map=...) to avoid slow 
re-enumeration on restart. Implement this with a configurable cache path.

## SSH host key handling
Config flow should offer a "trust on first connect" option (store fingerprint)
and a toggle for networks where host key checking should be disabled.

## Error handling
Implement reconnection with exponential backoff in coordinator.py.
Handle block_map staleness by catching exceptions and forcing re-scan.

## HA coding conventions
- Use async/await throughout
- Follow HA entity naming: {domain}_{block_name}_{channel}
- Implement unique_id based on device serial_number + block name + channel
- Use hass.config_entries for persistence
- Target HA 2024.x+ APIs

## Parlé Beamtracking Module

### TTP Subscriptions (add to coordinator.py alongside DSP blocks)
- `<instance_tag> subscribe audioSources 1 <label> 300`
  Returns: azimuth (0-360°, CCW from Biamp logo) + intensity (0.0-1.0) per beam
  Active talker threshold: intensity > 0.5
- `<instance_tag> subscribe segmentsActive 1 <label> 300`
  Returns: which zone segment is active (coarse)
- `<instance_tag> get lobeData` (firmware 4.11.2+)
  Returns: azimuth + elevation per beam

### New entities to create (sensor.py additions)
For each Parlé block discovered:
- Primary azimuth sensor (unit: °, state_class: MEASUREMENT)
  Extra attributes: full beams array, elevation, mic_model, instance_tag
- Intensity sensor (unit: None, 0.0-1.0)
- Active zone sensor (text state: zone name from config mapping)
- Talker count sensor (int, beams above threshold)

### Custom events to fire
Event: biamp_tesira_talker_location
Payload: instance_tag, room, azimuth, intensity, elevation, active_beams, timestamp
Fire when: intensity > 0.5 and azimuth change > 5° (debounce)

### Voice pipeline integration
Listen to assist_pipeline_run_event (type: run-start)
Snapshot beam state at pipeline start time → store in dict keyed by run_id
Expose as input_text.current_talker_zone for use in LLM system prompts
Create template sensor mapping azimuth → named zone (configurable in config flow)

### Zone mapping (configurable per installation)
Store azimuth→zone mapping in config entry options
Default: 4 quadrants named N/S/E/W
Allow named zones: {"window": [0,90], "door": [90,180], ...}