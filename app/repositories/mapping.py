
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.base import BaseRepository
from app.db.models import Mapping

class MappingRepository(BaseRepository[Mapping]):
    def __init__(self):
        super().__init__(Mapping)

    async def get_internal_key(
        self, db: AsyncSession, source: str, type: str, external_key: str
    ) -> Optional[str]:
        query = select(self.model.internal_key).where(
            self.model.source == source,
            self.model.type == type,
            self.model.external_key == external_key
        )
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def get_by_source_and_type(
        self, db: AsyncSession, source: str, type: str
    ) -> list[Mapping]:
        query = select(self.model).where(
            self.model.source == source,
            self.model.type == type
        )
        result = await db.execute(query)
        return result.scalars().all()
