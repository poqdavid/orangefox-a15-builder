#!/usr/bin/env python3
"""Refresh or add a firmware set of the a15 device tree from a stock recovery image.

usage:
  extract-prebuilts.py --recovery <AP_*.tar.md5 | recovery.img.lz4 | recovery.img | partition dump>
                       --tree <device tree dir> --firmware <set>
                       [--vendor-files <dir>] [--dry-run]

The tree keeps one set of prebuilts per One UI generation in
firmware/<set>/ (oneui7, oneui6, ...), because each only works with a
bootloader of its own generation. This replaces, all from the same stock
image, in firmware/<set>/:
  prebuilt/Image.gz, dtb and recovery_dtbo
  recovery/root/lib/modules/* (the stock recovery module set and lists)
  firmware.mk (the firmware version, board name and OS letter)
A set that does not exist yet is created. The image must be of the OS
generation (the 4th character from the end of the firmware version, B in
A155MUBS6BYE1) the set is for.

--vendor-files is the directory written by pull-vendor-files.sh on a phone
running the SAME firmware: the modules on TW_LOAD_VENDOR_MODULES (the touch
drivers and soc_temp_lvts.ko for the CPU temperature), every module they
depend on, vendor_dlkm's modules.dep and the touch firmware. With it, they
and their missing dependencies are installed into the ramdisk's
/lib/modules with modules.dep entries, so the recovery can load them
without a ROM installed, and the touch firmware into the set's
vendor/firmware. An optional module the phone does not have is skipped.
Without it, the set keeps the vendor modules it has.
"""

import argparse
import glob
import os
import shutil
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foxtools  # noqa: E402


def load_recovery(path):
    name = os.path.basename(path)
    if name.endswith((".tar", ".tar.md5")):
        with tarfile.open(path) as tar:
            for member in tar.getmembers():
                base = os.path.basename(member.name)
                if base in ("recovery.img", "recovery.img.lz4"):
                    print(f"reading {member.name} from {name}")
                    data = tar.extractfile(member).read()
                    return foxtools.decompress(data)[1] if base.endswith(".lz4") else data
        foxtools.die(f"no recovery.img(.lz4) in {name}; pass the AP tar")
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] == foxtools.BOOT_MAGIC:
        return data
    kind, payload = foxtools.decompress(data)
    if payload[:8] != foxtools.BOOT_MAGIC:
        foxtools.die(f"{name} is not a recovery image (after {kind} decompression)")
    return payload


FIRMWARE_MK = """\
#
# Copyright (C) 2026 The OrangeFox Recovery Project
# SPDX-License-Identifier: GPL-3.0-or-later
#

# Firmware set {name}, written by extract-prebuilts.py from the stock
# firmware {build}: the kernel, DTB, DTBO and modules of its
# recovery image, and the vendor_dlkm modules and touch firmware the
# recovery adds to them. Kernel {release}.
# Built with A15_FIRMWARE={name} (see ../firmware.mk).
A15_FIRMWARE_BUILD := {build}
# the board name in the header of the stock recovery image
A15_BOARD_NAME := {board}
# The OS letters of the bootloaders this set is for: the 4th character from
# the end of ro.boot.bootloader ({letter} in {build}). runatboot.sh warns
# when the phone's is another one.
A15_FIRMWARE_OS := {letters}
"""


def firmware_mk(name, build, release, board, letters):
    return FIRMWARE_MK.format(name=name, build=build, release=release, board=board,
                              letter=foxtools.os_letter(build), letters=letters)


def stock_firmware(ramdisk_props):
    """The firmware version of a stock recovery, from its prop.default."""
    for key in ("ro.build.version.incremental", "ro.vendor.build.version.incremental"):
        value = ramdisk_props.get(key, "")
        if foxtools.os_letter(value):
            return value
    return None


def compare_layout(hdr, mv, set_board):
    """Print the stock header next to the BoardConfig values. The board
    name is the set's own (written to its firmware.mk); the rest is shared
    by all sets."""
    base = int(mv["BOARD_KERNEL_BASE"], 0)
    args_mk = foxtools.mkbootimg_args(mv)
    print("\nlayout (stock image vs BOARD_MKBOOTIMG_ARGS)")
    if set_board is None:
        print(f"  new   --board            stock={hdr['board']}")
    elif set_board != hdr["board"]:
        print(f"  new   --board            stock={hdr['board']:12} set={set_board}")
    else:
        print(f"  ok    --board            stock={hdr['board']:12} set={set_board}")
    rows = [
        ("--header_version", hdr["header_version"], int(args_mk["header_version"], 0)),
        ("--pagesize", hdr["page_size"], int(args_mk["pagesize"], 0)),
        ("--kernel_offset", hdr["kernel_addr"] - base, int(args_mk["kernel_offset"], 0)),
        ("--ramdisk_offset", hdr["ramdisk_addr"] - base, int(args_mk["ramdisk_offset"], 0)),
        ("--tags_offset", hdr["tags_addr"] - base, int(args_mk["tags_offset"], 0)),
        ("--dtb_offset", (hdr["dtb_addr"] or 0) - base, int(args_mk["dtb_offset"], 0)),
    ]
    diffs = 0
    for var, stock, tree in rows:
        same = stock == tree
        diffs += not same
        fmt = (lambda v: f"0x{v:08x}") if isinstance(stock, int) and var.endswith("offset") else str
        print(f"  {'ok  ' if same else 'DIFF'}  {var:18} stock={fmt(stock):12} tree={fmt(tree)}")
    stock_cmd = set(hdr["cmdline"].split())
    tree_cmd = set(mv.get("BOARD_KERNEL_CMDLINE", "").split())
    if stock_cmd - tree_cmd:
        print(f"  note  stock cmdline has {' '.join(sorted(stock_cmd - tree_cmd))}")
    return diffs


def plan_vendor_modules(vendor_dir, wanted, stock_names, stock_deps, release):
    """Work out which vendor modules to add to /lib/modules and their dep lines.

    vendor_dlkm's modules.dep only knows the modules inside vendor_dlkm, so the
    dependencies on stock recovery modules are taken from each module's own
    depends= field. Returns (files_to_copy, dep_lines). Stops if a module or
    dependency is missing or was built for another kernel.
    """
    dep_path = os.path.join(vendor_dir, "modules.dep")
    if not os.path.isfile(dep_path):
        foxtools.die(f"{vendor_dir} has no modules.dep; run pull-vendor-files.sh again")
    with open(dep_path, encoding="utf-8") as f:
        vdeps = foxtools.parse_modules_dep(f.read())
    by_name = {n[:-3].replace("-", "_"): n for n in list(stock_names) + list(vdeps)}

    to_copy = []

    def add(name, needed_by):
        if name in stock_names or name in to_copy:
            return
        src = os.path.join(vendor_dir, name)
        if not os.path.isfile(src):
            foxtools.die(f"{name} (needed by {needed_by}) is missing from {vendor_dir}")
        vm = (foxtools.module_vermagic(src) or "").split(" ", 1)[0]
        if vm != release:
            foxtools.die(f"{name} was built for {vm}, the recovery kernel is {release}\n"
                         "       pull the files from a phone running the same firmware as the recovery image")
        to_copy.append(name)

    def chain(name, stack=()):
        """name followed by everything that has to be loaded before it."""
        if name in stack:
            return []
        if name in stock_names:
            return [name] + stock_deps.get(name, [])
        add(name, stack[-1] if stack else name)
        direct = list(vdeps.get(name, []))
        for dep in foxtools.module_depends(os.path.join(vendor_dir, name)):
            target = by_name.get(dep.replace("-", "_"))
            if target is None:
                foxtools.die(f"{name} depends on {dep}, which is neither in the stock recovery nor in vendor_dlkm")
            if target not in direct:
                direct.append(target)
        out = [name]
        for d in direct:
            out += chain(d, stack + (name,))
        return out

    dep_lines = []
    for mod in wanted:
        if mod not in vdeps:
            if mod in foxtools.OPTIONAL_VENDOR_MODULES:
                print(f"warning: {mod} is not in the phone's modules.dep; no "
                      f"{foxtools.vendor_module_role(mod)}")
                continue
            foxtools.die(f"{mod} is not in the phone's modules.dep")
        chain(mod)
    for name in to_copy:
        deps = chain(name)[1:]
        # libmodprobe loads the list back to front: keep each module's LAST
        # position so that its own dependencies still come after it
        ordered = [d for i, d in enumerate(deps) if d not in deps[i + 1:] and d != name]
        dep_lines.append(f"/lib/modules/{name}:" + "".join(f" /lib/modules/{d}" for d in ordered))
    return to_copy, dep_lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recovery", required=True)
    ap.add_argument("--tree", required=True)
    ap.add_argument("--firmware", required=True, help="the firmware set to refresh or add (oneui7, oneui6, ...)")
    ap.add_argument("--vendor-files")
    ap.add_argument("--dry-run", action="store_true", help="only report, do not write")
    args = ap.parse_args()

    tree = os.path.abspath(args.tree)
    boardconfig = os.path.join(tree, "BoardConfig.mk")
    if not os.path.isfile(boardconfig):
        foxtools.die(f"{tree} has no BoardConfig.mk")
    if not os.path.isfile(os.path.join(tree, foxtools.FIRMWARE_DIR, foxtools.FIRMWARE_MK)):
        foxtools.die(f"{tree} has no {foxtools.FIRMWARE_DIR}/{foxtools.FIRMWARE_MK}; "
                     "update it to a tree with firmware sets")
    name = args.firmware
    if not foxtools.FIRMWARE_NAME_RE.match(name):
        foxtools.die(f"--firmware {name}: use lower case letters, digits and _ (like oneui6)")
    sets = foxtools.firmware_sets(tree)
    is_new = name not in sets
    old = foxtools.firmware_vars(tree, name)
    mv = foxtools.board_vars(tree, name)

    hdr, sec = foxtools.parse_bootimg(load_recovery(args.recovery))
    release = foxtools.kernel_release(sec["kernel"])
    if not release:
        foxtools.die("could not find the kernel release string in the image")
    if not sec["dtb"]:
        foxtools.die("the image has no DTB section")
    if not sec["recovery_dtbo"]:
        if is_new:
            foxtools.die("the image has no recovery_dtbo")
        print("warning: the image has no recovery_dtbo; the set's recovery_dtbo is kept")

    kind, ramdisk = foxtools.decompress(sec["ramdisk"])
    modules = {}
    props = {}
    for entry, _mode, payload in foxtools.cpio_entries(ramdisk):
        if entry.startswith("twres/") or entry in ("system/bin/twrp", "sbin/twrp", "FFiles"):
            foxtools.die("this is a custom recovery (TWRP/OrangeFox), not the stock one; use recovery.img from the AP file")
        if entry.startswith("lib/modules/") and entry.count("/") == 2 and not entry.endswith("/"):
            modules[os.path.basename(entry)] = payload
        if entry == "prop.default":
            for line in payload.decode(errors="replace").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()

    build = stock_firmware(props)
    if not build:
        foxtools.die("the firmware version (ro.build.version.incremental) is not in the image's prop.default")
    letter = foxtools.os_letter(build)
    print(f"stock recovery of {build} (OS letter {letter}), kernel {release}")
    letters = old.get("A15_FIRMWARE_OS", "")
    if is_new:
        letters = letter
        print(f"new firmware set {name}; the others are: {', '.join(sets)}")
    elif letter not in letters:
        foxtools.die(f"{build} is an OS {letter} firmware, but the set {name} is for {letters} "
                     f"({old.get('A15_FIRMWARE_BUILD')}); a firmware of another One UI generation "
                     "needs a set of its own (pick a new --firmware name)")

    kos = sorted(n for n in modules if n.endswith(".ko"))
    print(f"ramdisk ({kind}): {len(kos)} modules in lib/modules")
    if not kos or "modules.load.recovery" not in modules or "modules.dep" not in modules:
        foxtools.die("lib/modules with modules.load.recovery and modules.dep not found in the stock ramdisk")

    bad = []
    for n in kos:
        vm = foxtools._VERMAGIC_RE.search(modules[n])
        vm = vm.group(1).decode(errors="replace") if vm else ""
        if vm.split(" ", 1)[0] != release:
            bad.append(n)
    if bad:
        foxtools.die(f"{len(bad)} stock modules do not match the stock kernel (e.g. {bad[0]}); wrong image?")

    diffs = compare_layout(hdr, mv, old.get("A15_BOARD_NAME"))

    wanted = foxtools.tw_vendor_modules(boardconfig)
    root = foxtools.firmware_root(tree, name)
    libmod = os.path.join(root, "lib/modules")
    fw_dir = os.path.join(root, "vendor/firmware")
    to_copy, dep_lines = [], []
    kept = {}
    if args.vendor_files:
        stock_deps = foxtools.parse_modules_dep(modules["modules.dep"].decode(errors="replace"))
        to_copy, dep_lines = plan_vendor_modules(args.vendor_files, wanted, set(kos), stock_deps, release)
        print(f"\nvendor modules added to /lib/modules: {', '.join(to_copy) or 'none'}")
    else:
        # keep the set's vendor modules if they still match
        stale = []
        for m in wanted:
            p = os.path.join(libmod, m)
            if os.path.isfile(p):
                if (foxtools.module_vermagic(p) or "").split(" ", 1)[0] != release:
                    stale.append(p)
                else:
                    with open(p, "rb") as f:
                        kept[m] = f.read()
        if stale:
            print(f"\nwarning: the vendor modules in the set {name} were built for another kernel:")
            for p in stale:
                print(f"  {os.path.relpath(p, tree)}: {foxtools.module_vermagic(p)}")
            print("  pass --vendor-files with the output of pull-vendor-files.sh from the same firmware")
            if not args.dry_run:
                foxtools.die("refusing to write a set with mismatched modules")
        if kept:
            print("\nwarning: vendor modules in lib/modules are kept, but their modules.dep entries and\n"
                  "         extra dependencies are not carried over without --vendor-files")
        elif is_new:
            print("\nwarning: without --vendor-files the new set has no touch drivers, and touch only\n"
                  "         works with the installed ROM's vendor_dlkm")

    if args.dry_run:
        print("\ndry run: nothing written")
        return

    kernel_path, dtb_path, dtbo_path = foxtools.prebuilt_paths(mv)
    set_dir = os.path.join(tree, foxtools.FIRMWARE_DIR, name)
    for path in (kernel_path, dtb_path, dtbo_path):
        if not path.startswith(set_dir + os.sep):
            foxtools.die("BoardConfig.mk does not take the prebuilt kernel, DTB and DTBO from $(A15_FIRMWARE_DIR)")
    os.makedirs(os.path.dirname(kernel_path), exist_ok=True)
    with open(kernel_path, "wb") as f:
        f.write(sec["kernel"])
    with open(dtb_path, "wb") as f:
        f.write(sec["dtb"])
    if sec["recovery_dtbo"]:
        with open(dtbo_path, "wb") as f:
            f.write(sec["recovery_dtbo"])

    for old_file in glob.glob(os.path.join(libmod, "*")):
        if os.path.isfile(old_file):
            os.remove(old_file)
    os.makedirs(libmod, exist_ok=True)
    for n, payload in modules.items():
        if n == "modules.load.recovery":
            # the stock list repeats most modules; init skips loaded ones, so
            # keeping the first occurrence of each gives the same load sequence
            lines = payload.decode(errors="replace").split()
            payload = ("\n".join(dict.fromkeys(lines)) + "\n").encode()
        with open(os.path.join(libmod, n), "wb") as f:
            f.write(payload)
    for n, payload in kept.items():
        with open(os.path.join(libmod, n), "wb") as f:
            f.write(payload)

    if args.vendor_files:
        for n in to_copy:
            shutil.copyfile(os.path.join(args.vendor_files, n), os.path.join(libmod, n))
        with open(os.path.join(libmod, "modules.dep"), "a", encoding="utf-8") as f:
            f.write("\n".join(dep_lines) + "\n")
        for fw in sorted(glob.glob(os.path.join(args.vendor_files, "*.bin"))):
            os.makedirs(fw_dir, exist_ok=True)
            shutil.copyfile(fw, os.path.join(fw_dir, os.path.basename(fw)))

    with open(os.path.join(set_dir, foxtools.FIRMWARE_MK), "w", encoding="utf-8") as f:
        f.write(firmware_mk(name, build, release, hdr["board"], letters))

    print(f"\nwrote kernel {release}, dtb, dtbo, {len(kos) + len(to_copy)} modules and "
          f"{foxtools.FIRMWARE_MK} to {os.path.relpath(set_dir, tree)}")
    if diffs:
        print("check the DIFF lines above: they are shared by every set, so update BoardConfig.mk\n"
              "only if the other sets' stock images have the same values")
    print(f"update the firmware sets in README.md, then run preflight.py --firmware {name}")

if __name__ == "__main__":
    main()
