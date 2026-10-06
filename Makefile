PY := .asp_env/bin/python
.DEFAULT_GOAL := help

.PHONY: help install run test clean-data

help: ## Показать список доступных команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-12s %s\n", $$1, $$2}'

install: ## Создать .asp_env и поставить зависимости из requirements.txt
	if [ ! -d .asp_env ]; then python3 -m venv .asp_env; fi
	$(PY) -m pip install -r requirements.txt

run: ## Запустить GUI (demo/main.py)
	$(PY) demo/main.py

test: ## Headless-проверка всего сценария без железа и дисплея (101 проверка)
	QT_QPA_PLATFORM=offscreen $(PY) -u demo/smoke_check.py

clean-data: ## Очистить demo/data/ от результатов прогонов
	rm -rf demo/data/*
