"""
OneStep GPS Day Start/End Breakdown — JSON parser + Flask endpoint.
Make.com POSTs the .json file as multipart/form-data (field name: 'file')
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


@app.route("/process", methods=["POST"])
def process():
    if API_KEY and request.headers.get("X-Api-Key") != API_KEY:
        return jsonify({"ok": False, "error": "unauthorized"}), 401

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"ok": False, "error": "missing file"}), 400

    file_bytes = upload.read()
    if not file_bytes:
        return jsonify({"ok": False, "error": "empty file"}), 400

    try:
        data = json.loads(file_bytes)
        rows = parse(data)
    except ValueError as e:
        log.warning("parse failed: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 422
    except Exception as e:
        log.exception("unexpected parse error")
        return jsonify({"ok": False, "error": f"parse failed: {e}"}), 500

    log.info("parsed rows=%d", len(rows))
    return jsonify(rows), 200


@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "gps-day-start-end-parser"}), 200


def parse(data: dict) -> list:
    tables = data.get("tables", [])
    rows = []

    for table in tables:
        header = table.get("header", {})
        device = header.get("entity_name", "")
        date = header.get("date", "")
        entity_groups = header.get("entity_groups", "")

        # Skip empty tables
        if date == "01/01/0001":
            continue

        for record in table.get("record_list", []):
            status = record.get("status", "")
            start_time = record.get("start_time", "")
            end_time = record.get("end_time", "")
            duration_s = record.get("duration", {}).get("value", 0) or 0
            duration_min = round(duration_s / 60, 2)

            # Distance
            distance_m = record.get("length", {}).get("value", 0) or 0
            distance_mi = round(distance_m * 0.000621371, 2)

            # Speed — top_speed is km/h, avg_speed is m/s
            top_speed_kmh = record.get("top_speed", {}).get("value", 0) or 0
            top_speed_mph = round(top_speed_kmh * 0.621371, 1)
            avg_speed_ms = record.get("avg_speed", {}).get("value", 0) or 0
            avg_speed_mph = round(avg_speed_ms * 2.23694, 1)

            # Zone and job number
            zone_names = record.get("zone_names") or []
            zone = ", ".join(zone_names) if zone_names else ""
            job_number = _extract_job_number(zone)

            # Address
            address = record.get("address", "") or ""

            # Engine idle
            engine_idle_s = record.get("engine_idle", {}).get("value", 0) or 0
            engine_idle_min = round(engine_idle_s / 60, 2)

            # Driver
            driver_names = record.get("driver_names") or []
            driver = ", ".join(driver_names) if driver_names else ""

            # Upsert key
            upsert_key = f"{device}_{date}_{start_time}"

            rows.append({
                "upsert_key": upsert_key,
                "device": device,
                "entity_groups": entity_groups,
                "date": date,
                "status": status,
                "start_time": start_time,
                "end_time": end_time,
                "duration_min": duration_min,
                "distance_mi": distance_mi,
                "zone": zone,
                "job_number": job_number,
                "address": address,
                "engine_idle_min": engine_idle_min,
                "top_speed_mph": top_speed_mph,
                "avg_speed_mph": avg_speed_mph,
                "driver": driver,
            })

    return rows


def _extract_job_number(zone: str) -> str:
    if not zone:
        return ""
    m = re.search(r"[-#]\s*(\d{3,5})\s*$", zone)
    if m:
        return m.group(1)
    m = re.search(r"^#?\s*(\d{3,5})\b", zone)
    if m:
        return m.group(1)
    return ""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
