import sqlite3
from datetime import date, datetime
from uuid import uuid4


class BeauPlDb:
    def __init__(self):
        self.con = sqlite3.connect('./databases/playlist.db')

        cur = self.con.cursor()

        try:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS playlist(pid INTEGER PRIMARY KEY AUTOINCREMENT, pname text, gid text, date text, uid)
            ''')
        except sqlite3.OperationalError:
            print('Playlist db Error')

        try:
            cur.execute('''
            CREATE TABLE IF NOT EXISTS song(sid text PRIMARY KEY, pid text, uid text, date text, FOREIGN KEY(pid) REFERENCES playlist(pid)) 
            ''')
        except sqlite3.OperationalError:
            print('Song db Error')

    def get_playlist(self, gid):
        cur = self.con.cursor()
        results = cur.execute(f"""
        select * from playlist
        where gid = "{gid}"
        """).fetchall()
        return results

    def add_playlist(self, pname, gid, uid):
        cur = self.con.cursor()
        try:
            cur.execute(f"""
            insert into playlist values (NULL, ?, ?, ?, ?)
            """, (pname, gid, date.today().strftime('%d/%m/%y'), uid))
            self.con.commit()
        except:
            return 0

        cur.close()
        return 1

    def rm_playlist(self, pid):
        cur = self.con.cursor()
        try:
            cur.execute(f"""
            delete from playlist where pid = {pid}
            """)
            self.con.commit()
        except:
            return 0

        cur.close()
        return 1
