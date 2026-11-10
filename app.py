"""Read-only Streamlit viewer. Not an investment recommendation or live price feed."""
from pathlib import Path

import pandas as pd
import streamlit as st

from sectorlab.config import SECTORS

ROOT = Path(__file__).resolve().parent
SIGNALS = ROOT / "data" / "processed" / "signals.csv"

st.set_page_config(page_title="Sector Intelligence Lab", page_icon="📊", layout="wide")
st.title("Sector Intelligence Lab")
st.caption("US Equity Sector Valuation & Rotation Research | Research Scaffold v0.1")
st.error("SYNTHETIC DEMO DATA — NOT LIVE MARKET DATA. Do not use for investment decisions.")
if not SIGNALS.exists():
    st.info("Run `python scripts/make_demo_data.py` then `python scripts/run_pipeline.py` first.")
    st.stop()

signals = pd.read_csv(SIGNALS)
if not (signals["provenance"] == "SYNTHETIC_DEMO").all():
    st.warning("Provenance has changed. Independently verify all sources and point-in-time coverage.")

signals["Sector"] = signals["ticker"].map(SECTORS)
valid = signals.dropna(subset=["valuation", "rotation", "opportunity"])

c1, c2, c3 = st.columns(3)
c1.metric("Sectors monitored", len(signals))
c2.metric("Scored with complete inputs", len(valid))
c3.metric("Model status", "Research / Not validated")

st.subheader("Sector Ranking (synthetic illustration)")
st.dataframe(
    signals[["ticker", "Sector", "valuation", "momentum", "revision", "quality",
             "rotation", "opportunity", "classification", "data_status"]],
    use_container_width=True, hide_index=True,
)

st.subheader("Valuation vs Rotation (synthetic illustration)")
if valid.empty:
    st.info("No complete scores. See Data Quality & Model Status.")
else:
    st.scatter_chart(valid, x="valuation", y="rotation", size="opportunity", color="ticker")

st.subheader("Data Quality & Model Status")
st.write("Missing components are **not imputed**. A complete score does not establish data accuracy, predictive power, or fair value.")
st.write("Valuation scores use a past-only robust median/MAD baseline. Relative valuation and constituent-level normalization are future work.")
st.write("See `docs/METHODOLOGY.md` and `docs/DATA_POLICY.md` before interpreting the model.")
