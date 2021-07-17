# -*- coding: utf-8 -*-

import time
import os
import textwrap
import subprocess
import yaspin

from pathlib import Path
from typing import Dict, Any, cast, Tuple, List

from .utils import (
    SOCKETS_DIR,
    SSH_CONFIG_PATH,
    create_key_pair,
    ec2,
    fail,
    gen_config,
    gen_key_path,
    gen_saved_instances,
    get_instances_for_user,
    get_name,
    instance_by_id,
    ok,
    username,
)


def find_ami() -> Dict[str, Any]:
    ami = None
    with yaspin.yaspin(text="Finding recent AMI") as spinner:
        amis = ec2().describe_images(Owners=["self"])
        for image in amis["Images"]:
            if image["Name"] == "pytorch-ondemand-ami":
                ami = image
                break

        if ami is None:
            fail(spinner)
            raise RuntimeError("Unable to locate on-demand ami")
        else:
            ok(spinner)

    return cast(Dict[str, Any], ami)


def find_or_create_ssh_key() -> Path:
    with yaspin.yaspin(text="Finding SSH key pair") as spinner:
        key_path = gen_key_path()
        if key_path.exists():
            # key already exists
            pass
        else:
            create_key_pair(key_path)

        ok(spinner)

    return key_path


def gen_startup_script() -> str:
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


def create_instance(ami: Dict[str, Any], key_path: Path, instance_type: str) -> Tuple[Dict[str, Any], str]:
    with yaspin.yaspin(text="Starting EC2 instance") as spinner:
        user_instances = get_instances_for_user(username())
        existing_names = [get_name(instance) for instance in user_instances]

        def gen_name() -> str:
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
            InstanceType=instance_type,
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
            SecurityGroupIds=["sg-00475f77ffc001e74"],  # SSH anywhere
        )
        ok(spinner)

    return instance, name


def wait_for_ip_address(instance: Dict[str, Any]) -> Dict[str, Any]:
    id = instance["InstanceId"]
    i = 0
    conditions = {"ip": False, "running": False}

    with yaspin.yaspin(text="Waiting for instance IP address") as spinner:
        while i < 100:
            fresh_instance = instance_by_id(id)
            if fresh_instance is None:
                raise RuntimeError(f"Expected instance {id} to exist")
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

    if fresh_instance is None:
        raise RuntimeError("Instance should not be None")
    return fresh_instance


def wait_for_ssh_access(instance: Dict[str, Any]) -> Dict[str, Any]:
    ssh_dest = instance["InstanceId"]

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

    return instance


def copy_files(instance: Dict[str, Any], files: List[Dict[str, str]]) -> None:
    ssh_dest = instance["InstanceId"]

    with yaspin.yaspin(text="Copying config files") as spinner:
        for f in files:
            dest = Path(f["dest_path"])
            cmd = [
                "ssh",
                ssh_dest,
                "mkdir",
                "-p",
                dest.parent,
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cmd = [
                "scp",
                f["source_path"],
                f"{ssh_dest}:{str(dest)}",
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        ok(spinner)


def add_ssh_config_include() -> None:
    ssh_config = Path(os.path.expanduser("~")) / ".ssh" / "config"
    with open(ssh_config, "r") as f:
        content = f.read()

    line = "Include ~/.pytorch-ondemand/ssh_config"
    if line in content:
        return

    content = line + "\n\n" + content

    with open(ssh_config, "w") as f:
        f.write(content)


def write_ssh_configs() -> None:
    add_ssh_config_include()

    def gen_ssh_config(name: str, hostname: str, key: Path) -> str:
        return textwrap.dedent(
            f"""
            Host {name}
                User ubuntu
                IdentityFile {str(key)}
                Hostname {str(hostname)}
                ControlMaster auto
                ControlPath {str((SOCKETS_DIR / name).resolve())}
                ControlPersist 600

        """
        ).strip()

    saved_instances = gen_saved_instances()
    output = ""
    for instance, data in saved_instances.items():
        output += (
            gen_ssh_config(instance, data["hostname"], Path(data["key_path"])) + "\n\n"
        )

    with open(SSH_CONFIG_PATH, "w") as f:
        f.write(output)
