#!/bin/bash
# PCG Daily Consolidation (Dreaming) — runs at 6am UTC
# Iterates over all known users, running consolidation per-user.
cd /home/eleazar/Projects/AIHomelab/services/personal-kg/services/personal-kg
source venv/bin/activate

# Known users (from _USER_BOLT_URIS in pic_routes.py)
USER_IDS=(
  "dfd9379f-a9cd-4241-99e7-140f5e89e3cd"  # Eleazar
  "096f1fe3-16b3-4642-a1d9-2a11bf3843dd"  # Claudia
)

for UID in "${USER_IDS[@]}"; do
  python3 -c "
import asyncio
from consolidation import get_consolidation_service
result = asyncio.run(get_consolidation_service().run(lookback_hours=24, user_id='$UID'))
print(f\"Consolidation [$UID]: {result.get('observations_gathered',0)} obs -> {result.get('insights_created',0)} insights, {result.get('promotions',0)} promotions, {result.get('decayed',0)} decayed\")
" >> /home/eleazar/Projects/AIHomelab/services/personal-kg/logs/consolidation.log 2>&1
done
