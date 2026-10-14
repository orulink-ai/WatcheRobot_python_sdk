# 实际验证记录

档案日志来自实际执行输出；仅移除 pytest 输出的行末空格以通过 git diff --check，不改测试结论、错误内容或执行结果。pip-validation.log 按标题明确保留安装/解析及 JS 输出摘录；其他日志保留该次测试输出。

## 环境与范围

- Linux 5.15.0-190-generic；Python 3.11.15；隔离 venv：/home/hermesadmin/work/sdk-0112-validation-venv。
- pip 26.2.1，pytest 9.1.1，mypy 1.20.2，Node v22.23.2（实际执行 `node --version` 核验）。
- 基线 commit：cc0a31993b4fe1a607e75ad920ad013e5ddc934a（main 的 SDK 0.1.12 发布 PR 合并提交）。
- 工作区存在不属于本任务的并发修改，未纳入修复；以 `git archive HEAD` 创建 /home/hermesadmin/work/sdk-0112-baseline，仅覆盖 sdk-ci.yml 和 test_release.py 后全量验证。

## 真实安装与解析

从 workflow 的最低步骤提取实际 pip 命令（去除 shell 续行，再用 shlex 拆分），使用 venv Python 执行，而不是构造伪造依赖响应：

```bash
python -m pip install -e ".[test]" \
  "av==16.*" "bleak==3.*" "esptool==4.8.*" "fastapi==0.129.*" \
  "huggingface-hub==1.32.*" "keyring==25.7.*" "packaging==24.*" \
  "Pillow==11.*" "psutil==5.9.*" "pyserial==3.5.*" "starlette==0.51.*" \
  "uvicorn==0.30.*" "websockets==14.*"
python -m pip check
```

- 旧 pin：将上述 huggingface-hub 换回 1.26.*，添加 --dry-run，返回 exit 1，ResolutionImpossible；明确报告 watcherobot 0.1.12 requires huggingface-hub<2,>=1.32。
- 新 pin：实际执行安装，exit 0；安装 huggingface-hub 1.32.0、watcherobot 0.1.12；pip check exit 0：No broken requirements found。
- 所有已安装版本及实际命令见 [pip-validation.log](pip-validation.log)。保留原运行声明，没有使用 --no-deps、忽略 pip check 或放宽发布门禁。

## 验证结果

| 检查 | 实际命令（使用 venv Python） | 结果 / 证据 |
|---|---|---|
| pin RED | python -m pytest tests/test_release.py::test_release_minimum_dependency_pins_satisfy_runtime_requirements -v | 1 failed；[记录](tdd-red.log) |
| pin GREEN | python -m pytest tests/test_release.py -v | 当时 13 passed；[记录](tdd-green.log) |
| resolver RED | python -m pytest tests/test_release.py::test_release_minimum_dependencies_are_resolved_together_with_sdk -v | 1 failed；[记录](resolver-red.log) |
| 最终专项 | python -m pytest tests/test_release.py tests/tools -v | 59 passed；[记录](release-regression.log)，格式整理后再跑同命令 59 passed |
| 示例 | python -m pytest examples/expression_lab/tests | 30 passed, 1 warning；[记录](expression-lab.log) |
| mypy | python -m mypy src/watcherobot | Success: no issues found in 125 source files；[记录](mypy.log) |
| JS | node --test tests/js/*.mjs | 225 passed, 0 failed；[摘要](pip-validation.log)，本机完整输出 /home/hermesadmin/work/sdk-0112-validation-logs/node-tests.log |
| 第一次全量（含并发测试修改） | python -m pytest | 1911 passed, 3 failed, 5 skipped, 4 warnings in 189.10s；[记录](pytest-full.log) |
| 仅本次改动快照全量 | python -m pytest -ra -q /home/hermesadmin/work/sdk-0112-baseline/tests -c /home/hermesadmin/work/sdk-0112-baseline/pyproject.toml | 1911 passed, 3 failed, 5 skipped；[记录](pytest-owned-snapshot.log) |
| Runtime 基线 | 在 git archive 基线中 python -m pytest tests/runtime/test_runtime_repository.py -q | 同样两项缓存测试失败；[记录](runtime-baseline.log) |
| 示例清单基线 | 在基线中 python -m pytest tests/test_examples.py -q | 漏列 joyinside_pi 的同样失败；[记录](examples-baseline.log) |
| Diff | git diff --check | exit 0 |

快照全量使用项目 addopts=-q 加命令 -q，pytest 未显示计数总结；1911/3/5 数量通过解析该次原始进度行中的 . / F / s 得到，共 1919 个结果符号，失败及跳过名称有原始总结佐证；不是套用第一次全量的计数。

## 未通过项目与调查

仅本次改动的快照失败：

1. tests/runtime/test_runtime_repository.py::test_same_size_rewrite_with_restored_mtime_invalidates_verification[False]
2. 同一用例 [True]
3. tests/test_examples.py::test_every_example_is_a_complete_managed_application

Runtime 两项在未修改 Runtime 文件的基线也复现。源码缓存基于 size、mtime、ctime 等元数据；最小探针在本机观察到立即同尺寸重写、恢复 mtime 后，ctime_ns 也保持不变：before=1792000763517522903，instant=1792000763517522903；等待 20ms 再写后 ctime=1792000763537523903。该证据支持本机时钟/文件时间粒度导致缓存无法辨识该瞬间重写的解释。更换 /dev/shm tmpfs 的局部验证仍失败；完整根治不属于最低依赖 pin 修复，未改运行源码或测试跳过策略。

第一次全量及 tmpfs 重试还出现 test_bundle_versions_coexist_and_reuse_verified_content 失败，但基线局部重试和独立快照全量未再失败，按实际记录保留间歇性说明，不声明已经修复。

示例清单失败是基线 EXAMPLE_IDS 没有已存在的 joyinside_pi，而非本次 pin 变更。并发工作区存在对应测试变更，本任务不代为提交。

5 个跳过全部为 Windows 专属行为：console inheritance、extended paths、native extension loader、CreateProcess、MAX_PATH。警告包括 Starlette/AnyIO、WebSockets 弃用及重复 zip 条目的测试警告，未通过过滤来掩盖。

## 缺口与发布边界

- 本机只有 Python 3.11 安装；未执行 Python 3.10/3.12、Windows、实际硬件或六格远端最低/最新矩阵。
- 未因全量基线失败扩展到运行缓存、Pi 示例源码或版本；需要独立修复/审核这些问题。
- 未触发发布、创建 tag、批准环境或合并；远端 CI 状态以 PR 后真实检查为准，不将本地专项通过写成远端全绿。
