"""Remote documents."""
import urllib.request
from pathlib import Path


def fetch_remote_document(address, target_path):
    with urllib.request.urlopen(address) as handle:
        data = handle.read()
    Path(target_path).write_bytes(data)
    return len(data)
