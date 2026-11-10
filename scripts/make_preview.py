"""Create a synthetic static preview for README, with a visible warning."""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parents[1]
path = root / "data" / "processed" / "signals.csv"
out = root / "assets" / "demo_preview.png"
df = pd.read_csv(path)
fig, axes = plt.subplots(1, 2, figsize=(13.4, 5.3), constrained_layout=True)
fig.patch.set_facecolor('#f8fafc')
for ax in axes:
    ax.set_facecolor('white')
    ax.grid(alpha=.17)
    ax.spines[['top', 'right']].set_visible(False)
axes[0].barh(df.iloc[::-1]['ticker'], df.iloc[::-1]['opportunity'], color='#537eae')
axes[0].set_xlim(0, 100)
axes[0].set_title('Illustrative Opportunity Score', loc='left', fontweight='bold')
axes[0].set_xlabel('0-100 | not expected return')
axes[1].scatter(df['valuation'], df['rotation'], s=df['opportunity']*2.0, color='#4b998f', alpha=.85)
offsets = {"XLF": (5, 9), "XLRE": (7, 7), "XLK": (-7, 8), "XLU": (-12, 8),
           "XLE": (6, 9), "XLI": (5, -13), "XLC": (-25, -10),
           "XLV": (5, -13), "XLY": (4, 8), "XLB": (5, -12), "XLP": (6, -11)}
for _, row in df.iterrows():
    axes[1].annotate(row['ticker'], (row['valuation'], row['rotation']),
                     xytext=offsets.get(row['ticker'], (4, 4)),
                     textcoords='offset points', fontsize=8)
axes[1].axvline(50, color='grey', alpha=.35, linestyle='--')
axes[1].axhline(50, color='grey', alpha=.35, linestyle='--')
axes[1].set_xlim(0,100)
axes[1].set_ylim(0,100)
axes[1].set_xlabel('Valuation Score')
axes[1].set_ylabel('Rotation Score')
axes[1].set_title('Valuation x Rotation', loc='left', fontweight='bold')
fig.suptitle('Sector Intelligence Lab  |  SYNTHETIC DEMO - NOT MARKET DATA', fontsize=14, fontweight='bold')
out.parent.mkdir(parents=True, exist_ok=True)
fig.savefig(out, dpi=145)
plt.close(fig)
print(out)
