import importlib.util
import subprocess
import sys

# --- Pre-run Check: Detect and Offer to Install Required Packages ---
REQUIRED_PACKAGES = {
    "skyfield": "skyfield",
    "tzdata": "tzdata",
}

missing = [pkg for module, pkg in REQUIRED_PACKAGES.items() if importlib.util.find_spec(module) is None]

if missing:
    pkg_list = ", ".join(missing)
    print(f"Missing required package(s): {pkg_list}")
    choice = input("Would you like to install them now? [Y/n]: ").strip().lower()
    if choice in ("", "y", "yes"):
        print(f"Installing {pkg_list} via pip...")
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("Installation complete!\n")
        except subprocess.CalledProcessError as err:
            sys.exit(f"Error during package installation: {err}")
    else:
        sys.exit("Cannot proceed without required dependencies. Exiting.")

import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from skyfield.api import load, wgs84
import json
import shutil
import urllib.request

# Try importing TimezoneFinder safely (handles missing h3 or broken C builds on mobile)
try:
    from timezonefinder import TimezoneFinder
    HAS_TZFINDER = True
except (ImportError, ModuleNotFoundError):
    HAS_TZFINDER = False

# Configure urllib opener with custom User-Agent to prevent CelesTrak 403 Forbidden
opener = urllib.request.build_opener()
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]
urllib.request.install_opener(opener)


def get_current_location():
    """Detects location via Termux hardware GPS, falling back to IP geolocation.

    Returns (lat, lon, elevation_m, location_name)
    """
    if shutil.which("termux-location"):
        try:
            cmd = ["termux-location", "-p", "network", "-r", "last"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                lat = data.get("latitude")
                lon = data.get("longitude")
                alt = data.get("altitude", 280.0)
                if lat is not None and lon is not None:
                    return lat, lon, alt, "Device Location (GPS/Network)"
        except Exception:
            pass

    try:
        req = urllib.request.Request(
            "https://ipapi.co/json/",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=4) as response:
            data = json.loads(response.read().decode())
            lat = data.get("latitude")
            lon = data.get("longitude")
            city = data.get("city", "Unknown")
            region = data.get("region_code", "")
            loc_name = f"{city}, {region} (IP Geolocation)"
            if lat is not None and lon is not None:
                return float(lat), float(lon), 280.0, loc_name
    except Exception:
        pass

    return 39.7294, -84.0633, 280.0, "Beavercreek, OH (Default)"


def get_compass_16(degrees):
    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]
    index = int((degrees + 11.25) // 22.5) % 16
    return directions[index]


def predict_passes(days: int = 30, min_alt: float = 15.0, start_hours: float = 0.0, show_sun: bool = False, show_az: bool = False, show_all: bool = False, auto_loc: bool = False):
    # --- 1. Observer Location Setup ---
    if auto_loc:
        lat, lon, elev, loc_desc = get_current_location()
    else:
        lat, lon, elev, loc_desc = 39.7294, -84.0633, 280.0, "Beavercreek, OH (Manual)"

    my_location = wgs84.latlon(lat, lon, elevation_m=elev)

    # --- Timezone Detection with Fallback ---
    tz_name = None
    if HAS_TZFINDER:
        try:
            tf = TimezoneFinder()
            tz_name = tf.timezone_at(lat=lat, lng=lon)
        except Exception:
            pass

    if not tz_name:
        tz_name = "America/New_York"

    local_tz = ZoneInfo(tz_name)

    # --- 2. Load Ephemeris & ISS TLE Data ---
    ts = load.timescale()
    t_now = ts.now()

    t_start = ts.utc(t_now.utc_datetime() + timedelta(hours=start_hours))
    t_end = ts.utc(t_start.utc_datetime() + timedelta(days=days))

    stations_url = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle'
    satellites = load.tle_file(stations_url, filename='stations.tle', reload=False)
    by_name = {sat.name: sat for sat in satellites}
    iss = by_name['ISS (ZARYA)']

    eph = load('de421.bsp')
    earth, sun = eph['earth'], eph['sun']

    now_local = t_now.utc_datetime().astimezone(local_tz)
    start_local = t_start.utc_datetime().astimezone(local_tz)
    end_local = t_end.utc_datetime().astimezone(local_tz)

    mode_str = "All Geometric Passes (--all enabled)" if show_all else "Visible Passes (Night + Sunlit ISS)"
    print(f"=== ISS PASS PREDICTIONS ({'ALL' if show_all else 'VISIBLE'}) ===")
    print(f"Observer Location: {loc_desc} [{lat:.4f}°, {lon:.4f}°]")
    print(f"Current Time     : {now_local.strftime('%b %d, %Y, %I:%M %p %Z')}")
    print(f"Search Window    : {start_local.strftime('%b %d %I:%M %p')} -> {end_local.strftime('%b %d %I:%M %p %Z')} ({days} days)")
    print(f"Timezone         : {tz_name}")
    print(f"Mode             : {mode_str}")
    print(f"Filter           : Max Alt >= {min_alt}°")
    print("-" * 55)

    # --- 3. Find All Pass Geometry ---
    times, events = iss.find_events(my_location, t_start, t_end, altitude_degrees=10.0)

    reported_pass_count = 0

    i = 0
    while i < len(times):
        if events[i] == 0:
            t_rise = times[i]
            t_peak = times[i+1] if (i+1 < len(times) and events[i+1] == 1) else None
            t_set = times[i+2] if (i+2 < len(times) and events[i+2] == 2) else None

            if t_peak is not None and t_set is not None:
                keep_pass = (t_set.tt >= t_now.tt) if start_hours >= 0 else True

                if keep_pass:
                    topocentric_peak = (iss - my_location).at(t_peak)
                    alt_peak, az_peak, _ = topocentric_peak.altaz()

                    is_sunlit = iss.at(t_peak).is_sunlit(eph)
                    observer_sun = (earth + my_location).at(t_peak).observe(sun).apparent()
                    sun_alt, _, _ = observer_sun.altaz()

                    is_visible = is_sunlit and (sun_alt.degrees < -6.0)

                    if (alt_peak.degrees >= min_alt) and (show_all or is_visible):
                        reported_pass_count += 1

                        _, az_rise, _ = (iss - my_location).at(t_rise).altaz()
                        dir_rise = get_compass_16(az_rise.degrees)
                        dir_peak = get_compass_16(az_peak.degrees)

                        if show_az:
                            dir_rise += f" ({az_rise.degrees:.1f}°)"
                            dir_peak += f" ({az_peak.degrees:.1f}°)"

                        dt_rise_local = t_rise.utc_datetime().astimezone(local_tz)
                        date_header = dt_rise_local.strftime('%a %b %d, %Y, %I:%M %p %Z')

                        if t_set.tt < t_now.tt:
                            status_tag = " [COMPLETED / PAST]"
                        elif t_rise.tt <= t_now.tt <= t_set.tt:
                            status_tag = " [IN PROGRESS NOW]"
                        else:
                            status_tag = ""

                        _, az_set, _ = (iss - my_location).at(t_set).altaz()
                        dir_set = get_compass_16(az_set.degrees)
                        if show_az:
                            dir_set += f" ({az_set.degrees:.1f}°)"

                        duration_sec = int((t_set - t_rise) * 86400)
                        duration_str = f"{round(duration_sec / 60)} min" if duration_sec >= 60 else f"{duration_sec} sec"

                        if is_visible:
                            vis_label = "VISIBLE (Sunlit at Night)"
                        elif not is_sunlit:
                            vis_label = "NOT VISIBLE (In Earth's Shadow)"
                        else:
                            vis_label = "NOT VISIBLE (Daylight)"

                        print(f"#{reported_pass_count} | {date_header}{status_tag}")
                        if show_all:
                            print(f"  Condition : {vis_label}")
                        print(f"  Duration  : {duration_str}")
                        print(f"  Max Alt   : {alt_peak.degrees:.0f}° ({dir_peak})")
                        print(f"  Appears   : {dir_rise}")
                        print(f"  Disappears: {dir_set}")

                        if show_sun:
                            print(f"  Sun Alt   : {sun_alt.degrees:.1f}°")

                        print("-" * 55)

            i += 3
        else:
            i += 1

    label = "passes" if show_all else "visible passes"
    print(f"\nSummary: Found {reported_pass_count} {label} in {days} days.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Predict International Space Station (ISS) passes."
    )

    parser.add_argument(
        "-d", "--days",
        type=int,
        default=30,
        help="Number of days to search ahead (default: 30)"
    )
    parser.add_argument(
        "-s", "--start",
        type=float,
        default=0.0,
        help="Search start offset in hours relative to current time (e.g. -4 for 4 hours ago, default: 0.0)"
    )
    parser.add_argument(
        "-a", "--alt",
        type=float,
        default=15.0,
        help="Minimum required peak elevation angle in degrees (default: 15.0)"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Show all geometric passes including daylight and eclipsed passes"
    )
    parser.add_argument(
        "--az",
        action="store_true",
        help="Display numerical azimuth degrees alongside compass directions"
    )
    parser.add_argument(
        "--sun",
        action="store_true",
        help="Display the Sun altitude angle in the pass output"
    )
    parser.add_argument(
        "--auto-loc",
        action="store_true",
        help="Automatically detect location (uses Termux GPS on mobile, IP lookup on desktop)"
    )

    args = parser.parse_args()

    predict_passes(
        days=args.days,
        min_alt=args.alt,
        start_hours=args.start,
        show_sun=args.sun,
        show_az=args.az,
        show_all=args.all,
        auto_loc=args.auto_loc
    )
