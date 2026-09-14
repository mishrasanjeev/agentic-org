# Developer entry points. See docs/quickstart-local.md.
#
#   make dev     build and start the local stack, wait until it is healthy, smoke-test it
#   make down    stop the stack (data volumes are kept)
#   make clean   stop the stack and delete its data volumes
#   make logs    follow the stack's logs
#   make ps      show service status

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE ?= docker compose -f docker-compose.dev.yml

.PHONY: help dev down clean logs ps

help:
	@echo "make dev     build and start the local stack and smoke-test it"
	@echo "make down    stop the stack (keeps data)"
	@echo "make clean   stop the stack and delete its data volumes"
	@echo "make logs    follow logs"
	@echo "make ps      service status"

dev:
	$(COMPOSE) build
	$(COMPOSE) up -d --wait --wait-timeout 600
	bash scripts/dev_stack_smoke.sh

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down --volumes --remove-orphans

logs:
	$(COMPOSE) logs -f

ps:
	$(COMPOSE) ps
