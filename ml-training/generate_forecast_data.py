"""
Generates synthetic time-series spend data for simulated Indian users,
for training a personal-finance spend forecaster.

Output: ml-training/data/synthetic_transactions.csv
Columns: user_id, date, amount, category, is_credit

Run:
    ./venv/bin/python3 generate_forecast_data.py
"""

import os
import numpy as np
import pandas as pd
from datetime import date, timedelta

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------
NUM_USERS = 500
NUM_DAYS = 90
START_DATE = date(2026, 1, 1)
SEED = 42

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "synthetic_transactions.csv")

FOOD_CATEGORIES = ["Food & Drinks"]
DISCRETIONARY_CATEGORIES = ["Shopping", "Entertainment"]
BILL_CATEGORIES = ["Electricity", "Mobile Recharge"]

rng = np.random.default_rng(SEED)


# ----------------------------------------------------------------------------
# Per-user profile generation
# ----------------------------------------------------------------------------
def make_user_profile(user_idx: int) -> dict:
    """
    Assigns each simulated user a stable financial profile so their 90 days
    of transactions look like one consistent person, not random noise.
    """
    # Income tier drives rent, EMI, salary, and discretionary scale together,
    # so a user's spending is internally consistent (a ₹25k/month earner
    # doesn't have a ₹40k rent).
    income_tier = rng.choice(["low", "mid", "high"], p=[0.35, 0.45, 0.20])

    if income_tier == "low":
        salary = rng.uniform(18000, 35000)
        rent_or_emi = rng.uniform(4000, 10000)
        food_daily_range = (60, 200)
        discretionary_scale = rng.uniform(0.5, 1.0)
    elif income_tier == "mid":
        salary = rng.uniform(35000, 80000)
        rent_or_emi = rng.uniform(10000, 25000)
        food_daily_range = (100, 350)
        discretionary_scale = rng.uniform(0.8, 1.6)
    else:  # high
        salary = rng.uniform(80000, 200000)
        rent_or_emi = rng.uniform(25000, 60000)
        food_daily_range = (150, 400)
        discretionary_scale = rng.uniform(1.3, 2.5)

    is_emi = rng.random() < 0.4  # affects amount magnitude only; both are one category
    housing_label = "Rent/EMI"

    return {
        "user_id": f"user_{user_idx:04d}",
        "income_tier": income_tier,
        "salary": round(salary, 2),
        "salary_day": int(rng.integers(1, 6)),  # salary usually credits early month
        "rent_or_emi": round(rent_or_emi, 2),
        "housing_day": int(rng.integers(1, 8)),  # rent/EMI usually due early-mid month
        "housing_label": housing_label,
        "food_daily_range": food_daily_range,
        "food_txns_per_day_range": (2, 4),
        "discretionary_scale": discretionary_scale,
        "electricity_base": round(rng.uniform(800, 3500), 2),
        "electricity_day": int(rng.integers(1, 28)),
        "mobile_recharge_base": round(rng.uniform(199, 799), 2),
        "mobile_recharge_day": int(rng.integers(1, 28)),
    }


# ----------------------------------------------------------------------------
# Per-user transaction generation
# ----------------------------------------------------------------------------
def generate_user_transactions(profile: dict, start_date: date, num_days: int) -> list:
    txns = []
    user_id = profile["user_id"]
    dates = [start_date + timedelta(days=i) for i in range(num_days)]

    for d in dates:
        day_of_month = d.day
        is_weekend = d.weekday() >= 5  # Sat/Sun

        # --- Salary (once per month) ---
        if day_of_month == profile["salary_day"]:
            amount = profile["salary"] * rng.uniform(0.97, 1.03)  # small variance
            txns.append((user_id, d, round(amount, 2), "Salary", True))

        # --- Rent / EMI (fixed amount, fixed day) ---
        if day_of_month == profile["housing_day"]:
            txns.append(
                (user_id, d, round(profile["rent_or_emi"], 2), profile["housing_label"], False)
            )

        # --- Electricity bill (monthly, slightly variable) ---
        if day_of_month == profile["electricity_day"]:
            variance = rng.uniform(0.75, 1.4)  # seasonal swing (AC usage etc.)
            amount = profile["electricity_base"] * variance
            txns.append((user_id, d, round(amount, 2), "Electricity", False))

        # --- Mobile recharge (monthly, slightly variable) ---
        if day_of_month == profile["mobile_recharge_day"]:
            variance = rng.uniform(0.9, 1.15)
            amount = profile["mobile_recharge_base"] * variance
            txns.append((user_id, d, round(amount, 2), "Mobile Recharge", False))

        # --- Food (2-4 small transactions per day) ---
        num_food_txns = rng.integers(
            profile["food_txns_per_day_range"][0],
            profile["food_txns_per_day_range"][1] + 1,
        )
        lo, hi = profile["food_daily_range"]
        for _ in range(num_food_txns):
            amount = rng.uniform(lo, hi)
            txns.append((user_id, d, round(amount, 2), "Food & Drinks", False))

        # --- Discretionary spend (irregular, weekend-biased) ---
        # Weekend: higher probability + higher typical amount.
        discretionary_prob = 0.45 if is_weekend else 0.15
        if rng.random() < discretionary_prob:
            category = rng.choice(DISCRETIONARY_CATEGORIES, p=[0.55, 0.45])
            base = rng.uniform(300, 3500) if is_weekend else rng.uniform(150, 1500)
            amount = base * profile["discretionary_scale"]
            txns.append((user_id, d, round(amount, 2), category, False))

    return txns


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_txns = []
    for i in range(1, NUM_USERS + 1):
        profile = make_user_profile(i)
        user_txns = generate_user_transactions(profile, START_DATE, NUM_DAYS)
        all_txns.extend(user_txns)

    df = pd.DataFrame(all_txns, columns=["user_id", "date", "amount", "category", "is_credit"])
    df = df.sort_values(["user_id", "date"]).reset_index(drop=True)
    df.to_csv(OUTPUT_PATH, index=False)

    print(f"Wrote {len(df):,} transactions for {NUM_USERS} users over {NUM_DAYS} days")
    print(f"-> {OUTPUT_PATH}\n")

    print_sanity_checks(df)


def print_sanity_checks(df: pd.DataFrame):
    print("=" * 60)
    print("SANITY CHECKS")
    print("=" * 60)

    # --- Avg monthly spend per user (expenses only, i.e. is_credit == False) ---
    expenses = df[~df["is_credit"]].copy()
    expenses["date"] = pd.to_datetime(expenses["date"])
    expenses["year_month"] = expenses["date"].dt.to_period("M")

    monthly_spend_per_user = (
        expenses.groupby(["user_id", "year_month"])["amount"].sum().reset_index()
    )
    avg_monthly_spend = monthly_spend_per_user["amount"].mean()
    median_monthly_spend = monthly_spend_per_user["amount"].median()

    print(f"\nAvg monthly spend per user:    Rs {avg_monthly_spend:,.2f}")
    print(f"Median monthly spend per user: Rs {median_monthly_spend:,.2f}")

    # --- Avg monthly income per user (credits) ---
    credits = df[df["is_credit"]].copy()
    credits["date"] = pd.to_datetime(credits["date"])
    credits["year_month"] = credits["date"].dt.to_period("M")
    monthly_income_per_user = (
        credits.groupby(["user_id", "year_month"])["amount"].sum().reset_index()
    )
    avg_monthly_income = monthly_income_per_user["amount"].mean()
    print(f"Avg monthly income (salary) per user: Rs {avg_monthly_income:,.2f}")

    # --- Category distribution: total amount ---
    print("\n--- Category distribution: TOTAL AMOUNT (Rs) ---")
    cat_amount = df.groupby("category")["amount"].sum().sort_values(ascending=False)
    for cat, amt in cat_amount.items():
        pct = 100 * amt / df["amount"].sum()
        print(f"  {cat:<20s} Rs {amt:>15,.2f}  ({pct:5.1f}%)")

    # --- Category distribution: transaction count ---
    print("\n--- Category distribution: TRANSACTION COUNT ---")
    cat_count = df.groupby("category").size().sort_values(ascending=False)
    for cat, count in cat_count.items():
        pct = 100 * count / len(df)
        print(f"  {cat:<20s} {count:>8,d} txns  ({pct:5.1f}%)")

    # --- Quick plausibility flags (expense categories only -- Salary is
    # income and will legitimately have the largest total, that's not
    # what we're checking here) ---
    print("\n--- Plausibility checks (expense categories only) ---")
    expense_cat_amount = cat_amount.drop("Salary", errors="ignore")
    expense_cat_count = cat_count.drop("Salary", errors="ignore")
    largest_single_category = expense_cat_amount.index[0]
    most_frequent_category = expense_cat_count.index[0]
    print(f"Largest single category by amount: {largest_single_category} "
          f"{'[OK - expected Rent/EMI]' if largest_single_category == 'Rent/EMI' else '[CHECK]'}")
    print(f"Most frequent category by count:   {most_frequent_category} "
          f"{'[OK - expected Food & Drinks]' if most_frequent_category == 'Food & Drinks' else '[CHECK]'}")

    avg_food_txns_per_user_per_day = (
        df[df["category"] == "Food & Drinks"].shape[0] / NUM_USERS / NUM_DAYS
    )
    print(f"Avg food transactions/user/day:    {avg_food_txns_per_user_per_day:.2f} "
          f"{'[OK - expected 2-4]' if 2 <= avg_food_txns_per_user_per_day <= 4 else '[CHECK]'}")

    print(f"\nUnique users: {df['user_id'].nunique()} (expected {NUM_USERS})")
    print(f"Date range:   {df['date'].min()} to {df['date'].max()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
