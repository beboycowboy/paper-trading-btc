import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# ============================================================
# PAPER TRADING - Estrategia #2233
# Familia: RSI_REVERSION | Dirección: LONG
# ============================================================
# ENTRADA:
#   RSI(10) cruza HACIA ABAJO 35.03
# SALIDA:
#   RSI(10) > 73.93
# Stop Loss: 6.6%
# Take Profit: 19.0%
# Trailing stop: OFF
#
# IMPORTANTE:
# - NO envía órdenes reales.
# - Usa velas cerradas de 1 hora de Yahoo Finance.
# - Evalúa señales, SL y TP al cierre de cada vela de 1h,
#   igual que el backtester multiestrategia.
# ============================================================

SYMBOL = "BTC-USD"
INTERVAL = "1h"
LOOKBACK = "60d"
POLL_SECONDS = 60

STARTING_BALANCE = 10_000.0
FEE_RATE = 0.001  # 0.10% por lado

RSI_PERIOD = 10
RSI_ENTRY = 35.03
RSI_EXIT = 73.93
STOP_LOSS = 0.066
TAKE_PROFIT = 0.190

# Railway define automáticamente RAILWAY_VOLUME_MOUNT_PATH si conectas un Volume.
# Si no existe, se guardan los archivos junto al script.
DATA_DIR = Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "."))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STATE_FILE = DATA_DIR / "paper_state_2233.json"
TRADES_FILE = DATA_DIR / "paper_trades_2233.csv"
EQUITY_FILE = DATA_DIR / "paper_equity_2233.csv"


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def default_state() -> dict:
    return {
        "starting_balance": STARTING_BALANCE,
        "cash": STARTING_BALANCE,
        "qty": 0.0,
        "entry_price": None,
        "entry_time": None,
        "entry_total_cost": None,
        "last_processed_candle": None,
        "closed_trades": 0,
        "wins": 0,
        "losses": 0,
    }


def load_state() -> dict:
    if not STATE_FILE.exists():
        return default_state()

    with STATE_FILE.open("r", encoding="utf-8") as f:
        saved = json.load(f)

    state = default_state()
    state.update(saved)
    return state


def save_state(state: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    tmp.replace(STATE_FILE)


def append_csv(path: Path, row: dict) -> None:
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def fetch_closed_hourly_data() -> pd.DataFrame:
    df = yf.download(
        SYMBOL,
        period=LOOKBACK,
        interval=INTERVAL,
        progress=False,
        auto_adjust=False,
        threads=False,
    )

    if df.empty:
        raise RuntimeError("Yahoo Finance no devolvió datos.")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df = df.dropna(subset=["Close"]).copy()

    # yfinance puede incluir la vela actual todavía abierta.
    now_utc = pd.Timestamp.now(tz="UTC")
    if df.index.tz is None:
        idx_utc = df.index.tz_localize("UTC")
    else:
        idx_utc = df.index.tz_convert("UTC")

    closed_mask = (idx_utc + pd.Timedelta(hours=1)) <= now_utc
    df = df.loc[closed_mask].copy()

    if len(df) < RSI_PERIOD + 5:
        raise RuntimeError("No hay suficientes velas cerradas.")

    df["RSI"] = rsi(df["Close"].astype(float), RSI_PERIOD)
    return df


def current_equity(state: dict, price: float) -> float:
    if state["qty"] > 0:
        # Valor aproximado si se liquidara ahora, incluyendo comisión de salida.
        return state["cash"] + state["qty"] * price * (1 - FEE_RATE)
    return state["cash"]


def open_position(state: dict, price: float, candle_time: str) -> None:
    cash = float(state["cash"])
    if cash <= 0:
        return

    # Usa todo el saldo ficticio y paga comisión de entrada.
    qty = cash / (price * (1 + FEE_RATE))
    total_cost = qty * price * (1 + FEE_RATE)

    state["cash"] = max(0.0, cash - total_cost)
    state["qty"] = qty
    state["entry_price"] = price
    state["entry_time"] = candle_time
    state["entry_total_cost"] = total_cost

    print(
        f"🟢 COMPRA PAPER | {candle_time} | BTC ${price:,.2f} | "
        f"qty={qty:.8f}",
        flush=True,
    )


def close_position(state: dict, price: float, candle_time: str, reason: str) -> None:
    qty = float(state["qty"])
    if qty <= 0:
        return

    gross_proceeds = qty * price
    exit_fee = gross_proceeds * FEE_RATE
    net_proceeds = gross_proceeds - exit_fee

    entry_total_cost = float(state["entry_total_cost"])
    pnl_usd = net_proceeds - entry_total_cost
    pnl_pct = pnl_usd / entry_total_cost if entry_total_cost else 0.0

    state["cash"] += net_proceeds
    state["qty"] = 0.0
    state["closed_trades"] += 1

    if pnl_usd > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    append_csv(
        TRADES_FILE,
        {
            "entry_time": state["entry_time"],
            "exit_time": candle_time,
            "entry_price": round(float(state["entry_price"]), 2),
            "exit_price": round(float(price), 2),
            "qty": round(qty, 8),
            "reason": reason,
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct * 100, 4),
            "balance_after": round(float(state["cash"]), 2),
        },
    )

    emoji = "✅" if pnl_usd > 0 else "❌"
    print(
        f"{emoji} VENTA PAPER | {candle_time} | {reason} | "
        f"BTC ${price:,.2f} | PnL {pnl_pct*100:+.2f}% "
        f"(${pnl_usd:+,.2f}) | Balance ${state['cash']:,.2f}",
        flush=True,
    )

    state["entry_price"] = None
    state["entry_time"] = None
    state["entry_total_cost"] = None


def process_latest_closed_candle(state: dict) -> bool:
    df = fetch_closed_hourly_data()

    row = df.iloc[-1]
    prev = df.iloc[-2]
    candle_time = pd.Timestamp(df.index[-1]).isoformat()

    if state["last_processed_candle"] == candle_time:
        return False

    price = float(row["Close"])
    rsi_now = float(row["RSI"])
    rsi_prev = float(prev["RSI"])

    print("\n" + "=" * 78, flush=True)
    print(f"🕒 Nueva vela cerrada: {candle_time}", flush=True)
    print(
        f"BTC: ${price:,.2f} | RSI({RSI_PERIOD}): {rsi_now:.2f} "
        f"| RSI anterior: {rsi_prev:.2f}",
        flush=True,
    )

    if state["qty"] <= 0:
        # Replica el generador/backtester:
        # entry = (rsi < entry) & (rsi.shift(1) >= entry)
        entry_signal = (
            not np.isnan(rsi_now)
            and not np.isnan(rsi_prev)
            and rsi_now < RSI_ENTRY
            and rsi_prev >= RSI_ENTRY
        )

        print(
            f"Entrada: RSI cruza bajo {RSI_ENTRY:.2f}: "
            f"{'SÍ' if entry_signal else 'NO'}",
            flush=True,
        )

        if entry_signal:
            open_position(state, price, candle_time)

    else:
        entry_price = float(state["entry_price"])
        gross_move = (price - entry_price) / entry_price

        stop_hit = gross_move <= -STOP_LOSS
        tp_hit = gross_move >= TAKE_PROFIT
        exit_signal = not np.isnan(rsi_now) and rsi_now > RSI_EXIT

        print(
            f"Posición desde ${entry_price:,.2f} | "
            f"Movimiento bruto {gross_move*100:+.2f}% | "
            f"RSI salida > {RSI_EXIT:.2f}: {'SÍ' if exit_signal else 'NO'}",
            flush=True,
        )

        if stop_hit:
            close_position(state, price, candle_time, "STOP_LOSS")
        elif tp_hit:
            close_position(state, price, candle_time, "TAKE_PROFIT")
        elif exit_signal:
            close_position(state, price, candle_time, "RSI_EXIT")

    equity = current_equity(state, price)
    total_return = equity / float(state["starting_balance"]) - 1.0
    win_rate = (
        state["wins"] / state["closed_trades"] * 100.0
        if state["closed_trades"] > 0
        else 0.0
    )

    append_csv(
        EQUITY_FILE,
        {
            "time": candle_time,
            "price": round(price, 2),
            "rsi10": round(rsi_now, 4),
            "equity": round(equity, 2),
            "return_pct": round(total_return * 100, 4),
            "position_open": state["qty"] > 0,
            "closed_trades": state["closed_trades"],
            "win_rate_pct": round(win_rate, 2),
        },
    )

    state["last_processed_candle"] = candle_time
    save_state(state)

    print(
        f"💰 Equity paper: ${equity:,.2f} | "
        f"Rendimiento: {total_return*100:+.2f}% | "
        f"Trades cerrados: {state['closed_trades']} | "
        f"Win rate: {win_rate:.1f}%",
        flush=True,
    )

    return True


def main() -> None:
    state = load_state()

    print("=" * 78, flush=True)
    print("🧪 PAPER TRADING - Estrategia #2233", flush=True)
    print("=" * 78, flush=True)
    print(f"Activo: {SYMBOL} | Timeframe: {INTERVAL}", flush=True)
    print(f"Capital inicial: ${state['starting_balance']:,.2f}", flush=True)
    print(f"Comisión: {FEE_RATE*100:.3f}% por lado", flush=True)
    print(
        f"Entrada: RSI({RSI_PERIOD}) cruza bajo {RSI_ENTRY:.2f}",
        flush=True,
    )
    print(f"Salida: RSI({RSI_PERIOD}) > {RSI_EXIT:.2f}", flush=True)
    print(
        f"SL: {STOP_LOSS*100:.1f}% | TP: {TAKE_PROFIT*100:.1f}% | "
        f"Trailing: OFF",
        flush=True,
    )
    print(f"Datos persistentes en: {DATA_DIR.resolve()}", flush=True)
    print("NO envía órdenes reales.", flush=True)
    print("=" * 78, flush=True)

    while True:
        try:
            processed = process_latest_closed_candle(state)
            if not processed:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"[{now}] Sin nueva vela cerrada.", flush=True)
        except Exception as exc:
            print(f"⚠️ Error temporal: {exc}", flush=True)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
