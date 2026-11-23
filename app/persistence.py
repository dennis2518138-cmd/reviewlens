from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from app.config import CHECKPOINT_DB

_saver: AsyncSqliteSaver | None = None


def get_checkpointer() -> AsyncSqliteSaver:
    if _saver is None:
        raise RuntimeError("检查点未初始化：应用没走 lifespan 启动（脚本请用 build_graph() 不传检查点）")
    return _saver


@asynccontextmanager
async def checkpoint_lifespan() -> AsyncIterator[AsyncSqliteSaver]:
    global _saver
    CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(CHECKPOINT_DB)) as saver:
        _saver = saver
        try:
            yield saver
        finally:
            _saver = None

