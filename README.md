# IND3 Shift Passdown — V3 Command Center

**V3 is the only active web application on this branch.** Starting `python run_server.py` listens on port `8765`. Opening the workshop computer's approved address at `http://HOST:8765/` enters the V3 MST login and Command Center. V2 routes are retired and cannot be opened or used; only the existing report/export engine remains as internal code.

- **Login:** `/v3/login`
- **Workspace:** `/v3`
- **Startup:** `python run_server.py`
- **Master credentials:** configure the approved environment variables described in [V3 deployment instructions](docs/V3_OPERATIONAL_PATCH_DEPLOYMENT.md). Do not store credentials in GitHub.
- **Safety:** stage and test on a backup DB; verify Walmart network/IT approval before allowing other laptops to connect.

**Important:** This change is on the development branch until reviewed and merged. The workshop PC will not change automatically from a GitHub commit.

---

## Archived implementation reference

The material below documents older components and migration history. V2 screens are not served by the V3-only application.

# IND3 Shift Passdown

Repository initialized. Application source pending private repository setup and review.
