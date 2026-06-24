import os, json, requests

# reuse the token call
r = requests.post(
    "https://www.fuel-finder.service.gov.uk/api/v1/oauth/generate_access_token",
    json={"client_id": os.environ["FUEL_FINDER_CLIENT_ID"],
          "client_secret": os.environ["FUEL_FINDER_CLIENT_SECRET"]},
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    timeout=30,
)
token = r.json().get("data", {}).get("access_token") or r.json().get("access_token")

# pull one batch of prices
g = requests.get(
    "https://www.fuel-finder.service.gov.uk/api/v1/pfs/fuel-prices",
    params={"batch-number": 1},
    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    timeout=30,
)
print("status:", g.status_code)
payload = g.json()
print("top-level type:", type(payload).__name__)
if isinstance(payload, dict):
    print("top-level keys:", list(payload.keys()))
fuel_types = set()
for station in payload:
    for entry in station["fuel_prices"]:
        fuel_types.add(entry["fuel_type"])
print(fuel_types)