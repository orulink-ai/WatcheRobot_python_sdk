# 重复验证

Python3.11.15，在工作目录 `/home/hermesadmin/sdk-pr148-repair` 用 Python subprocess.run(check=True) 顺序执行 100 个独立进程：

```sh
.venv/bin/python -m pytest -q \
  tests/test_robot_intercom.py::test_presence_scope_cancellation_waits_for_cleanup \
  tests/test_robot_intercom.py::test_presence_websocket_checks_origin_and_cleans_up_on_close \
  tests/test_robot_intercom.py::test_presence_websocket_retains_abnormal_disconnect_metadata \
  tests/runtime/test_runtime_repository.py
```

真实进程结果：100 轮全部 exit 0；日志计数 ITERATION=100，FAILED/ERROR=0。
完整原始输出保留在 `/tmp/pr148-repeat.log`；单轮 runtime+intercom 的详细 PASS 列表见 [target-green](races-target-green.log)。没有添加 sleep，没有删除或跳过原始目标断言。Windows MAX_PATH 条件跳过仍照实保留。
