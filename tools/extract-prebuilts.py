#!/usr/bin/env python3
"""Refresh the a15 device tree prebuilts from a stock recovery image.

usage:
  extract-prebuilts.py --recovery <AP_*.tar.md5 | recovery.img.lz4 | recovery.img | partition dump>
                       --tree <device tree dir>
                       [--vendor-files <dir>] [--dry-run]

Replaces, all from the same stock image:
  the prebuilt kernel, DTB and DTBO named in BoardConfig.mk
  recovery/root/lib/modules/* (the stock recovery module set and lists)

--vendor-files is the directory written by pull-vendor-files.sh on a phone
running the SAME firmware: the modules on TW_LOAD_VENDOR_MODULES (the touch
drivers, soc_temp_lvts.ko for the CPU temperature and leds-sm5714-fled.ko
for the flashlight), every module they depend on, vendor_dlkm's modules.dep
and the FocalTech touch firmware.
With it, they and their missing dependencies are installed into the
ramdisk's /lib/modules with modules.dep entries, so the recovery can load
them without a ROM installed. The Zinitix touch firmware is never
installed (see the tree's zinitix_touch.sh). An optional module the phone does not have is
skipped. Without it, the tree keeps the vendor modules it has.
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


def compare_layout(hdr, mv):
    """Print the stock header next to the BoardConfig values."""
    base = int(mv["BOARD_KERNEL_BASE"], 0)
    args_mk = foxtools.mkbootimg_args(mv)
    rows = [
        ("--header_version", hdr["header_version"], int(args_mk["header_version"], 0)),
        ("--pagesize", hdr["page_size"], int(args_mk["pagesize"], 0)),
        ("--board", hdr["board"], args_mk["board"]),
        ("--kernel_offset", hdr["kernel_addr"] - base, int(args_mk["kernel_offset"], 0)),
        ("--ramdisk_offset", hdr["ramdisk_addr"] - base, int(args_mk["ramdisk_offset"], 0)),
        ("--tags_offset", hdr["tags_addr"] - base, int(args_mk["tags_offset"], 0)),
        ("--dtb_offset", (hdr["dtb_addr"] or 0) - base, int(args_mk["dtb_offset"], 0)),
    ]
    diffs = 0
    print("\nlayout (stock image vs BOARD_MKBOOTIMG_ARGS)")
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
    ap.add_argument("--vendor-files")
    ap.add_argument("--dry-run", action="store_true", help="only report, do not write")
    args = ap.parse_args()

    tree = os.path.abspath(args.tree)
    boardconfig = os.path.join(tree, "BoardConfig.mk")
    if not os.path.isfile(boardconfig):
        foxtools.die(f"{tree} has no BoardConfig.mk")
    mv = foxtools.read_make_vars(boardconfig)

    hdr, sec = foxtools.parse_bootimg(load_recovery(args.recovery))
    release = foxtools.kernel_release(sec["kernel"])
    if not release:
        foxtools.die("could not find the kernel release string in the image")
    print(f"stock recovery kernel: {release}")
    if not sec["dtb"]:
        foxtools.die("the image has no DTB section")
    if not sec["recovery_dtbo"]:
        print("warning: the image has no recovery_dtbo; prebuilt/dtbo.img is kept")

    kind, ramdisk = foxtools.decompress(sec["ramdisk"])
    modules = {}
    for name, _mode, payload in foxtools.cpio_entries(ramdisk):
        if name.startswith("twres/") or name in ("system/bin/twrp", "sbin/twrp", "FFiles"):
            foxtools.die("this is a custom recovery (TWRP/OrangeFox), not the stock one; use recovery.img from the AP file")
        if name.startswith("lib/modules/") and name.count("/") == 2 and not name.endswith("/"):
            modules[os.path.basename(name)] = payload
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

    diffs = compare_layout(hdr, mv)

    wanted = foxtools.tw_vendor_modules(boardconfig)
    root = os.path.join(tree, "recovery/root")
    libmod = os.path.join(root, "lib/modules")
    vend_dir = os.path.join(root, "vendor/lib/modules")
    fw_dir = os.path.join(root, "vendor/firmware")
    to_copy, dep_lines = [], []
    kept = {}
    if args.vendor_files:
        stock_deps = foxtools.parse_modules_dep(modules["modules.dep"].decode(errors="replace"))
        to_copy, dep_lines = plan_vendor_modules(args.vendor_files, wanted, set(kos), stock_deps, release)
        print(f"\nvendor modules added to /lib/modules: {', '.join(to_copy) or 'none'}")
    else:
        # keep the tree's vendor modules, wherever they are, if they still match
        stale = []
        for d in (libmod, vend_dir):
            for m in wanted:
                p = os.path.join(d, m)
                if os.path.isfile(p):
                    if (foxtools.module_vermagic(p) or "").split(" ", 1)[0] != release:
                        stale.append(p)
                    elif d == libmod:
                        with open(p, "rb") as f:
                            kept[m] = f.read()
        if stale:
            print("\nwarning: the vendor modules in the tree were built for another kernel:")
            for p in stale:
                print(f"  {os.path.relpath(p, tree)}: {foxtools.module_vermagic(p)}")
            print("  pass --vendor-files with the output of pull-vendor-files.sh from the same firmware")
            if not args.dry_run:
                foxtools.die("refusing to write a tree with mismatched modules")
        if kept:
            print("\nwarning: vendor modules in lib/modules are kept, but their modules.dep entries and\n"
                  "         extra dependencies are not carried over without --vendor-files")

    if args.dry_run:
        print("\ndry run: nothing written")
        return

    kernel_path, dtb_path, dtbo_path = foxtools.prebuilt_paths(mv)
    for path in (kernel_path, dtb_path, dtbo_path):
        if not path.startswith(tree):
            foxtools.die("BoardConfig.mk does not name the prebuilt kernel, DTB and DTBO inside the tree")
    os.makedirs(os.path.dirname(kernel_path), exist_ok=True)
    with open(kernel_path, "wb") as f:
        f.write(sec["kernel"])
    with open(dtb_path, "wb") as f:
        f.write(sec["dtb"])
    if sec["recovery_dtbo"]:
        with open(dtbo_path, "wb") as f:
            f.write(sec["recovery_dtbo"])

    for old in glob.glob(os.path.join(libmod, "*")):
        if os.path.isfile(old):
            os.remove(old)
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
        # the copies under vendor/lib/modules are not used any more
        for m in wanted:
            old = os.path.join(vend_dir, m)
            if os.path.isfile(old):
                os.remove(old)
        if os.path.isdir(vend_dir) and not os.listdir(vend_dir):
            os.rmdir(vend_dir)
        for fw in sorted(glob.glob(os.path.join(args.vendor_files, "*.bin"))):
            # the Zinitix driver would flash it at every boot and leave touch
            # dead; older pulls still have it
            if os.path.basename(fw).startswith("zt7650m"):
                print(f"skipped {os.path.basename(fw)}: the recovery must not have the Zinitix touch firmware")
                continue
            os.makedirs(fw_dir, exist_ok=True)
            shutil.copyfile(fw, os.path.join(fw_dir, os.path.basename(fw)))

    print(f"\nwrote kernel {release}, dtb, dtbo and {len(kos) + len(to_copy)} modules to {tree}")
    if diffs:
        print("check the DIFF lines above and update BoardConfig.mk before building")
    print("update the firmware/kernel version in README.md, then run preflight.py")


if __name__ == "__main__":
    main()
