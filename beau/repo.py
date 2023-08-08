from typing import Any
import requests
import os


class BeauRepo:
    def __init__(self):
        self.api_hostname = (
            os.environ["BEAU_API_HOSTNAME"] + ":" + os.environ["BEAU_API_PORT"]
        )

    def init(self, guild_id: Any, guild_name: str):
        response = requests.get(
            self.api_hostname
            + "/init_guild_playlist/"
            + str(guild_id)
            + "-"
            + guild_name
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def length(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/get_playlist/" + str(guild_id))
        if response.json()["status"] == "success":
            return len(response.json()["data"])
        else:
            raise Exception(response.json()["error"])

    def get_playlist(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/get_playlist/" + str(guild_id))
        if response.json()["status"] == "success":
            return response.json()["data"]
        else:
            raise Exception(response.json()["error"])

    def add_song(self, guild_id: Any, song: dict):
        response = requests.get(
            self.api_hostname + "/add_song/" + str(guild_id), json=song
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def remove_song(self, guild_id: Any, index: int):
        response = requests.get(
            self.api_hostname + "/remove_song/" + str(guild_id) + "-" + str(index)
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def get_song(self, guild_id: Any, index: int):
        response = requests.get(
            self.api_hostname + "/get_song/" + str(guild_id) + "-" + str(index)
        )
        if response.json()["status"] == "success":
            return response.json()["data"]
        else:
            raise Exception(response.json()["error"])

    def is_loop(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/is_loop/" + str(guild_id))
        if response.json()["status"] == "success":
            return response.json()["data"]
        else:
            raise Exception(response.json()["error"])

    def trigger_loop(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/trigger_loop/" + str(guild_id))
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def clear_playlist(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/clear_playlist/" + str(guild_id))
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def get_volume(self, guild_id: Any):
        response = requests.get(self.api_hostname + "/get_volume/" + str(guild_id))
        if response.json()["status"] == "success":
            return response.json()["data"]
        else:
            raise Exception(response.json()["error"])

    def set_volume(self, guild_id: Any, volume: int):
        response = requests.get(
            self.api_hostname + "/set_volume/" + str(guild_id) + "-" + str(volume)
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def move_song_up(self, guild_id: Any, index: int):
        response = requests.get(
            self.api_hostname + "/move_song_up/" + str(guild_id) + "-" + str(index)
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])

    def swap_songs(self, guild_id: Any, index1: int, index2: int):
        response = requests.get(
            self.api_hostname
            + "/swap_songs/"
            + str(guild_id)
            + "-"
            + str(index1)
            + "-"
            + str(index2)
        )
        if response.json()["status"] == "success":
            return True
        else:
            raise Exception(response.json()["error"])
