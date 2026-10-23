"""Tiny standalone redirect server for the retired port 8765.

The real V3 Passdown app now only runs on 8965. This process's ONLY job is
to catch anyone hitting an old bookmarked http://<host>:8765/... link and
bounce them straight to the same path on 8965, instead of dead-ending in a
"can't reach this page" error. It intentionally does not import main.py or
touch the database -- it's pure routing glue, nothing else.

Run standalone:
    uv run python redirect_8765.py
"""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
import uvicorn

CURRENT_HOST = "10.239.69.183"
CURRENT_PORT = 8965

app = FastAPI()


@app.api_route("/{full_path:path}", methods=["GET", "POST", "HEAD"])
async def redirect_to_current_port(full_path: str, request: Request):
    target = f"http://{CURRENT_HOST}:{CURRENT_PORT}/{full_path}"
    if request.url.query:
        target += f"?{request.url.query}"
    # 308 preserves the HTTP method (important for POST /v3/login forms),
    # unlike a 301/302 which browsers may silently downgrade to GET.
    return RedirectResponse(target, status_code=308)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="warning")
