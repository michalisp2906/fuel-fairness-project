import os, requests
r = requests.post(
    "https://www.fuel-finder.service.gov.uk/api/v1/oauth/generate_access_token",
    json={"client_id": os.environ["FUEL_FINDER_CLIENT_ID"],
          "client_secret": os.environ["FUEL_FINDER_CLIENT_SECRET"]},
    headers={"Content-Type": "application/json", "Accept": "application/json"},
    timeout=30,
)
print(r.status_code)
print(r.json())