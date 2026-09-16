ROOT := $(CURDIR)
GO_DIR   ?= ../mibee-eye-go/internal/web/static
RS_DIR   ?= ../mibee-eye-rs/static
NB_DIR   ?= ../mibee-eye-notebook/crates/web/static

.PHONY: help sync-go sync-rs sync-notebook mock check

help:
	@echo "targets:"
	@echo "  sync-go       copy static/ into mibee-eye-go/internal/web/static/"
	@echo "  sync-rs       copy static/ into mibee-eye-rs/static/"
	@echo "  sync-notebook copy static/ into mibee-eye-notebook/crates/web/static/"
	@echo "  mock          serve static/ against a SPEC-conformant mock API (:8090)"

# rs and Go embed the directory contents directly; clean the destination
# first so removed modules never linger in the product repos.
RS_SYNC = $(RS_DIR)/index.html $(RS_DIR)/style.css $(RS_DIR)/js

sync-rs:
	@test -d ../mibee-eye-rs || (echo "mibee-eye-rs not found"; exit 1)
	rm -rf $(RS_DIR)/js
	cp -R $(ROOT)/static/. $(RS_DIR)/
	@echo "synced -> $(RS_DIR)"

sync-go:
	@test -d ../mibee-eye-go || (echo "mibee-eye-go not found"; exit 1)
	rm -rf $(GO_DIR)/js
	cp -R $(ROOT)/static/. $(GO_DIR)/
	@echo "synced -> $(GO_DIR)"

sync-notebook:
	@test -d ../mibee-eye-notebook || (echo "mibee-eye-notebook not found"; exit 1)
	rm -rf $(NB_DIR)/js $(NB_DIR)/src $(NB_DIR)/app.js $(NB_DIR)/app.bundle.js $(NB_DIR)/index.template.html
	cp -R $(ROOT)/static/. $(NB_DIR)/
	@echo "synced -> $(NB_DIR)"

mock:
	python3 tools/mock_server.py
