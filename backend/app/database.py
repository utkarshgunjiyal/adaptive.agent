from motor.motor_asyncio import AsyncIOMotorClient
from app.config import MONGO_URL, DB_NAME

client = AsyncIOMotorClient(MONGO_URL)
db = client[DB_NAME]

threads_collection = db["threads"]
messages_collection = db["messages"]
thread_summaries_collection = db["thread_summaries"]