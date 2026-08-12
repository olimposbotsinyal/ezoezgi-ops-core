# report_pnl.py
import json
from pathlib import Path
import pandas as pd

TR_TZ = "Europe/Istanbul"

def read_jsonl(path: str) -> pd.DataFrame:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return pd.json_normalize(rows)

def ensure_columns(df: pd.DataFrame) -> pd.DataFrame:
    # Flattened keys from json_normalize:
    # exchange, symbol, exit.ts_utc, pnl.realized_usdt, pnl.fees_usdt, pnl.funding_usdt, pnl.net_usdt
    for col in ["exchange", "symbol", "exit.ts_utc"]:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    for col in ["pnl.realized_usdt", "pnl.fees_usdt", "pnl.funding_usdt", "pnl.net_usdt"]:
        if col not in df.columns:
            df[col] = 0.0

    # numeric
    for col in ["pnl.realized_usdt", "pnl.fees_usdt", "pnl.funding_usdt", "pnl.net_usdt"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # compute net if empty (all zeros) but realized/fees/funding exist
    # safer: compute net_calc always, and use it if pnl.net_usdt is 0 but others not.
    df["pnl.net_calc_usdt"] = df["pnl.realized_usdt"] + df["pnl.fees_usdt"] + df["pnl.funding_usdt"]
    df["pnl.net_final_usdt"] = df["pnl.net_usdt"]
    mask_use_calc = (df["pnl.net_final_usdt"] == 0) & (df["pnl.net_calc_usdt"] != 0)
    df.loc[mask_use_calc, "pnl.net_final_usdt"] = df.loc[mask_use_calc, "pnl.net_calc_usdt"]

    # time columns
    df["exit_ts_utc"] = pd.to_datetime(df["exit.ts_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["exit_ts_utc"]).copy()
    df["exit_ts_tr"] = df["exit_ts_utc"].dt.tz_convert(TR_TZ)

    # daily bucket in TR (your risk reset is TR 00:00)
    df["date_tr"] = df["exit_ts_tr"].dt.date.astype(str)

    # monthly bucket
    df["month_tr"] = df["exit_ts_tr"].dt.to_period("M").astype(str)

    return df

def pivots(df: pd.DataFrame):
    # Exchange-based daily
    ex_daily = pd.pivot_table(
        df,
        index=["date_tr"],
        columns=["exchange"],
        values=["pnl.net_final_usdt", "pnl.realized_usdt", "pnl.fees_usdt", "pnl.funding_usdt"],
        aggfunc="sum",
        fill_value=0.0,
        margins=True,
        margins_name="TOTAL"
    )

    # Symbol-based daily (all exchanges combined)
    sym_daily = pd.pivot_table(
        df,
        index=["date_tr"],
        columns=["symbol"],
        values=["pnl.net_final_usdt"],
        aggfunc="sum",
        fill_value=0.0,
        margins=True,
        margins_name="TOTAL"
    )

    # Exchange + Symbol totals (lifetime)
    ex_sym_total = (
        df.groupby(["exchange", "symbol"], as_index=False)
          .agg(
              trades=("symbol", "size"),
              net_usdt=("pnl.net_final_usdt", "sum"),
              realized_usdt=("pnl.realized_usdt", "sum"),
              fees_usdt=("pnl.fees_usdt", "sum"),
              funding_usdt=("pnl.funding_usdt", "sum"),
          )
          .sort_values(["net_usdt"], ascending=True)  # worst to best
    )

    # Monthly summaries
    ex_monthly = (
        df.groupby(["month_tr", "exchange"], as_index=False)
          .agg(
              trades=("symbol", "size"),
              net_usdt=("pnl.net_final_usdt", "sum"),
              realized_usdt=("pnl.realized_usdt", "sum"),
              fees_usdt=("pnl.fees_usdt", "sum"),
              funding_usdt=("pnl.funding_usdt", "sum"),
          )
          .sort_values(["month_tr", "exchange"])
    )

    sym_monthly = (
        df.groupby(["month_tr", "symbol"], as_index=False)
          .agg(
              trades=("symbol", "size"),
              net_usdt=("pnl.net_final_usdt", "sum"),
              realized_usdt=("pnl.realized_usdt", "sum"),
              fees_usdt=("pnl.fees_usdt", "sum"),
              funding_usdt=("pnl.funding_usdt", "sum"),
          )
          .sort_values(["month_tr", "net_usdt"], ascending=[True, True])
    )

    # Overall KPI
    kpi = pd.DataFrame([{
        "trades": len(df),
        "net_usdt": df["pnl.net_final_usdt"].sum(),
        "realized_usdt": df["pnl.realized_usdt"].sum(),
        "fees_usdt": df["pnl.fees_usdt"].sum(),
        "funding_usdt": df["pnl.funding_usdt"].sum(),
        "avg_net_usdt": df["pnl.net_final_usdt"].mean() if len(df) else 0.0,
        "win_rate": (df["pnl.net_final_usdt"] > 0).mean() if len(df) else 0.0,
    }])

    return ex_daily, sym_daily, ex_sym_total, ex_monthly, sym_monthly, kpi

def export_excel(out_path: str, **sheets):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31])  # Excel sheet name limit

        # basic formatting
        workbook = writer.book
        money_fmt = workbook.add_format({"num_format": "#,##0.00"})
        pct_fmt = workbook.add_format({"num_format": "0.00%"})

        for sheet_name in writer.sheets:
            ws = writer.sheets[sheet_name]
            ws.set_column(0, 0, 14)  # first column
            ws.set_column(1, 200, 14, money_fmt)

        # special formatting for KPI win_rate if present
        if "KPI" in writer.sheets:
            ws = writer.sheets["KPI"]
            # Find win_rate column index roughly: write it as last col; set % format
            # Simple: set wide range; ok
            ws.set_column(0, 50, 14)
            # not perfect, but acceptable.

def main():
    input_path = "trades.jsonl"
    output_path = "reports/pnl_report.xlsx"

    df = read_jsonl(input_path)
    df = ensure_columns(df)

    ex_daily, sym_daily, ex_sym_total, ex_monthly, sym_monthly, kpi = pivots(df)

    # Also include raw trades for auditing
    raw_cols = [
        "exchange","symbol","exit_ts_utc","exit_ts_tr","date_tr","month_tr",
        "pnl.realized_usdt","pnl.fees_usdt","pnl.funding_usdt","pnl.net_final_usdt"
    ]
    raw = df[raw_cols].sort_values("exit_ts_utc")

    export_excel(
        output_path,
        KPI=kpi,
        RawTrades=raw,
        ExchangeDaily=ex_daily,
        SymbolDaily=sym_daily,
        ExchangeSymbolTotal=ex_sym_total,
        ExchangeMonthly=ex_monthly,
        SymbolMonthly=sym_monthly,
    )

    print(f"OK -> {output_path}")

if __name__ == "__main__":
    main()
