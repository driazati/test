# -*- coding: utf-8 -*-

import sys
import os
import boto3
import json
import random
import string
import yaspin

from pathlib import Path
from typing import List, Dict, Any, Optional, cast


HOME_DIR = Path(os.path.expanduser("~"))
CONFIG_PATH = HOME_DIR / ".pytorch-ondemand"
KEY_PATH = CONFIG_PATH / "keys"
CONFIG_FILE_PATH = CONFIG_PATH / "config.json"
SSH_CONFIG_PATH = CONFIG_PATH / "ssh_config"
SOCKETS_DIR = CONFIG_PATH / "sockets"
FILES_DIR = CONFIG_PATH / "files"
FILES_PATH = CONFIG_PATH / "files.json"

clients = {}


def fail(spinner: yaspin.Spinner) -> None:
    spinner.fail("💥 ")


def ok(spinner: yaspin.Spinner) -> None:
    spinner.ok("✅ ")


def find_key(key_name: str) -> Path:
    expected_key_path = KEY_PATH / key_name
    if not expected_key_path.exists():
        raise RuntimeError(f"Unable to find key {key_name}")

    return expected_key_path.resolve()


def ec2() -> Any:
    if "ec2" not in clients:
        clients["ec2"] = boto3.client("ec2", region_name="us-west-2")

    return clients["ec2"]


def create_key_pair(key_path: Path) -> str:
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


def get_instances_for_user(user: str) -> List[Dict[str, Any]]:
    instances = ec2().describe_instances()

    user_instances = []
    for reservation in instances["Reservations"]:
        for instance in reservation["Instances"]:
            name = get_name(instance)
            if name is not None and name.startswith(f"ondemand-{user}-"):
                user_instances.append(instance)

    return user_instances


def gen_config() -> Dict[str, Any]:
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
            json.dump({}, f, indent=2)

    with open(CONFIG_FILE_PATH, "r") as f:
        config = json.load(f)

    for desc, name in config_items:
        if name not in config:
            print(f"{desc}: ", end="")
            config[name] = input()

    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f, indent=2)

    return cast(Dict[str, Any], config)


def save_config(name: str, value: str) -> None:
    config = gen_config()
    config[name] = value
    with open(CONFIG_FILE_PATH, "w") as f:
        json.dump(config, f, indent=2)


def username() -> str:
    config = gen_config()
    return cast(str, config["github_username"])


def get_name(instance: Dict[str, Any]) -> Optional[str]:
    if "Tags" in instance:
        for tag in instance["Tags"]:
            if tag["Key"] == "Name":
                return cast(str, tag["Value"])

    return None


def instance_by_id(id: str) -> Optional[Dict[str, Any]]:
    user_instances = get_instances_for_user(username())
    for instance in user_instances:
        if instance["InstanceId"] == id:
            return instance

    return None


def instance_for_id_or_name(
    id: Optional[str], name: Optional[str], user_instances: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
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


def instance_for_id_or_name_or_guess(
    id: Optional[str], name: Optional[str]
) -> Dict[str, Any]:
    user_instances = get_instances_for_user(username())
    user_instances = [
        instance
        for instance in user_instances
        if instance["State"]["Name"] == "running"
    ]

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


def locate_vscode() -> Path:
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


def stop_instances(action: str, ids_to_stop: List[str]) -> None:
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


def init() -> None:
    gen_config()
    if not KEY_PATH.exists():
        os.mkdir(KEY_PATH)

    if not FILES_DIR.exists():
        os.mkdir(FILES_DIR)

    if not FILES_PATH.exists():
        with open(FILES_PATH, "w") as f:
            json.dump([], f, indent=2)

    if not SSH_CONFIG_PATH.exists():
        with open(SSH_CONFIG_PATH, "w") as f:
            f.write("")

    if not SOCKETS_DIR.exists():
        os.mkdir(SOCKETS_DIR)
