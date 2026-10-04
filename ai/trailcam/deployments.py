"""Where each camera was, and when: the two CSVs into the manifest.

A frame knows which camera took it and what that camera's clock said.  It
does not know where the camera was, and `sites-design.md` explains why the
camera cannot tell us: a Pi Zero has no battery-backed clock, so the 24
September cards filed a two-day campout under a week-old folder.  The
facts that put a frame somewhere are kept by hand, in two files at the top
of `ai/`:

    sites.csv         the places: slug, name, coordinates, geoprivacy
    deployments.csv   one camera at one site for one stretch

A deployment is keyed by the camera's `boot` id where it wrote one, and by
a date range on the camera's own clock where it did not (the cameras from
before the campout).  `load` copies both files into the manifest and
derives the one number nobody should compute twice: `clock_offset_s`, the
correction for a boot whose clock was wrong, from the `true_start` a
person wrote down and the uptime of the first frame we hold.  Then
`place_frames` stamps every frame with its deployment.

Everything here returns data and messages; `cli.py` does the printing.
The true coordinates of the back garden are read from the gitignored
overlay `config.SITES_PRIVATE_CSV` and go into the database on this
machine, never into the repository.
"""

import csv

from datetime import datetime, timedelta

from . import config


# ------------------------------------------------------------
# Reading the CSVs
# ------------------------------------------------------------

def _text(value):
    value = (value or "").strip()
    return value or None


def _number(value, kind=float):
    value = _text(value)
    return kind(value) if value is not None else None


def read_sites(path):
    """{slug: {...}} from sites.csv, coordinates as floats or None."""
    sites = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            slug = _text(row.get("slug"))
            if not slug:
                continue
            sites[slug] = {
                "slug": slug,
                "name": _text(row.get("name")) or slug,
                "description": _text(row.get("description")),
                "aliases": [a.strip() for a in (row.get("aliases") or "")
                            .split(";") if a.strip()],
                "latitude": _number(row.get("latitude")),
                "longitude": _number(row.get("longitude")),
                "uncertainty_m": _number(row.get("uncertainty_m")),
                "geoprivacy": _text(row.get("geoprivacy")) or "open",
            }
    return sites


def read_private_sites(path):
    """{slug: (latitude, longitude, uncertainty_m)} from the overlay, or
    {} when there is none -- a fresh clone has none, and that is fine
    until something needs a coordinate for an obscured site."""
    sites = {}
    try:
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                slug = _text(row.get("slug"))
                if slug:
                    sites[slug] = (float(row["latitude"]),
                                   float(row["longitude"]),
                                   _number(row.get("uncertainty_m")) or 0.0)
    except FileNotFoundError:
        pass
    return sites


def read_deployments(path):
    """The rows of deployments.csv, typed.  Blank boot means a date range."""
    rows = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            camera = _text(row.get("camera"))
            if not camera:
                continue
            rows.append({
                "camera": camera,
                "boot": _text(row.get("boot")),
                "site": _text(row.get("site")),
                "code": _text(row.get("code")),
                "clock_first": _text(row.get("clock_first")),
                "clock_last": _text(row.get("clock_last")),
                "photos": _number(row.get("photos"), int),
                "evidence": _text(row.get("evidence")),
                "true_start": _text(row.get("true_start")),
                "time_uncertainty_s":
                    _number(row.get("time_uncertainty_s")) or 0.0,
                "camera_model": _text(row.get("camera_model")),
                "rotation": _number(row.get("rotation"), int) or 0,
                "baited": _number(row.get("baited"), int) or 0,
                "range_start": _text(row.get("range_start")),
                "range_end": _text(row.get("range_end")),
            })
    return rows


# ------------------------------------------------------------
# The clock
# ------------------------------------------------------------

def _when(value):
    return value if isinstance(value, datetime) \
        else datetime.fromisoformat(value)


def clock_offset_s(true_start, clock_first, uptime_at_clock_first):
    """Seconds to add to the camera's clock to get the true time.

    The camera booted at `clock_first - uptime` on its own clock and at
    `true_start` in fact; the difference is the same for every frame of
    the boot, because within a boot the clock advances with uptime.  So
    one number corrects the whole boot, including frames that have a boot
    but no uptime of their own.  Positive when the clock was behind.
    """
    booted_on_clock = _when(clock_first) - timedelta(
        seconds=float(uptime_at_clock_first))
    return (_when(true_start) - booted_on_clock).total_seconds()


def _first_frame(database, camera, boot):
    """The earliest frame we hold for a boot, by uptime: (captured_at,
    uptime_s), or None if the manifest has no frame with an uptime."""
    return database.execute(
        """SELECT captured_at, uptime_s FROM frames
            WHERE camera = ? AND boot = ? AND uptime_s IS NOT NULL
            ORDER BY uptime_s LIMIT 1""", (camera, boot)).fetchone()


# ------------------------------------------------------------
# Loading
# ------------------------------------------------------------

def load(database, sites_csv=None, deployments_csv=None, private_csv=None):
    """Upsert the sites and deployments.  Returns
    {"sites": n, "deployments": n, "warnings": [str, ...]}.

    Warnings rather than errors for the things a person should look at
    but that should not stop the load: an obscured site with no
    coordinate (the overlay is missing), a boot the manifest has not
    scanned yet, a `clock_first` that disagrees with the frames.  A site
    named in deployments.csv that is not in sites.csv is a typo, and that
    one is an error.
    """
    warnings = []

    sites = read_sites(sites_csv or config.SITES_CSV)
    private = read_private_sites(private_csv or config.SITES_PRIVATE_CSV)

    for slug, site in sites.items():
        if slug in private:
            # The overlay is the one place the true coordinate lives, so
            # it wins over anything the public file says.
            (site["latitude"], site["longitude"],
             site["uncertainty_m"]) = private[slug]
        if site["geoprivacy"] == "obscured" and site["latitude"] is None:
            warnings.append(
                f"site {slug} is obscured but has no coordinate: nothing "
                f"from it can be published until {config.SITES_PRIVATE_CSV} "
                f"supplies one")

        database.execute(
            """INSERT INTO sites (slug, name, description, aliases,
                                  latitude, longitude, uncertainty_m,
                                  geoprivacy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(slug) DO UPDATE SET
                   name = excluded.name, description = excluded.description,
                   aliases = excluded.aliases, latitude = excluded.latitude,
                   longitude = excluded.longitude,
                   uncertainty_m = excluded.uncertainty_m,
                   geoprivacy = excluded.geoprivacy""",
            (slug, site["name"], site["description"],
             ";".join(site["aliases"]) or None, site["latitude"],
             site["longitude"], site["uncertainty_m"], site["geoprivacy"]))

    rows = read_deployments(deployments_csv or config.DEPLOYMENTS_CSV)
    for row in rows:
        label = f"{row['camera']}/{row['boot'] or row['range_start']}"

        if row["site"] not in sites:
            raise ValueError(f"deployments.csv: {label} is at site "
                             f"{row['site']!r}, which sites.csv does not "
                             f"have")
        if row["boot"] is None and not (row["range_start"]
                                        and row["range_end"]):
            raise ValueError(f"deployments.csv: {label} has no boot, so "
                             f"it needs range_start and range_end")
        if not row["camera_model"]:
            raise ValueError(f"deployments.csv: {label} has no camera_model")

        offset = None
        if row["boot"]:
            first = _first_frame(database, row["camera"], row["boot"])
            earliest = database.execute(
                "SELECT MIN(captured_at) FROM frames WHERE camera = ? "
                "AND boot = ?", (row["camera"], row["boot"])).fetchone()[0]

            if row["true_start"]:
                if first is None:
                    warnings.append(
                        f"{label}: true_start is set but the manifest has "
                        f"no frame with an uptime for that boot, so no "
                        f"clock offset could be derived")
                else:
                    offset = clock_offset_s(row["true_start"],
                                            first["captured_at"],
                                            first["uptime_s"])

            if earliest is None:
                warnings.append(f"{label}: no frames in the manifest for "
                                f"that boot (not scanned yet?)")
            elif row["clock_first"]:
                # clock_first in the CSV is when the boot began on the
                # camera's clock.  With an uptime we can say the same
                # thing from the frames; without one the first frame is
                # the best we have.
                if first is not None:
                    booted = (_when(first["captured_at"])
                              - timedelta(seconds=first["uptime_s"]))
                else:
                    booted = _when(earliest)
                gap = abs((booted - _when(row["clock_first"]))
                          .total_seconds())
                if gap > config.DEPLOYMENT_CLOCK_TOLERANCE_S:
                    warnings.append(
                        f"{label}: clock_first {row['clock_first']} is "
                        f"{gap:.0f} s from what the frames say "
                        f"({booted.isoformat(timespec='seconds')})")

        _upsert_deployment(database, row, offset)

    database.commit()
    return {"sites": len(sites), "deployments": len(rows),
            "warnings": warnings}


def _upsert_deployment(database, row, offset):
    """One row in, matched on (camera, boot) or, for a date-range row,
    on (camera, range_start).  The UNIQUE constraint cannot do the second
    for us because SQLite treats every NULL boot as distinct."""
    if row["boot"]:
        existing = database.execute(
            "SELECT id FROM deployments WHERE camera = ? AND boot = ?",
            (row["camera"], row["boot"])).fetchone()
    else:
        existing = database.execute(
            "SELECT id FROM deployments WHERE camera = ? AND boot IS NULL "
            "AND range_start = ?",
            (row["camera"], row["range_start"])).fetchone()

    values = (row["camera"], row["site"], row["boot"], row["range_start"],
              row["range_end"], row["clock_first"], row["clock_last"],
              row["true_start"], offset, row["time_uncertainty_s"],
              row["camera_model"], row["rotation"], row["baited"],
              row["code"], row["photos"], row["evidence"])

    if existing:
        database.execute(
            """UPDATE deployments SET
                   camera = ?, site = ?, boot = ?, range_start = ?,
                   range_end = ?, clock_first = ?, clock_last = ?,
                   true_start = ?, clock_offset_s = ?,
                   time_uncertainty_s = ?, camera_model = ?, rotation = ?,
                   baited = ?, code = ?, photos = ?, evidence = ?
             WHERE id = ?""", values + (existing["id"],))
    else:
        database.execute(
            """INSERT INTO deployments
                   (camera, site, boot, range_start, range_end,
                    clock_first, clock_last, true_start, clock_offset_s,
                    time_uncertainty_s, camera_model, rotation, baited,
                    code, photos, evidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            values)


# ------------------------------------------------------------
# Placing frames
# ------------------------------------------------------------

def place_frames(database):
    """Stamp frames.deployment_id from scratch.

    A boot row wins: a frame whose camera and boot match one belongs to
    it whatever its clock says.  Otherwise the camera's date-range rows
    are tried against `captured_at`, a range_end given as a bare date
    meaning the whole of that day.  Anything left is unplaced, and the
    caller prints those per camera and day the way `scan` prints frames
    with no CSV row.

    Returns (placed, unplaced) with unplaced a {(camera, day): count}.
    Re-stamping everything each time is what makes editing the CSV and
    running `load` again the whole procedure.
    """
    database.execute("UPDATE frames SET deployment_id = NULL")

    database.execute("""
        UPDATE frames SET deployment_id =
            (SELECT d.id FROM deployments d
              WHERE d.camera = frames.camera AND d.boot = frames.boot)
         WHERE boot IS NOT NULL
    """)

    database.execute("""
        UPDATE frames SET deployment_id =
            (SELECT d.id FROM deployments d
              WHERE d.camera = frames.camera AND d.boot IS NULL
                AND frames.captured_at >= d.range_start
                AND frames.captured_at <
                    CASE WHEN length(d.range_end) = 10
                         THEN date(d.range_end, '+1 day')
                         ELSE d.range_end END
              ORDER BY d.range_start LIMIT 1)
         WHERE deployment_id IS NULL
    """)
    database.commit()

    placed = database.execute(
        "SELECT COUNT(*) FROM frames WHERE deployment_id IS NOT NULL"
    ).fetchone()[0]
    unplaced = {(row["camera"], row["day"]): row["n"]
                for row in database.execute(
                    """SELECT camera, day, COUNT(*) AS n FROM frames
                        WHERE deployment_id IS NULL
                        GROUP BY camera, day ORDER BY camera, day""")}
    return placed, unplaced


def summary(database):
    """Every deployment with how many frames it holds, for `show`."""
    return database.execute(
        """SELECT d.*,
                  (SELECT COUNT(*) FROM frames f
                    WHERE f.deployment_id = d.id) AS frames
             FROM deployments d
            ORDER BY d.site, d.camera,
                     COALESCE(d.range_start, d.clock_first), d.id"""
    ).fetchall()
