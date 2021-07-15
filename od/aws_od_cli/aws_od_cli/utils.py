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


HOME_DIR = Path(os.path.expanduser("~"))
CONFIG_PATH = HOME_DIR / ".pytorch-ondemand"
KEY_PATH = CONFIG_PATH / "keys"
INSTANCES_PATH = CONFIG_PATH / "instances.json"
CONFIG_FILE_PATH = CONFIG_PATH / "config.json"
SSH_CONFIG_PATH = CONFIG_PATH / "ssh_config"
SOCKETS_DIR = CONFIG_PATH / "sockets"
FILES_DIR = CONFIG_PATH / "files"
FILES_PATH = CONFIG_PATH / "files.json"

clients = {}


def fail(spinner):
    spinner.fail("💥 ")


def ok(spinner):
    spinner.ok("✅ ")


def ec2():
    if "ec2" not in clients:
        clients["ec2"] = boto3.client("ec2", region_name="us-west-2")

    return clients["ec2"]


def create_key_pair(key_path: Path):
    key_pair = ec2().create_key_pair(KeyName=key_path.name)
    private_key = key_pair["KeyMaterial"]

    # write private key to file with 400 permissions
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o400), "w+") as f:
        f.write(private_key)

    return key_path.name


def gen_key_path() -> Path:
    config = gen_config()

    # Check for existing keys
    existing_keys = os.listdir(KEY_PATH)
    if len(existing_keys) > 0:
        # print(f"Using existing private key {existing_keys[0]}")
        return KEY_PATH / existing_keys[0]

    prefix = config["github_username"]
    while True:
        suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(4))
        key_path = KEY_PATH / f"{prefix}-{suffix}"
        if not key_path.exists():
            break

    return key_path


def get_instances_for_user(user):
    instances = ec2().describe_instances()

    user_instances = []
    for reservation in instances["Reservations"]:
        for instance in reservation["Instances"]:
            name = get_name(instance)
            if name is not None and name.startswith(f"ondemand-{user}-"):
                user_instances.append(instance)

    return user_instances


def gen_config():
    if not CONFIG_PATH.exists():
        os.mkdir(CONFIG_PATH)

    config_items = [
        ("GitHub Username", "github_username"),
        ("GitHub Email (for commits)", "github_email"),
        (
            "GitHub Personal Access Token (create at https://github.com/settings/tokens with 'repo', 'read:org', and 'workflow' permissions)",
            "github_oauth",
        ),
        ("Personal PyTorch fork for pushes (leave blank to skip)", "pytorch_fork"),
    ]

    if not CONFIG_FILE_PATH.exists():
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump({}, f)

    with open(CONFIG_FILE_PATH, "r") as f:
        config = json.load(f)

    for desc, name in config_items:
        if name not in config:
            print(f"{desc}: ", end="")
            config[name] = input()

    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f)

    return config


def save_config(name, value):
    config = gen_config()
    config[name] = value
    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f)


def username():
    config = gen_config()
    return config["github_username"]


def get_name(instance):
    if "Tags" in instance:
        for tag in instance["Tags"]:
            if tag["Key"] == "Name":
                return tag["Value"]

    return None


def instance_by_id(id):
    user_instances = get_instances_for_user(username())
    for instance in user_instances:
        if instance["InstanceId"] == id:
            return instance

    return None


def instance_for_id_or_name(id, name, user_instances):
    if (name is None and id is None) or (name is not None and id is not None):
        raise RuntimeError("Expected one of --name or --id")

    if name is not None:
        for instance in user_instances:
            if get_name(instance) == name:
                return instance
                break
    elif id is not None:
        for instance in user_instances:
            if instance["InstanceId"] == id:
                return instance
    else:
        raise RuntimeError("Unreachable")

    return None


def instance_for_id_or_name_or_guess(id, name):
    user_instances = get_instances_for_user(username())

    if id is None and name is None:
        if len(user_instances) == 1:
            return user_instances[0]

    if (name is None and id is None) or (name is not None and id is not None):
        raise RuntimeError("Expected one of --name or --id")

    if name is not None:
        for instance in user_instances:
            if get_name(instance) == name:
                return instance
    elif id is not None:
        for instance in user_instances:
            if instance["InstanceId"] == id:
                return instance

    raise RuntimeError("Unreachable")


def locate_vscode():
    if sys.platform != "darwin":
        raise NotImplementedError("vscode startup not supported on Linux/Windows")

    app_paths = [
        Path("/Applications"),
        Path(HOME_DIR / "Applications"),
    ]

    app_path = None
    for path in app_paths:
        codes = [x for x in os.listdir(path) if "Visual Studio Code.app" in x]
        if len(codes) > 0:
            app_path = path / codes[0]
            break

    if app_path is None:
        raise RuntimeError(
            f"Unable to locate Visual Studio Code.app in these dirs: {app_paths}"
        )

    return (app_path / "Contents" / "Resources" / "app" / "bin" / "code").resolve()


def stop_instances(action, ids_to_stop):
    with yaspin.yaspin(text="Stopping instances") as spinner:
        if action == "terminate":
            ec2().terminate_instances(InstanceIds=ids_to_stop)
        elif action == "stop":
            ec2().stop_instances(InstanceIds=ids_to_stop)
        else:
            raise RuntimeError(
                f"Unknown action {action}, expected 'stop' or 'terminate'"
            )

        ok(spinner)


def gen_saved_instances():
    with open(INSTANCES_PATH, "r") as f:
        return json.load(f)


def init():
    gen_config()
    if not KEY_PATH.exists():
        os.mkdir(KEY_PATH)

    if not INSTANCES_PATH.exists():
        with open(INSTANCES_PATH, "w") as f:
            json.dump({}, f)

    if not FILES_DIR.exists():
        os.mkdir(FILES_DIR)

    if not FILES_PATH.exists():
        with open(FILES_PATH, "w") as f:
            json.dump([], f)

    if not SSH_CONFIG_PATH.exists():
        with open(SSH_CONFIG_PATH, "w") as f:
            f.write("")

    if not SOCKETS_DIR.exists():
        os.mkdir(SOCKETS_DIR)


def save_instance(instance, key_path):
    with open(INSTANCES_PATH, "r") as f:
        data = json.load(f)

    id = instance["InstanceId"]
    data[id] = {
        "tags": instance["Tags"],
        "hostname": instance["PublicDnsName"],
        "key_path": str(key_path.resolve()),
    }

    with open(INSTANCES_PATH, "w") as f:
        json.dump(data, f)
