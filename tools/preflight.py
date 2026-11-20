#!/usr/bin/env python3
"""Static checks of the a15 OrangeFox device tree against the OrangeFox
maintainer guidelines and the tree's own consistency rules.

usage: preflight.py <device tree dir> [--device a15] [--device-info a15-device-info.txt]
Exit status is non-zero if any check fails.
"""

import argparse
import hashlib
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


def fox_mk_vars(tree, device):
    """OF_ variables declared in fox_<device>.mk (plain make assignments)."""
    path = os.path.join(tree, f"fox_{device}.mk")
    if not os.path.isfile(path):
        return {}
    return {k: v for k, v in foxtools.read_make_vars(path, tree).items() if k.startswith("OF_")}


def check_layout(tree, device):
    """The file layout of the official OrangeFox Samsung trees (a33x, a23xq)."""
    print("layout")
    expected = [
        "Android.mk", "AndroidProducts.mk", "BoardConfig.mk", "README.md", "device.mk",
        f"fox_{device}.mk", f"twrp_{device}.mk", "vendorsetup.sh", "system.prop", "mkboot/bootimg.mk",
        "recovery/root/system/etc/recovery.fstab", "recovery/root/system/etc/twrp.flags",
        "recovery/root/system/bin/multidisabler", "recovery/root/system/bin/postrecoveryboot.sh",
        "recovery/root/system/bin/runatboot.sh",
    ]
    missing = [f for f in expected if not os.path.isfile(os.path.join(tree, f))]
    if missing:
        fail("missing files: " + ", ".join(missing))
    else:
        ok(f"all {len(expected)} expected files are present")
    stray = ["bootimg.mk", "prebuilt/kernel", "prebuilt/dtb.img", "prebuilt/dtbo.img",
             "recovery/root/dsms.rc", "recovery/root/dsms_common.rc",
             "recovery/root/vendor/etc/init.insmod.mt6789.cfg"]
    left = [f for f in stray if os.path.exists(os.path.join(tree, f))]
    if left:
        fail("leftover files that should have been removed or renamed: " + ", ".join(left))
    else:
        ok("no leftover files from the old layout")
    bindir = os.path.join(tree, "recovery/root/system/bin")
    if os.path.isdir(bindir):
        noexec = [f for f in sorted(os.listdir(bindir)) if not os.access(os.path.join(bindir, f), os.X_OK)]
        if noexec:
            fail("not executable: " + ", ".join(noexec))
        else:
            ok("the scripts in system/bin are executable")


def check_vendorsetup(tree, device):
    print(f"vendorsetup.sh / fox_{device}.mk")
    env = fox_env(tree, device)
    if not env:
        return
    of_vars = fox_mk_vars(tree, device)
    both = sorted(k for k in of_vars if k in env)
    if both:
        warn("set in both vendorsetup.sh and the fox makefile: " + ", ".join(both))
    merged = dict(of_vars)
    merged.update({k: v for k, v in env.items() if k.startswith(("OF_", "FOX_"))})
    required = {
        "OF_SCREEN_H": "screen height for a non-1920x1080 panel",
        "OF_STATUS_H": "status bar height for the cutout",
        "OF_STATUS_INDENT_LEFT": "rounded corners",
        "OF_STATUS_INDENT_RIGHT": "rounded corners",
        "FOX_MAINTAINER_PATCH_VERSION": "maintainer patch version",
        "OF_MAINTAINER": "maintainer name",
    }
    for var, why in required.items():
        if merged.get(var):
            ok(f"{var}={merged[var]}")
        else:
            fail(f"{var} is not set ({why})")
    fox_in_mk = sorted(k for k in foxtools.read_make_vars(os.path.join(tree, f"fox_{device}.mk"), tree)
                       if k.startswith("FOX_")) if of_vars else []
    if fox_in_mk:
        fail("FOX_ variables must be exported from vendorsetup.sh, not set in a makefile: " + ", ".join(fox_in_mk))
    if "FOX_VERSION" in env:
        fail("FOX_VERSION is obsolete and stops the build; use FOX_MAINTAINER_PATCH_VERSION")
    bootimg = os.path.join(tree, "mkboot/bootimg.mk")
    if env.get("FOX_USE_SAMSUNG_SPECIAL") == "1" and os.path.isfile(bootimg):
        with open(bootimg, encoding="utf-8") as f:
            if "SEANDROIDENFORCE" in f.read():
                fail("FOX_USE_SAMSUNG_SPECIAL=1 but mkboot/bootimg.mk already appends SEANDROIDENFORCE")
    if env.get("FOX_BUILD_TYPE"):
        warn(f"FOX_BUILD_TYPE={env['FOX_BUILD_TYPE']} is set in the tree; set it at build time instead")
    for var in ("FOX_SETTINGS_ROOT_DIRECTORY", "FOX_MISCELLANEOUS_ROOT_DIRECTORY"):
        root = env.get(var)
        if root:
            mount = "/" + root.strip("/").split("/")[0]
            etc = os.path.join(tree, "recovery/root/system/etc")
            text = "".join(open(os.path.join(etc, n), encoding="utf-8").read() for n in ("recovery.fstab", "twrp.flags"))
            if re.search(rf"(^|\s){re.escape(mount)}\s", text, re.M):
                ok(f"{var}={root} is on a partition in the fstab")
            else:
                fail(f"{var}={root}: {mount} is not in recovery.fstab or twrp.flags")


def check_boardconfig(tree):
    print("BoardConfig.mk")
    path = os.path.join(tree, "BoardConfig.mk")
    text = open(path, encoding="utf-8").read()
    for var in ("TW_Y_OFFSET", "TW_H_OFFSET"):
        if re.search(rf"^\s*{var}\s*:?=", text, re.M):
            fail(f"{var} is set: shifting the UI to hide the cutout is not allowed, use OF_STATUS_H")
    if not re.search(r"^\s*TW_Y_OFFSET|^\s*TW_H_OFFSET", text, re.M):
        ok("no UI offsets")
    mv = foxtools.read_make_vars(path, tree)
    for var in ("TARGET_PREBUILT_KERNEL", "TARGET_PREBUILT_DTB", "BOARD_PREBUILT_DTBOIMAGE"):
        if var not in mv:
            warn(f"{var} not set")
    return mv


def check_crypto(tree, mv, device):
    """Decryption with Samsung's TEE: the blobs, patch levels and init wiring."""
    print("decryption")
    if mv.get("TW_INCLUDE_CRYPTO", "").strip() != "true":
        ok("off (TW_INCLUDE_CRYPTO is not true)")
        return
    of_vars = fox_mk_vars(tree, device)
    of_vars.update({k: v for k, v in fox_env(tree, device).items() if k.startswith("OF_")})
    if of_vars.get("OF_SKIP_FBE_DECRYPTION") == "1":
        fail(f"OF_SKIP_FBE_DECRYPTION=1 in fox_{device}.mk turns decryption off again")
    if mv.get("TW_FORCE_KEYMASTER_VER", "").strip() == "true":
        if of_vars.get("OF_DEFAULT_KEYMASTER_VERSION"):
            ok(f"TW_FORCE_KEYMASTER_VER with OF_DEFAULT_KEYMASTER_VERSION={of_vars['OF_DEFAULT_KEYMASTER_VERSION']}")
        else:
            fail("TW_FORCE_KEYMASTER_VER is set but OF_DEFAULT_KEYMASTER_VERSION is not; orangefox.mk stops the build")

    meta, files = foxtools.read_tee_manifest(tree)
    if meta is None:
        fail(f"{foxtools.TEE_MANIFEST} is missing; install the TEE files with install-tee-files.py")
        return
    bad = []
    for sha, rel, _phone in files:
        p = os.path.join(tree, rel)
        if not os.path.isfile(p):
            bad.append(f"{rel} (missing)")
        else:
            with open(p, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() != sha:
                    bad.append(f"{rel} (changed)")
    if bad:
        fail("TEE files do not match tee-files.txt: " + ", ".join(bad[:5]))
    else:
        ok(f"{len(files)} TEE files from {meta.get('firmware')} match tee-files.txt")

    # the TEE takes the versions from keymint (prepare_tee.sh sets them to the
    # installed firmware's); the header only mirrors the stock recovery
    spl, vendor_spl = mv.get("PLATFORM_SECURITY_PATCH", ""), mv.get("VENDOR_SECURITY_PATCH", "")
    if spl == meta.get("security_patch") and vendor_spl == meta.get("vendor_security_patch"):
        ok(f"patch levels {spl} / {vendor_spl} are those of {meta.get('firmware')}, as in its stock recovery")
    else:
        warn(f"PLATFORM_SECURITY_PATCH={spl}, VENDOR_SECURITY_PATCH={vendor_spl}, but the TEE files are "
             f"from {meta.get('security_patch')} / {meta.get('vendor_security_patch')}; "
             "run install-tee-files.py to set them")
    root_sh = os.path.join(tree, "recovery/root/system/bin/prepare_tee.sh")
    script = open(root_sh, encoding="utf-8").read() if os.path.isfile(root_sh) else ""
    if "ro.build.version.security_patch" in script:
        ok("prepare_tee.sh reports the installed firmware's versions to keymint")
    else:
        fail("prepare_tee.sh does not set ro.build.version.security_patch from the installed firmware")
    if "tee-files.list" in script:
        ok("prepare_tee.sh takes the TEE files from the installed firmware")
    else:
        fail("prepare_tee.sh does not copy the files on tee-files.list from the installed firmware")

    blist = os.path.join(tree, foxtools.TEE_BOOT_LIST)
    if os.path.isfile(blist):
        boot = set(foxtools.read_tee_boot_list(open(blist, encoding="utf-8").read()))
        unlisted = [phone for _sha, _rel, phone in files if phone.startswith("/vendor/") and phone not in boot]
        outside = [p for p in boot if not p.startswith("/vendor/")]
        if unlisted or outside:
            fail("tee-files.list does not match tee-files.txt; run install-tee-files.py again")
        else:
            ok(f"tee-files.list: {len(boot)} files are taken from the installed firmware at boot")
    else:
        fail(f"{foxtools.TEE_BOOT_LIST} is missing; run install-tee-files.py again")
    if "TARGET_RECOVERY_DEVICE_MODULES" in mv and "gatekeeper@1.0-impl" in mv["TARGET_RECOVERY_DEVICE_MODULES"]:
        fail("the AOSP gatekeeper implementation is built and relinked into system/lib64, where HIDL "
             "does not look; the stock one is taken from the firmware")

    root = os.path.join(tree, "recovery/root")
    rcs = [os.path.join(root, n) for n in os.listdir(root) if n.startswith("init.recovery.") and n.endswith(".rc")]
    imported = any("import /init.recovery.teegris.rc" in open(r, encoding="utf-8").read() for r in rcs)
    if os.path.isfile(os.path.join(root, "init.recovery.teegris.rc")) and imported:
        ok("init.recovery.teegris.rc is present and imported")
    else:
        fail("init.recovery.teegris.rc is missing or not imported from init.recovery.<hardware>.rc")
    if os.path.isfile(os.path.join(root, "system/bin/prepare_tee.sh")):
        ok("prepare_tee.sh is present")
    else:
        fail("recovery/root/system/bin/prepare_tee.sh is missing")
    # vold also adds each fscrypt key to the "fscrypt" session keyring, which
    # only init's install_keyring creates in recovery
    if any(re.search(r"^\s*install_keyring\s*$", open(r, encoding="utf-8").read(), re.M) for r in rcs):
        ok("init creates the fscrypt keyring (install_keyring)")
    else:
        fail("no init.recovery.*.rc runs install_keyring; vold cannot install the DE key "
             "(\"Unable to find device keyring\")")

    props = foxtools.read_props(os.path.join(tree, "vendor.prop"))
    if props.get("ro.hardware.keystore") and "vendor.prop" in mv.get("TARGET_VENDOR_PROP", ""):
        ok(f"vendor.prop sets ro.hardware.keystore={props['ro.hardware.keystore']}")
    else:
        fail("vendor.prop with ro.hardware.keystore is missing or not in TARGET_VENDOR_PROP")

    fstab = os.path.join(root, "system/etc/recovery.fstab")
    data = [l for l in open(fstab, encoding="utf-8") if re.match(r"\S+\s+/data\s", l)]
    if data and "fileencryption=" in data[0] and "keydirectory=" in data[0]:
        ok("/data has fileencryption= and keydirectory= (metadata encryption)")
    else:
        fail("the /data line in recovery.fstab lacks fileencryption= or keydirectory=")


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
    if storage and (len(storage) < 3 or storage[2] != "/data/media/0"
                    or not re.search(r'fsflags="?bind\b', flag_map["/storage"])):
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
    mv = foxtools.read_make_vars(os.path.join(tree, "BoardConfig.mk"), tree)
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
    # The optional ones (soc_temp_lvts.ko) only ever load from the ramdisk.
    boot_modules = mv.get("TW_LOAD_VENDOR_BOOT_MODULES") == "true"
    for ko in foxtools.tw_vendor_modules(os.path.join(tree, "BoardConfig.mk")):
        why = foxtools.ramdisk_loadable(libmod, ko)
        role = foxtools.vendor_module_role(ko)
        if ko in foxtools.OPTIONAL_VENDOR_MODULES:
            if why is None:
                ok(f"{ko} loads from the ramdisk ({role})")
            else:
                warn(f"{ko} cannot load from the ramdisk ({why}): no {role}; add it with "
                     "pull-vendor-files.sh and extract-prebuilts.py --vendor-files")
        elif why is None and boot_modules:
            ok(f"{ko} loads from the ramdisk")
        elif why is None:
            warn(f"{ko} is in the ramdisk but TW_LOAD_VENDOR_BOOT_MODULES is not set")
        else:
            warn(f"{ko} cannot load from the ramdisk ({why}); {role} needs the installed ROM's vendor_dlkm")

    # OrangeFox checks TW_CUSTOM_CPU_TEMP_PATH once, at startup, before it
    # loads TW_LOAD_VENDOR_MODULES; cpu_temp.sh makes the link from init
    temp_path = mv.get("TW_CUSTOM_CPU_TEMP_PATH", "").strip('"')
    if mv.get("TW_NO_CPU_TEMP") == "true":
        ok("CPU temperature off (TW_NO_CPU_TEMP)")
    elif temp_path == "/tmp/cpu_temp":
        if os.path.isfile(os.path.join(root, "system/bin/cpu_temp.sh")):
            ok("CPU temperature from /tmp/cpu_temp, linked by cpu_temp.sh")
        else:
            fail("TW_CUSTOM_CPU_TEMP_PATH is /tmp/cpu_temp but recovery/root/system/bin/cpu_temp.sh is missing")

    kernel = mv.get("TARGET_PREBUILT_KERNEL", "")
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
        p = mv.get(var, "")
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

    check_layout(tree, args.device)
    check_vendorsetup(tree, args.device)
    mv = check_boardconfig(tree)
    check_fstab(tree)
    check_crypto(tree, mv, args.device)
    check_modules(tree, mv)
    if args.device_info:
        check_device_info(tree, args.device_info)

    print()
    print(f"{len(failures)} failure(s), {len(warnings)} warning(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
