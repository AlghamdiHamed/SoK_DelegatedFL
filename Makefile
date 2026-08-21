SHELL := /bin/bash
GO_EXPECTED := go1.26.5
.PHONY: test validate init-results threat-models study1-build

test:
	python3 -m unittest discover -s tests -v

validate:
	python3 analysis/gates.py --root . --phase manuscript

init-results:
	python3 analysis/init_results.py --root .

threat-models:
	python3 analysis/generate_tables.py --root . --threat-model-only

study1-build:
	test "$$(go env GOVERSION)" = "$(GO_EXPECTED)" || { echo "Study I requires $(GO_EXPECTED); found $$(go env GOVERSION)" >&2; exit 1; }
	cd study1 && GOTOOLCHAIN=local go test ./... && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/owner ./cmd/owner && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/delegate ./cmd/delegate && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/aggregator ./cmd/aggregator && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/keygen ./cmd/keygen && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/validate ./cmd/validate && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/privacy ./cmd/privacy && GOTOOLCHAIN=local go build -trimpath -ldflags='-buildid=' -o bin/viewcheck ./cmd/viewcheck
