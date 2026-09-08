import argparse
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from skyfield.api import load, wgs84


def get_compass_16(degrees):
    directions = [
        "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"
    ]
    index = int((degrees + 11.25) // 22.5) % 16
    return directions[index]


def predict_passes(days: int = 30, min_alt: float = 15.0, start_hours: float = 0.0, show_sun: bool = False, show_az: bool = False, show_all: bool = False):
    # --- 1. Observer Location Setup (Beavercreek, OH) ---
    lat, lon = 39.7294, -84.0633
    my_location = wgs84.latlon(lat, lon, elevation_m=280)

    # Automatic dynamic timezone (adjusts for Daylight Saving Time per date)
    local_tz = ZoneInfo("America/New_York")

    # --- 2. Load Ephemeris & ISS TLE Data ---
    ts = load.timescale()
    t_now = ts.now()
    
    # Calculate search start based on the requested start_hours offset
    t_start = ts.utc(t_now.utc_datetime() + timedelta(hours=start_hours))
    t_end = ts.utc(t_start.utc_datetime() + timedelta(days=days))

    stations_url = 'https://celestrak.org/NORAD/elements/gp.php?GROUP=active&FORMAT=tle'
    satellites = load.tle_file(stations_url)
    by_name = {sat.name: sat for sat in satellites}
    iss = by_name['ISS (ZARYA)']

    # Load Sun ephemeris for twilight & illumination checks
    eph = load('de421.bsp')
    earth, sun = eph['earth'], eph['sun']

    now_local = t_now.utc_datetime().astimezone(local_tz)
    start_local = t_start.utc_datetime().astimezone(local_tz)
    end_local = t_end.utc_datetime().astimezone(local_tz)

    mode_str = "All Geometric Passes (--all enabled)" if show_all else "Visible Passes (Night + Sunlit ISS)"
    print(f"=== ISS PASS PREDICTIONS ({'ALL' if show_all else 'VISIBLE'}) - Beavercreek, OH ===")
    print(f"Current Time   : {now_local.strftime('%b %d, %Y, %I:%M %p %Z')}")
    print(f"Search Window  : {start_local.strftime('%b %d %I:%M %p')} -> {end_local.strftime('%b %d %I:%M %p %Z')} ({days} days)")
    print(f"Mode           : {mode_str}")
    print(f"Filter         : Max Alt >= {min_alt}°")
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
                # If searching in the past (start_hours < 0), display past passes.
                # If start_hours >= 0, only display passes that haven't concluded yet.
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
                        date_header = dt_rise_local.strftime('%b %d, %Y, %I:%M %p %Z')

                        # Status tags
                        if t_set.tt < t_now.tt:
                            status_tag = " [COMPLETED / PAST]"
                        elif t_rise.tt <= t_now.tt <= t_set.tt:
                            status_tag = " [IN PROGRESS NOW]"
                        else:
                            status_tag = ""

                        # Calculate duration
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
        description="Predict International Space Station (ISS) passes for Beavercreek, OH."
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

    args = parser.parse_args()

    predict_passes(
        days=args.days, 
        min_alt=args.alt, 
        start_hours=args.start, 
        show_sun=args.sun, 
        show_az=args.az, 
        show_all=args.all
    )
