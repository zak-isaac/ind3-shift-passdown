"""
IND3 Passdown Server launcher.

Why this exists: `uvicorn main:app` on its own uses uvicorn's default
logging config, which omits timestamps from access-log lines
(e.g. `INFO: 10.239.69.183:65228 - "GET /api/active-shifts HTTP/1.1" 200 OK`).
That makes it impossible to tell real request cadence from server.log
alone -- you can't spot a client polling too fast or a stacked
setInterval bug if every line looks the same with no "when".

This wraps uvicorn.run() with a patched LOGGING_CONFIG that prefixes
both the "default" and "access" loggers with a timestamp.
"""
import uvicorn
from uvicorn.config import LOGGING_CONFIG

TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

LOGGING_CONFIG["formatters"]["default"]["fmt"] = (
    "%(asctime)s %(levelprefix)s %(message)s"
)
LOGGING_CONFIG["formatters"]["default"]["datefmt"] = TIMESTAMP_FMT

LOGGING_CONFIG["formatters"]["access"]["fmt"] = (
    '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
)
LOGGING_CONFIG["formatters"]["access"]["datefmt"] = TIMESTAMP_FMT

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8765,
        log_config=LOGGING_CONFIG,
    )
