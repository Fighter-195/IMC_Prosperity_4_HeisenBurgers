import pandas as pd
import matplotlib.pyplot as plt

# 1. Load the data
# Update to your actual file name!
df = pd.read_csv('ROUND_1\prices_round_1_day_-1.csv', sep=';')

# 2. Clean the data (FIX FOR THE SPIKES TO ZERO)
# We drop any rows where mid_price is missing or exactly 0
df = df.dropna(subset=['mid_price'])
df = df[df['mid_price'] > 0]

# 3. Separate the assets
pepper = df[df['product'] == 'INTARIAN_PEPPER_ROOT'].copy()
osmium = df[df['product'] == 'ASH_COATED_OSMIUM'].copy()

# 4. Create the Plots in a HUGE scale!
# figsize=(16, 10) makes it widescreen and very tall
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 10), dpi=120)

# Graph 1: Pepper Root
ax1.ticklabel_format(useOffset=False, style='plain', axis='y')
ax1.plot(pepper['timestamp'], pepper['mid_price'], color='forestgreen', linewidth=1.5)
ax1.set_title('Intarian Pepper Root Mid Price (Cleaned)', fontsize=16, fontweight='bold')
ax1.set_ylabel('Price', fontsize=12)
ax1.grid(True, linestyle='--', alpha=0.6)

# Graph 2: Osmium
ax2.plot(osmium['timestamp'], osmium['mid_price'], color='dimgrey', linewidth=1.5)
ax2.set_title('Ash-Coated Osmium Mid Price', fontsize=16, fontweight='bold')
ax2.set_xlabel('Timestamp', fontsize=12)
ax2.set_ylabel('Price', fontsize=12)
ax2.grid(True, linestyle='--', alpha=0.6)

# Automatically adjust spacing
plt.tight_layout()

# Show the massive graph
plt.show()