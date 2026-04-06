#!/bin/bash
# Production deployment script for PCG and Context Bridge

echo "╔══════════════════════════════════════════════════════════╗"
echo "║   Personal Context Graph (PCG) Production Deployment     ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running as root for systemd operations
check_root() {
    if [ "$EUID" -ne 0 ]; then 
        log_warning "Some operations may require sudo privileges"
    fi
}

# Deploy systemd services
deploy_services() {
    log_info "Installing systemd services..."
    
    sudo cp /home/eleazar/Projects/AIHomelab/services/personal-kg/pcg.service /etc/systemd/system/
    sudo cp /home/eleazar/Projects/AIHomelab/services/context-bridge/context-bridge.service /etc/systemd/system/
    sudo systemctl daemon-reload
    
    log_success "Systemd services installed"
}

# Enable services
enable_services() {
    log_info "Enabling services for auto-start..."
    
    sudo systemctl enable pcg.service
    sudo systemctl enable context-bridge.service
    
    log_success "Services enabled"
}

# Start services
start_services() {
    log_info "Starting services..."
    
    sudo systemctl start pcg.service
    sleep 2
    sudo systemctl start context-bridge.service
    sleep 3
    
    log_success "Services started"
}

# Check service status
check_status() {
    log_info "Checking service status..."
    echo ""
    
    echo "PCG Service:"
    sudo systemctl is-active pcg.service 2>/dev/null | grep -q "active" && log_success "PCG is running" || log_error "PCG is not running"
    
    echo "Context Bridge Service:"
    sudo systemctl is-active context-bridge.service 2>/dev/null | grep -q "active" && log_success "Context Bridge is running" || log_error "Context Bridge is not running"
    
    echo ""
    log_info "Service Health:"
    curl -s http://localhost:8765/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"  PCG: {d.get('status','unknown')}\")" 2>/dev/null || echo "  PCG: unreachable"
    curl -s http://localhost:8764/health 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"  Context Bridge: {d.get('status','unknown')}\")" 2>/dev/null || echo "  Context Bridge: unreachable"
}

# Run health check
run_health_check() {
    log_info "Running comprehensive health check..."
    echo ""
    /home/eleazar/Projects/AIHomelab/services/personal-kg/pcg-health.sh
}

# Show logs
show_logs() {
    log_info "Recent service logs:"
    echo ""
    echo "--- PCG Service ---"
    sudo journalctl -u pcg.service --no-pager -n 10 2>/dev/null
    echo ""
    echo "--- Context Bridge Service ---"
    sudo journalctl -u context-bridge.service --no-pager -n 10 2>/dev/null
}

# Full deployment
full_deploy() {
    echo "Starting full production deployment..."
    echo ""
    
    check_root
    deploy_services
    enable_services
    start_services
    sleep 5
    
    echo ""
    check_status
    
    echo ""
    run_health_check
    
    echo ""
    log_success "Production deployment complete!"
    echo ""
    echo "Services are now:"
    echo "  • PCG: http://localhost:8765"
    echo "  • Context Bridge: http://localhost:8764"
    echo ""
    echo "Management commands:"
    echo "  sudo systemctl status pcg.service"
    echo "  sudo systemctl status context-bridge.service"
    echo "  /home/eleazar/Projects/AIHomelab/services/personal-kg/pcg-health.sh"
}

# Main menu
case "${1:-deploy}" in
    deploy|full)
        full_deploy
        ;;
    status)
        check_status
        ;;
    health)
        run_health_check
        ;;
    logs)
        show_logs
        ;;
    restart)
        log_info "Restarting services..."
        sudo systemctl restart pcg.service
        sudo systemctl restart context-bridge.service
        sleep 3
        check_status
        ;;
    stop)
        log_info "Stopping services..."
        sudo systemctl stop pcg.service
        sudo systemctl stop context-bridge.service
        log_success "Services stopped"
        ;;
    *)
        echo "Usage: $0 {deploy|status|health|logs|restart|stop}"
        echo ""
        echo "Commands:"
        echo "  deploy  - Full production deployment (default)"
        echo "  status  - Check service status"
        echo "  health  - Run health check"
        echo "  logs    - Show recent logs"
        echo "  restart - Restart services"
        echo "  stop    - Stop services"
        ;;
esac
