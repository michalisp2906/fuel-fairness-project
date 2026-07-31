# Collecting from an Android phone (Termux)

Backup collection path for when the Windows PC is off (travel, etc.). It runs
the same `fuel_snapshot.py` collector and pushes snapshots to GitHub, driven by
`run_collection.sh` (the bash port of `run_collection.ps1`) on a cron schedule
inside Termux.

## Known unknown before you rely on this

The Fuel Finder API blocks data-centre IPs. We have confirmed it answers a UK
residential IP and refuses a data-centre (VPN) IP, but we have NOT confirmed it
answers a **non-UK residential/mobile** IP, because no commercial VPN offers
one to test with. So the only real test is running a collection from the phone
on the actual foreign mobile network once you are there.

Do all the setup below on UK wifi first, and confirm a snapshot pushes
successfully from home. Then the only variable left when you travel is the IP.
On arrival, run one manual collection (`bash run_collection.sh`). If a snapshot
lands, you are collecting for the whole trip. If it returns HTTP 403, the API
is geo/residential-fussy, and we document the gap as a limitation.

## One-time setup (do on UK wifi)

### 1. Install Termux and the Boot addon

Install both from **F-Droid**, not the Play Store (the Play Store build is
outdated and broken):

- Termux
- Termux:Boot (lets cron start automatically after a reboot)

Open Termux:Boot once after installing so Android registers it.

### 2. Install packages

```sh
pkg update && pkg upgrade
pkg install python git cronie termux-services openssh
pip install requests
```

`requests` is the only Python dependency the collector needs. There is no need
for uv or the full project environment on the phone.

### 3. Stop Android from killing it

Android aggressively suspends background apps, which silently stops cron.

- Android Settings > Apps > Termux > Battery > set to **Unrestricted**
  (do the same for Termux:Boot if listed).
- In Termux, acquire a wake-lock: `termux-wake-lock`
  (the boot script below re-acquires it on every reboot).

### 4. Clone the repo

Use a GitHub personal access token so the phone can push. Create one at
github.com > Settings > Developer settings > Personal access tokens. A
fine-grained token scoped to the `fuel-fairness-project` repo with
**Contents: Read and write** is enough (or a classic token with `repo`).

```sh
cd ~
git clone https://github.com/michalisp2906/fuel-fairness-project.git
cd fuel-fairness-project
```

When git asks for a password during the first push, paste the token (not your
GitHub password). Save it so you are not asked every run:

```sh
git config --global credential.helper store
git config --global user.name  "michalisp2906"
git config --global user.email "michalakis2906@gmail.com"
```

The repo history includes all accumulated snapshots, so clone over wifi, not
mobile data.

### 5. Create the .env (it is gitignored, so it did NOT come with the clone)

```sh
cat > .env <<'EOF'
FUEL_FINDER_CLIENT_ID=your_client_id_here
FUEL_FINDER_CLIENT_SECRET=your_client_secret_here
EOF
```

Copy the two values from the Windows PC's `.env` (project root). Do not commit
this file; `.gitignore` already excludes it.

### 6. Test a manual run

```sh
bash run_collection.sh
```

Watch the output. Success looks like `pushed N file(s)` and `=== done ===`, and
you should see a new `snapshot:` commit appear on GitHub. If it fails, read
`logs/collection.log`. A 403 at the token step on UK wifi would mean the token
or credentials are wrong (on foreign mobile data it would instead mean the IP
was refused).

## Scheduling with cron

Enable the cron daemon (termux-services):

```sh
# after installing termux-services, restart Termux once, then:
sv-enable crond
```

Edit the crontab:

```sh
crontab -e
```

To mirror the current Windows cadence (Mon-Fri, 4x/day). Note cron uses the
phone's **local** time, so in Cyprus (UTC+3) these fire 3 hours earlier in UK
terms, which does not matter for price sampling:

```cron
0 9 * * 1-5 cd ~/fuel-fairness-project && bash run_collection.sh >> logs/cron.log 2>&1
30 11 * * 1-5 cd ~/fuel-fairness-project && bash run_collection.sh >> logs/cron.log 2>&1
0 14 * * 1-5 cd ~/fuel-fairness-project && bash run_collection.sh >> logs/cron.log 2>&1
30 16 * * 1-5 cd ~/fuel-fairness-project && bash run_collection.sh >> logs/cron.log 2>&1
```

Optional improvement: because the phone is always on (unlike the PC), you could
collect more often and on weekends to close the documented nights/weekends
sampling gap, for example every 2 hours daily:

```cron
0 */2 * * * cd ~/fuel-fairness-project && bash run_collection.sh >> logs/cron.log 2>&1
```

If you change the cadence, note it in the write-up so the sampling record stays
honest.

## Survive reboots (Termux:Boot)

Create a boot script so cron and the wake-lock come back after a restart:

```sh
mkdir -p ~/.termux/boot
cat > ~/.termux/boot/start-collection.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
crond
EOF
chmod +x ~/.termux/boot/start-collection.sh
```

After any reboot you must unlock the phone once for Termux:Boot to fire.

## Daily check while travelling

- Confirm new `snapshot:` commits are appearing on GitHub through the day.
- If they stop, open Termux and run `bash run_collection.sh` by hand and read
  the output / `logs/collection.log`.
- The CI rebuild of `data/gold/app_data.parquet` runs automatically on each
  snapshot push, so the live app keeps updating as long as pushes land.

## Not covered by this: the wholesale refresh

`build_external.py` (NYMEX wholesale via yfinance) still needs a periodic run,
and `build_gold.py` warns when wholesale is >21 days stale. yfinance is not
IP-restricted, so this one can and should move into a GitHub Action rather than
onto the phone. Track that separately.
