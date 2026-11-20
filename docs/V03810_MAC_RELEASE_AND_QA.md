# TimePilot v0.38.10 Mac physical release gate

A physical PASS requires CPython 3.12.12 with DuckDB, PyArrow, Perspective and a reviewed JS/WASM bundle. The source ZIP alone is not an offline runtime.

Run `python scripts/target_mac_acceptance_v03810.py --run --mode all --out reports/v03810/mac_acceptance.json` on the target Mac. This does not install or rewrite runtime datasets. Each stage has its own receipt to prevent overwriting evidence.

Official offline builds must additionally pass exact ZIP verification and restored runtime/browser tests. Never treat inventory_only as acceptance.
