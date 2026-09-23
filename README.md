<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.png">
    <img src="assets/logo-light.png" alt="whySYNC" width="520">
  </picture>
</p>

<p align="center">
  <i>Live one-way folder mirror. Linux, systemd, rsync.</i>
</p>

Live one-way folder mirror for Linux. When something changes in the source
folder, whySYNC waits for writes to settle and mirrors the source onto the
target with rsync. It runs as a systemd user service and keeps working with no
window open.

Made for keeping a second disk as an exact copy of the first. It is built
to assume a disk may be unplugged at any time.

## Safety

- **Missing disk, no action.** If the source or target folder does not exist
  (disk not mounted), the pair waits. whySYNC never creates the target folder,
  so it never writes into an empty mount point on your system disk.
- **Mass deletion is held.** A round that would delete more than
  `max_deletes` files (default 50), or would delete anything while the source
  is empty, stops and asks for approval. An approval is valid only for that
  exact list of deletions.
- **A new pair's first round asks about files only in the target.** Even one
  such file holds the first round: it may be the only copy. Keep them (copied
  into the source, nothing overwritten) or move them to trash. Later rounds
  treat them as files deleted in the source.
- **Nothing is deleted outright.** Deleted and overwritten files are moved to
  `.whysync-trash/<time>/` inside the target and purged after 30 days.
- **An overdue copy is reported.** If a pair has not finished a round for 7
  days (the disk stayed unplugged, a round waits for approval, rsync keeps
  failing), a desktop notification says so, once.
- **Copies that go bad are caught.** Rounds compare size and date. Every 30
  days the contents are compared as well, in the background, to find files
  that changed inside without either changing (a failing disk). They are
  listed and reported; `whysync repair` or Repair in the window copies the
  source's version again and keeps the target's in the trash.
- **Changes made while it was off are picked up.** A full check runs at
  start-up and every hour, and takes a couple of seconds on ~75k files.

## Trash and restore

Each round that deletes or overwrites something gets its own folder in the
target, `.whysync-trash/<time>/`, laid out like the pair. Pairs never share a
trash, and the desktop trash is not involved. From the window (Trash, inside a
pair) or `whysync restore`, files go back into the **source**, which is the
side that gets mirrored; from there they reach the target again. If a file
already exists at its old place it stays, and the restored copy gets the round
in its name.

## Install

Arch Linux (AUR):

```sh
yay -S whysync
systemctl --user enable --now whysync
```

From source: Python 3.11+, `rsync` and a systemd user session. The window also
needs PyGObject with GTK 4 and libadwaita from your distribution (Arch:
`python-gobject gtk4 libadwaita`, Debian/Ubuntu: `python3-gi gir1.2-adw-1`).

```sh
git clone https://github.com/byrdltd/whySYNC && cd whySYNC
./install.sh
```

## Use

```sh
whysync gui              # the window; also in the app menu as whySYNC
whysync add /run/media/someone/Photos /mnt/backup/Photos
whysync                  # status
whysync sync photos      # sync now
whysync approve photos   # review and approve a held round
whysync adopt photos     # first round: keep target-only files by copying them into the source
whysync set photos --max-deletes 100 --exclude '*.tmp'   # show or change a pair's settings
whysync repair photos    # copy again files whose contents differ from the source
whysync pause photos | resume photos | remove photos
whysync trash photos     # deleted / overwritten files, round by round
whysync restore photos 2026-09-23T16-02-03 [PATH...]
journalctl --user -u whysync -f
```

Each pair has its own settings, in the window (Settings, inside a pair) or with
`whysync set`: the deletion limit, how long the trash is kept, after how many
days without a sync to warn, how often to compare contents, and patterns to
skip. The folders themselves do not change; other folders are a new pair.

Settings live in `~/.config/whysync/pairs.json`; the service picks up changes
within a couple of seconds. Changes between versions: [CHANGELOG](CHANGELOG.md).

## Languages

Commands and notifications are available in English and Turkish, following
the system locale or the `language` field in `pairs.json`.

## License

MIT
