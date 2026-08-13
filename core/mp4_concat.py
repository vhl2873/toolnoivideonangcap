"""
Python-level MP4 concatenation.

Đọc/ghi raw AVCC bytes trực tiếp từ container MP4, hoàn toàn không dùng
h264_mp4toannexb hay bất kỳ bitstream filter nào của FFmpeg.

Hỗ trợ: non-fragmented MP4, stco hoặc co64, chuẩn YouTube / thông thường.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Callable, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Primitive readers / writers
# ─────────────────────────────────────────────────────────────────────────────

def _r8(d: bytes, o: int) -> int:
    return d[o]

def _r16(d: bytes, o: int) -> int:
    return struct.unpack_from(">H", d, o)[0]

def _r32(d: bytes, o: int) -> int:
    return struct.unpack_from(">I", d, o)[0]

def _r32s(d: bytes, o: int) -> int:
    return struct.unpack_from(">i", d, o)[0]

def _r64(d: bytes, o: int) -> int:
    return struct.unpack_from(">Q", d, o)[0]

def _box(t: str, payload: bytes) -> bytes:
    """Build MP4 box: [size(4)] [type(4)] [payload]."""
    size = len(payload) + 8
    return struct.pack(">I4s", size, t.encode()[:4]) + payload


# ─────────────────────────────────────────────────────────────────────────────
# Box iteration and lookup
# ─────────────────────────────────────────────────────────────────────────────

def _iter_boxes(data: bytes):
    """
    Yield (box_start, box_size, box_type, header_size) for each
    top-level box in `data`.
    box_start: byte offset of box header within `data`.
    box_size:  total byte size including header.
    header_size: 8 (normal) or 16 (extended-size).
    """
    pos = 0
    while pos + 8 <= len(data):
        raw = _r32(data, pos)
        btype = data[pos + 4: pos + 8].decode("ascii", errors="replace")
        hdr = 8
        if raw == 1:                     # extended 64-bit size
            if pos + 16 > len(data):
                break
            raw = _r64(data, pos + 8)
            hdr = 16
        elif raw == 0:                   # box extends to EOF
            raw = len(data) - pos
        if raw < hdr:
            break
        yield pos, raw, btype, hdr
        pos += raw


def _find(data: bytes, btype: str) -> Optional[tuple[int, int, bytes]]:
    """
    Return (box_start, box_size, box_payload) for the FIRST box
    of the given type in `data`, or None.
    """
    for start, size, t, hdr in _iter_boxes(data):
        if t == btype:
            return start, size, data[start + hdr: start + size]
    return None


def _find_nested(data: bytes, *path: str) -> Optional[bytes]:
    """Walk a box path and return the innermost payload, or None."""
    cur = data
    for name in path:
        r = _find(cur, name)
        if r is None:
            return None
        cur = r[2]
    return cur


# ─────────────────────────────────────────────────────────────────────────────
# Sample-table parsers
# ─────────────────────────────────────────────────────────────────────────────

def _parse_stts(d: bytes) -> list[tuple[int, int]]:
    """time-to-sample: [(count, delta), ...]"""
    n = _r32(d, 4)
    return [struct.unpack_from(">II", d, 8 + i * 8) for i in range(n)]


def _parse_stss(d: bytes) -> list[int]:
    """sync-sample (keyframes): [1-based sample index, ...]"""
    n = _r32(d, 4)
    return [_r32(d, 8 + i * 4) for i in range(n)]


def _parse_ctts(d: bytes) -> list[tuple[int, int]]:
    """composition-time-offset: [(count, signed_offset), ...]"""
    n = _r32(d, 4)
    return [(_r32(d, 8 + i * 8), _r32s(d, 12 + i * 8)) for i in range(n)]


def _parse_stsc(d: bytes) -> list[tuple[int, int, int]]:
    """sample-to-chunk: [(first_chunk, samples_per_chunk, desc_index), ...]"""
    n = _r32(d, 4)
    return [struct.unpack_from(">III", d, 8 + i * 12) for i in range(n)]


def _parse_stsz(d: bytes) -> list[int]:
    """sample-sizes: [size, ...]"""
    default, n = _r32(d, 4), _r32(d, 8)
    if default:
        return [default] * n
    return [_r32(d, 12 + i * 4) for i in range(n)]


def _parse_stco(d: bytes) -> list[int]:
    """chunk-offsets (32-bit): [absolute_offset, ...]"""
    n = _r32(d, 4)
    return [_r32(d, 8 + i * 4) for i in range(n)]


def _parse_co64(d: bytes) -> list[int]:
    """chunk-offsets (64-bit): [absolute_offset, ...]"""
    n = _r32(d, 4)
    return [_r64(d, 8 + i * 8) for i in range(n)]


# ─────────────────────────────────────────────────────────────────────────────
# Sample-table builders
# ─────────────────────────────────────────────────────────────────────────────

def _build_stts(entries: list[tuple[int, int]]) -> bytes:
    # Compact adjacent equal-delta runs
    compact: list[list[int]] = []
    for c, d in entries:
        if compact and compact[-1][1] == d:
            compact[-1][0] += c
        else:
            compact.append([c, d])
    body = struct.pack(">II", 0, len(compact))
    for c, d in compact:
        body += struct.pack(">II", c, d)
    return _box("stts", body)


def _build_stss(indices: list[int]) -> bytes:
    body = struct.pack(">II", 0, len(indices))
    for i in indices:
        body += struct.pack(">I", i)
    return _box("stss", body)


def _build_ctts(entries: list[tuple[int, int]]) -> bytes:
    body = struct.pack(">II", 1, len(entries))   # version=1 → signed offsets
    for c, o in entries:
        body += struct.pack(">Ii", c, o)
    return _box("ctts", body)


def _build_stsc(entries: list[tuple[int, int, int]]) -> bytes:
    body = struct.pack(">II", 0, len(entries))
    for fc, spc, sdi in entries:
        body += struct.pack(">III", fc, spc, sdi)
    return _box("stsc", body)


def _build_stsz(sizes: list[int]) -> bytes:
    body = struct.pack(">III", 0, 0, len(sizes))  # default_size=0
    for s in sizes:
        body += struct.pack(">I", s)
    return _box("stsz", body)


def _build_co64(offsets: list[int]) -> bytes:
    """Always use 64-bit offsets — safe for large concatenated files."""
    body = struct.pack(">II", 0, len(offsets))
    for o in offsets:
        body += struct.pack(">Q", o)
    return _box("co64", body)


# ─────────────────────────────────────────────────────────────────────────────
# Track data container
# ─────────────────────────────────────────────────────────────────────────────

class _Track:
    __slots__ = (
        "handler", "timescale", "duration",
        "stts", "stss", "ctts", "stsc", "stsz", "stco",
        "stsd_raw",   # raw stsd box bytes (includes 8-byte header)
        "hdlr_raw",   # raw hdlr box bytes
        "orig_volume",  # from tkhd, preserved in output
        "orig_w", "orig_h",  # width/height from tkhd (fixed-point 16.16)
        "sample_count", "chunk_count",
        "mdat_offset_adj",  # = output_mdat_payload_start - original_mdat_payload_start
    )

    def __init__(self) -> None:
        self.handler = ""
        self.timescale = 0
        self.duration = 0
        self.stts: list[tuple[int, int]] = []
        self.stss: list[int] = []
        self.ctts: list[tuple[int, int]] = []
        self.stsc: list[tuple[int, int, int]] = []
        self.stsz: list[int] = []
        self.stco: list[int] = []
        self.stsd_raw = b""
        self.hdlr_raw = b""
        self.orig_volume = 0
        self.orig_w = 0
        self.orig_h = 0
        self.sample_count = 0
        self.chunk_count = 0
        self.mdat_offset_adj = 0


# ─────────────────────────────────────────────────────────────────────────────
# File parser
# ─────────────────────────────────────────────────────────────────────────────

class _MP4Info:
    __slots__ = ("ftyp_raw", "mdat_file_offset", "mdat_size", "tracks",
                 "movie_timescale", "movie_duration", "is_fragmented")

    def __init__(self) -> None:
        self.ftyp_raw = b""
        self.mdat_file_offset = 0   # absolute offset of mdat PAYLOAD in original file
        self.mdat_size = 0          # mdat payload size in bytes
        self.tracks: list[_Track] = []
        self.movie_timescale = 0
        self.movie_duration = 0
        self.is_fragmented = False  # True nếu là fMP4 (có mvex trong moov)


def _parse_tkhd(tkhd_payload: bytes) -> tuple[int, int, int]:
    """Return (volume, width_fp, height_fp) from tkhd payload."""
    version = tkhd_payload[0]
    if version == 1:
        # v1: 4+8+8+4+4+8 = 36 before reserved[2](8) → layer@44, altgr@46, vol@48
        # matrix starts @52 (36 bytes) → width@88, height@92
        volume = _r16(tkhd_payload, 48)
        w = _r32(tkhd_payload, 88)
        h = _r32(tkhd_payload, 92)
    else:
        # v0: 4+4+4+4+4+4 = 24 before reserved[2](8) → layer@32, altgr@34, vol@36
        # matrix starts @40 (36 bytes) → width@76, height@80
        volume = _r16(tkhd_payload, 36)
        w = _r32(tkhd_payload, 76)
        h = _r32(tkhd_payload, 80)
    return volume, w, h


def _parse_track(trak_payload: bytes) -> Optional[_Track]:
    """Parse a trak box payload and return _Track, or None on error."""
    t = _Track()

    # tkhd
    tkhd_r = _find(trak_payload, "tkhd")
    if not tkhd_r:
        return None
    t.orig_volume, t.orig_w, t.orig_h = _parse_tkhd(tkhd_r[2])

    # mdia
    mdia_r = _find(trak_payload, "mdia")
    if not mdia_r:
        return None
    mdia = mdia_r[2]

    # mdhd → timescale + duration
    mdhd_r = _find(mdia, "mdhd")
    if not mdhd_r:
        return None
    mdhd = mdhd_r[2]
    version = mdhd[0]
    if version == 1:
        t.timescale = _r32(mdhd, 20)
        t.duration = _r64(mdhd, 24)
    else:
        t.timescale = _r32(mdhd, 12)
        t.duration = _r32(mdhd, 16)

    # hdlr → handler type
    hdlr_r = _find(mdia, "hdlr")
    if not hdlr_r:
        return None
    hdlr_payload = hdlr_r[2]
    t.handler = hdlr_payload[8:12].decode("ascii", errors="replace")
    # Preserve raw hdlr box (for output)
    t.hdlr_raw = mdia[hdlr_r[0]: hdlr_r[0] + hdlr_r[1]]

    # stbl
    stbl = _find_nested(mdia, "minf", "stbl")
    if stbl is None:
        return None

    for bname, parser, attr in [
        ("stts", _parse_stts, "stts"),
        ("stss", _parse_stss, "stss"),
        ("ctts", _parse_ctts, "ctts"),
        ("stsc", _parse_stsc, "stsc"),
        ("stsz", _parse_stsz, "stsz"),
    ]:
        r = _find(stbl, bname)
        if r:
            setattr(t, attr, parser(r[2]))

    # chunk offsets (prefer co64 for large files)
    co64_r = _find(stbl, "co64")
    if co64_r:
        t.stco = _parse_co64(co64_r[2])
    else:
        stco_r = _find(stbl, "stco")
        if stco_r:
            t.stco = _parse_stco(stco_r[2])

    # stsd (raw, preserved for output)
    stsd_r = _find(stbl, "stsd")
    if stsd_r:
        t.stsd_raw = stbl[stsd_r[0]: stsd_r[0] + stsd_r[1]]

    t.sample_count = sum(c for c, _ in t.stts)
    t.chunk_count = len(t.stco)
    return t


def _parse_file(path: str) -> Optional[_MP4Info]:
    """
    Parse an MP4 file and return _MP4Info, or None on error.
    Reads the entire file into memory (required to locate moov/mdat boxes).
    """
    data = Path(path).read_bytes()
    info = _MP4Info()

    ftyp_r = _find(data, "ftyp")
    if ftyp_r:
        info.ftyp_raw = data[ftyp_r[0]: ftyp_r[0] + ftyp_r[1]]

    # moov
    moov_r = _find(data, "moov")
    if not moov_r:
        return None
    moov = moov_r[2]  # moov payload (without box header)

    # mvhd → movie timescale + duration
    mvhd_r = _find(moov, "mvhd")
    if not mvhd_r:
        return None
    mvhd = mvhd_r[2]
    version = mvhd[0]
    if version == 1:
        info.movie_timescale = _r32(mvhd, 20)
        info.movie_duration  = _r64(mvhd, 24)
    else:
        info.movie_timescale = _r32(mvhd, 12)
        info.movie_duration  = _r32(mvhd, 16)

    # Detect fragmented MP4: có mvex trong moov → fMP4
    if _find(moov, "mvex") is not None:
        info.is_fragmented = True
        return info  # tracks sẽ rỗng; caller kiểm tra is_fragmented

    # trak boxes — CRITICAL: use box_start + hdr, not just hdr
    for trak_start, trak_size, btype, trak_hdr in _iter_boxes(moov):
        if btype == "trak":
            trak_payload = moov[trak_start + trak_hdr: trak_start + trak_size]
            t = _parse_track(trak_payload)
            if t:
                info.tracks.append(t)

    # mdat — find FIRST mdat (standard non-fragmented MP4 has exactly one)
    mdat_r = _find(data, "mdat")
    if not mdat_r:
        return None
    info.mdat_file_offset = mdat_r[0] + 8   # skip 8-byte box header
    info.mdat_size = mdat_r[1] - 8

    return info


# ─────────────────────────────────────────────────────────────────────────────
# Box builders (ISO 14496-12 compliant)
# ─────────────────────────────────────────────────────────────────────────────

def _build_mvhd(timescale: int, duration: int, next_track_id: int) -> bytes:
    """mvhd version 0 (32-bit timestamps)."""
    body  = struct.pack(">I", 0)                    # version=0, flags=0
    body += struct.pack(">IIII",
                        0, 0,                       # creation, modification
                        timescale,
                        min(duration, 0xFFFFFFFF))
    body += struct.pack(">i", 0x00010000)           # rate = 1.0
    body += struct.pack(">H", 0x0100)               # volume = 1.0
    body += b"\x00" * 10                            # reserved (2+4+4)
    body += struct.pack(">iiiiiiiii",               # identity matrix
                        0x00010000, 0, 0,
                        0, 0x00010000, 0,
                        0, 0, 0x40000000)
    body += b"\x00" * 24                            # pre_defined[6]
    body += struct.pack(">I", next_track_id)
    return _box("mvhd", body)


def _build_tkhd(track_id: int, duration: int,
                volume: int, width_fp: int, height_fp: int) -> bytes:
    """
    tkhd version 0 (32-bit timestamps).
    ISO 14496-12 §8.3.2 — payload must be exactly 84 bytes.
    """
    body  = struct.pack(">I", 3)                    # version=0, flags=3 (enabled+in_movie)
    body += struct.pack(">IIIII",
                        0, 0,                       # creation, modification
                        track_id,
                        0,                          # reserved
                        min(duration, 0xFFFFFFFF))
    body += struct.pack(">II", 0, 0)                # reserved[2]
    body += struct.pack(">HHHh",
                        0, 0,                       # layer, alternate_group
                        volume, 0)                  # volume, reserved
    body += struct.pack(">iiiiiiiii",               # transformation matrix (identity)
                        0x00010000, 0, 0,
                        0, 0x00010000, 0,
                        0, 0, 0x40000000)
    body += struct.pack(">II", width_fp, height_fp)
    assert len(body) == 84, f"tkhd payload wrong: {len(body)}"
    return _box("tkhd", body)


def _build_mdhd(timescale: int, duration: int) -> bytes:
    """mdhd version 0 (32-bit timestamps)."""
    body  = struct.pack(">I", 0)                    # version=0, flags=0
    body += struct.pack(">IIII",
                        0, 0,                       # creation, modification
                        timescale,
                        min(duration, 0xFFFFFFFF))
    body += b"\x55\xc4\x00\x00"                    # language='und', pre_defined=0
    return _box("mdhd", body)


def _build_dinf() -> bytes:
    """dinf / dref with one self-contained URL entry."""
    # Self-contained URL entry: size=12, type='url ', FullBox(v=0,f=1)
    url_entry = struct.pack(">I4sI", 12, b"url ", 1)
    dref_payload = struct.pack(">II", 0, 1) + url_entry   # v+f=0, entry_count=1
    return _box("dinf", _box("dref", dref_payload))


def _build_vmhd() -> bytes:
    """vmhd: 12-byte payload. flags=1 as per spec."""
    body = struct.pack(">I", 1)               # version=0, flags=1
    body += struct.pack(">HHHH", 0, 0, 0, 0)  # graphicsMode=0, opcolor[3]=0
    return _box("vmhd", body)


def _build_smhd() -> bytes:
    """smhd: 8-byte payload."""
    body = struct.pack(">I", 0)   # version=0, flags=0
    body += struct.pack(">HH", 0, 0)  # balance=0, reserved=0
    return _box("smhd", body)


def _build_stbl(merged_track: _Track, merged_stco: list[int]) -> bytes:
    parts = [merged_track.stsd_raw]
    parts.append(_build_stts(merged_track.stts))
    if merged_track.stss:
        parts.append(_build_stss(merged_track.stss))
    if merged_track.ctts:
        parts.append(_build_ctts(merged_track.ctts))
    parts.append(_build_stsc(merged_track.stsc))
    parts.append(_build_stsz(merged_track.stsz))
    parts.append(_build_co64(merged_stco))           # always co64 (safe for >4 GB)
    return _box("stbl", b"".join(parts))


def _build_moov(
    tracks_per_file: list[list[_Track]],
    movie_timescale: int,
    total_movie_duration: int,
) -> bytes:
    n_files = len(tracks_per_file)
    n_tracks = len(tracks_per_file[0])

    mvhd = _build_mvhd(movie_timescale, total_movie_duration, n_tracks + 1)

    trak_boxes = b""
    for track_idx in range(n_tracks):
        first_t = tracks_per_file[0][track_idx]
        is_video = "vide" in first_t.handler

        # ── Merge sample tables ──────────────────────────────────────────────
        merged_stts: list[tuple[int, int]] = []
        merged_stss: list[int] = []
        merged_ctts: list[tuple[int, int]] = []
        merged_stsc: list[tuple[int, int, int]] = []
        merged_stsz: list[int] = []
        merged_stco: list[int] = []

        cum_samples = 0
        cum_chunks  = 0
        total_dur_ts = 0

        for file_idx in range(n_files):
            t = tracks_per_file[file_idx][track_idx]

            merged_stts.extend(t.stts)

            # Keyframe indices: shift by cumulative sample count
            merged_stss.extend(s + cum_samples for s in t.stss)

            merged_ctts.extend(t.ctts)

            # Sample-to-chunk: shift first_chunk by cumulative chunk count
            for fc, spc, sdi in t.stsc:
                merged_stsc.append((fc + cum_chunks, spc, sdi))

            merged_stsz.extend(t.stsz)

            # Chunk offsets: original absolute offset + adjustment for this file
            for off in t.stco:
                merged_stco.append(off + t.mdat_offset_adj)

            cum_samples += t.sample_count
            cum_chunks  += t.chunk_count
            total_dur_ts += t.duration

        # ── Build merged track object for stbl builder ───────────────────────
        mt = _Track()
        mt.stts = merged_stts
        mt.stss = merged_stss
        mt.ctts = merged_ctts
        mt.stsc = merged_stsc
        mt.stsz = merged_stsz
        mt.stsd_raw = first_t.stsd_raw

        stbl = _build_stbl(mt, merged_stco)
        dinf = _build_dinf()
        media_hdr = _build_vmhd() if is_video else _build_smhd()
        minf = _box("minf", media_hdr + dinf + stbl)
        mdhd = _build_mdhd(first_t.timescale, total_dur_ts)
        mdia = _box("mdia", mdhd + first_t.hdlr_raw + minf)

        # tkhd duration in movie timescale
        dur_movie = int(total_dur_ts * movie_timescale / first_t.timescale) \
            if first_t.timescale else 0
        tkhd = _build_tkhd(
            track_idx + 1, dur_movie,
            first_t.orig_volume, first_t.orig_w, first_t.orig_h
        )

        trak_boxes += _box("trak", tkhd + mdia)

    return _box("moov", mvhd + trak_boxes)


# ─────────────────────────────────────────────────────────────────────────────
# Main concat function
# ─────────────────────────────────────────────────────────────────────────────

def mp4_concat(
    input_paths: list[str],
    output_path: str,
    *,
    emit_log: Callable[[str], None],
) -> bool:
    """
    Concatenate MP4 files at container level (mdat copy + moov rebuild).
    Copies raw AVCC video and AAC audio without any bitstream conversion.

    Returns True on success, False on failure (caller should log and fallback).
    """
    emit_log(f"[mp4_concat] Phan tich {len(input_paths)} file...")

    # ── Parse all inputs ─────────────────────────────────────────────────────
    infos: list[_MP4Info] = []
    for p in input_paths:
        try:
            info = _parse_file(p)
        except Exception as exc:
            emit_log(f"[mp4_concat] Loi doc file {Path(p).name}: {exc}")
            return False
        if info is None:
            emit_log(f"[mp4_concat] Khong the phan tich: {Path(p).name}")
            return False
        if info.is_fragmented:
            emit_log(f"[mp4_concat] File la fMP4 (fragmented): {Path(p).name}")
            emit_log("[mp4_concat] fMP4 can FFmpeg concat truc tiep — tra ve False")
            return False
        if not info.tracks:
            emit_log(f"[mp4_concat] Khong co track: {Path(p).name}")
            return False
        infos.append(info)

    n_tracks = len(infos[0].tracks)
    for i, info in enumerate(infos):
        if len(info.tracks) != n_tracks:
            emit_log(f"[mp4_concat] File {i+1} co {len(info.tracks)} track, "
                     f"khac file dau ({n_tracks} track)")
            return False

    # ── Compute output layout ─────────────────────────────────────────────────
    # Layout: [ftyp] [mdat_0] [mdat_1] ... [mdat_N-1] [moov]
    # mdat PAYLOAD starts right after the 8-byte mdat box header.
    ftyp_raw  = infos[0].ftyp_raw
    ftyp_size = len(ftyp_raw)

    mdat_payload_starts: list[int] = []
    cursor = ftyp_size
    for info in infos:
        cursor += 8                          # 8-byte mdat box header
        mdat_payload_starts.append(cursor)
        cursor += info.mdat_size

    # cursor = offset where moov will be written (we don't need it here since
    # moov comes after all mdats and chunk offsets are already absolute)

    # ── Assign offset adjustments to each track ───────────────────────────────
    tracks_per_file: list[list[_Track]] = []
    for file_idx, info in enumerate(infos):
        file_tracks: list[_Track] = []
        for t in info.tracks:
            # Original chunk offset points to an absolute position in the
            # original file's mdat payload area.
            # In output, this same data is at:
            #   mdat_payload_starts[file_idx] + (original_offset - info.mdat_file_offset)
            # = original_offset + (mdat_payload_starts[file_idx] - info.mdat_file_offset)
            t.mdat_offset_adj = mdat_payload_starts[file_idx] - info.mdat_file_offset
            file_tracks.append(t)
        tracks_per_file.append(file_tracks)

    # ── Build merged moov ─────────────────────────────────────────────────────
    movie_ts = infos[0].movie_timescale or 1000
    # Total movie duration: sum video track durations converted to movie_ts
    total_movie_dur = 0
    for info in infos:
        for t in info.tracks:
            if "vide" in t.handler and t.timescale > 0:
                total_movie_dur += int(t.duration * movie_ts / t.timescale)
                break

    emit_log(f"[mp4_concat] Xay dung moov ({n_tracks} track, "
             f"{len(input_paths)} file)...")
    moov = _build_moov(tracks_per_file, movie_ts, total_movie_dur)

    # ── Write output ──────────────────────────────────────────────────────────
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    emit_log(f"[mp4_concat] Ghi output: {out.name}")
    CHUNK = 8 * 1024 * 1024   # 8 MB read chunks

    with out.open("wb") as f:
        f.write(ftyp_raw)

        for file_idx, info in enumerate(infos):
            src_path = input_paths[file_idx]
            payload_size = info.mdat_size

            # mdat box header
            f.write(struct.pack(">I4s", payload_size + 8, b"mdat"))

            # copy mdat payload in chunks
            with open(src_path, "rb") as src:
                src.seek(info.mdat_file_offset)
                remaining = payload_size
                while remaining > 0:
                    n = min(CHUNK, remaining)
                    chunk = src.read(n)
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)

            emit_log(f"[mp4_concat] mdat {file_idx + 1}/{len(input_paths)}: "
                     f"{payload_size / (1024*1024):.1f} MB")

        f.write(moov)

    out_size = out.stat().st_size
    emit_log(f"[mp4_concat] Xong: {out.name} — {out_size / (1024*1024):.1f} MB")
    return True
