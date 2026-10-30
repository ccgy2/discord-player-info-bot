"""Small, sanitized JSON protocol for short-lived workers."""
import contextlib
import json
import sys

class JobError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def main(handler):
    output = sys.stdout
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise JobError("too_large")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise JobError("invalid_file")
        # Keep library progress bars/logs away from protocol stdout.
        with contextlib.redirect_stdout(sys.stderr):
            response = handler(request)
        result = {"ok": True, **response}
    except JobError as exc:
        result = {"ok": False, "error": exc.code, "kind": type(exc).__name__}
    except ImportError as exc:
        result = {"ok": False, "error": "missing_dependency", "kind": type(exc).__name__}
    except Exception as exc:
        result = {"ok": False, "error": "failed", "kind": type(exc).__name__}
    encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
    if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
        encoded = '{"ok":false,"error":"too_large","kind":"OutputLimit"}'
    output.write(encoded)
    output.flush()
