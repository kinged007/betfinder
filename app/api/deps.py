from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.services.the_odds_api import TheOddsAPIClient
from app.services.standardizer import DataStandardizer
from app.services.ingester import DataIngester
from app.repositories.mapping import MappingRepository

async def get_so_client():
    return TheOddsAPIClient()

async def get_standardizer(db: AsyncSession = Depends(get_db)):
    mapping_repo = MappingRepository() # BaseRepository needs class, mapping repo is specific.
    # Actually MappingRepository init is self.model = Mapping.
    # But get_internal_key requires instance method.
    return DataStandardizer(mapping_repo)

async def get_ingester(
    so_client: TheOddsAPIClient = Depends(get_so_client),
    standardizer: DataStandardizer = Depends(get_standardizer)
):
    return DataIngester(so_client, standardizer)
