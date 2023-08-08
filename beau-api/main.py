from fastapi import FastAPI
import pymongo
import json
import os


connect_string = (
    "mongodb://"
    + os.environ["MONGO_USERNAME"]
    + ":"
    + os.environ["MONGO_PASSWORD"]
    + "@mongo:27017"
)
db = pymongo.MongoClient(connect_string)["Beau"]
app = FastAPI()
json_serializer = lambda x: json.dumps(x).encode("utf8")
json_deserializer = lambda x: json.loads(x.decode("utf8"))


@app.get("/init_guild_playlist/{guild_id}-{guild_name}")
def init_guild_playlist(guild_id: str, guild_name: str):
    try:
        db.playlist.insert_one(
            {
                "_id": guild_id,
                "name": guild_name,
                "songs": [],
                "volume": 0.5,
                "loop": False,
            }
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/get_all_guilds")
def get_all_guilds():
    try:
        cursor = db.playlist.find({})
        return {"status": "success", "data": list(cursor)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/is_loop/{guild_id}")
def is_loop(guild_id: str):
    try:
        return {
            "status": "success",
            "data": db.playlist.find_one({"_id": guild_id})["loop"],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/trigger_loop/{guild_id}")
def trigger_loop(guild_id: str):
    try:
        is_loop = db.playlist.find_one({"_id": guild_id})["loop"]
        db.playlist.update_one(
            {"_id": guild_id},
            {"$set": {"loop": not is_loop}},
        )
        return {"status": "success", "data": not is_loop}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/add_song/{guild_id}")
def add_song(guild_id: str, song: dict):
    try:
        db.playlist.update_one(
            {"_id": guild_id},
            {"$push": {"songs": song}},
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/get_playlist/{guild_id}")
def get_playlist(guild_id: str):
    try:
        return {
            "status": "success",
            "data": db.playlist.find_one({"_id": guild_id})["songs"],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/get_song/{guild_id}-{index}")
def get_song(guild_id: str, index: int):
    try:
        current_list = db.playlist.find_one({"_id": guild_id})["songs"]
        if len(current_list) <= index or index < 0:
            raise IndexError("Index out of range")
        else:
            return {"status": "success", "data": current_list[index]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/remove_song/{guild_id}-{index}")
def remove_song(guild_id: str, index: int):
    try:
        current_list = db.playlist.find_one({"_id": guild_id})["songs"]
        if len(current_list) <= index or index < 0:
            raise IndexError("Index out of range")
        else:
            db.playlist.update_one(
                {"_id": guild_id},
                {"$pull": {"songs": current_list[index]}},
            )
            return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/swap_song/{guild_id}-{index1}-{index2}")
def swap_song(guild_id: str, index1: int, index2: int):
    try:
        current_list = db.playlist.find_one({"_id": guild_id})["songs"]
        if (
            len(current_list) <= index1
            or index1 < 0
            or len(current_list) <= index2
            or index2 < 0
        ):
            raise IndexError("Index out of range")
        else:
            temp = current_list[index1]
            current_list[index1] = current_list[index2]
            current_list[index2] = temp
            db.playlist.update_one(
                {"_id": guild_id},
                {"$set": {"songs": current_list}},
            )
            return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/clear_playlist/{guild_id}")
def clear_playlist(guild_id: str):
    try:
        db.playlist.update_one(
            {"_id": guild_id},
            {"$set": {"songs": []}},
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/get_volume/{guild_id}")
def get_volume(guild_id: str):
    try:
        return {"status": "success", "data": db.get_volume(guild_id)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/set_volume/{guild_id}-{volume}")
def set_volume(guild_id: str, volume: float):
    try:
        db.playlist.update_one(
            {"_id": guild_id},
            {"$set": {"volume": volume}},
        )
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/move_song_up/{guild_id}-{index}")
def move_song_up(guild_id: str, index: int):
    try:
        current_list = db.playlist.find_one({"_id": guild_id})["songs"]
        if len(current_list) <= index or index < 0:
            raise IndexError("Index out of range")
        else:
            temp = current_list[index]
            current_list[index] = current_list[index - 1]
            current_list[index - 1] = temp
            db.playlist.update_one(
                {"_id": guild_id},
                {"$set": {"songs": current_list}},
            )
            return {"status": "success"}
    except Exception as e:
        return {"status": "error", "error": str(e)}
