# Path to official OrangeFox for the Galaxy A15 (SM-A155F)

OrangeFox only accepts a device after the maintainer applies. Status as of
November 2026: `a15` has no official OrangeFox build, so the device is open.
Official Samsung devices on the current manifests (for example `a33x` on
`fox_12.1`, and `a55x`) are useful references on gitlab.com/OrangeFox/device.

Sources: [Road to be an official maintainer](https://wiki.orangefox.tech/dev/maintainerships),
[Guidelines for official maintainers](https://wiki.orangefox.tech/dev/maintainers_guidelines),
[Build variables](https://wiki.orangefox.tech/dev/build_vars).

## 1. Unofficial phase

- [ ] Build with this repo's workflow (`Unofficial`, from pristine fox_12.1).
- [ ] Run `tools/device-info.sh` on the phone and confirm the values marked
      "verify" in [TESTING.md](TESTING.md) (status bar height, OTG path,
      partitions).
- [ ] Pass the test suite in [TESTING.md](TESTING.md). Fix what you can and
      write down everything you can't (bugs that aren't fixed don't
      disqualify you, as long as every release documents them).
- [ ] Keep one recovery log per test operation, plus `install.log` and
      `post-install.log` from flashing the OrangeFox zip. The application is
      rejected without them.
- [ ] Host the complete device tree on **GitLab** (your account), default
      branch `fox_12.1`.
- [ ] Open an XDA thread for the unofficial build and maintain it. Wait **at
      least 4 weeks** after the first unofficial release before applying, and
      the builds need to be well received.

## 2. Apply

Requirements: Telegram, GitLab and OrangeFox Discord accounts, the device in
hand, and a tree that already follows the guidelines.

Post in `#apply-for-maintainer` on the OrangeFox Discord with:

- the GitLab link to the tree
- the XDA thread link
- separate logs for each test operation (ROM flash, image flash, backup,
  restore, MicroSD/OTG backup, settings, password, theme...)
- `install.log` and `post-install.log` from flashing the OrangeFox zip

## 3. After acceptance

- [ ] Ask for GitLab permissions and push the tree to the OrangeFox GitLab
      (default branch `fox_12.1`, kept in sync with the source; work for the
      next release goes in `master`).
- [ ] First release is **Beta**. **Stable** only after at least 14 days of
      Beta testing.
- [ ] Release only through FoxBox (orangefox.download). Never upload the
      zips anywhere else, test groups included. Official zips must be signed.
- [ ] Push the exact tree used for each release, keep a copy of every
      released zip, and bump `FOX_MAINTAINER_PATCH_VERSION` for every build
      (same OrangeFox version means a higher patch number; never reuse one).
- [ ] Test every release build with the test suite, including stock and
      custom ROMs on each Android version available for the device (14, 15
      and 16 for the A15). The build you release must be the build you
      tested.
- [ ] Build only from pristine OrangeFox sources: no changes to
      `bootable/recovery`, `vendor/recovery` or the build system.
- [ ] Support users in the official Telegram groups (Beta group for Beta
      builds).
- [ ] Stop publishing unofficial builds for the device.

## Build types

| `FOX_BUILD_TYPE` | Who / when |
|------------------|------------|
| (unset) → Unofficial | Everything before acceptance |
| `Alpha` | Official maintainer; private test builds, shared by direct link only |
| `Beta` | Official; partially tested. Must boot, basic features must work, and it must be able to flash other OrangeFox zips/images for rollback. Required for the first release after a major update |
| `Stable` | Official; fully tested against the test suite, no major regressions |
