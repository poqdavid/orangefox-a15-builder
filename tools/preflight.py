#!/usr/bin/env python3
"""Static checks of the a15 OrangeFox device tree against the OrangeFox
maintainer guidelines and the tree's own consistency rules.

usage: preflight.py <device tree dir> [--device a15] [--device-info a15-device-info.txt]
Exit status is non-zero if any check fails.
"""

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import foxtools  # noqa: E402

LOGICAL = ("system", "system_ext", "vendor", "product", "odm", "vendor_dlkm", "odm_dlkm", "system_dlkm")

failures = []
warnings = []


def fail(msg):
    failures.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg):
    warnings.append(msg)
    print(f"  WARN  {msg}")


def ok(msg):
    print(f"  ok    {msg}")


def fox_env(tree, device):
    """Source vendorsetup.sh in a clean bash and return the exported variables."""
    script = os.path.join(tree, "vendorsetup.sh")
    if not os.path.isfile(script):
        fail("vendorsetup.sh is missing")
        return {}
    cmd = f'export FOX_BUILD_DEVICE="{device}"; . "{script}" >/dev/null; env -0'
    out = subprocess.run(["bash", "--noprofile", "--norc", "-c", cmd], env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                         stdout=subprocess.PIPE, check=True).stdout
    env = {}
    for item in out.split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode()] = v.decode(errors="replace")
    return env


def check_vendorsetup(tree, device):
    print("vendorsetup.sh")
    env = fox_env(tree, device)
    if not env:
        return
    required = {
        "OF_SCREEN_H": "screen height for a non-1920x1080 panel",
        "OF_STATUS_H": "status bar height for the cutout",
        "OF_STATUS_INDENT_LEFT": "rounded corners",
        "OF_STATUS_INDENT_RIGHT": "rounded corners",
        "FOX_MAINTAINER_PATCH_VERSION": "maintainer patch version",
        "OF_MAINTAINER": "maintainer name",
    }
    for var, why in required.items():
        if env.get(var):
            ok(f"{var}={env[var]}")
        else:
            fail(f"{var} is not exported ({why})")
    if "FOX_VERSION" in env:
        fail("FOX_VERSION is obsolete and stops the build; use FOX_MAINTAINER_PATCH_VERSION")
    if env.get("FOX_USE_SAMSUNG_SPECIAL") == "1" and os.path.isfile(os.path.join(tree, "bootimg.mk")):
        with open(os.path.join(tree, "bootimg.mk"), encoding="utf-8") as f:
            if "SEANDROIDENFORCE" in f.read():
                fail("FOX_USE_SAMSUNG_SPECIAL=1 but bootimg.mk already appends SEANDROIDENFORCE")
    if env.get("FOX_BUILD_TYPE"):
        warn(f"FOX_BUILD_TYPE={env['FOX_BUILD_TYPE']} is set in the tree; set it at build time instead")


def check_boardconfig(tree):
    print("BoardConfig.mk")
    path = os.path.join(tree, "BoardConfig.mk")
    text = open(path, encoding="utf-8").read()
    for var in ("TW_Y_OFFSET", "TW_H_OFFSET"):
        if re.search(rf"^\s*{var}\s*:?=", text, re.M):
            fail(f"{var} is set: shifting the UI to hide the cutout is not allowed, use OF_STATUS_H")
    if not re.search(r"^\s*TW_Y_OFFSET|^\s*TW_H_OFFSET", text, re.M):
        ok("no UI offsets")
    mv = foxtools.read_make_vars(path)
    for var in ("TARGET_PREBUILT_KERNEL", "TARGET_PREBUILT_DTB", "BOARD_PREBUILT_DTBOIMAGE"):
        if var not in mv:
            warn(f"{var} not set")
    return mv


def parse_fstab(path):
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line.split())
    return entries


def check_fstab(tree):
    print("recovery.fstab / twrp.flags")
    etc = os.path.join(tree, "recovery/root/system/etc")
    fstab = parse_fstab(os.path.join(etc, "recovery.fstab"))
    flags_path = os.path.join(etc, "twrp.flags")
    flags = parse_fstab(flags_path) if os.path.isfile(flags_path) else []

    v2_mounts = {}
    for e in fstab:
        if e[0].startswith("/") and not e[0].startswith("/dev/") and not e[0].startswith("/devices/"):
            v2_mounts.setdefault(e[0], e)  # v1 style line
        else:
            v2_mounts.setdefault(e[1], e)
    flag_map = {e[0]: " ".join(e[3:]) if len(e) > 3 else "" for e in flags}

    # Rule E: no backup/flash for individual logical partitions
    bad = []
    for mnt, e in v2_mounts.items():
        name = mnt.lstrip("/")
        if name in LOGICAL and re.search(r"backup=(1|y|true)|flashimg", flag_map.get(mnt, "")):
            bad.append(mnt)
    for mnt, fl in flag_map.items():
        base = mnt.lstrip("/")
        if base.endswith("_image") and base[:-6] in LOGICAL:
            bad.append(mnt)
    if bad:
        fail("logical partitions must not offer backup/flash on a dynamic device: " + ", ".join(sorted(set(bad))))
    else:
        ok("no backup/flash entries for logical partitions")

    # Rule A/B/C: storage mount points
    def has(mnt, pattern=None):
        fl = flag_map.get(mnt)
        if fl is None:
            return False
        return pattern is None or re.search(pattern, fl) is not None

    if "/sdcard1" in v2_mounts or "/sdcard1" in flag_map:
        ok("MicroSD at /sdcard1")
    else:
        fail("MicroSD must mount at /sdcard1")
    if ("/usb_otg" in v2_mounts or "/usb_otg" in flag_map) and has("/usb_otg", r'display="USB-Storage"'):
        ok('USB OTG at /usb_otg named "USB-Storage"')
    else:
        fail('USB OTG must mount at /usb_otg with display="USB-Storage"')
    if has("/storage", r"backup=(1|y|true)"):
        ok("internal storage at /storage with backup enabled")
    else:
        fail("internal storage must mount at /storage with backup enabled")
    storage = next((e for e in flags if e[0] == "/storage"), None)
    if storage and (len(storage) < 3 or storage[2] != "/data/media/0" or 'fsflags="bind"' not in flag_map["/storage"]):
        fail("/storage must bind-mount /data/media/0 (device /data/media/0, fsflags=\"bind\")")
    for bad_mp in ("/external_sd", "/usb-otg", "/usbotg"):
        if bad_mp in v2_mounts or bad_mp in flag_map:
            fail(f"{bad_mp} is used; OrangeFox expects /sdcard1 and /usb_otg")


def check_device_info(tree, report):
    """Every /dev/block/by-name device the tree uses must exist on the phone."""
    print("device report")
    text = open(report, encoding="utf-8", errors="replace").read()
    m = re.search(r"=== block devices.*?===\n(.*?)\n\n", text, re.S)
    if not m:
        warn("no block device list in the report")
        return
    have = {line.split()[0] for line in m.group(1).splitlines() if line.strip()}
    etc = os.path.join(tree, "recovery/root/system/etc")
    used = set()
    for name in ("recovery.fstab", "twrp.flags"):
        with open(os.path.join(etc, name), encoding="utf-8") as f:
            used |= set(re.findall(r"/dev/block/by-name/(\w+)", f.read()))
    missing = sorted(used - have)
    if missing:
        fail("partitions used by the tree but missing on the phone: " + ", ".join(missing))
    else:
        ok(f"all {len(used)} by-name partitions exist on the phone")
    mv = foxtools.read_make_vars(os.path.join(tree, "BoardConfig.mk"))
    for part, var in (("recovery", "BOARD_RECOVERYIMAGE_PARTITION_SIZE"), ("boot", "BOARD_BOOTIMAGE_PARTITION_SIZE"),
                      ("super", "BOARD_SUPER_PARTITION_SIZE")):
        size = re.search(rf"^{part} (\d+)$", m.group(1), re.M)
        if size and var in mv:
            if int(size.group(1)) == int(mv[var], 0):
                ok(f"{var} matches the phone ({size.group(1)})")
            else:
                fail(f"{var}={mv[var]} but the phone's {part} is {size.group(1)} bytes")


def check_modules(tree, mv):
    print("kernel modules")
    root = os.path.join(tree, "recovery/root")
    libmod = os.path.join(root, "lib/modules")
    vendmod = os.path.join(root, "vendor/lib/modules")
    load_list = os.path.join(libmod, "modules.load.recovery")
    if os.path.isfile(load_list):
        names = [l.strip() for l in open(load_list, encoding="utf-8") if l.strip()]
        dups = sorted({n for n in names if names.count(n) > 1})
        if dups:
            warn(f"modules.load.recovery lists {len(dups)} module(s) more than once")
        else:
            ok(f"modules.load.recovery: {len(names)} unique entries")
        missing = [n for n in names if not os.path.isfile(os.path.join(libmod, n))]
        if missing:
            # first-stage init aborts the boot if a listed module cannot be loaded
            fail("modules.load.recovery lists missing modules: " + ", ".join(missing[:10]))
        else:
            ok("every listed module is present")
    # Touch drivers: the recovery can only load them from the ramdisk when they
    # sit in /lib/modules with modules.dep entries and all dependencies, and
    # TW_LOAD_VENDOR_BOOT_MODULES is set. Otherwise it needs the ROM's vendor_dlkm.
    boot_modules = mv.get("TW_LOAD_VENDOR_BOOT_MODULES") == "true"
    for ko in foxtools.tw_vendor_modules(os.path.join(tree, "BoardConfig.mk")):
        why = foxtools.ramdisk_loadable(libmod, ko)
        if why is None and boot_modules:
            ok(f"{ko} loads from the ramdisk")
        elif why is None:
            warn(f"{ko} is in the ramdisk but TW_LOAD_VENDOR_BOOT_MODULES is not set")
        else:
            warn(f"{ko} cannot load from the ramdisk ({why}); touch needs the installed ROM's vendor_dlkm")

    kernel = mv.get("TARGET_PREBUILT_KERNEL", "").replace("$(DEVICE_PATH)", tree)
    if not os.path.isfile(kernel):
        warn("prebuilt kernel not found; skipping vermagic check")
        return
    release = foxtools.kernel_release(open(kernel, "rb").read())
    if not release:
        warn("could not read the kernel release from the prebuilt kernel")
        return
    ok(f"prebuilt kernel {release}")
    bad = foxtools.check_modules(release, [libmod, vendmod])
    if bad:
        for path, vm in bad[:10]:
            fail(f"{os.path.relpath(path, tree)}: vermagic '{vm}' does not match the kernel")
    else:
        ok("all modules match the prebuilt kernel")

    for var, label in (("TARGET_PREBUILT_DTB", "dtb"), ("BOARD_PREBUILT_DTBOIMAGE", "dtbo")):
        p = mv.get(var, "").replace("$(DEVICE_PATH)", tree)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                magic = f.read(4)
            if magic != foxtools.DT_TABLE_MAGIC:
                warn(f"{label} image does not start with the DT table magic")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tree")
    ap.add_argument("--device", default="a15")
    ap.add_argument("--device-info", help="report written by device-info.sh; checks partitions and sizes")
    args = ap.parse_args()
    tree = os.path.abspath(args.tree)
    if not os.path.isfile(os.path.join(tree, "BoardConfig.mk")):
        foxtools.die(f"{tree} does not look like a device tree")

    check_vendorsetup(tree, args.device)
    mv = check_boardconfig(tree)
    check_fstab(tree)
    check_modules(tree, mv)
    if args.device_info:
        check_device_info(tree, args.device_info)

    print()
    print(f"{len(failures)} failure(s), {len(warnings)} warning(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
