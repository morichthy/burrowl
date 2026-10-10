# 兼容性标本

每个子目录是某个 burrowl 版本写出的数据目录标本（`data/`）及其工作根（`work/`），由 `tools/make_compat_fixture.py` 只经公开操作生成，`FIXTURE.json` 记录生成版本、提交、真源格式指纹和内容清单。

- **不要修改已提交的标本**：它代表用户手里的真实旧数据。新代码读不了时，修代码（补旧格式读取），不改标本。
- 什么时候生成新标本见 [docs/TESTING](../../../docs/TESTING.md) 的"兼容性标本"。
- 检验见 `tests/service/test_compat_fixtures.py`。
