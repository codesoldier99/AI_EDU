PY ?= python3
PORT ?= 8900

.PHONY: help dev migrate seed demo mock-llm test test-state test-study test-kpmatch lint replay gap practice skills exam exam-setup kpmatch signals deck clean reset check

help:
	@echo "院长实验班 AI 教学系统"
	@echo ""
	@echo "  make setup                    一键就绪（迁移 + 种子 + 演示数据）"
	@echo "  make dev                      启动本地环境（API + 前端，默认 :$(PORT)）"
	@echo "  make migrate                  数据库迁移"
	@echo "  make seed                     导入知识图谱 / 项目任务 / 知识库种子"
	@echo "  make demo N=60                生成虚拟班级演示数据"
	@echo "  make mock-llm                 本机假底座（无 Key 也能演示接入大模型）"
	@echo "  make test                     全量测试"
	@echo "  make test-state               仅测状态层（改 BKT 后必跑）"
	@echo "  make test-study               仅测学习工作台（题库/判分/解题/调研/图示）"
	@echo "  make test-exam                仅测在线考试（凭据/计时/判分/切线/权限）"
	@echo "  make lint                     静态检查（无 ruff 时退化为语法与规范自检）"
	@echo "  make replay STUDENT=<id>      从事件流重算状态，校验一致性"
	@echo "  make gap STUDENT=<id> TASK=<code>   打印任务知识缺口"
	@echo "  make practice STUDENT=<id> [TASK=<code>]  打印此刻该练什么及其理由"
	@echo "  make skills                   列出已装载的教学技能包"
	@echo ""
	@echo "  make kpmatch A=\"propose PRJ-DAC\"  知识点自动匹配（候选进待审队列）"
	@echo "                                子命令：propose/queue/why/accept/reject/stats/eval"
	@echo "  make signals                  采集全部项目的五类信号"
	@echo "  make deck [D=pm]              生成汇报 PPT（D=all 院领导版 / D=pm 教师版）"
	@echo ""
	@echo "  make exam-setup               导入并发布选拔考卷 + 签发准考证"
	@echo "  make exam A=\"rank ML-SELECT-2026\"   考试运维（import/publish/tickets/"
	@echo "                                monitor/sweep/pending/score/rank/export）"
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

mock-llm:
	$(PY) scripts/mock_llm.py --port $(or $(MOCKPORT),8910)

test:
	$(PY) -m unittest discover -s tests -t tests -v

test-state:
	cd tests && $(PY) -m unittest test_bkt test_replay -v

test-study:
	cd tests && $(PY) -m unittest test_tools test_quiz test_study test_skills -v

test-exam:
	cd tests && $(PY) -m unittest test_exam -v

test-kpmatch:
	cd tests && $(PY) -m unittest test_kpmatch -v

lint:
	$(PY) scripts/lint.py

check:
	cd tests && $(PY) -m unittest test_layering -v

replay:
	$(PY) scripts/replay.py $(STUDENT)

gap:
	$(PY) scripts/gap.py $(STUDENT) $(TASK)

practice:
	$(PY) scripts/practice.py $(STUDENT) $(TASK)

skills:
	$(PY) scripts/skills.py

kpmatch:
	$(PY) scripts/kpmatch.py $(A)

signals:
	$(PY) scripts/signals.py

deck:
	$(PY) scripts/make_deck.py $(or $(D),all)

exam:
	$(PY) scripts/exam.py $(A)

exam-setup:
	$(PY) scripts/exam.py import data/seed/exam_ml_selection.yaml
	$(PY) scripts/exam.py publish ML-SELECT-2026
	@echo ""
	@echo "签发准考证：make exam A=\"tickets ML-SELECT-2026\""
	@echo "考场地址：  http://<服务器>/exam.html?exam=ML-SELECT-2026"

reset:
	rm -rf var/aiedu.db var/aiedu.db-wal var/aiedu.db-shm
	@echo "已清空数据库。执行 make setup 重建。"

clean: reset
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
