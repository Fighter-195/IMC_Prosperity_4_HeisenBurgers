import subprocess
import re
import os
import json
import random
from tqdm import tqdm

# --- CONFIG ---
TRADER_FILE = "1900.py"  # Ensure this matches your bot's filename
PROGRESS_FILE = "multi_sweep_progress.json"
NUM_TESTS = 200 # How many random combinations to test from the grid

# --- SWEEP RANGES ---
phi1_vals = [round(x, 2) for x in [-0.45, -0.35, -0.25]]
phi2_vals = [round(x, 2) for x in [0.25, 0.35, 0.45]]
divisor_vals = [18.0, 24.0, 30.0, 34.0]
power_vals = [1.5, 2.0, 2.5]
lr_vals = [0.005, 0.02, 0.04]
inv_vals = [0.01, 0.03, 0.05]
bb_alpha_vals = [0.3, 0.5, 0.8]
bb_thresh_vals = [2.0, 2.5, 3.0]
bb_window_vals = [10, 15, 20]

# --- GENERATE COMBINATIONS ---
all_combinations = []
for p1 in phi1_vals:
    for p2 in phi2_vals:
        # AR(2) Stationarity/Stability check
        if abs(p2) < 1 and (p1+p2) < 1 and (p2-p1) < 1:
            for div in divisor_vals:
                for pwr in power_vals:
                    for lr in lr_vals:
                        for inv in inv_vals:
                            for alpha in bb_alpha_vals:
                                for thresh in bb_thresh_vals:
                                    for window in bb_window_vals:
                                        all_combinations.append((p1, p2, div, pwr, lr, inv, alpha, thresh, window))

# --- LOAD PROGRESS ---
results = {}
if os.path.exists(PROGRESS_FILE):
    try:
        with open(PROGRESS_FILE, "r") as f:
            results = json.load(f)
        print(f"[*] Resuming! Found {len(results)} existing results.")
    except:
        pass

untested_combinations = [c for c in all_combinations if str(c) not in results]

if len(untested_combinations) > NUM_TESTS:
    print(f"[*] Randomly selecting {NUM_TESTS} out of {len(untested_combinations)} untested.")
    test_batch = random.sample(untested_combinations, NUM_TESTS)
else:
    test_batch = untested_combinations

# --- RUN SWEEP ---
if not test_batch:
    print("[*] All combinations already tested!")
else:
    # Emoji-free tqdm for Windows compatibility
    pbar = tqdm(test_batch, desc="Backtesting Sweep")
    for combo in pbar:
        p1, p2, div, pwr, lr, inv, alpha, thresh, window = combo
        
        # Inject ENV variables
        env = os.environ.copy()
        env["TOMATOES_INITIAL_PHI1"] = str(p1)
        env["TOMATOES_INITIAL_PHI2"] = str(p2)
        env["SWEEP_DIV"] = str(div)
        env["SWEEP_POW"] = str(pwr)
        env["SWEEP_LR"] = str(lr)
        env["SWEEP_INV"] = str(inv)
        env["SWEEP_BB_ALPHA"] = str(alpha)
        env["SWEEP_BB_THRESH"] = str(thresh)
        env["SWEEP_BB_WINDOW"] = str(window)

        # Execute backtester module
        COMMAND = f"python -m prosperity4mcbt {TRADER_FILE}"
        proc = subprocess.run(COMMAND, shell=True, capture_output=True, text=True, env=env)
        
        # Parse PnL from stdout
        match = re.search(r"Mean total PnL:\s*([-\d,.]+)", proc.stdout, re.IGNORECASE)
            
        if match:
            pnl = float(match.group(1).replace(',', ''))
            results[str(combo)] = pnl
            with open(PROGRESS_FILE, "w") as f:
                json.dump(results, f)
                
            pbar.set_postfix({"Best PnL": f"{max(results.values()):,.0f}"})
        else:
            print(f"\n[!] ERROR on combo {combo}. Check backtester output:")
            # Show snippet of error to help debug
            print(proc.stderr if proc.stderr else proc.stdout[:500])
            break 

# --- FINAL LEADERBOARD ---
print("\n[*] TOP 10 CONFIGURATIONS:")
sorted_res = sorted(results.items(), key=lambda x: x[1], reverse=True)[:10]

# Header
print(f"{'PnL':<10} | {'P1':<5} | {'P2':<5} | {'DIV':<5} | {'PWR':<4} | {'LR':<5} | {'INV':<4} | {'BB_A':<4} | {'BB_T':<4} | {'WIN':<3}")
print("-" * 85)

for combo_str, pnl in sorted_res:
    c = eval(combo_str)
    # Mapping indices: p1(0), p2(1), div(2), pwr(3), lr(4), inv(5), alpha(6), thresh(7), window(8)
    print(f"{pnl:<10,.0f} | {c[0]:<5} | {c[1]:<5} | {c[2]:<5} | {c[3]:<4} | {c[4]:<5} | {c[5]:<4} | {c[6]:<4} | {c[7]:<4} | {c[8]:<3}")

print(f"\n[*] Full results saved to {PROGRESS_FILE}")