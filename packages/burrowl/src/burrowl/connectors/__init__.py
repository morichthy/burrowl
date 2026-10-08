"""知识中枢连接器：把本地挑选出的知识发布到外部系统（WeKnora、Markdown 库……），并从那里检索 / 读写 Wiki。

本地版本化记忆始终是唯一的权威数据；外部系统只是派生的发布目标。
新连接器只需实现 spi.HubConnector（可选 SupportsWiki），并通过入口点 `burrowl.connectors` 注册。
本层只依赖 burrowl.core，不读取记忆存储。
"""
