"""iNaturalist: credentials, and the fixes and uploads that use them.

The first command here exists because of a mistake.  The seventy
observations uploaded by hand in the first days of October were all
submitted with open geoprivacy, including the twenty-five from the back
garden, whose place name on the public page was the street address.  The
upload instructions said "Obscured"; the setting did not take.
`fix_geoprivacy` reads every observation the account has, finds the ones
whose true position lies within `OBSCURE_RADIUS_M` of a site marked
obscured in the private overlay, and sets geoprivacy on the ones that are
not already obscured.  It prints what it would do before doing it, and
`--dry-run` stops there.

Everything that talks to iNaturalist goes through a small `Api` object so
the logic can be tested against a fake.  `pyinaturalist` is imported
inside the functions that need it: the detector pass and the test suite
do not require it (pyproject's optional `publish` extra installs it).

Credentials live in the system keyring, written once by `login`, and
nowhere in this repository.
"""

import csv
import json
import getpass
import math

from . import config


# ------------------------------------------------------------
# Sites with private coordinates
# ------------------------------------------------------------

def read_private_sites(path=None):
    """{slug: (latitude, longitude, uncertainty_m)} from the gitignored
    overlay, or {} if there is none."""
    path = path or config.SITES_PRIVATE_CSV
    sites = {}
    try:
        with open(path, newline="") as handle:
            for row in csv.DictReader(handle):
                sites[row["slug"]] = (float(row["latitude"]),
                                      float(row["longitude"]),
                                      float(row["uncertainty_m"] or 0))
    except FileNotFoundError:
        pass
    return sites


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance, metres.  Haversine; good to a metre at these
    distances, which is a thousand times better than needed."""
    radius = 6_371_000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlam = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * radius * math.asin(math.sqrt(a))


def needs_obscuring(observations, centres, radius_m=None):
    """The observations near an obscured site whose geoprivacy is not
    already `obscured`.

    `observations` are dicts as the API returns them to their owner, with
    `location` the TRUE "lat,lon" string (the owner sees true
    coordinates even on obscured observations) and `geoprivacy` one of
    None, 'open', 'obscured', 'private'.  `centres` is a list of
    (latitude, longitude).  Pure, so it is tested without the network.
    """
    radius_m = radius_m or config.OBSCURE_RADIUS_M
    chosen = []
    for observation in observations:
        location = observation.get("location")
        if not location:
            continue
        lat, lon = (float(part) for part in location.split(","))
        near = any(distance_m(lat, lon, clat, clon) <= radius_m
                   for clat, clon in centres)
        if near and observation.get("geoprivacy") != "obscured":
            chosen.append(observation)
    return chosen


# ------------------------------------------------------------
# The API, behind one small object
# ------------------------------------------------------------

class Api:
    """The handful of iNaturalist calls this module makes, bound to one
    access token.  Tests pass a fake with the same method names."""

    def __init__(self, access_token=None):
        import pyinaturalist                     # noqa: F401  (import check)
        from pyinaturalist import get_access_token
        self.token = access_token or get_access_token()

    def my_observations(self, user_id):
        """Every observation of one user, authenticated, so `location` is
        the true position and `geoprivacy` is filled in."""
        from pyinaturalist import get_observations
        page, results = 1, []
        while True:
            response = get_observations(user_id=user_id, per_page=200,
                                        page=page, access_token=self.token)
            batch = response.get("results", [])
            results.extend(batch)
            if len(batch) < 200:
                return results
            page += 1

    def set_geoprivacy(self, observation_id, geoprivacy):
        from pyinaturalist import update_observation
        # pyinaturalist sets ignore_photos=1 on updates by default, so this
        # touches nothing but the one field.
        return update_observation(observation_id, geoprivacy=geoprivacy,
                                  access_token=self.token)


# ------------------------------------------------------------
# Commands
# ------------------------------------------------------------

def login():
    """Store the account's credentials in the system keyring, once.

    Needs an OAuth application registered at
    https://www.inaturalist.org/oauth/applications/new (any name, any
    redirect URL; "confidential" unticked).  The app id and secret identify
    this script; the username and password are the account's own.  All
    four go to the keyring and nowhere else, and the token is fetched once
    here to prove they work.
    """
    from pyinaturalist import get_access_token
    from pyinaturalist.auth import set_keyring_credentials

    username = input("iNaturalist username: ").strip()
    password = getpass.getpass("iNaturalist password: ")
    app_id = input("OAuth application id: ").strip()
    app_secret = getpass.getpass("OAuth application secret: ")

    set_keyring_credentials(username, password, app_id, app_secret)
    get_access_token(refresh=True)
    print("Credentials stored in the keyring and a token fetched: logged in.")
    return 0


def fix_geoprivacy(api=None, dry_run=False, user_id=None, private_sites=None,
                   radius_m=None, out=print):
    """Obscure every observation near an obscured site that is not yet.

    Returns the number changed (or that would be changed, under dry run).
    """
    private_sites = (read_private_sites() if private_sites is None
                     else private_sites)
    centres = [(lat, lon) for lat, lon, _ in private_sites.values()]
    if not centres:
        out(f"No private site coordinates in {config.SITES_PRIVATE_CSV}; "
            f"nothing to compare against.")
        return 0

    api = api or Api()
    observations = api.my_observations(user_id or config.INAT_USER_ID)
    chosen = needs_obscuring(observations, centres, radius_m)

    out(f"{len(observations)} observations on the account, "
        f"{len(chosen)} within {int(radius_m or config.OBSCURE_RADIUS_M)} m "
        f"of an obscured site and not yet obscured.")
    for observation in chosen:
        taxon = (observation.get("taxon") or {}).get("name") or "(no taxon)"
        out(f"  {observation['id']}  {observation.get('observed_on')}  "
            f"{taxon}  geoprivacy={observation.get('geoprivacy')}")

    if dry_run or not chosen:
        if dry_run and chosen:
            out("Dry run: nothing changed.")
        return len(chosen)

    for observation in chosen:
        api.set_geoprivacy(observation["id"], "obscured")
        out(f"  obscured {observation['id']}")

    out(f"Changed {len(chosen)}. Run again to confirm it reports 0.")
    return len(chosen)


# ------------------------------------------------------------
# Recording what is already on iNaturalist
# ------------------------------------------------------------
#
# The first ninety observations were uploaded by hand from a staging
# folder, and nothing here knew which frames they were.  `import_existing`
# reads the account back (the read side of the API needs no token) and
# matches each observation to its frames through the staging folder's
# manifest.csv, which lists every file that was dragged into the uploader
# with the time that was written into its EXIF.  An observation matches a
# staging folder when its Trap ID field names the folder's camera and its
# observed time is within a couple of minutes of the folder's EXIF time.
# Phone photographs have no Trap ID and are left alone.

STAGING_TIME_TOLERANCE_S = 120


def read_staging_manifest(path):
    """{folder: {"camera", "when", "paths": [...]}} from ~/inaturalist/manifest.csv."""
    folders = {}
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            entry = folders.setdefault(row["observation"], {"paths": []})
            entry["paths"].append((int(row["photo"]), row["source_path"]))
            entry["camera"] = row["source_path"].split("/")[0]
            entry["when"] = row["exif_datetime"]        # "YYYY-MM-DD HH:MM:SS"
    for entry in folders.values():
        entry["paths"] = [p for _, p in sorted(entry["paths"])]
    return folders


def observed_at(observation):
    """The observation's local time, naive, from either shape the API
    client gives: the raw API's `time_observed_at` string, or
    pyinaturalist's `observed_on` already parsed to a datetime."""
    from datetime import datetime
    when = observation.get("time_observed_at") or observation.get("observed_on")
    if not when:
        return None
    if isinstance(when, str):
        if len(when) <= 10:
            return None                       # a bare date: no time to match on
        when = datetime.fromisoformat(when)
    return when.replace(tzinfo=None)


def _text(value):
    """created_at and friends as ISO text whatever the client handed back."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _field(observation, field_id):
    for value in observation.get("ofvs") or []:
        if value.get("field_id") == field_id:
            return value.get("value")
    return None


def match_observations(observations, folders, tolerance_s=None):
    """Pair each trail-camera observation with the staging folder it came from.

    Returns (matched, unmatched): matched is a list of (observation,
    folder_name), unmatched the trail-camera observations (those with a
    Trap ID) that no folder explains.  Pure, for the tests.
    """
    from datetime import datetime
    tolerance_s = tolerance_s or STAGING_TIME_TOLERANCE_S
    taken = set()
    matched, unmatched = [], []
    for observation in observations:
        camera = _field(observation, config.INAT_FIELD_TRAP_ID)
        if not camera:
            continue                                   # a phone photograph
        observed = observed_at(observation)
        if observed is None:
            unmatched.append(observation); continue
        best = None
        for name, folder in folders.items():
            if name in taken or folder["camera"] != camera:
                continue
            staged = datetime.fromisoformat(folder["when"])
            gap = abs((observed - staged).total_seconds())
            if gap <= tolerance_s and (best is None or gap < best[0]):
                best = (gap, name)
        if best:
            taken.add(best[1]); matched.append((observation, best[1]))
        else:
            unmatched.append(observation)
    return matched, unmatched


def record_match(database, observation, folder, paths):
    """Write one matched upload into observations/observation_frames/
    publications/taxa.  Idempotent on the remote id."""
    from . import manifest as manifest_module
    remote_id = str(observation["id"])
    if database.execute("SELECT 1 FROM publications WHERE destination='inaturalist' AND remote_id=?",
                        (remote_id,)).fetchone():
        return None
    frames = []
    for position, path in enumerate(paths, start=1):
        row = database.execute("SELECT id, deployment_id FROM frames WHERE path = ?", (path,)).fetchone()
        if row is None:
            raise LookupError(f"{folder}: {path} is not in the manifest")
        detection = database.execute(
            """SELECT d.id FROM detections d JOIN frame_results r ON r.id = d.frame_result_id
                WHERE r.frame_id = ? AND d.category = 'animal' ORDER BY d.confidence DESC LIMIT 1""",
            (row["id"],)).fetchone()
        frames.append((row["id"], position, detection["id"] if detection else None, row["deployment_id"]))
    deployment_id = frames[0][3]
    taxon = observation.get("taxon") or {}
    if taxon.get("id"):
        database.execute(
            """INSERT OR REPLACE INTO taxa (id, name, scientific_name, rank, fetched_at)
               VALUES (?, ?, ?, ?, datetime('now'))""",
            (taxon["id"], taxon.get("preferred_common_name") or taxon.get("name"),
             taxon.get("name"), taxon.get("rank")))
    observation_id = manifest_module.add_observation(
        database, deployment_id, "backfill", taxon_guess=taxon.get("preferred_common_name"),
        notes=observation.get("description"))
    database.execute(
        """UPDATE observations SET taxon_id = ?, status = 'approved', curated_by = ?,
                  curated_at = ? WHERE id = ?""",
        (taxon.get("id"), config.INAT_CURATOR, _text(observation.get("created_at")), observation_id))
    manifest_module.add_observation_frames(database, observation_id, [f[:3] for f in frames])
    publication_id = manifest_module.add_publication(
        database, observation_id, "inaturalist", remote_id,
        remote_url=observation.get("uri"), state="created",
        remote_state=json.dumps({k: observation.get(k) for k in
                                 ("quality_grade", "geoprivacy", "project_ids", "tags")}))
    manifest_module.complete_publication(database, publication_id, len(observation.get("photos") or []))
    database.execute("UPDATE publications SET published_at = ? WHERE id = ?",
                     (_text(observation.get("created_at")), publication_id))
    database.commit()
    return observation_id


def import_existing(database, observations, staging_manifest=None, out=print):
    """Back-fill every hand upload the staging manifest can explain."""
    folders = read_staging_manifest(staging_manifest or config.INAT_STAGING_MANIFEST)
    matched, unmatched = match_observations(observations, folders)
    new = 0
    for observation, folder in matched:
        if record_match(database, observation, folder, folders[folder]["paths"]) is not None:
            new += 1
    trailcam = sum(1 for o in observations if _field(o, config.INAT_FIELD_TRAP_ID))
    out(f"{len(observations)} observations on the account, {trailcam} from the cameras: "
        f"{len(matched)} matched a staging folder ({new} new), {len(unmatched)} unmatched.")
    for observation in unmatched:
        taxon = (observation.get("taxon") or {}).get("preferred_common_name") or "(no taxon)"
        out(f"  unmatched {observation['id']}  {observed_at(observation)}  "
            f"{_field(observation, config.INAT_FIELD_TRAP_ID)}  {taxon}")
    return matched, unmatched


def public_observations(user_id=None):
    """Every observation of one user, from the public API.  No token:
    obscured coordinates come back obscured, which is fine for matching."""
    from pyinaturalist import get_observations
    page, results = 1, []
    while True:
        response = get_observations(user_id=user_id or config.INAT_USER_ID, per_page=200, page=page)
        batch = response.get("results", [])
        results.extend(batch)
        if len(batch) < 200:
            return results
        page += 1
