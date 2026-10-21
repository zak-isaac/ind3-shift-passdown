IND3 V3 LOGIN REDESIGN — LOCAL TEST PATCH

1. Back up the existing project before applying.
2. Stop the running IND3 server (Control+C).
3. In Terminal, run:
   cd /Users/isaacssaku/Desktop/ind3-shift-passdown-main
   python3 /path/to/apply_v3_login.py .
4. Validate syntax:
   python3 -m py_compile main.py v3_routes.py v3_identity.py
5. Restart:
   ~/.venv/bin/python run_server.py
6. Open http://127.0.0.1:8765/v3/login

The original V2 banner HTML is preserved verbatim. Ordinary associates
enter only their number. Active shift cards remain on the page.
The test master code is 8972, verified server-side. Shift selection opens
after verification and permits only currently eligible S1-S5 shifts.
Expired shifts are closed on login-page access; future shifts are hidden.

CAUTION: This is a test-only change. The master code is hardcoded as requested.
Associate numbers alone are not secure authentication. Do not deploy publicly.
The patch has not been tested against a full local copy of the repository.
Do not run on the live operational database until tested in a separate copy.

GITHUB UPLOAD (after reviewing/test):
  git add main.py v3_routes.py v3_identity.py templates/v3/login.html
  git commit -m "Redesign V3 login and fix active shift eligibility"
  git push
