from re import I, sub
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

from pathlib import Path

HOME_DIR = Path(os.path.expanduser("~"))
CONFIG_PATH = HOME_DIR / ".pytorch-ondemand"
KEY_PATH = CONFIG_PATH / "keys"
INSTANCES_PATH = CONFIG_PATH / "instances.json"
CONFIG_FILE_PATH = CONFIG_PATH / "config.json"




def gen_startup_script():
    config = gen_config()
    # Install user specific things
    # (oauth token for pushes)
    # user files
    return textwrap.dedent(f"""
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

        sudo -u ubuntu bash -c 'export PATH="/home/ubuntu/miniconda3/bin:$PATH" && echo {config['github_oauth']} | gh auth login --with-token'

        echo done > /home/ubuntu/.done.log
    """).strip() + "\n"



clients = {}


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


def save_instance(instance, key_path):
    with open(INSTANCES_PATH, "r") as f:
        data = json.load(f)

    id = instance["InstanceId"]
    data[id] = {
        "tags": instance["Tags"],
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
        ("GitHub Personal Access Token (create at https://github.com/settings/tokens with 'repo', 'read:org', and 'workflow' permissions)", "github_oauth"),
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


@click.option("--no-login", is_flag=True, help="skip automatic SSH once on-demand is up")
@cli.command()
def create(no_login):
    """
    Create a new on-demand

    TODO: this doesn't work when Packer is updating the AMI (since it goes into
    pending status), there should be a fallback AMI that's the old one
    """
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

    with yaspin.yaspin(text="Waiting for SSH access") as spinner:
        i = 0
        while i < 50:
            cmd = [
                "ssh",
                "-o",
                "ConnectTimeout=3",
                "-o",
                "StrictHostKeyChecking=no",
                "-i",
                str(key_path),
                f"ubuntu@{fresh_instance['PublicDnsName']}",
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
            "-i",
            str(key_path),
            f"ubuntu@{fresh_instance['PublicDnsName']}",
        ]
        subprocess.run(cmd)
    # print(json.dumps(ami, indent=2))
    # print(instance)


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


@click.option("--id")
@click.option("--name")
@cli.command()
def ssh(id, name):
    """
    SSH into a running on-demand by name or instance ID (see 'aws_od_cli list')
    """
    user_instances = get_instances_for_user(username())
    user_instances = [instance for instance in user_instances if instance["State"]["Name"] == "running"]

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


@cli.command()
def list():
    """
    List all your on-demands
    """
    user_instances = get_instances_for_user(username())

    rows = []
    for instance in user_instances:
        state = instance["State"]["Name"]
        if state == "terminated":
            continue
        rows.append(
            {
                "Name": get_name(instance),
                "Status": instance["State"]["Name"],
                "Id": instance["InstanceId"],
                "Launched": instance["LaunchTime"]
                .astimezone()
                .strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    if len(rows) == 0:
        print("No on-demands found! Start one with 'aws_od_cli create'")
    else:
        print(tabulate.tabulate([d.values() for d in rows], headers=rows[0].keys()))


if __name__ == "__main__":
    cli()
