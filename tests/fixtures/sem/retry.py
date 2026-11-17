"""Sending helpers."""
import time


def deliver_payload(send_fn, payload):
    delay = 1
    last_error = None
    for tries in range(5):
        try:
            return send_fn(payload)
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
            delay = delay * 2
    raise RuntimeError(
        "could not deliver after 5 tries") from last_error
