"""Archive the public Cornucopia Stage widget; preserve unconfirmed timestamp zone."""
import csv
import json
import math
import os
from datetime import datetime
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API_URL = "https://api.datacake.co/graphql/?op=fetchPublicDeviceDashboardData"
DASHBOARD_ID = "cbfbd949-a2b9-4886-b825-cc7c3f354b50"
WIDGET_ID = "30d35fea-9a9d-47b3-a879-34e4befa24d1"
CSV_FILE = Path(__file__).resolve().parent / "Cornucopia_Water_Level_All.csv"
QUERY = """
query fetchPublicDeviceDashboardData($id: String!, $token: String!,
    $widgetIds: [String!], $dashboardConfig: JSONString) {
  publicDevice(id: $id, token: $token) {
    dashboardData(dashboardConfig: $dashboardConfig, widgetIds: $widgetIds)
  }
}
"""


def find_series(node, path="root"):
    """Find timestamp/value arrays only inside the requested Stage widget."""
    found = []
    if isinstance(node, list):
        if node and all(isinstance(row, list) and len(row) >= 2
                        and isinstance(row[0], str)
                        and len(row[0]) > 10 and row[0][10] == "T"
                        for row in node):
            return [(path, node)]
        for i, child in enumerate(node):
            found.extend(find_series(child, f"{path}[{i}]"))
    elif isinstance(node, dict):
        for key, child in node.items():
            found.extend(find_series(child, f"{path}.{key}"))
    return found


def timestamp_key(value):
    # Do not attach UTC to timestamps that lack an explicit offset.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def merge_save(series, csv_file=CSV_FILE):
    rows = {}
    if csv_file.exists():
        with csv_file.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != ["Time", "Stage_value"]:
                raise ValueError("Existing CSV must have columns Time,Stage_value")
            for row in reader:
                rows[timestamp_key(row["Time"])] = row
    valid = 0
    for timestamp, value, *_ in series:
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError("Unexpected boolean Stage value")
        value = float(value)
        if not math.isfinite(value):
            continue
        key = timestamp_key(timestamp)
        rows[key] = {"Time": timestamp, "Stage_value": value}
        valid += 1
    if not valid:
        raise ValueError("No valid measurements returned; master CSV was not changed")
    # Sorting rejects a mixture of offset-aware and naive timestamps instead
    # of silently merging incompatible time conventions.
    ordered = [rows[key] for key in sorted(rows)]
    temp = csv_file.with_suffix(".csv.tmp")
    with temp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Time", "Stage_value"])
        writer.writeheader()
        writer.writerows(ordered)
    os.replace(temp, csv_file)
    print(f"Downloaded {valid}; master contains {len(ordered)} measurements")
    print(f"Range: {ordered[0]['Time']} to {ordered[-1]['Time']}")


def main():
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=Retry(
        total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["POST"])))
    response = session.post(API_URL, json={
        "operationName": "fetchPublicDeviceDashboardData",
        "variables": {"id": DASHBOARD_ID, "token": "", "widgetIds": [WIDGET_ID]},
        "query": QUERY,
    }, timeout=(15, 120))
    response.raise_for_status()
    result = response.json()
    if result.get("errors"):
        raise RuntimeError(f"GraphQL errors: {result['errors']}")
    device = (result.get("data") or {}).get("publicDevice")
    if not device or "dashboardData" not in device:
        raise RuntimeError("No public dashboard data returned")
    data = device["dashboardData"]
    if isinstance(data, str):
        data = json.loads(data)
    candidates = find_series(data)
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected one Stage series, found {len(candidates)}. "
            f"Paths: {[path for path, _ in candidates]}. Master CSV unchanged."
        )
    path, series = candidates[0]
    print(f"Cornucopia Stage widget series: {path}")
    print("Units, datum and timestamp zone remain unconfirmed.")
    merge_save(series)


if __name__ == "__main__":
    main()
