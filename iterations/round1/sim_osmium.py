import subprocess
import re
import os
import json
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# --- CONFIG ---
TRADER_FILE = "osmium.py"  
PROGRESS_FILE = "osmium_sweep_results.json"
NUM_TESTS = 150
MAX_WORKERS = 4  # Set to roughly the number of logical cores on your CPU (e.g., 4 to 8)

# --- SWEEP RANGES ---
q_vals = [1e-5, 5e-5, 1e-4]
r_vals = [0.1, 0.2, 0.3]
phi1_vals = [-0.3, -0.45, -0.6]
mr_strength_vals = [0.4, 0.5, 0.6]
inv_risk_vals = [0.01, 0.015, 0.02]

# --- GENERATE COMBINATIONS ---
all_combinations = []
for q in q_vals:
    for r in r_vals:
        for p1 in phi1_vals:
            for mr in mr_strength_vals:
                for inv in inv_risk_vals:
                    all_combinations.append((q, r, p1, mr, inv))

# --- LOAD PROGRESS ---
results = {}
if os.path.exists(PROGRESS_FILE):
    try:
        with open(PROGRESS_FILE, "r") as f:
            results = json.load(f)
        print(f"[*] Found {len(results)} existing results.")
    except:
        pass

untested = [c for c in all_combinations if str(c) not in results]
test_batch = random.sample(untested, min(len(untested), NUM_TESTS))

# --- MULTI-THREADED WORKER ---
def evaluate_config(combo):
    q, r, p1, mr, inv = combo
    env = os.environ.copy()
    
    # Inject variables into the environment for osmium.py to catch
    env["KALMAN_Q"] = str(q)
    env["KALMAN_R"] = str(r)
    env["PHI1"] = str(p1)
    env["MR_STRENGTH"] = str(mr)
    env["BASE_INV_RISK"] = str(inv)

    COMMAND = f"prosperity4btest {TRADER_FILE} 1"
    
    # Run the backtest silently
    proc = subprocess.run(COMMAND, shell=True, capture_output=True, text=True, env=env)
    
    # Parse output for "Total profit: <number>"
    pnl_matches = re.findall(r"Total profit:\s*([-\d,.]+)", proc.stdout, re.IGNORECASE)
    
    if pnl_matches:
        # Sum all daily profits printed in this run
        total_pnl = sum(float(m.replace(',', '')) for m in pnl_matches)
        return combo, total_pnl, None
    else:
        # Failsafe for crash/error
        return combo, None, proc.stderr or proc.stdout

# --- RUN SWEEP ---
if test_batch:
    print(f"[*] Starting parallel sweep of {len(test_batch)} configurations with {MAX_WORKERS} workers...")
    best_pnl = max(results.values()) if results else 0
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        futures = {executor.submit(evaluate_config, combo): combo for combo in test_batch}
        
        # Process results as they complete
        with tqdm(total=len(test_batch), desc="Sweeping") as pbar:
            for future in as_completed(futures):
                combo, pnl, error = future.result()
                
                if pnl is not None:
                    results[str(combo)] = pnl
                    if pnl > best_pnl:
                        best_pnl = pnl
                        
                    # Save progress continuously
                    with open(PROGRESS_FILE, "w") as f:
                        json.dump(results, f)
                else:
                    print(f"\n[!] Error with config {combo}:\n{error}")
                
                pbar.set_postfix({"Best PnL": f"{best_pnl:,.0f}"})
                pbar.update(1)

# --- LEADERBOARD ---
if results:
    print("\n" + "="*70)
    print("🏆 TOP 10 OSMIUM CONFIGURATIONS 🏆")
    print("="*70)
    sorted_res = sorted(results.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"{'PnL':<10} | {'Q':<8} | {'R':<5} | {'PHI1':<6} | {'MR':<5} | {'RISK':<6}")
    print("-" * 70)
    for combo_str, pnl in sorted_res:
        c = eval(combo_str)
        print(f"{pnl:<10,.0f} | {c[0]:<8.1e} | {c[1]:<5} | {c[2]:<6} | {c[3]:<5} | {c[4]:<6}")