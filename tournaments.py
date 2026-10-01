import os
import csv
import pandas as pd
from tabulate import tabulate
from iqoptionapi.iqapi import IQOptionClient


def fetch_accounts(client):
    return [(a.id, a.name, a.balance)
            for a in client.account_manager.get_tournament_accounts()]

def print_table(rows, headers=("ID", "Name", "Balance")):

    df = pd.DataFrame(
        [(acc.id, acc.name, acc.balance) for acc in client.account_manager.get_tournament_accounts()],
        columns=["ID", "Name", "Balance"]
    )

    print(df.to_string(index=False))
    # or sort/filter:
    # print(df[df["Balance"] > 0].sort_values("Balance", ascending=False))

def save_csv(rows, path, headers=("ID", "Name", "Balance"), append=False):
    exists = os.path.exists(path)
    mode = "a" if append else "w"
    with open(path, mode, newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not (append and exists):
            w.writerow(headers)
        w.writerows(rows)
    return path


if __name__ == "__main__":
    client = IQOptionClient()
    client.connect()
    if client._connected:
        rows = fetch_accounts(client)
        print_table(rows)
        path = save_csv(rows, "tournaments.csv")
        print(f"✅ Saved to {path}")