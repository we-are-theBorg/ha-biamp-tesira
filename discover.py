#!/usr/bin/env python3
"""
Tesira block discovery probe.
Connects to the DSP, enumerates every alias, logs:
  - which blocks pytesira loaded successfully
  - which blocks it skipped (unknown type) and their raw TTP type string
  - for each loaded block: channel count, attributes, schema

Usage:  python discover.py <host> <user> <password>
"""
import sys
import json
import logging

# Verbose logging so we see every pytesira decision
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
# Suppress overly chatty paramiko crypto negotiation
logging.getLogger("paramiko").setLevel(logging.WARNING)

from pytesira.dsp import DSP
from pytesira.transport.ssh import SSH

HOST = sys.argv[1] if len(sys.argv) > 1 else "172.17.9.1"
USER = sys.argv[2] if len(sys.argv) > 2 else "default"
PASS = sys.argv[3] if len(sys.argv) > 3 else "default"

print(f"\n{'='*70}")
print(f"  Connecting to {HOST} as {USER}")
print(f"{'='*70}\n")

dsp = DSP()
ssh = SSH(hostname=HOST, username=USER, password=PASS, host_key_check=False)
dsp.connect(backend=ssh)

print(f"\n{'='*70}")
print(f"  Device: {dsp.hostname}  S/N: {dsp.serial_number}  FW: {dsp.software_version}")
print(f"  Loaded blocks  : {len(dsp.blocks)}")
print(f"{'='*70}\n")

# ── 1. Loaded blocks ──────────────────────────────────────────────────────────
print("LOADED BLOCKS (pytesira recognised the type)\n")
for block_id, block in dsp.blocks.items():
    btype = type(block).__name__
    print(f"  [{btype:30s}]  {block_id}")

    # Channels
    if hasattr(block, "channels"):
        for idx, ch in block.channels.items():
            schema = ch.schema if hasattr(ch, "schema") else {}
            print(f"      ch{idx:>2d}  {schema}")

    # SourceSelector sources
    if hasattr(block, "sources"):
        for idx, src in block.sources.items():
            schema = src.schema if hasattr(src, "schema") else {}
            print(f"      src{idx:>2d} {schema}")

    # SourceSelector top-level attrs
    for attr in ("ganged", "stereo", "num_input", "num_output",
                 "min_output_level", "max_output_level"):
        if hasattr(block, attr):
            print(f"      .{attr} = {getattr(block, attr)}")
    print()

# ── 2. Skipped blocks (unknown type) ─────────────────────────────────────────
print(f"\n{'='*70}")
print("SKIPPED BLOCKS (pytesira has no module for these types)\n")

try:
    raw_map = dsp._DSP__block_map
except AttributeError:
    raw_map = {}
    print("  (cannot access raw block map)")

skipped = {k: v for k, v in raw_map.items() if k not in dsp.blocks}
if not skipped:
    print("  (none — all discovered blocks were loaded successfully)")
else:
    for block_id, info in skipped.items():
        btype = info.get("type", "???")
        print(f"  [{btype:30s}]  {block_id}")

        # Try to get some info about this unknown block via raw TTP
        print(f"      Probing TTP attributes...")
        for attr in ("numChannels", "numInputs", "numOutputs", "numBeams",
                     "micModel", "model", "serialNumber", "version",
                     "audioSources", "segmentsActive"):
            try:
                resp = dsp.device_command(f'"{block_id}" get {attr}')
                if hasattr(resp, "value") and resp.value is not None:
                    print(f"        get {attr:<22s} → {resp.value}")
            except Exception:
                pass
        print()

# ── 3. Raw alias list (everything the DSP reported) ──────────────────────────
print(f"\n{'='*70}")
print("ALL DSP ALIASES (raw SESSION get aliases)\n")
try:
    aliases = dsp._DSP__dsp_aliases
    for a in aliases:
        in_blocks = "✓ loaded" if a in dsp.blocks else ("✗ skipped" if a in raw_map else "— device")
        rtype = raw_map.get(a, {}).get("type", "")
        print(f"  {in_blocks:12s}  [{rtype:30s}]  {a}")
except AttributeError:
    print("  (cannot access alias list)")

print(f"\n{'='*70}")
print("Discovery complete.")
dsp.close()
