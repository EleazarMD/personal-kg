#!/bin/bash
# PCG Health Check and Monitoring Script

PCG_URL="http://localhost:8765"
CONTEXT_BRIDGE_URL="http://localhost:8764"
LOG_FILE="/var/log/pcg-health.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

check_service() {
    local name=$1
    local url=$2
    local endpoint=$3
    
    response=$(curl -s -o /dev/null -w "%{http_code}" "${url}${endpoint}" 2>/dev/null)
    
    if [ "$response" == "200" ]; then
        echo -e "${GREEN}✅${NC} $name is healthy (HTTP $response)"
        return 0
    else
        echo -e "${RED}❌${NC} $name is unhealthy (HTTP $response)"
        return 1
    fi
}

check_pcg_components() {
    echo "=== PCG Component Status ==="
    
    # Check Neo4j via PCG health
    health=$(curl -s "${PCG_URL}/health" 2>/dev/null)
    if [ $? -eq 0 ]; then
        neo4j_status=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('neo4j','unknown'))" 2>/dev/null)
        chromadb_status=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('chromadb','unknown'))" 2>/dev/null)
        pic_status=$(echo "$health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('pic','unknown'))" 2>/dev/null)
        
        [ "$neo4j_status" == "connected" ] && echo -e "${GREEN}✅${NC} Neo4j: $neo4j_status" || echo -e "${RED}❌${NC} Neo4j: $neo4j_status"
        [ "$chromadb_status" == "connected" ] && echo -e "${GREEN}✅${NC} ChromaDB: $chromadb_status" || echo -e "${RED}❌${NC} ChromaDB: $chromadb_status"
        [ "$pic_status" == "initialized" ] && echo -e "${GREEN}✅${NC} PIC: $pic_status" || echo -e "${YELLOW}⚠️${NC} PIC: $pic_status"
    else
        echo -e "${RED}❌${NC} Could not get PCG health status"
    fi
}

check_endpoints() {
    echo ""
    echo "=== Endpoint Tests ==="
    
    # PCG Endpoints
    check_service "PCG Health" "$PCG_URL" "/health"
    check_service "PCG LIAM Dimensions" "$PCG_URL" "/api/liam/dimensions"
    check_service "PCG PIC Context" "$PCG_URL" "/api/pic/context"
    check_service "PCG KG Stats" "$PCG_URL" "/api/kg/stats"
    
    # Context Bridge Endpoints
    check_service "Context Bridge Health" "$CONTEXT_BRIDGE_URL" "/health"
}

check_resources() {
    echo ""
    echo "=== Resource Usage ==="
    
    # Check if services are running
    pcg_pid=$(pgrep -f "uvicorn api:app.*port 8765" | head -1)
    bridge_pid=$(pgrep -f "uvicorn api:app.*port 8764" | head -1)
    
    if [ -n "$pcg_pid" ]; then
        pcg_mem=$(ps -o rss= -p "$pcg_pid" 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')
        echo -e "${GREEN}✅${NC} PCG (PID $pcg_pid): Memory $pcg_mem"
    else
        echo -e "${RED}❌${NC} PCG not running"
    fi
    
    if [ -n "$bridge_pid" ]; then
        bridge_mem=$(ps -o rss= -p "$bridge_pid" 2>/dev/null | awk '{printf "%.1f MB", $1/1024}')
        echo -e "${GREEN}✅${NC} Context Bridge (PID $bridge_pid): Memory $bridge_mem"
    else
        echo -e "${RED}❌${NC} Context Bridge not running"
    fi
}

restart_services() {
    echo "Restarting services..."
    sudo systemctl restart pcg.service
    sudo systemctl restart context-bridge.service
    sleep 3
    echo "Services restarted. Checking health..."
    sleep 2
    main
}

show_logs() {
    echo "=== Recent Logs ==="
    echo "--- PCG Service ---"
    sudo journalctl -u pcg.service --no-pager -n 20 2>/dev/null || echo "No journal logs available"
    echo ""
    echo "--- Context Bridge Service ---"
    sudo journalctl -u context-bridge.service --no-pager -n 20 2>/dev/null || echo "No journal logs available"
}

main() {
    clear
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║     Personal Context Graph (PCG) Health Monitor          ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    
    check_service "PCG Service" "$PCG_URL" "/health"
    check_service "Context Bridge" "$CONTEXT_BRIDGE_URL" "/health"
    echo ""
    
    check_pcg_components
    check_endpoints
    check_resources
    
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "Health check completed at $(date)"
}

# Handle command line arguments
case "${1:-}" in
    restart)
        restart_services
        ;;
    logs)
        show_logs
        ;;
    *)
        main
        ;;
esac
