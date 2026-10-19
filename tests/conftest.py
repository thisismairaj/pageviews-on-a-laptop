import gzip
import http.server
import threading

import pytest


def make_gz(path, lines: list[str], trailing_newline: bool = True) -> bytes:
    text = "\n".join(lines) + ("\n" if trailing_newline else "")
    path.write_bytes(gzip.compress(text.encode("utf-8")))
    return path.read_bytes()


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}

    def do_GET(self):
        body = self.files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[6:].split("-")[0])
            self.send_response(206)
            body = body[start:]
        else:
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def http_server():
    handler = type("H", (_RangeHandler,), {"files": {}})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", handler.files
    srv.shutdown()
