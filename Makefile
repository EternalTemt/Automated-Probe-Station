# Makefile — панель быстрых команд проекта Automated Probe Station.
# Проект на Python, собирать нечего: цели ниже — именованные ярлыки
# для длинных команд (установка окружения, запуск GUI, headless-проверка).
# Внимание: отступы в рецептах — табы, не пробелы.

# Интерпретатор берём из виртуального окружения проекта напрямую —
# так не нужно делать source .asp_env/bin/activate перед каждой командой.
PY := .asp_env/bin/python

# Цель по умолчанию (make без аргументов) — подсказка.
.DEFAULT_GOAL := help

.PHONY: help install run test clean-data

help: ## Показать список доступных команд
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  make %-12s %s\n", $$1, $$2}'

install: ## Создать .asp_env/ (если нет) и поставить зависимости из requirements.txt
	if [ ! -d .asp_env ]; then python3 -m venv .asp_env; fi
	$(PY) -m pip install -r requirements.txt

run: ## Запустить GUI (demo/main.py)
	$(PY) demo/main.py

test: ## Headless-проверка всего сценария без железа и дисплея (61 проверка)
	QT_QPA_PLATFORM=offscreen $(PY) -u demo/smoke_check.py

clean-data: ## Очистить demo/data/ от результатов прогонов
	rm -rf demo/data/json demo/data/csv demo/data/graphs
