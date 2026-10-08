"""可移植包（burrowl-bundle）：整个数据目录的备份、恢复与跨机器迁移。

只依赖 core 与 storage.layout；不读写记忆语义，按字节原样打包，恢复后固定引用全部有效。
"""
