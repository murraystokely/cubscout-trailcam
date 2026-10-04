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
