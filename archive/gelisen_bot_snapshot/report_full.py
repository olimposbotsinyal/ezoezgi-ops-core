# report_full.py
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

def prep_daily(daily_path: str) -> pd.DataFrame:
    df = read_jsonl(daily_path)

    # required columns
    req = ["date_tr", "equity.e0_usdt", "equity.eclose_usdt", "risk.kill_switch_triggered"]
    for c in req:
        if c not in df.columns:
            raise ValueError(f"daily_summary missing column: {c}")

    # numeric
    for c in ["equity.e0_usdt", "equity.eclose_usdt", "equity.emin_usdt", "equity.emax_usdt"]:
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["date_tr"] = pd.to_datetime(df["date_tr"], errors="coerce").dt.date.astype(str)
    df = df.dropna(subset=["date_tr"]).copy()
    df = df.sort_values("date_tr")

    # DD_close = (E0 - Eclose) / E0
    df["dd_close"] = (df["equity.e0_usdt"] - df["equity.eclose_usdt"]) / df["equity.e0_usdt"]

    # marker series: only show value on trigger days, else blank
    df["ks_marker_equity"] = df.apply(
        lambda r: r["equity.eclose_usdt"] if bool(r["risk.kill_switch_triggered"]) else None,
        axis=1
    )
    df["ks_marker_dd"] = df.apply(
        lambda r: r["dd_close"] if bool(r["risk.kill_switch_triggered"]) else None,
        axis=1
    )

    df["dd_close"] = (df["equity.e0_usdt"] - df["equity.eclose_usdt"]) / df["equity.e0_usdt"]

    # DD limit constant series (0.30)
    df["dd_limit"] = 0.30

    # markers
    df["ks_marker_equity"] = df.apply(
        lambda r:r["equity.eclose_usdt"] if bool(r["risk.kill_switch_triggered"]) else None,
        axis=1
    )
    df["ks_marker_dd"] = df.apply(
        lambda r:r["dd_close"] if bool(r["risk.kill_switch_triggered"]) else None,
        axis=1
    )
    return df

def prep_trades(trades_path: str) -> pd.DataFrame:
    df = read_jsonl(trades_path)

    # minimal required
    for c in ["exchange", "symbol", "exit.ts_utc"]:
        if c not in df.columns:
            raise ValueError(f"trades missing column: {c}")

    # user fields optional but requested
    for c in ["user_id", "user_name"]:
        if c not in df.columns:
            df[c] = None

    # pnl fields
    for c in ["pnl.realized_usdt", "pnl.fees_usdt", "pnl.funding_usdt", "pnl.net_usdt"]:
        if c not in df.columns:
            df[c] = 0.0
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0)

    df["pnl.net_calc_usdt"] = df["pnl.realized_usdt"] + df["pnl.fees_usdt"] + df["pnl.funding_usdt"]
    df["pnl.net_final_usdt"] = df["pnl.net_usdt"]
    mask_use_calc = (df["pnl.net_final_usdt"] == 0) & (df["pnl.net_calc_usdt"] != 0)
    df.loc[mask_use_calc, "pnl.net_final_usdt"] = df.loc[mask_use_calc, "pnl.net_calc_usdt"]

    # time
    df["exit_ts_utc"] = pd.to_datetime(df["exit.ts_utc"], utc=True, errors="coerce")
    df = df.dropna(subset=["exit_ts_utc"]).copy()
    df["exit_ts_tr"] = df["exit_ts_utc"].dt.tz_convert(TR_TZ)
    df["date_tr"] = df["exit_ts_tr"].dt.date.astype(str)

    return df

def make_users_sheet(trades: pd.DataFrame) -> pd.DataFrame:
    # "kullanıcıların ne yaptığını görelim" => özet aktivite tablosu
    g = (trades
         .groupby(["user_id", "user_name", "exchange"], dropna=False, as_index=False)
         .agg(
            trades=("symbol", "size"),
            symbols=("symbol", lambda s: s.nunique()),
            net_usdt=("pnl.net_final_usdt", "sum"),
            gross_win_usdt=("pnl.net_final_usdt", lambda x: x[x > 0].sum()),
            gross_loss_usdt=("pnl.net_final_usdt", lambda x: x[x < 0].sum()),
            win_rate=("pnl.net_final_usdt", lambda x: (x > 0).mean() if len(x) else 0.0),
            first_trade_tr=("exit_ts_tr", "min"),
            last_trade_tr=("exit_ts_tr", "max"),
         )
         .sort_values(["net_usdt"], ascending=True)
    )
    return g

def make_trades_by_user_symbol(trades: pd.DataFrame) -> pd.DataFrame:
    # user + symbol + exchange bazlı performans
    g = (trades
         .groupby(["user_id", "user_name", "exchange", "symbol"], dropna=False, as_index=False)
         .agg(
            trades=("symbol", "size"),
            net_usdt=("pnl.net_final_usdt", "sum"),
            realized_usdt=("pnl.realized_usdt", "sum"),
            fees_usdt=("pnl.fees_usdt", "sum"),
            funding_usdt=("pnl.funding_usdt", "sum"),
         )
         .sort_values(["net_usdt"], ascending=True)
    )
    return g

def export_excel(daily: pd.DataFrame, trades: pd.DataFrame, out_path: str):
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    users = make_users_sheet(trades)
    user_symbol = make_trades_by_user_symbol(trades)

    # Daily join with trades daily pnl (optional)
    trades_day = (trades.groupby("date_tr", as_index=False)
                        .agg(trades=("symbol", "size"), net_usdt=("pnl.net_final_usdt", "sum")))
    daily2 = daily.merge(trades_day, on="date_tr", how="left").fillna({"trades": 0, "net_usdt": 0.0})

    with pd.ExcelWriter(out, engine="xlsxwriter") as writer:
        daily2.to_excel(writer, sheet_name="Daily", index=False)
        trades.to_excel(writer, sheet_name="RawTrades", index=False)
        users.to_excel(writer, sheet_name="Users", index=False)
        user_symbol.to_excel(writer, sheet_name="UserSymbol", index=False)

        workbook = writer.book
        money_fmt = workbook.add_format({"num_format": "#,##0.00"})
        pct_fmt = workbook.add_format({"num_format": "0.00%"})
        date_fmt = workbook.add_format({"num_format": "yyyy-mm-dd"})

        # Format columns a bit
        for sh in ["Daily", "Users", "UserSymbol", "RawTrades"]:
            ws = writer.sheets[sh]
            ws.set_column(0, 0, 14)
            ws.set_column(1, 50, 16)

        # Add Charts sheet
        chart_ws = workbook.add_worksheet("Charts")
        # Put chart data references from Daily sheet
        daily_ws = writer.sheets["Daily"]

        # Find column indexes by name (robust)
        cols = {name: i for i, name in enumerate(daily2.columns)}
        def xl_col(n):  # 0->A
            s = ""
            n0 = n
            while True:
                n, r = divmod(n, 26)
                s = chr(ord("A") + r) + s
                if n == 0:
                    break
                n -= 1
            return s

        nrows = len(daily2) + 1  # header row included in Excel (1-based)
        # Excel ranges: e.g. 'Daily'!$A$2:$A$N
        def rng(col_name, start_row=2, end_row=None):
            if end_row is None:
                end_row = nrows
            c = xl_col(cols[col_name])
            return f"=Daily!${c}${start_row}:${c}${end_row}"

        # Equity curve chart
        equity_chart = workbook.add_chart({"type": "line"})
        equity_chart.add_series({
            "name": "Eclose (USDT)",
            "categories": rng("date_tr"),
            "values": rng("equity.eclose_usdt"),
        })
        # Kill-switch markers on equity
        equity_chart.add_series({
            "name": "KillSwitch Trigger",
            "categories": rng("date_tr"),
            "values": rng("ks_marker_equity"),
            "marker": {"type": "circle", "size": 7, "border": {"color": "red"}, "fill": {"color": "red"}},
            "line": {"none": True},
        })
        equity_chart.set_title({"name": "Equity Curve (Eclose) + KillSwitch Triggers"})
        equity_chart.set_x_axis({"name": "Date (TR)"})
        equity_chart.set_y_axis({"name": "USDT"})
        equity_chart.set_size({"width": 960, "height": 360})
        chart_ws.insert_chart("A1", equity_chart)

        # DD curve chart
        dd_chart = workbook.add_chart({"type": "line"})
        dd_chart.add_series({
            "name": "DD Close",
            "categories": rng("date_tr"),
            "values": rng("dd_close"),
        })
        dd_chart.add_series({
            "name": "KillSwitch Trigger",
            "categories": rng("date_tr"),
            "values": rng("ks_marker_dd"),
            "marker": {"type": "circle", "size": 7, "border": {"color": "red"}, "fill": {"color": "red"}},
            "line": {"none": True},
        })
        dd_chart.set_title({"name": "Drawdown (DD) Curve + Triggers"})
        dd_chart.set_x_axis({"name": "Date (TR)"})
        dd_chart.set_y_axis({"name": "DD"})
        dd_chart.set_size({"width": 960, "height": 360})
        chart_ws.insert_chart("A21", dd_chart)
        dd_chart = workbook.add_chart({"type": "line"})
        dd_chart.add_series({
            "name": "DD Close",
            "categories": rng("date_tr"),
            "values": rng("dd_close"),
        })

        # DD limit line
        dd_chart.add_series({
            "name": "DD Limit (0.30)",
            "categories": rng("date_tr"),
            "values": rng("dd_limit"),
            "line": {"color": "orange", "width": 2},
        })

        dd_chart.add_series({
            "name": "KillSwitch Trigger",
            "categories": rng("date_tr"),
            "values": rng("ks_marker_dd"),
            "marker": {"type": "circle", "size": 7, "border": {"color": "red"}, "fill": {"color": "red"}},
            "line": {"none": True},
        })

        # Optional: DD limit line (0.30) as constant series: easiest by adding a column
        # (Skip here to keep it simple.)

    print(f"OK -> {out}")

def main():
    daily = prep_daily("daily_summary.jsonl")
    trades = prep_trades("trades.jsonl")
    export_excel(daily, trades, "reports/full_report.xlsx")

if __name__ == "__main__":
    main()
