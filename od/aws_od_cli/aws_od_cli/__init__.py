# -*- coding: utf-8 -*-

from typing import Optional, Any, Dict
import click
import textwrap
import json
import tabulate
import sys
import os
import yaspin

from aws_od_cli.create import (
    find_ami,
    find_or_create_ssh_key,
    create_instance,
    find_security_group,
    wait_for_ip_address,
    wait_for_ssh_access,
    write_ssh_configs,
    copy_files,
)
from aws_od_cli.utils import (
    SSH_CONFIG_PATH,
    init,
    ec2,
    instance_for_id_or_name,
    instance_for_id_or_name_or_guess,
    stop_instances,
    ok,
    fail,
    locate_vscode,
    FILES_PATH,
    LOGS_DIR,
    get_instances_for_user,
    username,
    save_config,
    gen_config,
    log,
    init_logger,
    run_cmd,
    get_logger,
    TimedText
)
from aws_od_cli.list import get_live_ondemands
from aws_od_cli.configs import add_file, remove_file, list_files


@click.group()
def cli() -> None:
    """
    Create and manage PyTorch OSS On-Demand machines. Machines are provisioned in
    AWS based on the most recent build of the 'viable/strict' branch of PyTorch.

    This tool provisions SSH keys so only you are able to log in and verifies
    that you are an active FB employee. A GitHub OAuth token is required to
    enable pushing from the on-demand's PyTorch repo.

    Note: On-demands are stopped every night at 3 AM PST. A stopped on-demand's
    data will still be there when it is re-started. Once an on-demand has not
    been started for 3 days it will be permanently terminated (and the data will
    be lost). TODO: This is unimplemented
    """
    init()
    init_logger()

    def exception_handler(exc_type: Any, exc_value: Any, exc_traceback: Any) -> None:
        get_logger().error(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback)
        )
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = exception_handler
    log(f"Invoked with: {sys.argv}")


@cli.command()
def sync() -> None:
    """
    Clear SSH config to match local state to what's in AWS

    1. Clean out stale entries from ~/.aws_od_cli/instances.json
    2. Clean out stale entries from ~/.aws_od_cli/ssh_config
    """
    instances = get_instances_for_user(username())
    with open(SSH_CONFIG_PATH, "w") as f:
        f.write("")

    for instance in instances:
        if instance["State"]["Name"] == "running":
            write_ssh_configs(instance)

    print(f"Synced {SSH_CONFIG_PATH}")


@click.option(
    "--no-login", is_flag=True, help="Skip automatic SSH once on-demand is up"
)
@click.option(
    "--no-files", is_flag=True, help="Skip copying files from 'aws_od_cli configs'"
)
@click.option(
    "--no-rm",
    is_flag=True,
    help="Don't stop the on-demand once the SSH session is exited",
)
@click.option("--gpu", is_flag=True, help="Make default GPU instance")
@click.option(
    "--instance-type", "user_instance_type", help="Directly specify instance type"
)
@click.option("--ami", "user_ami", help="Directly specify AMI")
@click.option("--volume_size", type=int, default=50, help="Instance volume size in GB")
@cli.command()
def create(
    no_login: bool,
    no_files: bool,
    no_rm: bool,
    gpu: bool,
    user_ami: Optional[str],
    user_instance_type: Optional[str],
    volume_size: int,
) -> None:
    """
    Create a new on-demand

    TODO: this doesn't work when Packer is updating the AMI (since it goes into
    pending status), there should be a fallback AMI that's the old one
    """
    rm = not no_rm
    if no_login and rm:
        raise RuntimeError(
            "--rm can only be used when auto-ssh is enabled, so remove the --no-login flag"
        )
    instance_type = "c5a.4xlarge"
    if gpu and user_instance_type is not None:
        raise RuntimeError("Cannot use both --gpu and --instance-type")
    if gpu and user_ami is not None:
        raise RuntimeError("Cannot use both --gpu and --ami")

    if gpu:
        instance_type = "g4dn.8xlarge"
    if user_instance_type is not None:
        instance_type = user_instance_type

    log(f"Using instance_type {instance_type}")

    if user_ami is not None:
        ami = {"ImageId": user_ami}
    else:
        ami = find_ami(gpu=gpu)

    log(f"Using ami {ami}")

    key_path = find_or_create_ssh_key()
    log(f"Using key {key_path}")

    # TODO: corp net sec group
    security_group = find_security_group("ondemand_ssh_and_mosh")

    # Make the instance via boto3
    instances, name = create_instance(
        ami,
        key_path,
        instance_type,
        use_startup_script=not no_files,
        security_group=security_group,
        volume_size=volume_size,
    )
    instance = instances["Instances"][0]

    log(f"Made instance {instance}")

    # Get it's DNS name and write it to an SSH config for later access
    instance = wait_for_ip_address(instance)
    write_ssh_configs(instance)

    # Spin until ssh <instance-id> runs successfully
    instance = wait_for_ssh_access(instance)
    ssh_dest = instance["InstanceId"]

    log(f"Using SSH destination {ssh_dest}")

    if not no_files:
        with open(FILES_PATH, "r") as f:
            files = json.load(f)

        copy_files(instance, files)

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
        ssh_impl(ssh_dest)

        if rm:
            was_stopped = ask_to_stop_instance(instance)
            if not was_stopped:
                print(
                    "Manual actions:\n"
                    f"    SSH: aws_od_cli ssh --name {name}\n"
                    f" Remove: aws_od_cli stop --name {name}\n"
                )


def ask_to_stop_instance(instance: Dict[str, Any]) -> bool:
    print("Delete this instance? (y/n): ", end="")
    response = input().lower()
    if response in {"y", "yes", "ok"}:
        with yaspin.yaspin(text=TimedText("Stopping instance")) as spinner:
            ec2().terminate_instances(InstanceIds=[instance["InstanceId"]])
            ok(spinner)
        return True
    else:
        return False


def ssh_impl(ssh_dest: str) -> None:
    login_command = gen_config().get("login_command", None)
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        ssh_dest,
    ]
    if login_command is not None:
        cmd += [x.strip() for x in login_command.split(",")]
    run_cmd(cmd)


@click.option("--name")
@click.option("--id")
@click.option("--all", is_flag=True)
@click.option("--action", default="terminate")
@cli.command()
def stop(name: Optional[str], all: bool, id: Optional[str], action: str) -> None:
    """
    Delete an on-demand. Use '--action stop' to pause an on-demand, or leave this
    option off to permanently terminate an on-demand.
    """
    with yaspin.yaspin(text=TimedText("Gathering instances")) as spinner:
        user_instances = get_instances_for_user(username())
        ids_to_stop = []
        if all:
            log("Stopping all instances")
            for instance in user_instances:
                ids_to_stop.append(instance["InstanceId"])
        else:
            to_stop = instance_for_id_or_name(id, name, user_instances)

            if to_stop is None:
                raise RuntimeError(f"Instance {name} not found")

            ids_to_stop.append(to_stop["InstanceId"])

        ok(spinner)

    log(f"Setting instances {ids_to_stop} to {action}")
    stop_instances(action, ids_to_stop)


@click.option("--id")
@click.option("--name")
@click.option("--folder")
@cli.command()
def vscode(id: Optional[str], name: Optional[str], folder: Optional[str]) -> None:
    """
    Launch vscode for a remote

    If you only have a single on-demand the --id or --name flags aren't necessary.
    Also see 'aws_od_cli list'.
    """
    code_exe = locate_vscode()
    log(f"Found VSCode at {code_exe}")
    instance = instance_for_id_or_name_or_guess(id, name)
    name = instance["InstanceId"]
    if folder is None:
        folder = "/home/ubuntu/pytorch"

    run_cmd(
        [str(code_exe), "--folder-uri", f"vscode-remote://ssh-remote+{name}{folder}"]
    )


@click.option("--id")
@click.option("--name")
@cli.command()
def ssh(id: Optional[str], name: Optional[str]) -> None:
    """
    SSH into a running on-demand

    If you only have a single on-demand the --id or --name flags aren't necessary.
    Also see 'aws_od_cli list'.
    """
    # TODO: stop instance when exiting, start instnace before ssh-ing in
    instance = instance_for_id_or_name_or_guess(id, name)
    ssh_impl(instance["InstanceId"])
    ask_to_stop_instance(instance)


@click.option("--add")
@click.option("--login-command")
@click.option("--remove-id")
@click.option("--list", is_flag=True)
@cli.command()
def configs(
    add: Optional[str],
    login_command: Optional[str],
    remove_id: Optional[str],
    list: bool,
) -> None:
    """
    Manage files to copy to on-demand instances (dotfiles, etc)
    """
    if add is not None:
        add_file(path=add)

    if remove_id is not None:
        remove_file(id=remove_id)

    if login_command is not None:
        save_config(name="login_command", value=login_command)

    if list:
        rows = list_files()

        if len(rows) == 0:
            print("No files yet, use 'aws_od_cli configs --add <some file>")
        else:
            print(
                tabulate.tabulate(
                    [d.values() for d in rows], headers=[k for k in rows[0].keys()]
                )
            )
        login_command = gen_config().get("login_command", None)
        if login_command is not None:
            print(f"Login command: {login_command.split(',')}")


@click.option("--full", is_flag=True, help="Show more info about each on-demand")
@cli.command()
def list(full: bool) -> None:
    """
    List all your current on-demands
    """
    log(f"Fetching full: {full}")
    rows = get_live_ondemands(full=full)
    log(f"{rows}")

    if len(rows) == 0:
        print("No on-demands found! Start one with 'aws_od_cli create'")
    else:
        print(
            tabulate.tabulate(
                [d.values() for d in rows], headers=[k for k in rows[0].keys()]
            )
        )


@click.option("--number", type=int, help="Number of recent logs to output", default=1)
@cli.command()
def rage(number: int) -> None:
    """
    Output the logs from the most recent few runs
    """
    logs = LOGS_DIR.glob("rage-*")
    paths = [x for x in reversed(sorted(logs, key=os.path.getmtime))]
    paths = paths[1 : number + 1]
    for x in paths:
        with open(x) as f:
            print(f.read())


if __name__ == "__main__":
    cli()
