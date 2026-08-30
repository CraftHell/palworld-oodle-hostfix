#!/usr/bin/env python3
"""
palcommon.py
============
Shared plumbing for the palworld-oodle-hostfix tools (``hostfix.py`` and
``optioneditor.py``):

- The Oodle/Kraken (``PlM``) decompression monkeypatch for
  ``palworld-save-tools``, using the cross-platform ``ooz`` bindings.
- Unreal ``FGuid`` <-> display-string byte-order conversion helpers.
- The ``SavFile`` load/save wrapper.

Not meant to be run directly -- it's imported by the other scripts in this
folder.
"""
from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

try:
    import ooz
except ImportError:
    print("Missing dependency 'ooz'. Install with: pip install pyooz", file=sys.stderr)
    sys.exit(1)

try:
    from palworld_save_tools import palsav
except ImportError:
    print(
        "Missing dependency 'palworld-save-tools'. Install with: "
        "pip install palworld-save-tools",
        file=sys.stderr,
    )
    sys.exit(1)


SINGLEPLAYER_HOST_UID = "00000000-0000-0000-0000-000000000001"
UID_HEX_RE = re.compile(r"^[0-9a-fA-F]{32}$")
GUID_STR_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


# --------------------------------------------------------------------------
# Oodle ("PlM") decompression support
#
# palworld-save-tools only knows how to decompress the classic zlib
# ("PlZ") format. This monkeypatches it to also handle the newer
# Oodle/Kraken ("PlM") format used by dedicated servers (and possibly
# local saves) since Palworld's 2026 update, using the `ooz` bindings.
# Recompression always uses plain zlib -- Palworld reads both formats
# fine, so there is no need to write Oodle back out.
# --------------------------------------------------------------------------
_orig_decompress = palsav.decompress_sav_to_gvas


def _patched_decompress_sav_to_gvas(data: bytes):
    magic_bytes = data[8:11]
    data_start_offset = 12
    if magic_bytes == b"CNK":
        magic_bytes = data[20:23]
        data_start_offset = 24
    if magic_bytes != b"PlM":
        return _orig_decompress(data)

    uncompressed_len = int.from_bytes(data[0:4], byteorder="little")
    compressed_len = int.from_bytes(data[4:8], byteorder="little")
    save_type = data[data_start_offset - 1]
    payload = data[data_start_offset:]

    if save_type not in (0x31, 0x32):
        raise Exception(f"unhandled PlM compression type: {save_type}")

    if save_type == 0x31:
        if compressed_len != len(payload):
            raise Exception(f"incorrect compressed length: {compressed_len}")
        out = ooz.decompress(payload, uncompressed_len)
    else:
        intermediate = ooz.decompress(payload, compressed_len)
        out = ooz.decompress(intermediate, uncompressed_len)

    if len(out) != uncompressed_len:
        raise Exception(
            f"incorrect uncompressed length: expected {uncompressed_len}, got {len(out)}"
        )
    return out, save_type


palsav.decompress_sav_to_gvas = _patched_decompress_sav_to_gvas
# convert.py (and anything else) does `from palworld_save_tools.palsav import
# decompress_sav_to_gvas` -- re-patch that binding too, in case it was
# imported before this module ran.
try:
    import palworld_save_tools.commands.convert as _convert_mod

    _convert_mod.decompress_sav_to_gvas = _patched_decompress_sav_to_gvas
except ImportError:
    pass


# --------------------------------------------------------------------------
# GUID <-> raw bytes
#
# Palworld (Unreal FGuid) stores GUIDs as 4 little-endian uint32 words,
# which is NOT the same byte order as Python's standard uuid.UUID. This
# converts between the display string and the exact 16 raw bytes as they
# appear in the decompressed save.
# --------------------------------------------------------------------------
_REORDER = [0x3, 0x2, 0x1, 0x0, 0x7, 0x6, 0x5, 0x4, 0xB, 0xA, 0x9, 0x8, 0xF, 0xE, 0xD, 0xC]


def guid_str_to_raw(guid_str: str) -> bytes:
    b = uuid.UUID(guid_str).bytes
    return bytes(b[i] for i in _REORDER)


def raw_to_guid_str(raw: bytes) -> str:
    b = raw
    return "%08x-%04x-%04x-%04x-%04x%08x" % (
        (b[3] << 24) | (b[2] << 16) | (b[1] << 8) | b[0],
        (b[7] << 8) | b[6],
        (b[5] << 8) | b[4],
        (b[0xB] << 8) | b[0xA],
        (b[9] << 8) | b[8],
        (b[0xF] << 24) | (b[0xE] << 16) | (b[0xD] << 8) | b[0xC],
    )


def filename_uid_to_guid_str(stem: str) -> str:
    """'AAAAAAAA000000000000000000000000' -> 'aaaaaaaa-0000-0000-0000-000000000000'"""
    h = stem.lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def guid_str_to_filename_uid(guid_str: str) -> str:
    """'aaaaaaaa-0000-0000-0000-000000000000' -> 'AAAAAAAA000000000000000000000000'"""
    return guid_str.replace("-", "").upper()


# --------------------------------------------------------------------------
# Low-level .sav wrapper
# --------------------------------------------------------------------------
@dataclass
class SavFile:
    path: Path
    raw_gvas: bytes
    save_type: int

    @staticmethod
    def load(path: Path) -> "SavFile":
        data = path.read_bytes()
        raw, save_type = palsav.decompress_sav_to_gvas(data)
        return SavFile(path=path, raw_gvas=raw, save_type=save_type)

    def write(self, out_path: Path) -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(palsav.compress_gvas_to_sav(self.raw_gvas, self.save_type))
