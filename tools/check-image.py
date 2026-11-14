#!/usr/bin/env python3
"""Check a built a15 recovery image before it is flashed or released.

usage: check-image.py <OrangeFox-*.img> <device tree dir>

Verifies that the image matches the stock recovery layout configured in
BoardConfig.mk, carries SEANDROIDENFORCE and an AVB hash footer, fits the
recovery partition, and that the kernel and every module in the ramdisk
belong together.
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foxtools  # noqa: E402

failures = []


def check(cond, good, bad):
    if cond:
        print(f"  ok    {good}")
    else:
        print(f"  FAIL  {bad}")
        failures.append(bad)


def num(v):
    return int(v, 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image")
    ap.add_argument("tree")
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    mv = foxtools.read_make_vars(os.path.join(args.tree, "BoardConfig.mk"))
    hdr, sec = foxtools.parse_bootimg(data)

    print("header")
    args_mk = foxtools.mkbootimg_args(mv)
    base = num(mv["BOARD_KERNEL_BASE"])
    want_version = num(args_mk["header_version"])
    want_page = num(args_mk["pagesize"])
    check(hdr["header_version"] == want_version,
          f"header v{hdr['header_version']}", f"header v{hdr['header_version']}, expected v{want_version}")
    check(hdr["page_size"] == want_page, f"page size {hdr['page_size']}",
          f"page size {hdr['page_size']}, expected {want_page}")
    check(hdr["board"] == args_mk["board"], f"board {hdr['board']}",
          f"board '{hdr['board']}', expected {args_mk['board']}")
    for field, arg in (("kernel_addr", "kernel_offset"), ("ramdisk_addr", "ramdisk_offset"),
                       ("tags_addr", "tags_offset"), ("dtb_addr", "dtb_offset")):
        want = (base + num(args_mk[arg])) & 0xFFFFFFFFFFFFFFFF
        check(hdr[field] == want, f"{field} 0x{hdr[field]:08x}", f"{field} 0x{hdr[field]:08x}, expected 0x{want:08x}")
    for token in mv.get("BOARD_KERNEL_CMDLINE", "").split():
        check(token in hdr["cmdline"].split(), f"cmdline has {token}", f"cmdline lacks {token}")

    print("payload")
    check(len(sec["dtb"]) > 0 and sec["dtb"][:4] == foxtools.DT_TABLE_MAGIC, f"dtb ({len(sec['dtb'])} bytes)", "dtb missing")
    check(len(sec["recovery_dtbo"]) > 0, f"recovery_dtbo ({len(sec['recovery_dtbo'])} bytes)", "recovery_dtbo missing")

    end = hdr["end_of_image"]
    check(data[end:end + 16] == b"SEANDROIDENFORCE", "SEANDROIDENFORCE after the image",
          "SEANDROIDENFORCE missing right after the image")

    footer = data[-64:]
    part_size = num(mv["BOARD_RECOVERYIMAGE_PARTITION_SIZE"])
    check(len(data) <= part_size, f"size {len(data)} fits recovery ({part_size})",
          f"size {len(data)} exceeds recovery ({part_size})")
    if footer[:4] == b"AVBf":
        original_size = struct.unpack(">Q", footer[12:20])[0]
        check(original_size == end + 16, f"AVB hash footer (image size {original_size})",
              f"AVB footer covers {original_size} bytes, expected {end + 16}")
    else:
        check(False, "", "AVB hash footer missing")

    print("kernel and modules")
    release = foxtools.kernel_release(sec["kernel"])
    check(release is not None, f"kernel {release}", "kernel release string not found")
    kind, ramdisk = foxtools.decompress(sec["ramdisk"])
    print(f"  info  ramdisk is {kind}, {len(ramdisk)} bytes uncompressed")
    names = set()
    bad = []
    for name, _mode, payload in foxtools.cpio_entries(ramdisk):
        names.add(name)
        if name.endswith(".ko"):
            vm = foxtools._VERMAGIC_RE.search(payload)
            vm = vm.group(1).decode(errors="replace") if vm else ""
            if release and vm.split(" ", 1)[0] != release:
                bad.append(name)
    check(not bad, "all ramdisk modules match the kernel",
          f"{len(bad)} module(s) do not match the kernel, e.g. {', '.join(bad[:5])}")
    dep_text = ""
    for name, _mode, payload in foxtools.cpio_entries(ramdisk):
        if name == "lib/modules/modules.dep":
            dep_text = payload.decode(errors="replace")
    deps = foxtools.parse_modules_dep(dep_text)
    for ko in foxtools.tw_vendor_modules(os.path.join(args.tree, "BoardConfig.mk")):
        missing = [d for d in deps.get(ko, []) if f"lib/modules/{d}" not in names]
        if f"lib/modules/{ko}" in names and ko in deps and not missing:
            print(f"  ok    {ko} loads from the ramdisk")
        else:
            print(f"  note  {ko} is not loadable from the ramdisk; touch needs the ROM's vendor_dlkm")
    for f in ("system/etc/recovery.fstab", "system/etc/twrp.flags", "lib/modules/modules.load.recovery"):
        check(f in names, f"{f} in ramdisk", f"{f} missing from the ramdisk")

    print()
    print(f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
