#!/bin/bash
# PCG Daily Consolidation (Dreaming) — runs at 6am UTC
# Clusters recent observations, creates insights, promotes patterns, decays stale entries.
cd /home/eleazar/Projects/AIHomelab/services/personal-kg/services/personal-kg
source venv/bin/activate
python3 -c "
import asyncio
from consolidation import get_consolidation_service
result = asyncio.run(get_consolidation_service().run(lookback_hours=24))
print(f\"Consolidation: {result[\"observations_gathered\"]} obs → {result[\"insights_created\"]} insights, {result[\"promotions\"]} promotions, {result[\"decayed\"]} decayed\")
" >> /home/eleazar/Projects/AIHomelab/services/personal-kg/logs/consolidation.log 2>&1
