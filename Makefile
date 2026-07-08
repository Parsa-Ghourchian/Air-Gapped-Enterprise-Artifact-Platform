.PHONY: up down restart logs ps init test backup restore sync-docker sync-python \
        apt-repos apt-configs apt-mini upload-apt-mini sbom audit upload-backup \
        size portal-build portal-logs clean-generated final-check

up:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f --tail=200

ps:
	docker compose ps

init:
	bash scripts/init-nexus.sh
	bash scripts/create-apt-proxy-repos.sh
	bash scripts/generate-apt-client-configs.sh

test:
	bash scripts/test-platform.sh

backup:
	bash scripts/backup-nexus.sh

upload-backup:
	bash scripts/upload-backup-to-nexus.sh

restore:
	@echo "Usage: make restore BACKUP=backups/nexus-backup-YYYYMMDD-HHMMSS.tar.gz"
	@test -n "$(BACKUP)" || exit 1
	bash scripts/restore-nexus.sh "$(BACKUP)"

sync-docker:
	bash scripts/sync-docker-images.sh

sync-python:
	bash scripts/sync-python-packages.sh

apt-repos:
	bash scripts/create-apt-proxy-repos.sh

apt-configs:
	bash scripts/generate-apt-client-configs.sh

apt-mini:
	bash scripts/create-apt-mini-repo.sh

upload-apt-mini:
	bash scripts/upload-apt-mini-repo-to-nexus.sh

sbom:
	@echo "Usage: make sbom IMAGE=localhost:5002/library/nginx:1.27"
	@test -n "$(IMAGE)" || exit 1
	bash scripts/generate-sbom.sh "$(IMAGE)"

audit:
	@echo "Usage: make audit IMAGE=localhost:5002/library/nginx:1.27"
	@test -n "$(IMAGE)" || exit 1
	bash scripts/audit-image.sh "$(IMAGE)"

size:
	bash scripts/size-report.sh

portal-build:
	docker compose build portal

portal-logs:
	docker logs -f airgap-portal

clean-generated:
	rm -rf reports/jobs/* offline-bundles/portal-jobs/* apt-mini-repos/*

final-check:
	docker compose config >/dev/null
	@echo "Compose config OK"
	@grep -q "portal.local" docker-compose.override.yml && echo "Portal route OK"
	@grep -q "airgap-portal" docker-compose.override.yml && echo "Portal service OK"
	@git add -n . >/tmp/airgap-git-dryrun.txt || true
	@echo "Git dry-run written to /tmp/airgap-git-dryrun.txt"
