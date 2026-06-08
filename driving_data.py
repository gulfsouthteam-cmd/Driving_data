"""
OneStep GPS Day Start/End Breakdown — JSON parser + Flask endpoint.
Returns two endpoints:
  /process/stops — stop events at named zones/addresses
  /process/summary — daily summary per device
"""

from __future__ import annotations

import logging
import os
import re
import json
from flask import Flask, jsonify, request

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = Flask(__name__)

API_KEY = os.environ.get("PIPELINE_API_KEY")


def check_auth():
    if API_KEY and request.headers.get("X-Api-Key") != API_KEY:
        return False
    return True


def get_file_bytes():
    upload = request.files.get("file")
    if upload:
        return upload.read()
    return None


@app.route("/process/stops", methods=["POST"])
def process_stops():
    if not check_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    file_bytes = get_file_bytes()
    if not file_bytes:
        return jsonify({"ok": False, "error": "missing file"}), 400
    try:
        data = json.loads(file_bytes)
        rows = parse_stops(data)
    except Exception as e:
        log.exception("parse error")
        return jsonify({"ok": False, "error": str(e)}), 500
    log.info("stops parsed rows=%d", len(rows))
    return jsonify(rows), 200


@app.route("/process/summary", methods=["POST"])
def process_summary():
    if not check_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    file_bytes = get_file_bytes()
    if not file_bytes:
        return jsonify({"ok": False, "error": "missing file"}), 400
    try:
        data = json.loads(file_bytes)
        rows = parse_summary(data)
    except Exception as e:
        log.exception("parse error")
        return jsonify({"ok": False, "error": str(e)}), 500
    log.info("summary parsed rows=%d", len(rows))
    return jsonify(rows), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "gps-day-start-end-parser"}), 200


def parse_stops(data: dict) -> list:
    tables = data.get("tables", [])
    rows = []

    for table in tables:
        header = table.get("header", {})
        device = header.get("entity_name", "")
        date = header.get("date", "")
        entity_groups = header.get("entity_groups", "")

        if date == "01/01/0001":
            continue

        for record in table.get("record_list", []):
            status = record.get("status", "")

            if status != "stop":
                continue

            start_time = record.get("start_time", "")
            end_time = record.get("end_time", "")
            duration_s = record.get("duration", {}).get("value", 0) or 0
            duration_min = round(duration_s / 60, 2)
            zone_names = record.get("zone_names") or []
            zone = ", ".join(zone_names) if zone_names else ""
            job_number = _extract_job_number(zone)
            address = record.get("address", "") or ""
            engine_idle_s = record.get("engine_idle", {}).get("value", 0) or 0
            engine_idle_min = round(engine_idle_s / 60, 2)
            driver_names = record.get("driver_names") or []
            driver = ", ".join(driver_names) if driver_names else ""
            upsert_key = f"{device}_{date}_{start_time}"

            rows.append({
                "upsert_key": upsert_key,
                "device": device,
                "entity_groups": entity_groups,
                "date": date,
                "start_time": start_time,
                "end_time": end_time,
                "duration_min": duration_min,
                "zone": zone,
                "job_number": job_number,
                "address": address,
                "engine_idle_min": engine_idle_min,
                "driver": driver,
            })

    return rows


def parse_summary(data: dict) -> list:
    tables = data.get("tables", [])
    rows = []

    for table in tables:
        header = table.get("header", {})
        device = header.get("entity_name", "")
        date = header.get("date", "")
        entity_groups = header.get("entity_groups", "")

        if date == "01/01/0001":
            continue

        footer = table.get("footer", {})

        total_distance_mi = footer.get("total_distance", {}).get("value", 0) or 0
        total_driving_s = footer.get("total_time_driving", {}).get("value", 0) or 0
        total_driving_min = round(total_driving_s / 60, 2)
        total_stopped_s = footer.get("total_time_stopped", {}).get("value", 0) or 0
        total_stopped_min = round(total_stopped_s / 60, 2)
        total_worked_s = footer.get("total_time_worked", {}).get("value", 0) or 0
        total_worked_min = round(total_worked_s / 60, 2)
        number_stops = footer.get("number_stops", 0) or 0
        start_time = footer.get("start_time", "")
        end_time = footer.get("end_time", "")
        start_address = footer.get("start_address", "") or ""
        end_address = footer.get("end_address", "") or ""
        engine_idle_s = footer.get("total_engine_idle", {}).get("value", 0) or 0
        engine_idle_min = round(engine_idle_s / 60, 2)
        stopped_idle_s = footer.get("total_stopped_engine_idle", {}).get("value", 0) or 0
        stopped_idle_min = round(stopped_idle_s / 60, 2)

        upsert_key = f"{device}_{date}"

        rows.append({
            "upsert_key": upsert_key,
            "device": device,
            "entity_groups": entity_groups,
            "date": date,
            "start_time": start_time,
            "end_time": end_time,
            "start_address": start_address,
            "end_address": end_address,
            "total_distance_mi": round(float(total_distance_mi), 2),
            "total_driving_min": total_driving_min,
            "total_stopped_min": total_stopped_min,
            "total_worked_min": total_worked_min,
            "number_stops": number_stops,
            "engine_idle_min": engine_idle_min,
            "stopped_idle_min": stopped_idle_min,
        })

    return rows


def _extract_job_number(zone: str) -> str:
    if not zone:
        return ""
    # Pattern 1: trailing number after - or # (e.g. "Job Name - 3921" or "Job Name #3754")
    m = re.search(r"[-#]\s*(\d{3,5})\s*$", zone)
    if m:
        return m.group(1)
    # Pattern 2: leading number after # (e.g. "#2342 Job Name")
    m = re.search(r"^#?\s*(\d{3,5})\b", zone)
    if m:
        return m.group(1)
    # Pattern 3: trailing number after just a space (e.g. "Bonne Ecole Roof 4775")
    m = re.search(r"\s(\d{3,5})\s*$", zone)
    if m:
        return m.group(1)
    return ""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
