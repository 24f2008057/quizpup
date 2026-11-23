import gzip
import os

from flask import request

COMPRESSIBLE = (
    "text/css",
    "text/javascript",
    "application/javascript",
    "application/json",
    "image/svg+xml",
    "font/ttf",
    "application/manifest+json",
    "application/x-javascript",
)
ONE_YEAR = 31536000
ONE_DAY = 86400


def init_static_assets(app):
    static_root = app.static_folder
    version_cache = {}

    def file_version(filename):
        path = os.path.normpath(os.path.join(static_root, filename))
        if not path.startswith(static_root) or not os.path.isfile(path):
            return None
        mtime = os.path.getmtime(path)
        cached = version_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        version = format(int(mtime), "x")
        version_cache[path] = (mtime, version)
        return version

    @app.url_defaults
    def add_static_version(endpoint, values):
        if endpoint != "static" or "v" in values:
            return
        filename = values.get("filename")
        if not filename:
            return
        version = file_version(filename)
        if version:
            values["v"] = version

    @app.after_request
    def static_cache_and_compression(response):
        if request.endpoint != "static" or request.method not in ("GET", "HEAD"):
            return response
        if response.status_code not in (200, 304):
            return response
        if request.args.get("v") or request.path.startswith("/static/fonts/"):
            response.headers["Cache-Control"] = f"public, max-age={ONE_YEAR}, immutable"
        else:
            response.headers["Cache-Control"] = f"public, max-age={ONE_DAY}"
        response.headers["Vary"] = "Accept-Encoding"
        if response.status_code != 200 or response.headers.get("Content-Encoding"):
            return response
        mimetype = (response.mimetype or "").lower()
        if mimetype not in COMPRESSIBLE:
            return response
        if "gzip" not in request.headers.get("Accept-Encoding", "").lower():
            return response
        response.direct_passthrough = False
        data = response.get_data()
        if len(data) < 512:
            return response
        compressed = gzip.compress(data, compresslevel=6, mtime=0)
        response.set_data(compressed)
        response.headers["Content-Encoding"] = "gzip"
        response.headers["Content-Length"] = str(len(compressed))
        etag = response.headers.get("ETag")
        if etag and not etag.startswith("W/"):
            response.headers["ETag"] = "W/" + etag
        return response