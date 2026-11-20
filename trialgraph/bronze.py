"""Bronze layer: land source records exactly as received, append-only.

Bronze is the layer you can always rebuild everything else from, so it stores
the raw payload untouched and records where and when each record arrived.
Three properties matter, and each one is tested:

* Append-only and idempotent. Re-running the same load adds nothing: a row is
  identified by (nct_id, sha256 of payload), so an unchanged record is skipped
  and a revised record is stored alongside its earlier version. History is kept,
  not overwritten.
* Nothing is silently dropped. A record with no NCT ID cannot be keyed, but it
  is still evidence that the source sent something malformed, so it goes to a
  quarantine table with the reason, and the row counts reconcile exactly.
* The current view is derived, not stored. `v_latest` picks the newest version
  of each trial by its registry update date, so a late-arriving older version
  can never overwrite a newer one just because it was loaded second.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .bulk import create_and_load

API_URL = "https://clinicaltrials.gov/api/v2/studies"

DDL = """
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE TABLE IF NOT EXISTS bronze.raw_studies (
    nct_id              VARCHAR NOT NULL,
    payload             VARCHAR NOT NULL,
    payload_sha256      VARCHAR NOT NULL,
    last_update_posted  VARCHAR,
    source              VARCHAR NOT NULL,
    batch_id            VARCHAR NOT NULL,
    ingested_at         TIMESTAMP NOT NULL
);
CREATE TABLE IF NOT EXISTS bronze.quarantine (
    reason          VARCHAR NOT NULL,
    payload         VARCHAR NOT NULL,
    payload_sha256  VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    batch_id        VARCHAR NOT NULL,
    ingested_at     TIMESTAMP NOT NULL
);
CREATE OR REPLACE VIEW bronze.v_latest AS
SELECT nct_id, payload, payload_sha256, last_update_posted, source, batch_id, ingested_at
FROM bronze.raw_studies
QUALIFY row_number() OVER (
    PARTITION BY nct_id
    ORDER BY last_update_posted DESC NULLS LAST, ingested_at DESC
) = 1;
"""


# ------------------------------------------------------------------ reading
def _studies_from_obj(obj) -> Iterator[dict]:
    if isinstance(obj, list):
        yield from obj
    elif isinstance(obj, dict) and "studies" in obj:
        yield from obj["studies"]
    elif isinstance(obj, dict) and "protocolSection" in obj:
        yield obj
    else:
        raise ValueError("unrecognised JSON shape: expected an array of studies, "
                         "an API page with a 'studies' key, or a single study")


def _studies_from_text(text: str, name: str) -> Iterator[dict]:
    if name.endswith(".jsonl") or name.endswith(".ndjson"):
        for line in text.splitlines():
            if line.strip():
                yield from _studies_from_obj(json.loads(line))
    else:
        yield from _studies_from_obj(json.loads(text))


def load_file(path: str | Path) -> Iterator[dict]:
    """Read studies from a registry download: .json, .jsonl, or a .zip of either.

    The website's JSON download is a bare array; the API returns pages with a
    'studies' key. Both are accepted, so a file exported by hand and a file
    saved from the API land identically.
    """
    p = Path(path)
    if p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as zf:
            for info in zf.infolist():
                n = info.filename.lower()
                if info.is_dir() or not n.endswith((".json", ".jsonl", ".ndjson")):
                    continue
                with zf.open(info) as fh:
                    yield from _studies_from_text(io.TextIOWrapper(fh, "utf-8").read(), n)
    else:
        yield from _studies_from_text(p.read_text("utf-8"), p.name.lower())


def fetch_api(condition: str, interventional_only: bool = True, page_size: int = 1000,
              max_pages: int | None = None, session=None, sleep=time.sleep,
              max_retries: int = 5) -> Iterator[dict]:
    """Page through the ClinicalTrials.gov API v2, retrying throttling and 5xx.

    Retries use exponential backoff and honour Retry-After. A 4xx other than
    429 is raised immediately: retrying a malformed request only hides it.
    """
    if session is None:
        import requests
        session = requests.Session()
    params = {"format": "json", "query.cond": condition, "pageSize": page_size}
    if interventional_only:
        params["filter.advanced"] = "AREA[StudyType]INTERVENTIONAL"
    token, pages = None, 0
    while True:
        if token:
            params["pageToken"] = token
        for attempt in range(max_retries + 1):
            r = session.get(API_URL, params=params, timeout=60)
            if r.status_code == 200:
                break
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == max_retries:
                    r.raise_for_status()
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                sleep(wait)
                continue
            r.raise_for_status()
        page = r.json()
        yield from page.get("studies", [])
        pages += 1
        token = page.get("nextPageToken")
        if not token or (max_pages and pages >= max_pages):
            return


# ------------------------------------------------------------------ writing
def _get(d: dict, path: str):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


RAW_COLS = [("nct_id", "VARCHAR"), ("payload", "VARCHAR"), ("payload_sha256", "VARCHAR"),
            ("last_update_posted", "VARCHAR"), ("source", "VARCHAR"), ("batch_id", "VARCHAR"),
            ("ingested_at", "TIMESTAMP")]
QUAR_COLS = [("reason", "VARCHAR"), ("payload", "VARCHAR"), ("payload_sha256", "VARCHAR"),
             ("source", "VARCHAR"), ("batch_id", "VARCHAR"), ("ingested_at", "TIMESTAMP")]


def _append(con, table: str, cols, rows) -> None:
    """Append through a temporary table so bronze is never recreated."""
    tmp = "bronze._load_" + uuid.uuid4().hex[:8]
    create_and_load(con, tmp, cols, rows)
    con.execute(f"INSERT INTO {table} SELECT * FROM {tmp}")
    con.execute(f"DROP TABLE {tmp}")


def init(con) -> None:
    con.execute(DDL)


def ingest(con, studies: Iterable[dict], source: str, batch_id: str | None = None) -> dict:
    """Append records to bronze. Returns counts for reconciliation."""
    init(con)
    batch_id = batch_id or uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    existing = {(n, h) for n, h in con.execute(
        "SELECT nct_id, payload_sha256 FROM bronze.raw_studies").fetchall()}
    q_existing = {h for (h,) in con.execute(
        "SELECT payload_sha256 FROM bronze.quarantine").fetchall()}

    rows, qrows = [], []
    counts = {"received": 0, "inserted": 0, "unchanged": 0, "quarantined": 0}
    for s in studies:
        counts["received"] += 1
        payload = json.dumps(s, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(payload.encode()).hexdigest()
        nct = _get(s, "protocolSection.identificationModule.nctId")
        if not nct:
            if h not in q_existing:
                qrows.append(("missing nctId", payload, h, source, batch_id, now))
                q_existing.add(h)
            counts["quarantined"] += 1
            continue
        if (nct, h) in existing:
            counts["unchanged"] += 1
            continue
        existing.add((nct, h))
        rows.append((nct, payload, h,
                     _get(s, "protocolSection.statusModule.lastUpdatePostDateStruct.date"),
                     source, batch_id, now))
        counts["inserted"] += 1

    if rows:
        _append(con, "bronze.raw_studies", RAW_COLS, rows)
    if qrows:
        _append(con, "bronze.quarantine", QUAR_COLS, qrows)
    counts["batch_id"] = batch_id
    return counts
