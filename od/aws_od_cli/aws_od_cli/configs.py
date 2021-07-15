import sys
import time
import os
import click
import textwrap
import subprocess
import boto3
import json
import random
import string
import tabulate
import yaspin
import shutil

from pathlib import Path

from aws_od_cli.utils import *

def load_files():
    with open(FILES_PATH, "r") as f:
        return json.load(f)

def save_files(files):
    with open(FILES_PATH, "w") as f:
        json.dump(files, f)

def gen_source_path(file: Path):
    dest_path = FILES_DIR / file.name
    i = 0
    while True:
        if not dest_path.exists():
            break
        dest_path = FILES_DIR / f"{file.name}-{i}"
        i += 1

    return dest_path

def gen_id():
    files = load_files()
    i = 0
    while True:
        ok = True
        for f in files:
            if f["id"] == i:
                ok = False
                break

        if ok:
            return i
        i += 1


def add_file(path: str):
    file = Path(path)
    files = load_files()

    source_path = gen_source_path(file)
    shutil.copy(file, source_path)
    dest_path = Path("/home/ubuntu/") / file.relative_to(HOME_DIR)

    files.append(
        {
            "id": gen_id(),
            "name": file.name,
            "dest_path": str(dest_path),
            "source_path": str(source_path),
        }
    )
    save_files(files)
    print(f"Added {source_path} -> {dest_path}")

def remove_file(id: str):
    remove_id = int(id)
    files = load_files()
    idx_to_remove = None
    for index, f in enumerate(files):
        if f["id"] == remove_id:
            idx_to_remove = index
            break

    if idx_to_remove is None:
        raise RuntimeError(
            f"Id {remove_id} not found, check 'aws_od_cli configs --list'"
        )
    else:
        f = files.pop(idx_to_remove)
        save_files(files)
        print(f"Removed file {remove_id}: {f['name']}")
    
def list_files():
    files = load_files()
    rows = []
    for f in files:
        rows.append(
            {
                "Id": f["id"],
                "Name": f["name"],
                "Path": f'{f["source_path"]} -> {f["dest_path"]}',
            }
        )
    
    return rows