default:
	llama-manager --dev

# Install Python deps into .venv and build the production frontend
build:
	scripts/build.sh

# Run llama-manager as a background service (macOS LaunchAgent / systemd unit)
register-service:
	scripts/service.sh register

# Stop and remove the background service
unregister-service:
	scripts/service.sh unregister

# Show whether the service is loaded and running
service-status:
	scripts/service.sh status
