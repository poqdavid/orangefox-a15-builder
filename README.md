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
| `firmware` | `all` | the firmware set (One UI generation) to build, `oneui7` or `oneui6`; `all` builds every set the tree has, each in a job of its own. A tree from before the firmware sets only builds with `all` |
| `build_type` | `Unofficial` | `Alpha`/`Beta`/`Stable` only once you are the official maintainer |
| `patch_version` | empty | overrides `FOX_MAINTAINER_PATCH_VERSION` from `vendorsetup.sh` |
| `draft_release` | off | attach the files to a draft release |

Each set is a build of its own, because the kernel and modules only work
with a bootloader of the One UI generation they come from (see the tree's
README). For each set, the job syncs pristine OrangeFox sources with the
official sync script, runs `tools/preflight.py` on the tree, builds
`mka adbd recoveryimage` with `A15_FIRMWARE` set, checks the image with
`tools/check-image.py`, and uploads, with the set at the end of the names:

- `OrangeFox-*-<set>.img` and `OrangeFox-*-<set>.zip`
- `OrangeFox-*-<set>-odin.tar` (recovery.img for Odin's AP slot)
- `SHA256SUMS`, `BUILD_INFO.md`, `CHECK.txt`, and the build log

With `draft_release`, one draft release gets the files of every set, once
all of them have built and passed the image check.

## Build locally

```bash
git clone https://gitlab.com/OrangeFox/sync.git && cd sync
./orangefox_sync.sh --branch 12.1 --path ~/fox_12.1
cd ~/fox_12.1
git clone https://github.com/poqdavid/android_device_samsung_a15 -b fox_12.1 device/samsung/a15
source build/envsetup.sh
export A15_FIRMWARE=oneui6   # the firmware set; oneui7 without it
lunch twrp_a15-eng
mka adbd recoveryimage
```

Run `mka installclean` before building another set in the same `out`
directory.

## Tools

| Tool | Runs on | Purpose |
|------|---------|---------|
| `tools/preflight.py <tree> [--firmware <set>]` | PC / CI | Checks the tree's layout (the one used by the official a33x/a23xq trees), the OrangeFox guidelines (fstab rules, no UI offsets, Fox vars), that each firmware set (or only the one named) is complete, every module matches its kernel and the touch drivers can load from the ramdisk, and, with decryption on, that the TEE files match `tee-files.txt` and the patch levels are the firmware's |
| `tools/check-image.py <img> <tree> [--firmware <set>]` | PC / CI | Checks a built image: stock header layout, os version and patch level, SEANDROIDENFORCE, AVB footer, size, kernel/module match, that the kernel and the build's properties are those of the firmware set, touch modules present, and the TEE files when decryption is on |
| `tools/device-info.sh` | phone (root) | Reports the values the tree depends on: cutout/status bar, backlight, partitions and sizes, MicroSD/OTG sysfs paths, current vendor fstab. `preflight.py --device-info <report>` checks the tree against it |
| `tools/pull-vendor-files.sh <dir> [--recovery]` | PC (adb) | Copies the touch modules, the SoC temperature sensor module, their dependencies, `modules.dep` and the touch firmware from a rooted phone; `--recovery` also dumps the stock recovery partition |
| `tools/extract-prebuilts.py --firmware <set>` | PC | Refreshes a firmware set, or adds a new one, from a stock recovery image: kernel, DTB, DTBO, recovery modules, its `firmware.mk`, and with `--vendor-files` the touch drivers, `soc_temp_lvts.ko` and touch firmware |
| `tools/tee-info.sh` | phone (root) | Reports what /data decryption depends on: the real and the reported Android version and patch levels, the patch level in the boot/recovery headers, the TEE (TEEGRIS), keymint and gatekeeper services and their init scripts, the trusted apps, and which lock screen protectors are in use. Read-only |
| `tools/pull-tee-files.py <dir>` | PC (adb) | Copies the TEE stack from a rooted phone on stock firmware: the services above, every library they load (followed recursively), the trusted apps, vintf fragments and build.prop files, with a `MANIFEST.tsv`; writes `<dir>.zip` |
| `tools/install-tee-files.py <dir or zip> --tree <tree>` | PC | Installs the TEE services, the gatekeeper module and every library they need from `pull-tee-files.py` output into the tree, writes `tee-files.txt` (firmware, patch levels, checksums) and sets `PLATFORM_SECURITY_PATCH` to the firmware's |

### Refreshing or adding a firmware set

The tree keeps a firmware set per One UI generation in `firmware/<set>`
(listed in the tree's README): the kernel, DTB, DTBO and modules of one
stock firmware's recovery. The touch drivers and the SoC temperature sensor
driver (`soc_temp_lvts.ko`, for the CPU temperature) are not part of the
stock recovery: they and `tuihw-inf.ko`, which the touch drivers need, are
taken from `vendor_dlkm` of the same firmware. To move a set to a newer
firmware of its generation, or to add a set for another generation, use a
rooted phone running that stock firmware; a custom ROM is fine as long as
the vendor and bootloader are that firmware's.

```bash
# --recovery also dumps the phone's recovery partition (only while it is still stock)
tools/pull-vendor-files.sh vendor-files/ --recovery
tools/extract-prebuilts.py --recovery vendor-files/recovery.img \
    --tree ~/fox_12.1/device/samsung/a15 --firmware oneui6 --vendor-files vendor-files/
tools/preflight.py ~/fox_12.1/device/samsung/a15 --firmware oneui6
```

The set is named by `--firmware`. A name the tree does not have yet adds a
set; an existing one is refused if the image is of another OS generation
(the 4th character from the end of the firmware version, `B` in
A155MUBS6BYE1) than the set is for. A new set also needs a row in the tree's
README and an option in this workflow's `firmware` input.

If the phone already has a custom recovery, pass the AP file instead:
`--recovery AP_A155F...tar.md5`.

`pull-vendor-files.sh` copies the touch drivers, `soc_temp_lvts.ko`, every
module they depend on and `modules.dep`. `extract-prebuilts.py` installs the
ones the stock recovery lacks into the set's `lib/modules` (the ramdisk's
`/lib/modules`) with their `modules.dep` entries, so touch no longer depends on the installed ROM and
the tree's `cpu_temp.sh` can load the temperature sensor before OrangeFox
starts. A firmware without `soc_temp_lvts.ko` is skipped with a warning. It refuses to
write a set that mixes modules from different kernel builds, and prints
any difference between the stock image layout and `BoardConfig.mk`. Use
`--dry-run` to only compare.

## Docs

- [docs/TESTING.md](docs/TESTING.md): first boot, values to verify, test suite, known limitations
- [docs/OFFICIAL.md](docs/OFFICIAL.md): steps from unofficial builds to official maintainership
