
import io
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

# Set up the correct timezone (UTC+3)
utc_plus_3 = timezone(timedelta(hours=3))

# Load XML from local file first, then fallback to URL
xml_content = None
try:
    with open(r'C:\Users\Muhaymn\Desktop\xtckHrCmAy.xml', 'r', encoding='utf-8') as f:
        xml_content = f.read()
    print("Loaded XML from local file.")
except (FileNotFoundError, IOError) as e:
    print(f"Local file not found or could not be read ({e}), falling back to URL.")
    url = 'https://www.open-epg.com/generate/xtckHrCmAy.xml'
    try:
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        xml_content = response.content.decode('utf-8')
        print("Loaded XML from URL.")
    except requests.RequestException as e_req:
        print(f"Failed to fetch XML from URL: {e_req}")
        raise  # Exit if both local and remote sources fail

# Parse XML
try:
    root = ET.fromstring(xml_content)
except ET.ParseError as e:
    print(f"Failed to parse XML: {e}")
    raise

def parse_time_to_utc3(t_str):
    """Parses the time string and converts it to a timezone-aware datetime object in UTC+3."""
    try:
        t_utc = datetime.strptime(t_str[:14], '%Y%m%d%H%M%S')
        t_utc = t_utc.replace(tzinfo=timezone.utc)
        return t_utc.astimezone(utc_plus_3)
    except (ValueError, TypeError) as e:
        print(f"Error parsing time {t_str}: {e}")
        return None

all_programs = []
for programme in root.findall('programme'):
    start_str = programme.attrib.get('start')
    stop_str = programme.attrib.get('stop')

    if not start_str or not stop_str:
        continue

    start_dt = parse_time_to_utc3(start_str)
    stop_dt = parse_time_to_utc3(stop_str)

    if start_dt and stop_dt:
        all_programs.append({
            'channel': programme.attrib.get('channel', ''),
            'start': start_dt,
            'stop': stop_dt,
            'title': programme.findtext('title', default='').strip(),
            'description': programme.findtext('desc', default='').strip()
        })

epg_data = []
today = datetime.now(utc_plus_3).date()

# 1. Try to get programs for today or the future
for prog in all_programs:
    if prog['stop'].date() >= today:
        epg_data.append(prog)

# 2. If no current programs, find the last available day and get its programs
if not epg_data and all_programs:
    print("No current programs found. Falling back to the last available day.")
    latest_start_date = max(p['start'].date() for p in all_programs)
    print(f"Last available day with programs is: {latest_start_date}")
    for prog in all_programs:
        if prog['start'].date() == latest_start_date:
            epg_data.append(prog)

# Format datetime objects into the desired string format
def format_datetime(dt):
    # Format to ISO 8601 with timezone, then add colon in timezone offset
    iso_format_with_colon = dt.strftime('%Y-%m-%dT%H:%M:%S%z')
    return iso_format_with_colon[:-2] + ':' + iso_format_with_colon[-2:]

output_data = []
for prog in epg_data:
    prog_copy = prog.copy()
    prog_copy['start'] = format_datetime(prog_copy['start'])
    prog_copy['stop'] = format_datetime(prog_copy['stop'])
    output_data.append(prog_copy)

# 3. Check if there is any data to write to the file
if not output_data:
    print("No valid programs found to generate JSON file. Exiting gracefully.")
    exit(0) # Exit without an error

# 4. Save as JSON
try:
    with io.open('epg-pro.json', 'w', encoding='utf-8') as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"EPG JSON generated successfully with {len(output_data)} programs.")
except IOError as e:
    print(f"Failed to write JSON file: {e}")
    raise
