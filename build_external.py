"""
build_external.py

Processes raw external reference data into clean Parquet files under data/external/.

Outputs
-------
data/external/wholesale_prices.parquet
    Weekly wholesale proxy prices in pence/litre for petrol (RBOB gasoline) and
    diesel (heating oil), derived from NYMEX futures via yfinance and converted
    using the GBP/USD spot rate.

data/external/desnz_pump_prices.parquet
    Weekly national average UK pump prices and duty/VAT rates from DESNZ.
    Source: DESNZ "Weekly road fuel prices" CSV (2018-present).

data/external/msoa_house_prices.parquet
    Most recent median house price per MSOA (England and Wales).
    Source: ONS "Median house prices by MSOA" dataset (year ending Sep 2025).

data/external/rural_urban_classification.parquet
    Rural-urban classification per MSOA (England and Wales).
    Source: DEFRA/ONS 2011 Rural Urban Classification lookup tables.
    Superseded for modelling by the per-postcode RUC21 indicator in
    postcode_lookup.parquet; kept for reference.

data/external/postcode_lookup.parquet
    One row per UK postcode (current and terminated): 2021 MSOA code and the
    2021 rural-urban classification indicator, from the ONS National
    Statistics Postcode Lookup (NSPL, May 2026 release). Used to join
    stations to MSOA-level data (house prices) and to classify each
    station's postcode as urban or rural. Northern Ireland has no MSOAs
    and no RUC21, so those columns are null for NI postcodes.

Wholesale price notes
---------------------
- RBOB Gasoline (RB=F) is a US refined product contract, used as an
  internationally correlated proxy for UK petrol wholesale prices.
- NYMEX Heating Oil (HO=F) is used as a proxy for UK diesel wholesale prices.
- Both are priced in USD/US gallon and are converted to pence/litre.
- Conversion: pence_per_litre = usd_per_gallon / 3.78541 * 100 / gbpusd_rate
- Limitation: these are US contracts. UK wholesale (Platts/Argus CIF NWE) tracks
  closely but is not identical. CMA uses Rotterdam prices; this is the closest
  publicly available free proxy.

Duty note
---------
Fuel duty was cut from 57.95p to 52.95p on 28 March 2022 and has remained
at 52.95p/litre since. The DESNZ CSV reflects this correctly.
"""

import zipfile

import pandas as pd
import requests
import yfinance as yf
from pathlib import Path

DATA_DIR = Path("data/external")
RAW_DESNZ_CSV = DATA_DIR / "desnz_weekly_fuel_prices.csv"
RAW_MSOA_XLSX = DATA_DIR / "ons_msoa_house_prices.xlsx"
RAW_RUC_ODS = DATA_DIR / "ons_rural_urban_classification.ods"
RAW_NSPL_ZIP = DATA_DIR / "nspl_may_2026.zip"

# ONS Open Geography portal, "National Statistics Postcode Lookup (May 2026)"
# CSV Collection. Release-specific: the portal publishes a new item id each
# quarter (Feb/May/Aug/Nov), so update both the id and RAW_NSPL_ZIP on refresh.
NSPL_URL = (
    "https://www.arcgis.com/sharing/rest/content/items/"
    "7668e0d35cab4f6db6f15f03be610fb0/data"
)

LITRES_PER_US_GALLON = 3.78541

# RUC21 indicator descriptions, from the names-and-codes CSVs shipped inside
# the NSPL zip (Documents/). England and Wales use lettered codes with an
# explicit urban/rural flag; Scotland uses the numeric 6-fold classification
# where 5 and 6 are rural. Northern Ireland has no RUC21.
_RUC21_DESC = {
    "RLF1": "Larger rural: Further from a major town or city",
    "RLN1": "Larger rural: Nearer to a major town or city",
    "RSF1": "Smaller rural: Further from a major town or city",
    "RSN1": "Smaller rural: Nearer to a major town or city",
    "UF1":  "Urban: Further from a major town or city",
    "UN1":  "Urban: Nearer to a major town or city",
    "1":    "Large Urban Areas",
    "2":    "Other Urban Areas",
    "3":    "Accessible Small Towns",
    "4":    "Remote Small Towns",
    "5":    "Accessible Rural",
    "6":    "Remote Rural",
}
_RUC21_RURAL_CODES = {"RLF1", "RLN1", "RSF1", "RSN1", "5", "6"}


def build_desnz_pump_prices() -> pd.DataFrame:
    df = pd.read_csv(RAW_DESNZ_CSV)
    df.columns = [
        "week_commencing",
        "ulsp_pump_ppl",
        "ulsd_pump_ppl",
        "ulsp_duty_ppl",
        "ulsd_duty_ppl",
        "ulsp_vat_pct",
        "ulsd_vat_pct",
    ]
    df["week_commencing"] = pd.to_datetime(df["week_commencing"], dayfirst=True)
    return df.sort_values("week_commencing").reset_index(drop=True)


def _download_series(ticker: str, start: str, name: str) -> pd.Series:
    raw = yf.download(ticker, start=start, progress=False, auto_adjust=True)["Close"]
    # yfinance in pandas 3.x may return a single-column DataFrame instead of a Series
    if isinstance(raw, pd.DataFrame):
        raw = raw.iloc[:, 0]
    raw.name = name
    return raw


def build_wholesale_prices(start: str = "2018-01-01") -> pd.DataFrame:
    print("  Downloading RBOB gasoline futures (RB=F)...")
    rb = _download_series("RB=F", start, "rbob_usd_per_gal")

    print("  Downloading heating oil futures (HO=F)...")
    ho = _download_series("HO=F", start, "ho_usd_per_gal")

    print("  Downloading GBP/USD spot rate (GBPUSD=X)...")
    fx = _download_series("GBPUSD=X", start, "gbpusd")

    raw = pd.concat([rb, ho, fx], axis=1, sort=True).dropna()
    raw.index.name = "date"

    # Resample to weekly (Monday) to align with DESNZ data
    weekly = raw.resample("W-MON").mean()

    weekly["petrol_wholesale_ppl"] = (
        weekly["rbob_usd_per_gal"] / LITRES_PER_US_GALLON * 100 / weekly["gbpusd"]
    )
    weekly["diesel_wholesale_ppl"] = (
        weekly["ho_usd_per_gal"] / LITRES_PER_US_GALLON * 100 / weekly["gbpusd"]
    )

    return weekly[["petrol_wholesale_ppl", "diesel_wholesale_ppl", "gbpusd"]].reset_index()


def build_msoa_house_prices() -> pd.DataFrame:
    print("  Reading ONS MSOA house prices (this may take a moment)...")
    # Sheet '1a' = all dwellings, England and Wales
    # Row 3 (0-indexed: 2) = header; rows 3+ = data
    raw = pd.read_excel(RAW_MSOA_XLSX, sheet_name="1a", header=2, engine="openpyxl")

    # Columns: 'Local authority code', 'Local authority name', 'MSOA code',
    #          'MSOA name', then year-ending period columns
    id_cols = raw.columns[:4].tolist()
    year_cols = raw.columns[4:].tolist()

    # Find the last non-null year column as the most recent price
    price_cols = raw[year_cols]
    latest_col = price_cols.columns[price_cols.notna().any()].tolist()[-1]
    print(f"  Using most recent period: {latest_col}")

    out = raw[id_cols + [latest_col]].copy()
    out.columns = ["la_code", "la_name", "msoa_code", "msoa_name", "median_house_price"]
    out = out.dropna(subset=["msoa_code", "median_house_price"])
    out["msoa_code"] = out["msoa_code"].str.strip()

    # Compute a house price index relative to England and Wales median
    national_median = out["median_house_price"].median()
    out["house_price_index"] = out["median_house_price"] / national_median

    print(f"  {len(out):,} MSOAs. National median price: £{national_median:,.0f}")
    return out.reset_index(drop=True)


def build_rural_urban_classification() -> pd.DataFrame:
    print("  Reading rural-urban classification (ODS)...")
    # Sheet 'MSOA11': row 0 = table title, row 1 = column headers, rows 2+ = data
    # Columns: MSOA 2011 Code, MSOA 2011 Name, RUC code, 10-fold description, 2-fold (Urban/Rural)
    raw = pd.read_excel(RAW_RUC_ODS, sheet_name="MSOA11", header=1, engine="odf")

    # Drop the first row which contains row-count metadata text
    raw = raw.iloc[1:].reset_index(drop=True)

    # Rename to stable short names regardless of exact column wording
    raw.columns = [
        "msoa_code",
        "msoa_name",
        "ruc_code",
        "ruc_10fold",
        "ruc_2fold",
    ]

    out = raw.dropna(subset=["msoa_code"]).copy()
    out["msoa_code"] = out["msoa_code"].astype(str).str.strip()
    print(f"  {len(out):,} MSOAs. RUC categories: {out['ruc_2fold'].value_counts().to_dict()}")
    return out.reset_index(drop=True)


def build_postcode_lookup() -> pd.DataFrame:
    """
    Build a postcode -> (MSOA 2021, RUC21) lookup from the ONS NSPL.

    Downloads the NSPL zip on first run (~180 MB, gitignored) and reads the
    per-postcode-area CSVs straight out of it. Terminated postcodes are kept
    (a station's recorded postcode may be an old one) and flagged.

    The join key pcd_key is the postcode with all whitespace removed and
    uppercased, so it matches regardless of spacing conventions.
    """
    if not RAW_NSPL_ZIP.exists():
        print(f"  Downloading NSPL zip (~180 MB) -> {RAW_NSPL_ZIP} ...")
        with requests.get(NSPL_URL, stream=True, timeout=600) as resp:
            resp.raise_for_status()
            with open(RAW_NSPL_ZIP, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
    else:
        print(f"  Using cached {RAW_NSPL_ZIP}")

    usecols = ["pcds", "doterm", "msoa21cd", "ruc21ind"]
    frames = []
    with zipfile.ZipFile(RAW_NSPL_ZIP) as zf:
        csv_names = [
            n for n in zf.namelist()
            if n.startswith("Data/multi_csv/") and n.endswith(".csv")
        ]
        print(f"  Reading {len(csv_names)} postcode-area CSVs from the zip...")
        for name in csv_names:
            with zf.open(name) as fh:
                frames.append(pd.read_csv(fh, usecols=usecols, dtype=str))
    out = pd.concat(frames, ignore_index=True)

    out["pcd_key"] = out["pcds"].str.replace(r"\s+", "", regex=True).str.upper()
    out["is_terminated"] = out["doterm"].notna()

    # Pseudo-codes like E99999999 mean "not allocated", not a real MSOA.
    out.loc[out["msoa21cd"].str.endswith("99999999", na=False), "msoa21cd"] = pd.NA

    out["ruc21desc"] = out["ruc21ind"].map(_RUC21_DESC)
    out["ruc_2fold"] = pd.Series(pd.NA, index=out.index, dtype="object")
    known = out["ruc21ind"].isin(_RUC21_DESC)
    out.loc[known, "ruc_2fold"] = "Urban"
    out.loc[out["ruc21ind"].isin(_RUC21_RURAL_CODES), "ruc_2fold"] = "Rural"

    unknown = out.loc[out["ruc21ind"].notna() & ~known, "ruc21ind"].unique()
    if len(unknown):
        print(f"  WARNING: unknown RUC21 codes left unclassified: {sorted(unknown)}")

    cols = ["pcd_key", "pcds", "msoa21cd", "ruc21ind", "ruc21desc", "ruc_2fold",
            "is_terminated"]
    out = out[cols]
    dupes = out["pcd_key"].duplicated().sum()
    if dupes:
        print(f"  WARNING: {dupes} duplicate postcode keys, keeping first")
        out = out.drop_duplicates(subset="pcd_key", keep="first")
    return out.reset_index(drop=True)


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("\n[1/5] DESNZ weekly pump prices...")
    pump = build_desnz_pump_prices()
    pump.to_parquet(DATA_DIR / "desnz_pump_prices.parquet", index=False)
    print(f"  {len(pump)} weeks, {pump['week_commencing'].min().date()} to "
          f"{pump['week_commencing'].max().date()}")
    print(f"  Current duty: {pump['ulsp_duty_ppl'].iloc[-1]}p/litre")

    print("\n[2/5] Wholesale prices from yfinance...")
    wholesale = build_wholesale_prices()
    wholesale.to_parquet(DATA_DIR / "wholesale_prices.parquet", index=False)
    print(f"  {len(wholesale)} weeks, {wholesale['date'].min().date()} to "
          f"{wholesale['date'].max().date()}")
    last = wholesale.iloc[-1]
    print(f"  Latest: petrol wholesale {last['petrol_wholesale_ppl']:.1f}p/L, "
          f"diesel {last['diesel_wholesale_ppl']:.1f}p/L, GBP/USD {last['gbpusd']:.4f}")

    print("\n[3/5] ONS MSOA house prices...")
    house = build_msoa_house_prices()
    house.to_parquet(DATA_DIR / "msoa_house_prices.parquet", index=False)
    print(f"  Saved {len(house):,} MSOAs")

    print("\n[4/5] Rural-urban classification...")
    try:
        ruc = build_rural_urban_classification()
        ruc.to_parquet(DATA_DIR / "rural_urban_classification.parquet", index=False)
        print(f"  Saved {len(ruc):,} MSOA records")
    except ValueError as e:
        print(f"  WARNING: {e}")
        print("  Skipping rural-urban classification. Run manually after inspecting sheets.")

    print("\n[5/5] NSPL postcode lookup...")
    postcodes = build_postcode_lookup()
    postcodes.to_parquet(DATA_DIR / "postcode_lookup.parquet", index=False)
    print(f"  Saved {len(postcodes):,} postcodes "
          f"({postcodes['is_terminated'].mean():.0%} terminated, "
          f"MSOA coverage {postcodes['msoa21cd'].notna().mean():.1%})")

    print("\nDone. Output files in data/external/:")
    for f in sorted(DATA_DIR.glob("*.parquet")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
