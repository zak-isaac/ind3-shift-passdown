# IND3 Shift Passdown — V3 / V2 Function Parity

Development build. Run in a separate test folder before deploying.

V3 uses the V2 homepage identity flow and preserves V2 operations at `/v3/operations?shift_id=<id>`. V3 adds single-file Cetaris XLSX import, technician selection, Closed non-PM work orders as Shift Happenings, separate completed PM counts, and a View/Edit modal.

Associate numbers alone are not strong authentication. Run only in a restricted, authorized environment pending corporate SSO. Never commit employee rosters, live databases, Cetaris exports, credentials or `.env` files.

## Setup

Install with `python -m pip install -r requirements.txt`; configure the approved roster source and `IND3_V3_SESSION_SECRET`; run `python run_server.py`. V3 login is at `http://localhost:8765/v3/login`.

**Repository status:** Initial configuration files only; full application source is not yet uploaded. Do not deploy this repository as-is.
