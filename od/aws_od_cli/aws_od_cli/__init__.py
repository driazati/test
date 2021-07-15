# -*- coding: utf-8 -*-

from typing import Optional
import click
import textwrap
import subprocess
import json
import tabulate
import yaspin

from aws_od_cli.create import (
    find_ami,
    find_or_create_ssh_key,
    create_instance,
    wait_for_ip_address,
    wait_for_ssh_access,
    write_ssh_configs,
    copy_files,
)
from aws_od_cli.utils import (
    init,
    save_instance,
    ec2,
    instance_for_id_or_name,
    instance_for_id_or_name_or_guess,
    stop_instances,
    ok,
    fail,
    locate_vscode,
    FILES_PATH,
    get_instances_for_user,
    username,
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
def create(no_login: bool, no_files: bool, rm: bool) -> None:
    """
    Create a new on-demand

    TODO: this doesn't work when Packer is updating the AMI (since it goes into
    pending status), there should be a fallback AMI that's the old one
    """
    if no_login and rm:
        raise RuntimeError(
            "--rm can only be used when auto-ssh is enabled, so remove the --no-login flag"
        )

    ami = find_ami()
    key_path = find_or_create_ssh_key()
    instances, name = create_instance(ami, key_path)
    instance = instances["Instances"][0]

    save_instance(instance, key_path)

    instance = wait_for_ip_address(instance)

    # Re-save to get DNS name in
    save_instance(instance, key_path)
    write_ssh_configs()

    instance = wait_for_ssh_access(instance)
    ssh_dest = instance["InstanceId"]

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
        cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            ssh_dest,
        ]
        subprocess.run(cmd)

        if rm:
            ec2().terminate_instances(InstanceIds=[instance["InstanceId"]])


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

    stop_instances(action, ids_to_stop)


@click.option("--id")
@click.option("--name")
@cli.command()
def vscode(id: Optional[str], name: Optional[str]) -> None:
    """
    Launch vscode for a remote

    If you only have a single on-demand the --id or --name flags aren't necessary.
    Also see 'aws_od_cli list'.
    """
    code_exe = locate_vscode()
    instance = instance_for_id_or_name_or_guess(id, name)
    name = instance["InstanceId"]
    subprocess.run(
        [
            code_exe,
            "--folder-uri",
            f"vscode-remote://ssh-remote+{name}/home/ubuntu/pytorch",
        ]
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
    subprocess.run(["ssh", "-o", "StrictHostKeyChecking=no", instance["InstanceId"]])


@click.option("--add")
@click.option("--remove-id")
@click.option("--list", is_flag=True)
@cli.command()
def configs(add: Optional[str], remove_id: Optional[str], list: bool) -> None:
    """
    Manage files to copy to on-demand instances (dotfiles, etc)
    """
    if add is not None:
        add_file(path=add)

    if remove_id is not None:
        remove_file(id=remove_id)

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


@click.option("--full", is_flag=True, help="Show more info about each on-demand")
@cli.command()
def list(full: bool) -> None:
    """
    List all your on-demands
    """
    rows = get_live_ondemands(full=full)

    if len(rows) == 0:
        print("No on-demands found! Start one with 'aws_od_cli create'")
    else:
        print(
            tabulate.tabulate(
                [d.values() for d in rows], headers=[k for k in rows[0].keys()]
            )
        )


@cli.command()
def rage() -> None:
    """
    Output logs from the most recent few runs
    """
    raise NotImplementedError()


if __name__ == "__main__":
    cli()
