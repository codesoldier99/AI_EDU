PY ?= python3
PORT ?= 8900

.PHONY: help dev migrate seed demo test test-state lint replay gap clean reset check

help:
	@echo "苏格拉底式 AI 教学系统"
	@echo ""
	@echo "  make setup                    一键就绪（迁移 + 种子 + 演示数据）"
	@echo "  make dev                      启动本地环境（API + 前端，默认 :$(PORT)）"
	@echo "  make migrate                  数据库迁移"
	@echo "  make seed                     导入知识图谱 / 项目任务 / 知识库种子"
	@echo "  make demo N=60                生成虚拟班级演示数据"
	@echo "  make test                     全量测试"
	@echo "  make test-state               仅测状态层（改 BKT 后必跑）"
	@echo "  make lint                     静态检查（无 ruff 时退化为语法与规范自检）"
	@echo "  make replay STUDENT=<id>      从事件流重算状态，校验一致性"
	@echo "  make gap STUDENT=<id> TASK=<code>   打印任务知识缺口"
	@echo "  make check                    架构铁律自检"
	@echo "  make reset                    清空数据库（不动代码与种子）"
	@echo ""
	@echo "  没有 make 的环境：python3 aiedu.py <同名子命令>，完全等价"

setup: migrate seed demo
	@echo "\n就绪。执行 make dev 后打开 http://127.0.0.1:$(PORT)/"

dev:
	AIEDU_PORT=$(PORT) $(PY) -m apps.api.server

migrate:
	$(PY) scripts/migrate.py

seed:
	$(PY) scripts/seed.py

demo:
	$(PY) scripts/demo.py $(or $(N),60)

test:
	$(PY) -m unittest discover -s tests -t tests -v

test-state:
	cd tests && $(PY) -m unittest test_bkt test_replay -v

lint:
	$(PY) scripts/lint.py

check:
	cd tests && $(PY) -m unittest test_layering -v

replay:
	$(PY) scripts/replay.py $(STUDENT)

gap:
	$(PY) scripts/gap.py $(STUDENT) $(TASK)

reset:
	rm -rf var/aiedu.db var/aiedu.db-wal var/aiedu.db-shm
	@echo "已清空数据库。执行 make setup 重建。"

clean: reset
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
