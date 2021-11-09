# -*- coding: utf-8 -*-

import time
import json
import tempfile
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
    find_key,
    gen_config,
    gen_key_path,
    get_instances_for_user,
    get_name,
    instance_by_id,
    ok,
    log,
    run_cmd,
    username,
    TimedText,
)


def find_ami(gpu: bool, windows: bool) -> Dict[str, Any]:
    ami = None
    ami_name = "pytorch-ondemand-ami"
    if gpu:
        ami_name = "pytorch-ondemand-ami-gpu"
    with yaspin.yaspin(text=TimedText("Finding recent AMI")) as spinner:
        amis = ec2().describe_images(Owners=["self"])
        for image in amis["Images"]:
            if image["Name"] == ami_name:
                ami = image
                break

        if ami is None:
            fail(spinner)
            raise RuntimeError("Unable to locate on-demand ami")
        else:
            ok(spinner)

    return cast(Dict[str, Any], ami)


def find_or_create_ssh_key() -> Path:
    with yaspin.yaspin(text=TimedText("Finding SSH key pair")) as spinner:
        key_path = gen_key_path()
        if key_path.exists():
            # key already exists
            pass
        else:
            create_key_pair(key_path)

        ok(spinner)

    return key_path


def gen_windows_startup_script(privkey: Path) -> str:
    r = run_cmd(["ssh-keygen", "-y", "-f", privkey], stdout=subprocess.PIPE)
    pubkey = r.stdout.decode()
    return textwrap.dedent(
        f"""
        <powershell>
        Add-Content -Path C:\ProgramData\ssh\administrators_authorized_keys -Value '{pubkey}'
        </powershell>
    """
    ).strip()


def gen_startup_script() -> str:
    config = gen_config()
    # Install user specific things
    # * oauth token for pushes
    # * user files
    pytorch_fork = ""
    if config.get("pytorch_fork", "").strip() != "":
        pytorch_fork = f"""
        git remote add me https://{config['github_username']}:{config['github_oauth']}@github.com/{config['pytorch_fork']}.git
        """

    parts = [
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
        """,
        pytorch_fork,
        f"""
        sudo -u ubuntu bash -c 'export PATH="/home/ubuntu/miniconda3/bin:$PATH" && echo {config['github_oauth']} | gh auth login --with-token'

        echo done > /home/ubuntu/.done.log
        """,
    ]
    parts = [textwrap.dedent(part).strip() for part in parts]
    script = "\n".join(parts) + "\n"
    return script


def find_security_group(name: str) -> str:
    with yaspin.yaspin(text=TimedText("Finding security group")) as spinner:
        response = ec2().describe_security_groups(
            Filters=[{"Name": "tag:Name", "Values": [name]}]
        )
        response = response["SecurityGroups"]
        if len(response) == 0:
            raise RuntimeError(f"Group {name} not found")

        ok(spinner)

    return str(response[0]["GroupId"])


def scp(ssh_dest: str, path: str) -> str:
    with tempfile.NamedTemporaryFile() as f:
        cmd = ["scp", f"{ssh_dest}:{path}", f.name]
        log(f"Running {cmd}")
        subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        with open(f.name) as f_r:
            return f_r.read()


def sync_files_to_local(ssh_dest: str, files: List[Dict[str, Any]]) -> None:
    record = json.loads(scp(ssh_dest, "~/.copy_record"))
    log("Syncing files down with")
    log(str(record))
    files = [x for x in files if x.get("is_two_way", False)]
    with yaspin.yaspin(text=TimedText("Syncing down remote files")) as spinner:
        for data in files:
            remote_lines = scp(ssh_dest, data["dest_path"]).split("\n")
            line_to_start_keeping = record[data["name"]]
            remote_lines = remote_lines[line_to_start_keeping:]
            if len(remote_lines) == 0:
                log(f"No changes for {data}")
                continue
            log(f"Writing remote content {data}")
            remote_content = "\n".join(remote_lines).strip()

            with open(data["source_path"], "a") as f:
                f.write("\n")
                f.write(remote_content)
                f.write("\n")
            ok(spinner)


def create_instance(
    ami: Dict[str, Any],
    key_path: Path,
    instance_type: str,
    use_startup_script: bool,
    windows: bool,
    security_group: str,
    volume_size: int,
) -> Tuple[Dict[str, Any], str]:
    with yaspin.yaspin(text=TimedText("Starting EC2 instance")) as spinner:
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
        startup_script = gen_startup_script() if use_startup_script else ""
        if windows:
            startup_script = gen_windows_startup_script(key_path)
        instance = ec2().run_instances(
            BlockDeviceMappings=[
                {
                    "DeviceName": "/dev/sda1",
                    "Ebs": {
                        "DeleteOnTermination": True,
                        "VolumeSize": volume_size,
                        "VolumeType": "gp2",
                    },
                },
            ],
            Placement={"AvailabilityZone": "us-west-2a"},
            ImageId=ami["ImageId"],
            MinCount=1,
            MaxCount=1,
            KeyName=key_path.name,
            InstanceType=instance_type,
            UserData=startup_script,
            TagSpecifications=[
                {
                    "ResourceType": "instance",
                    "Tags": [
                        {"Key": "Environment", "Value": "pytorch-ondemand"},
                        {"Key": "pytorch-ondemand", "Value": username()},
                        {"Key": "Name", "Value": name},
                    ],
                }
            ],
            Monitoring={"Enabled": False},
            SecurityGroupIds=[security_group],
        )
        ok(spinner)

    return instance, name


def wait_for_ip_address(instance: Dict[str, Any]) -> Dict[str, Any]:
    id = instance["InstanceId"]
    i = 0
    conditions = {"ip": False, "running": False}

    with yaspin.yaspin(text=TimedText("Waiting for instance IP address")) as spinner:
        while i < 100:
            fresh_instance = instance_by_id(id)
            if fresh_instance is None:
                continue
                # raise RuntimeError(f"Expected instance {id} to exist")
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

    with yaspin.yaspin(text=TimedText("Waiting for SSH access")) as spinner:
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


def lines_in_file(path: str) -> int:
    with open(path) as f:
        return f.read().count("\n")


def copy_files(ssh_dest: str, files: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    with yaspin.yaspin(text=TimedText("Copying config files")) as spinner:
        for file_data in files:
            dest = Path(file_data["dest_path"])
            cmd = [
                "ssh",
                ssh_dest,
                "mkdir",
                "-p",
                str(dest.parent),
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            cmd = [
                "scp",
                file_data["source_path"],
                f"{ssh_dest}:{str(dest)}",
            ]
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Keep the file sizes of when we copied it so we know where to start reading
        # in the new configs
        record = {}
        for file_data in files:
            record[file_data["name"]] = lines_in_file(file_data["source_path"])

        with tempfile.NamedTemporaryFile() as f:
            with open(f.name, "w") as f_w:
                json.dump(record, f_w, indent=2)
            run_cmd(
                ["scp", f.name, f"{ssh_dest}:~/.copy_record"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

        ok(spinner)

    return files


def add_ssh_config_include() -> None:
    ssh_config = Path(os.path.expanduser("~")) / ".ssh" / "config"
    with open(ssh_config, "r") as f:
        content = f.read()

    line = "Include ~/.aws_od_cli/ssh_config"
    if line in content:
        return

    content = line + "\n\n" + content

    with open(ssh_config, "w") as f:
        f.write(content)


def write_ssh_configs(instance: Dict[str, Any]) -> None:
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

    output = ""
    if instance["PublicDnsName"].strip() == "":
        raise RuntimeError("Could not add instance since hostname was empty")

    key = find_key(instance["KeyName"])
    output += (
        gen_ssh_config(instance["InstanceId"], instance["PublicDnsName"], key) + "\n\n"
    )

    with open(SSH_CONFIG_PATH, "w") as f:
        f.write(output)
