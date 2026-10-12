"""Validate/extract the pinned public dataset. Never execute archive contents."""
import csv
import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data/raw/kaggle"
OUT = ROOT / "audit/reports"
ARCHIVE = RAW / "v515.zip"
DEST = RAW / "v515"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "source": "https://www.kaggle.com/datasets/eoinamoore/historical-nba-data-and-player-box-scores",
        "download_url": "https://www.kaggle.com/api/v1/datasets/download/eoinamoore/historical-nba-data-and-player-box-scores?datasetVersionNumber=515",
        "metadata": json.loads((RAW / "metadata.json").read_text()),
        "archive_bytes": ARCHIVE.stat().st_size,
        "archive_sha256": digest(ARCHIVE),
        "files": [],
    }
    with zipfile.ZipFile(ARCHIVE) as z:
        for member in z.infolist():
            if member.is_dir():
                continue
            name = Path(member.filename)
            if name.is_absolute() or ".." in name.parts or len(name.parts) != 1:
                raise ValueError(f"Unexpected archive path: {member.filename}")
            if name.suffix not in {".csv", ".parquet"}:
                raise ValueError(f"Unexpected file type: {name}")
            # Reading to EOF verifies the archive member CRC as well.
            h = hashlib.sha256()
            dest = DEST / name
            with z.open(member) as src, dest.open("wb") as out:
                for block in iter(lambda: src.read(8 * 1024 * 1024), b""):
                    h.update(block)
                    out.write(block)
            item = {"name": name.name, "bytes": dest.stat().st_size,
                    "sha256": h.hexdigest(), "zip_crc_validated": True}
            if name.suffix == ".csv":
                with dest.open(newline="", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    item["columns"] = reader.fieldnames
                    item["sample_rows"] = [next(reader, None) for _ in range(2)]
            manifest["files"].append(item)
            print(f"Verified {name}: {dest.stat().st_size:,} bytes", flush=True)
    (OUT / "snapshot_manifest.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
