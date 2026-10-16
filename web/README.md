# React web client

The Vite client uses React, React Three Fiber/Drei, Framer Motion, and Zustand.
It shares the `scripts/api.py` REST service and season SQLite database with the
Unity client.

From the repository root, start the API and web client in separate terminals:

```bash
pip install -r scripts/requirements-api.txt
PYTHONPATH=scripts uvicorn api:app --app-dir scripts --host 127.0.0.1 --port 8000
cd web
npm install
npm run dev
```

The web app is at <http://localhost:5173>. Vite proxies `/api` to the local
service, which also works through a Codespaces forwarded port. Set
`VITE_API_BASE_URL` to an absolute `/api/v1` endpoint for production hosting.
The API intentionally binds to loopback by default and has no user authentication;
do not expose it to an untrusted network.

The client parses replay uploads with the existing replay scanner, saves the
players you pick from each match into a season (nobody is picked until you do,
and the API logs only those players), and exports season reports as JSON. School
catalogs can be imported in the School Selection module. `GET /api/v1/events` lists
the Liquipedia events and NECC divisions the Streamlit app's League & Team page offers. An authorized HTTPS
feed can be configured for the API with `NECC_R6_DATA_URL`; the payload contract
is described in [scripts/README.md](../scripts/README.md#necc-school-data).