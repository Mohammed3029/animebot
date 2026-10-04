import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "anime_bot_db")

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]
anime_collection = db["anime_posts"]
force_sub_collection = db["force_subs"]

async def save_anime_post(anime_id, title, channel_msg_id):
    """Creates an initial anime post record in MongoDB."""
    data = {
        "_id": anime_id,
        "title": title,
        "channel_msg_id": channel_msg_id,
        "resolutions": {}  # Stores e.g., {"720p": "file_id_or_link"}
    }
    await anime_collection.update_one(
        {"_id": anime_id},
        {"$setOnInsert": data},
        upsert=True
    )

async def add_resolution(anime_id, quality, file_id_or_link):
    """Adds or updates a resolution file_id or link in MongoDB."""
    await anime_collection.update_one(
        {"_id": anime_id},
        {"$set": {f"resolutions.{quality}": file_id_or_link}}
    )

async def get_anime_post(anime_id):
    """Retrieves an anime post from MongoDB by its ID."""
    return await anime_collection.find_one({"_id": anime_id})

async def add_force_sub(channel_id: int):
    """Adds a channel or chat ID to the force-sub list."""
    await force_sub_collection.update_one(
        {"_id": channel_id},
        {"$set": {"channel_id": channel_id}},
        upsert=True
    )

async def remove_force_sub(channel_id: int):
    """Removes a channel or chat ID from the force-sub list."""
    await force_sub_collection.delete_one({"_id": channel_id})

async def get_force_subs():
    """Retrieves all force-sub channel IDs from MongoDB as a list."""
    cursor = force_sub_collection.find({})
    channels = await cursor.to_list(length=None)
    return [channel["_id"] for channel in channels]