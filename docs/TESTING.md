# Testing the a15 build

Collect a recovery log for every operation (Menu → Logs → copy to MicroSD,
or `adb pull /tmp/recovery.log`) and name it after the test. Without
decryption, save logs to MicroSD or OTG, not internal storage.

## First boot

1. Odin: flash `*-odin.tar` in AP, Auto Reboot off.
2. Hold Volume Up + Power with a USB cable connected until OrangeFox starts.
3. OrangeFox keeps itself installed: on every boot `postrecoveryboot.sh`
   checks whether boot is still the pristine stock image and, if so, runs
   `multidisabler` to repack it. With a rooted or custom boot image it does
   nothing. Check `/tmp/recovery.log` for the "multidisabler:" lines.

If the screen stays black or the phone reboots to Download mode, keep a
copy of `/sys/fs/pstore/console-ramoops*` if you can still get it from
Android, and report the firmware version from `device-info.sh`.

## Values checked on the phone

From a `tools/device-info.sh` report (A155FXXS7CYG4, Android 15). Run
`tools/preflight.py <tree> --device-info <report>` to re-check the
partitions and sizes after a firmware update.

| Setting | Value | Status |
|---------|-------|--------|
| Screen / `OF_SCREEN_H` | 1080 x 2340 | confirmed |
| `OF_STATUS_H` | 80 | confirmed (Android's status bar frame is 80 px) |
| Backlight | `/sys/class/backlight/panel`, `max_brightness` 483 (0-255 normal, above is HBM) | confirmed |
| Battery | `/sys/class/power_supply/battery/{capacity,status}` | confirmed |
| Vibrator | `/sys/class/timed_output/vibrator` | confirmed |
| Partition sizes | recovery 80 MiB, boot 64 MiB, super 11744051200 | confirmed |
| Partitions in fstab | all 20 exist, including dtbo, vbmeta_system, vbmeta_vendor | confirmed |
| EFS | `efs` (Android: `/mnt/vendor/efs`) is `/efs` in recovery; `sec_efs` (Android: `/efs`) is `/sec_efs` | confirmed |
| `/sdcard1` | `/dev/block/mmcblk0p1` (`mmcblk0` is the MicroSD; storage is UFS) | confirmed in Android; check in recovery |
| `/usb_otg` | `/dev/block/sdd1` (first SCSI disk after the UFS LUNs `sda`-`sdc`) | not tested: check with an OTG drive in recovery (`ls /dev/block/sd*`) |
| Touch drivers | `zt7650m_ts.ko`, `focaltech_ts_ft3418.ko` and `tuihw-inf.ko` in `vendor_dlkm` | confirmed |

## Test suite

Basic:

- [ ] Flash a zip (Magisk/KernelSU zip, a ROM) from MicroSD and from OTG
- [ ] ADB sideload a zip
- [ ] Flash an image: boot, recovery (the OrangeFox image itself), vbmeta
- [ ] Backup and restore: Boot, EFS + NVRAM/NVDATA/NVCFG/Protect, Super, Data
- [ ] Backup to MicroSD and to OTG
- [ ] Read and write MicroSD (FAT32 and exFAT) and OTG (FAT32, exFAT, NTFS)
- [ ] Format Data, then boot Android
- [ ] Settings persist after a reboot. They are stored in `/cache/Fox`
      (`FOX_SETTINGS_ROOT_DIRECTORY`), prepared by `runatboot.sh`; check
      that the folder exists and that a changed setting survives a reboot to
      Android and back
- [ ] Built-in features: terminal, file manager, Fox addons, reboot to
      system/recovery/download/power off
- [ ] MTP in recovery

Advanced:

- [ ] Recovery password survives a reboot
- [ ] Theme and splash survive a reboot (splash change repacks the
      recovery image; check that it still boots afterwards)

ROMs (each Android version the device has):

- [ ] Stock One UI 6 (Android 14), One UI 7 (Android 15), One UI 8/8.5
      (Android 16): boot after flashing through Odin, recovery still boots
- [ ] A custom ROM or GSI flashed from the recovery

Device:

- [ ] Touch on both panel types if you can borrow one (Zinitix ZT7650M and
      FocalTech FT3418)
- [ ] Touch with stock firmware installed, and again with `super` wiped or
      a GSI installed (works only once the touch drivers load from the
      ramdisk, see below)
- [ ] Brightness slider covers the full range without jumping to HBM
- [ ] Battery percentage and charging indicator
- [ ] Vibration (sec_vibrator, `/sys/class/timed_output/vibrator`)
- [ ] Status bar clears the cutout; clock left and right; rounded corners

## Known limitations to state in every release

- No /data decryption (Samsung TEEGRIS). Internal storage is only readable
  between Format Data and the next Android boot, which encrypts it again;
  use MicroSD, OTG or ADB sideload.
- Settings, themes and the recovery password are stored in the cache
  partition (`/cache/Fox`), so wiping cache resets them.
- No flashlight (the flash LED modules aren't in the recovery ramdisk).
- No CPU temperature (thermal modules aren't loaded in recovery).
