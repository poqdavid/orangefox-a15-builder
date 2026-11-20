# OrangeFox builder for the Galaxy A15 (SM-A155F)

GitHub Actions workflow and helper tools for building and maintaining
OrangeFox for the Samsung Galaxy A15 4G (`a15`, MT6789) from the
`fox_12.1` manifest. The device tree itself lives in its own repo.

## Build in GitHub Actions

Actions → **Build OrangeFox (a15)** → Run workflow:

| Input | Default | Notes |
|-------|---------|-------|
| `tree_url` | `https://github.com/poqdavid/android_device_samsung_a15` | GitLab works too |
| `tree_ref` | `fox_12.1` | branch, tag or commit |
| `build_type` | `Unofficial` | `Alpha`/`Beta`/`Stable` only once you are the official maintainer |
| `patch_version` | empty | overrides `FOX_MAINTAINER_PATCH_VERSION` from `vendorsetup.sh` |
| `draft_release` | off | attach the files to a draft release |

The job syncs pristine OrangeFox sources with the official sync script,
runs `tools/preflight.py` on the tree, builds `mka adbd recoveryimage`,
checks the image with `tools/check-image.py`, and uploads:

- `OrangeFox-*.img` and `OrangeFox-*.zip`
- `OrangeFox-*-odin.tar` (recovery.img for Odin's AP slot)
- `SHA256SUMS`, `BUILD_INFO.md`, `CHECK.txt`, and the build log

## Build locally

```bash
git clone https://gitlab.com/OrangeFox/sync.git && cd sync
./orangefox_sync.sh --branch 12.1 --path ~/fox_12.1
cd ~/fox_12.1
git clone https://github.com/poqdavid/android_device_samsung_a15 -b fox_12.1 device/samsung/a15
source build/envsetup.sh
lunch twrp_a15-eng
mka adbd recoveryimage
```

## Tools

| Tool | Runs on | Purpose |
|------|---------|---------|
| `tools/preflight.py <tree>` | PC / CI | Checks the tree's layout (the one used by the official a33x/a23xq trees), the OrangeFox guidelines (fstab rules, no UI offsets, Fox vars), that every module matches the prebuilt kernel, whether the touch drivers can load from the ramdisk, and, with decryption on, that the TEE files match `tee-files.txt` and the patch levels are the firmware's |
| `tools/check-image.py <img> <tree>` | PC / CI | Checks a built image: stock header layout, os version and patch level, SEANDROIDENFORCE, AVB footer, size, kernel/module match, touch modules present, and the TEE files when decryption is on |
| `tools/device-info.sh` | phone (root) | Reports the values the tree depends on: cutout/status bar, backlight, partitions and sizes, MicroSD/OTG sysfs paths, current vendor fstab. `preflight.py --device-info <report>` checks the tree against it |
| `tools/pull-vendor-files.sh <dir> [--recovery]` | PC (adb) | Copies the touch modules, the SoC temperature sensor module, their dependencies, `modules.dep` and the FocalTech touch firmware from a rooted phone; `--recovery` also dumps the stock recovery partition |
| `tools/extract-prebuilts.py` | PC | Replaces kernel, DTB, DTBO and recovery modules from a stock recovery image |
| `tools/tee-info.sh` | phone (root) | Reports what /data decryption depends on: the real and the reported Android version and patch levels, the patch level in the boot/recovery headers, the TEE (TEEGRIS), keymint and gatekeeper services and their init scripts, the trusted apps, and which lock screen protectors are in use. Read-only |
| `tools/pull-tee-files.py <dir>` | PC (adb) | Copies the TEE stack from a rooted phone on stock firmware: the services above, every library they load (followed recursively), the trusted apps, vintf fragments and build.prop files, with a `MANIFEST.tsv`; writes `<dir>.zip` |
| `tools/install-tee-files.py <dir or zip> --tree <tree>` | PC | Installs the TEE services, the gatekeeper module and every library they need from `pull-tee-files.py` output into the tree, writes `tee-files.txt` (firmware, patch levels, checksums) and sets `PLATFORM_SECURITY_PATCH` to the firmware's |

### Refreshing the prebuilts from newer firmware

The tree's kernel, DTB, DTBO and modules all come from one stock firmware
(named in the tree's README). The touch drivers and the SoC temperature
sensor driver (`soc_temp_lvts.ko`, for the CPU temperature) are not part of
the stock recovery: they and `tuihw-inf.ko`, which the touch drivers need,
are taken from `vendor_dlkm`. To move to a newer firmware, use a rooted phone running that
stock firmware.

```bash
# --recovery also dumps the phone's recovery partition (only while it is still stock)
tools/pull-vendor-files.sh vendor-files/ --recovery
tools/extract-prebuilts.py --recovery vendor-files/recovery.img \
    --tree ~/fox_12.1/device/samsung/a15 --vendor-files vendor-files/
tools/preflight.py ~/fox_12.1/device/samsung/a15
```

If the phone already has a custom recovery, pass the AP file instead:
`--recovery AP_A155F...tar.md5`.

`pull-vendor-files.sh` copies the touch drivers, `soc_temp_lvts.ko`, every
module they depend on and `modules.dep`. `extract-prebuilts.py` installs the
ones the stock recovery lacks into the ramdisk's `/lib/modules` with their
`modules.dep` entries, so touch no longer depends on the installed ROM and
the tree's `cpu_temp.sh` can load the temperature sensor before OrangeFox
starts. A firmware without `soc_temp_lvts.ko` is skipped with a warning. It refuses to
write a tree that mixes modules from different kernel builds, and prints
any difference between the stock image layout and `BoardConfig.mk`. Use
`--dry-run` to only compare.

## Docs

- [docs/TESTING.md](docs/TESTING.md): first boot, values to verify, test suite, known limitations
- [docs/OFFICIAL.md](docs/OFFICIAL.md): steps from unofficial builds to official maintainership
