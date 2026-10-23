"""Restore a Sakuya workspace backup while Sakuya is stopped.

Usage: python -X utf8 scripts/restore-workspace.py workspace-YYYYMMDD-HHMMSS-xxxxxxxx.sqlite3
"""
from pathlib import Path
import argparse
import os
import re
import sqlite3
from contextlib import closing
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"bootstrap"))
from common import InstanceLock, data_directory

SAFE=re.compile(r"^workspace-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.sqlite3$")


def restore(data: Path, name: str):
    if not isinstance(name,str) or not SAFE.fullmatch(name):
        raise ValueError("Укажите имя резервной копии из папки backups")
    source=data/"backups"/name
    if source.is_symlink() or not source.is_file():
        raise ValueError("Резервная копия не найдена")
    with closing(sqlite3.connect(str(source))) as connection:
        valid=connection.execute("PRAGMA integrity_check").fetchone()
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not valid or valid[0]!="ok" or not {"conversations","messages","memories"}.issubset(tables):
            raise ValueError("Файл не является исправной резервной копией Sakuya")
    original=data/"workspace.sqlite3"
    if original.exists():
        safety=data/"backups"/("before-restore-"+str(int(time.time()))+".sqlite3")
        with closing(sqlite3.connect(str(original))) as current:
            with closing(sqlite3.connect(str(safety))) as target:
                current.backup(target)
    temp=data/("workspace-restoring-"+str(os.getpid())+".sqlite3")
    try:
        from shutil import copyfile
        copyfile(source,temp)
        os.replace(temp,original)
    finally:
        temp.unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("name",help="Имя backup-файла в папке backups")
    args=parser.parse_args()
    data=data_directory()
    lock=InstanceLock(data/"run"/"instance.lock")
    try:
        if not lock.acquire():
            raise RuntimeError("Сначала завершите Sakuya через трей")
        restore(data,args.name)
        print("История и память Sakuya восстановлены")
    finally:
        lock.close()


if __name__=="__main__":
    main()
