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


def gen_startup_script():
    config = gen_config()
    # Install user specific things
    # (oauth token for pushes)
    # user files
    return (
        textwrap.dedent(
            f"""
        #!/bin/bash
        su ubuntu

        set -eux
        cd /home/ubuntu

        cat <<EOF > /home/ubuntu/.ghstackrc
        [ghstack]
        github_url = github.com
        github_oauth = {config['github_oauth']}
        github_username = {config['github_username']}
        EOF
        chmod 644 /home/ubuntu/.ghstackrc

        sudo -u ubuntu git config --global user.name {config['github_username']}
        sudo -u ubuntu git config --global user.email {config['github_email']}
        sudo -u ubuntu git config --global push.default current

        cd /home/ubuntu/pytorch
        sudo -u ubuntu git remote set-url origin https://{config['github_username']}:{config['github_oauth']}@github.com/pytorch/pytorch.git

        sudo -u ubuntu bash -c 'export PATH="/home/ubuntu/miniconda3/bin:$PATH" && echo {config['github_oauth']} | gh auth login --with-token'

        echo done > /home/ubuntu/.done.log
    """
        ).strip()
        + "\n"
    )


clients = {}


def add_ssh_config_include():
    ssh_config = Path(os.path.expanduser("~")) / ".ssh" / "config"
    with open(ssh_config, "r") as f:
        content = f.read()
    
    line = "Include ~/.pytorch-ondemand/ssh_config"
    if line in content:
        return
    
    content = line + "\n\n" + content

    with open(ssh_config, "w") as f:
        f.write(content)


def write_ssh_configs():
    init()

    add_ssh_config_include()

    def gen_ssh_config(name: str, hostname: str, key: Path):
        return textwrap.dedent(f"""
            Host {name}
                User ubuntu
                IdentityFile {str(key)}
                Hostname {str(hostname)}
                ControlMaster auto
                ControlPath {str((SOCKETS_DIR / name).resolve())}
                ControlPersist 600

        """).strip()

    saved_instances = gen_saved_instances()
    output = ""
    for instance, data in saved_instances.items():
        output += gen_ssh_config(instance, data["hostname"], Path(data["key_path"])) + "\n\n"

    with open(SSH_CONFIG_PATH, "w") as f:
        f.write(output)


def gen_saved_instances():
    with open(INSTANCES_PATH, "r") as f:
        return json.load(f)


def ec2():
    if "ec2" not in clients:
        clients["ec2"] = boto3.client("ec2", region_name="us-west-2")

    return clients["ec2"]


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
        with open(FILES_PATH, "w") as f:
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


def create_key_pair(key_path: Path):
    key_pair = ec2().create_key_pair(KeyName=key_path.name)
    private_key = key_pair["KeyMaterial"]

    # write private key to file with 400 permissions
    with os.fdopen(os.open(key_path, os.O_WRONLY | os.O_CREAT, 0o400), "w+") as f:
        f.write(private_key)

    return key_path.name


def username():
    config = gen_config()
    return config["github_username"]


def cmd(s, **kwargs):
    print(s)
    subprocess.run(s, shell=True, **kwargs)


@click.group()
def cli():
    """
    Create and manage PyTorch OSS On-Demand machines. Machines are provisioned in
    AWS based on the most recent build of the 'viable/strict' branch of PyTorch.

    This tool provisions SSH keys so only you are able to log in and verifies
    that you are an active FB employee. A GitHub OAuth token is required to
    enable pushing from the on-demand's PyTorch repo.

    Note: On-demands are stopped every night at 3 AM PST. A stopped on-demand's
    data will still be there when it is re-started. Once an on-demand has not
    been started for 3 days it will be permanently terminated (and the data will
    be lost).
    """
    pass


def fail(spinner):
    spinner.fail("💥 ")


def ok(spinner):
    spinner.ok("✅ ")


@click.option(
    "--no-login", is_flag=True, help="skip automatic SSH once on-demand is up"
)
@click.option(
    "--no-files", is_flag=True, help="skip copying files from 'aws_od_cli configs'"
)
@click.option(
    "--rm", is_flag=True, help="stop the on-demand once the SSH session is exited"
)
@cli.command()
def create(no_login, no_files, rm):
    """
    Create a new on-demand

    TODO: this doesn't work when Packer is updating the AMI (since it goes into
    pending status), there should be a fallback AMI that's the old one
    """
    if no_login and rm:
        raise RuntimeError(
            "--rm can only be used when auto-ssh is enabled, so remove the --no-login flag"
        )

    init()

    with yaspin.yaspin(text="Finding recent AMI") as spinner:
        amis = ec2().describe_images(Owners=["self"])
        ami = None
        for image in amis["Images"]:
            if image["Name"] == "learn-packer-linux-aws":
                ami = image
                break

        if ami is None:
            fail(spinner)
            raise RuntimeError("Unable to locate on-demand ami")
        else:
            ok(spinner)

    with yaspin.yaspin(text="Finding SSH key pair") as spinner:
        key_path = gen_key_path()
        if key_path.exists():
            # key already exists
            pass
        else:
            print(f"Creating key pair at {key_path}")
            create_key_pair(key_path)

        ok(spinner)

    with yaspin.yaspin(text="Starting EC2 instance") as spinner:
        user_instances = get_instances_for_user(username())
        existing_names = [get_name(instance) for instance in user_instances]

        def gen_name():
            instance_index = 0
            base = f"ondemand-{username()}"
            while True:
                maybe_name = f"{base}-{instance_index}"
                if maybe_name in existing_names:
                    instance_index += 1
                else:
                    return maybe_name

        name = gen_name()
        instance = ec2().run_instances(
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "DeleteOnTermination": True,
                        "VolumeSize": 50,
                        "VolumeType": "gp2",
                    },
                },
            ],
            ImageId=ami["ImageId"],
            MinCount=1,
            MaxCount=1,
            KeyName=key_path.name,
            InstanceType="c5a.4xlarge",
            UserData=gen_startup_script(),
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "pytorch-ondemand", "Value": username()},
                        {"Key": "Name", "Value": name},
                    ],
                }
            ],
            Monitoring={"Enabled": False},
            # # TODO: corp net sec group
            SecurityGroupIds=["sg-00475f77ffc001e74",],  # SSH anywhere
        )
        ok(spinner)

    i = 0
    instance_id = instance["Instances"][0]["InstanceId"]
    save_instance(instance["Instances"][0], key_path)
    conditions = {"ip": False, "running": False}
    with yaspin.yaspin(text="Waiting for instance IP address") as spinner:
        while i < 100:
            fresh_instance = instance_by_id(instance_id)
            # print(fresh_instance)
            if fresh_instance["PublicDnsName"].strip() != "":
                conditions["ip"] = True
                # break
            if fresh_instance["State"]["Name"] == "running":
                conditions["running"] = True

            if all(conditions.values()):
                break
            time.sleep(1)

        if all(conditions.values()):
            spinner.ok("✅ ")
        else:
            spinner.fail("💥 ")
            raise RuntimeError(
                "Exceeded max checking timeout but instance was not assigned a public DNS name"
            )

    # Re-save to get DNS name in
    save_instance(fresh_instance, key_path)

    write_ssh_configs()

    ssh_dest = fresh_instance["InstanceId"]

    with yaspin.yaspin(text="Waiting for SSH access") as spinner:
        i = 0
        while i < 50:
            cmd = [
                "ssh",
                "-o",
                "ConnectTimeout=3",
                "-o",
                "StrictHostKeyChecking=no",
                ssh_dest,
                "ls",
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0:
                break
            time.sleep(1)

        if i >= 50:
            fail(spinner)
            raise RuntimeError("Could not get SSH access")
        else:
            ok(spinner)

    if not no_files:
        with open(FILES_PATH, "r") as f:
            files = json.load(f)
        with yaspin.yaspin(text="Copying config files") as spinner:
            for f in files:
                dest = Path(f["dest_path"])
                cmd = [
                    "ssh",
                    "-i",
                    str(key_path),
                    f"ubuntu@{fresh_instance['PublicDnsName']}",
                    "mkdir",
                    "-p",
                    dest.parent,
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                cmd = [
                    "scp",
                    "-i",
                    str(key_path),
                    f["source_path"],
                    f"ubuntu@{fresh_instance['PublicDnsName']}:{str(dest)}",
                ]
                subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            ok(spinner)

    if no_login:
        print(
            textwrap.dedent(
                f"""
            Instance created! Log in with:

                aws_od_cli ssh --name {name}
        """
            )
        )
    else:
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            ssh_dest,
        ]
        subprocess.run(cmd)

        if rm:
            ec2().terminate_instances(InstanceIds=[fresh_instance["InstanceId"]])


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


@click.option("--name")
@click.option("--id")
@click.option("--all", is_flag=True)
@click.option("--action", default="terminate")
@cli.command()
def stop(name, all, id, action):
    """
    Delete an on-demand
    """
    init()

    with yaspin.yaspin(text="Gathering instances") as spinner:
        user_instances = get_instances_for_user(username())
        ids_to_stop = []
        if all:
            for instance in user_instances:
                ids_to_stop.append(instance["InstanceId"])
        else:
            to_stop = instance_for_id_or_name(id, name, user_instances)

            if to_stop is None:
                raise RuntimeError(f"Instance {name} not found")

            ids_to_stop.append(to_stop["InstanceId"])

        ok(spinner)

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


def get_name(instance):
    if "Tags" in instance:
        for tag in instance["Tags"]:
            if tag["Key"] == "Name":
                return tag["Value"]

    return None


def get_instances_for_user(user):
    instances = ec2().describe_instances()

    user_instances = []
    for reservation in instances["Reservations"]:
        for instance in reservation["Instances"]:
            name = get_name(instance)
            if name is not None and name.startswith(f"ondemand-{user}-"):
                user_instances.append(instance)

    return user_instances


def locate_vscode():
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


@click.option("--id")
@click.option("--name")
@cli.command()
def vscode(id, name):
    """
    Launch vscode for a remote
    """
    if sys.platform != "darwin":
        raise NotImplementedError("vscode startup not supported on Linux/Windows")

    user_instances = get_instances_for_user(username())
    user_instances = [
        instance
        for instance in user_instances
        if instance["State"]["Name"] == "running"
    ]

    if len(user_instances) == 1 and id is None and name is None:
        instance = user_instances[0]
    else:
        instance = instance_for_id_or_name(id, name, user_instances)

    with open(INSTANCES_PATH, "r") as f:
        saved_instances = json.load(f)

    code_exe = locate_vscode()
    name = instance["InstanceId"]
    cmd = [
        code_exe,
        "--folder-uri",
        f"vscode-remote://ssh-remote+{name}/home/ubuntu/pytorch",
    ]
    subprocess.run(cmd)


@click.option("--id")
@click.option("--name")
@cli.command()
def ssh(id, name):
    """
    SSH into a running on-demand by name or instance ID (see 'aws_od_cli list')
    """
    user_instances = get_instances_for_user(username())
    user_instances = [
        instance
        for instance in user_instances
        if instance["State"]["Name"] == "running"
    ]

    if len(user_instances) == 1 and id is None and name is None:
        instance = user_instances[0]
    else:
        instance = instance_for_id_or_name(id, name, user_instances)
    with open(INSTANCES_PATH, "r") as f:
        saved_instances = json.load(f)

    id = instance["InstanceId"]
    if id in saved_instances:
        key_path = Path(saved_instances[id]["key_path"])
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-i",
            str(key_path),
            f"ubuntu@{instance['PublicDnsName']}",
        ]
        subprocess.run(cmd)
    else:
        raise RuntimeError(f"Instance not found in {INSTANCES_PATH}")


@click.option("--add")
@click.option("--remove-id")
@click.option("--list", is_flag=True)
@cli.command()
def configs(add, remove_id, list):
    """
    Manage files to copy to on-demand instances (dotfiles, etc)
    """
    init()

    with open(FILES_PATH, "r") as f:
        files = json.load(f)

    def save():
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

    if add is not None:
        file = Path(add)

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
        save()
        print(f"Added {source_path} -> {dest_path}")

    if remove_id is not None:
        remove_id = int(remove_id)
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
            save()
            print(f"Removed file {remove_id}: {f['name']}")

    if list:
        rows = []
        for f in files:
            rows.append(
                {
                    "Id": f["id"],
                    "Name": f["name"],
                    "Path": f'{f["source_path"]} -> {f["dest_path"]}',
                }
            )
        if len(rows) == 0:
            print("No files yet, use 'aws_od_cli configs --add <some file>")
        else:
            print(tabulate.tabulate([d.values() for d in rows], headers=rows[0].keys()))


@click.option("--full", is_flag=True, help="Show more info")
@cli.command()
def list(full):
    """
    List all your on-demands
    """
    init()

    user_instances = get_instances_for_user(username())

    with open(INSTANCES_PATH, "r") as f:
        saved_instances = json.load(f)

    rows = []
    for instance in user_instances:
        state = instance["State"]["Name"]
        if state == "terminated":
            continue

        data = {
            "Name": get_name(instance),
            "Status": instance["State"]["Name"],
            "Launched": instance["LaunchTime"]
            .astimezone()
            .strftime("%Y-%m-%d %H:%M:%S"),
        }

        if full:
            id = instance["InstanceId"]
            data["Instance Id"] = id
            data["DNS"] = instance["PublicDnsName"]

            if id in saved_instances:
                data["Key File"] = str(saved_instances[id]["key_path"])
            else:
                data["Key File"] = "<unknown>"

        rows.append(data)

    if len(rows) == 0:
        print("No on-demands found! Start one with 'aws_od_cli create'")
    else:
        print(tabulate.tabulate([d.values() for d in rows], headers=rows[0].keys()))


if __name__ == "__main__":
    cli()
