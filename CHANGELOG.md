# Changelog

## 0.2.0

- A pair that has not finished a round for 7 days (setting) is reported once.
- Contents are compared every 30 days (setting) to find copies that went bad;
  `whysync repair` or Repair copies the source's version again, keeping the
  target's in the trash.
- A pair's limits and skip patterns can be changed after it is added (window:
  Settings; CLI: `whysync set`).
- After a restart the window still shows each pair's last round.
- Keeping target-only files and restoring from the trash never write into the
  source folder's place when its disk is unplugged, and never overwrite.
- The wizard calls files that exist only in the target what they are: the
  first round asks about them.

## 0.1.0

- Live one-way mirror as a systemd user service, with a GTK window and a CLI.
- Waits for unplugged disks, holds mass deletions, keeps a trash per pair with
  one-click restore, and asks about target-only files on a new pair's first
  round.
