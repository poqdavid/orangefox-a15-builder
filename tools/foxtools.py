"""Shared helpers for the a15 OrangeFox tools (Python 3.8+, stdlib only)."""

import lzma
import os
import re
import shutil
import struct
import subprocess
import sys
import zlib

BOOT_MAGIC = b"ANDROID!"
DT_TABLE_MAGIC = b"\xd7\xb7\xab\x1e"

_VERMAGIC_RE = re.compile(rb"vermagic=([^\x00]+)")
_LINUX_VERSION_RE = re.compile(rb"Linux version (\S+) ")


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


# --- compression -----------------------------------------------------------

LZ4_FRAME_MAGIC = b"\x04\x22\x4d\x18"
LZ4_LEGACY_MAGIC = b"\x02\x21\x4c\x18"   # "lz4 -l", used for Android ramdisks
_LZ4_LEGACY_BLOCK = 8 << 20


def _lz4_block_py(src):
    """Pure-python LZ4 block decoder (fallback when no lz4 module/tool exists)."""
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        if offset == 0:
            die("corrupt lz4 data")
        mlen = token & 15
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(out) - offset
        if offset >= mlen:
            out += out[start:start + mlen]
        else:  # overlapping match: repeat the last `offset` bytes
            chunk = bytes(out[start:])
            out += (chunk * (mlen // offset + 1))[:mlen]
    return bytes(out)


def _lz4_legacy_decompress(data):
    try:
        import lz4.block  # type: ignore

        def block(b):
            return lz4.block.decompress(b, uncompressed_size=_LZ4_LEGACY_BLOCK)
    except ImportError:
        if shutil.which("lz4"):
            return subprocess.run(["lz4", "-dc"], input=data, stdout=subprocess.PIPE, check=True).stdout
        block = _lz4_block_py
    out = []
    pos = 4
    while pos + 4 <= len(data):
        raw = data[pos:pos + 4]
        pos += 4
        if raw == LZ4_LEGACY_MAGIC:      # concatenated frame
            continue
        size = struct.unpack("<I", raw)[0]
        if size == 0 or pos + size > len(data):
            break                         # padding after the last block
        out.append(block(data[pos:pos + size]))
        pos += size
    return b"".join(out)


def _lz4_frame_decompress(data):
    try:
        import lz4.frame  # type: ignore

        return lz4.frame.decompress(data)
    except ImportError:
        pass
    if not shutil.which("lz4"):
        die("this file is lz4-compressed: install the lz4 tool or 'pip install lz4'")
    return subprocess.run(["lz4", "-dc"], input=data, stdout=subprocess.PIPE, check=True).stdout


def decompress(data):
    """Return (kind, payload) for gzip, lz4 (frame or legacy), xz, lzma or raw data."""
    if data[:2] == b"\x1f\x8b":
        # a boot image section can carry padding after the gzip stream
        d = zlib.decompressobj(wbits=31)
        return "gzip", d.decompress(data) + d.flush()
    if data[:4] == LZ4_FRAME_MAGIC:
        return "lz4", _lz4_frame_decompress(data)
    if data[:4] == LZ4_LEGACY_MAGIC:
        return "lz4-legacy", _lz4_legacy_decompress(data)
    if data[:6] == b"\xfd7zXZ\x00":
        return "xz", lzma.decompress(data)
    if data[:3] == b"\x5d\x00\x00":
        return "lzma", lzma.decompress(data, format=lzma.FORMAT_ALONE)
    return "raw", data


# --- kernel / modules ------------------------------------------------------

def kernel_release(kernel_blob):
    """Kernel release string (e.g. 5.10.198-android12-9-28575149) from an Image/Image.gz."""
    _, image = decompress(kernel_blob)
    m = _LINUX_VERSION_RE.search(image)
    return m.group(1).decode() if m else None


def module_vermagic(path):
    with open(path, "rb") as f:
        m = _VERMAGIC_RE.search(f.read())
    return m.group(1).decode(errors="replace").strip() if m else None


def check_modules(release, module_dirs):
    """Return a list of (path, vermagic) whose vermagic does not start with release."""
    bad = []
    for d in module_dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".ko"):
                continue
            path = os.path.join(d, name)
            vm = module_vermagic(path)
            if vm is None or vm.split(" ", 1)[0] != release:
                bad.append((path, vm))
    return bad


def parse_modules_dep(text):
    """modules.dep -> {module.ko: [dependency.ko, ...]} (basenames, file order)."""
    deps = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        mod, rest = line.split(":", 1)
        deps[os.path.basename(mod.strip())] = [os.path.basename(d) for d in rest.split()]
    return deps


def ramdisk_loadable(libmod_dir, module):
    """Why `module` cannot be loaded from the ramdisk's /lib/modules, or None if it can."""
    dep_file = os.path.join(libmod_dir, "modules.dep")
    if not os.path.isfile(os.path.join(libmod_dir, module)):
        return "not in lib/modules"
    if not os.path.isfile(dep_file):
        return "lib/modules has no modules.dep"
    with open(dep_file, encoding="utf-8") as f:
        deps = parse_modules_dep(f.read())
    if module not in deps:
        return "no modules.dep entry"
    missing = [d for d in deps[module] if not os.path.isfile(os.path.join(libmod_dir, d))]
    if missing:
        return "dependencies missing: " + ", ".join(missing)
    return None


def module_depends(path):
    """The module's own depends= list (module names, not file names)."""
    with open(path, "rb") as f:
        m = re.search(rb"depends=([^\x00]*)", f.read())
    return [d for d in m.group(1).decode().split(",") if d] if m else []


# --- boot image (header v0-v2) -------------------------------------------

def _pages(size, page):
    return (size + page - 1) // page


def parse_bootimg(data):
    """Parse an Android boot/recovery image with header v0, v1 or v2."""
    if data[:8] != BOOT_MAGIC:
        die("not an Android boot image (missing ANDROID! magic)")
    (kernel_size, kernel_addr, ramdisk_size, ramdisk_addr, second_size, second_addr,
     tags_addr, page_size, header_version, os_version) = struct.unpack_from("<10I", data, 8)
    if header_version > 2:
        die(f"boot image header v{header_version} is not supported here (expected v2)")
    name = data[48:64].split(b"\0", 1)[0].decode(errors="replace")
    cmdline = data[64:576].split(b"\0", 1)[0]
    extra = data[608:1632].split(b"\0", 1)[0]
    hdr = {
        "header_version": header_version,
        "page_size": page_size,
        "kernel_addr": kernel_addr,
        "ramdisk_addr": ramdisk_addr,
        "second_addr": second_addr,
        "tags_addr": tags_addr,
        "os_version": os_version,
        "board": name,
        "cmdline": (cmdline + extra).decode(errors="replace"),
        "dtb_addr": None,
    }
    off = 1632
    recovery_dtbo_size = 0
    dtb_size = 0
    if header_version >= 1:
        recovery_dtbo_size, _recovery_dtbo_offset, _header_size = struct.unpack_from("<IQI", data, off)
        off += 16
    if header_version >= 2:
        dtb_size, dtb_addr = struct.unpack_from("<IQ", data, off)
        hdr["dtb_addr"] = dtb_addr

    p = page_size
    pos = p  # header occupies the first page(s)
    sections = {}
    for key, size in (("kernel", kernel_size), ("ramdisk", ramdisk_size), ("second", second_size),
                      ("recovery_dtbo", recovery_dtbo_size), ("dtb", dtb_size)):
        sections[key] = data[pos:pos + size]
        pos += _pages(size, p) * p
    hdr["end_of_image"] = pos
    return hdr, sections


# --- cpio (newc) -----------------------------------------------------------

def cpio_entries(data):
    """Yield (name, mode, payload) from a newc/crc cpio archive."""
    pos = 0
    while pos + 110 <= len(data):
        magic = data[pos:pos + 6]
        if magic not in (b"070701", b"070702"):
            die(f"bad cpio header at offset {pos}")
        fields = [int(data[pos + 6 + i * 8:pos + 14 + i * 8], 16) for i in range(13)]
        mode, filesize, namesize = fields[1], fields[6], fields[11]
        name_start = pos + 110
        name = data[name_start:name_start + namesize - 1].decode(errors="replace")
        pos = (name_start + namesize + 3) & ~3
        payload = data[pos:pos + filesize]
        pos = (pos + filesize + 3) & ~3
        if name == "TRAILER!!!":
            return
        yield name, mode, payload


# --- device tree helpers ---------------------------------------------------

_MAKE_REF = re.compile(r"\$\((\w+)\)")


def read_make_vars(path, tree=None):
    """Small reader for 'VAR := value' / 'VAR += value' lines in a makefile.

    $(VAR) references to variables of the same file are expanded, and
    DEVICE_PATH/LOCAL_PATH become the tree directory, so prebuilt paths can be
    opened directly. Conditionals are not evaluated.
    """
    tree = tree or os.path.dirname(os.path.abspath(path))
    raw = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^\s*([A-Za-z0-9_]+)\s*(\+=|:=|=)\s*(.*?)\s*$", line)
            if not m or line.lstrip().startswith("#"):
                continue
            name, op, value = m.groups()
            raw[name] = (raw.get(name, "") + " " + value).strip() if op == "+=" else value

    def expand(value, depth=0):
        def repl(m):
            name = m.group(1)
            if name in ("DEVICE_PATH", "LOCAL_PATH"):
                return tree
            if name in raw and depth < 8:
                return expand(raw[name], depth + 1)
            return m.group(0)
        return _MAKE_REF.sub(repl, value)

    return {k: expand(v) for k, v in raw.items()}


def mkbootimg_args(mv):
    """BOARD_MKBOOTIMG_ARGS as a dict: {'header_version': '2', 'board': 'SRP...', ...}."""
    out = {}
    tokens = mv.get("BOARD_MKBOOTIMG_ARGS", "").split()
    for i, tok in enumerate(tokens):
        if tok.startswith("--") and i + 1 < len(tokens):
            out[tok[2:]] = tokens[i + 1].strip('"')
    return out


def prebuilt_paths(mv):
    """(kernel, dtb, dtbo) paths from BoardConfig.mk."""
    return (mv.get("TARGET_PREBUILT_KERNEL", ""), mv.get("TARGET_PREBUILT_DTB", ""),
            mv.get("BOARD_PREBUILT_DTBOIMAGE", ""))


def tw_vendor_modules(boardconfig):
    raw = read_make_vars(boardconfig).get("TW_LOAD_VENDOR_MODULES", "")
    return raw.strip('"').split()
