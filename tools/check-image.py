#!/usr/bin/env python3
"""Check a built a15 recovery image before it is flashed or released.

usage: check-image.py <OrangeFox-*.img> <device tree dir> [--firmware <set>]

Verifies that the image matches the stock recovery layout configured in
BoardConfig.mk and the firmware set it was built with (--firmware, else
$A15_FIRMWARE, else the tree's default), carries SEANDROIDENFORCE and an
AVB hash footer, fits the recovery partition, and that the kernel and every
module in the ramdisk belong together.
"""

import argparse
import os
import re
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
    ap.add_argument("--firmware", help="the firmware set the image was built with")
    args = ap.parse_args()

    data = open(args.image, "rb").read()
    tree = os.path.abspath(args.tree)
    firmware = foxtools.pick_firmware(tree, args.firmware)
    mv = foxtools.board_vars(tree, firmware)
    hdr, sec = foxtools.parse_bootimg(data)
    if firmware:
        print(f"firmware set {firmware}")

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
    version, patch = foxtools.os_version_str(hdr["os_version"])
    crypto = mv.get("TW_INCLUDE_CRYPTO", "").strip() == "true"
    if crypto:
        # the TEE sees these; they must be the firmware's (see tee-files.txt)
        want_version = mv.get("PLATFORM_VERSION", "")
        want_patch = mv.get("PLATFORM_SECURITY_PATCH", "")[:7]
        check(version is not None and version.split(".")[0] == want_version.split(".")[0],
              f"os version {version}", f"os version {version}, expected {want_version}")
        check(patch == want_patch, f"os patch level {patch}", f"os patch level {patch}, expected {want_patch}")
    else:
        print(f"  info  os version {version}, patch level {patch}")

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
    boot_list = None
    rc_text = ""
    props = {}
    bad = []
    for name, _mode, payload in foxtools.cpio_entries(ramdisk):
        names.add(name)
        if name == "prop.default":
            for line in payload.decode("utf-8", "replace").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
        if "/" not in name and name.endswith(".rc"):
            rc_text += payload.decode("utf-8", "replace") + "\n"
        if name == foxtools.TEE_BOOT_LIST[len("recovery/root/"):]:
            boot_list = foxtools.read_tee_boot_list(payload.decode("utf-8", "replace"))
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
    for ko in foxtools.tw_vendor_modules(os.path.join(tree, "BoardConfig.mk")):
        missing = [d for d in deps.get(ko, []) if f"lib/modules/{d}" not in names]
        role = foxtools.vendor_module_role(ko)
        if f"lib/modules/{ko}" in names and ko in deps and not missing:
            print(f"  ok    {ko} loads from the ramdisk")
        elif ko in foxtools.OPTIONAL_VENDOR_MODULES:
            print(f"  note  {ko} is not loadable from the ramdisk: no {role}")
        else:
            print(f"  note  {ko} is not loadable from the ramdisk; {role} needs the ROM's vendor_dlkm")
    if mv.get("TW_CUSTOM_CPU_TEMP_PATH", "").strip('"') == "/tmp/cpu_temp":
        check("system/bin/cpu_temp.sh" in names and "cpu_temp.sh" in rc_text,
              "cpu_temp.sh links /tmp/cpu_temp at boot",
              "TW_CUSTOM_CPU_TEMP_PATH is /tmp/cpu_temp but cpu_temp.sh is missing or not run by init")
    for f in ("system/etc/recovery.fstab", "system/etc/twrp.flags", "lib/modules/modules.load.recovery"):
        check(f in names, f"{f} in ramdisk", f"{f} missing from the ramdisk")

    if firmware:
        print("firmware set")
        kernel = mv.get("TARGET_PREBUILT_KERNEL", "")
        want = foxtools.kernel_release(open(kernel, "rb").read()) if os.path.isfile(kernel) else None
        check(release is not None and release == want, f"kernel {release} is the {firmware} set's",
              f"kernel {release}, but the {firmware} set's is {want}")
        letters = foxtools.firmware_vars(tree, firmware).get("A15_FIRMWARE_OS", "")
        got = (props.get("ro.vendor.a15.firmware"), props.get("ro.vendor.a15.firmware.os"))
        check(got == (firmware, letters), f"ro.vendor.a15.firmware={firmware}, for OS {letters} bootloaders",
              f"prop.default has ro.vendor.a15.firmware={got[0]} and .os={got[1]}, "
              f"expected {firmware} and {letters}")

    if crypto:
        print("decryption")
        _meta, files = foxtools.read_tee_manifest(tree)
        wanted = [rel[len("recovery/root/"):] for _sha, rel, _phone in files] + [
            "init.recovery.teegris.rc", "system/bin/prepare_tee.sh"]
        missing = [w for w in wanted if w not in names]
        check(not missing, f"{len(wanted)} TEE files and scripts in the ramdisk",
              "missing from the ramdisk: " + ", ".join(missing[:6]))
        check(re.search(r"^\s*install_keyring\s*$", rc_text, re.M) is not None,
              "init creates the fscrypt keyring (install_keyring)",
              "no init script runs install_keyring; vold cannot install the DE key")
        check(boot_list is not None, f"tee-files.list: {len(boot_list or [])} files taken from the firmware at boot",
              "vendor/etc/tee-files.list is missing, so nothing is taken from the firmware at boot")
        boot = set(boot_list or [])
        # HIDL passthrough only looks in {odm,vendor,system}/lib64/hw
        gk = "/vendor/bin/hw/android.hardware.gatekeeper@1.0-service"
        check(gk[1:] in names or gk in boot, "gatekeeper service", "the gatekeeper service is neither "
              "in the ramdisk nor on tee-files.list")
        impl = [n for n in names if n.endswith("/lib64/hw/android.hardware.gatekeeper@1.0-impl.so")]
        impl += [p[1:] for p in boot if p == "/vendor/lib64/hw/android.hardware.gatekeeper@1.0-impl.so"]
        check(bool(impl), f"gatekeeper implementation at /{impl[0] if impl else ''}",
              "android.hardware.gatekeeper@1.0-impl.so is not in a lib64/hw directory, "
              "so the gatekeeper service cannot load it")
        stray = [n for n in names if n.startswith("system/lib64/") and n.endswith("-impl.so")]
        check(not stray, "no passthrough implementations in system/lib64",
              "passthrough implementations in system/lib64, where HIDL does not look: " + ", ".join(stray))

    print()
    print(f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
