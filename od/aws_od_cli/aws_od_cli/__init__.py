import sys
import os
import click
import textwrap
import subprocess

from pathlib import Path


def cmd(s, **kwargs):
    print(s)
    subprocess.run(s, shell=True, **kwargs)


@click.group()
def cli():
    pass



@cli.command()
def list_disk():
    if sys.platform == "darwin":
        subprocess.run(["diskutil", "list"])
    else:
        raise RuntimeError(f"{sys.platform} not supported")


@cli.command()
@click.option("--volume", required=True)
@click.option("--ssid", required=True)
def wifi(volume, ssid):
    """
    Write wifi information to SD card and enable SSH

    raspi-tool wifi --volume /Volumes/boot --ssid mywifi
    """
    print("WiFi Password: ", end="")
    password = input()

    supplicant = textwrap.dedent(f"""
        ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
        country=US
        update_config=1

        network={{
          ssid="{ssid}"
          psk="{password}"
        }}
    """).lstrip()

    with open(Path(volume) / "wpa_supplicant.conf", "w") as f:
        f.write(supplicant)
    
    with open(Path(volume) / "ssh", "w") as f:
        f.write("")


@cli.command()
@click.option("--dest", required=True)
def setup_config(dest):
    def ssh(cmd):
        split_cmd = ["ssh", dest] + cmd.split(" ")
        print(" ".join(split_cmd))
        subprocess.run(split_cmd)

    ssh("sudo raspi-config nonint do_ssh 1")
    ssh("sudo raspi-config nonint do_camera 1")
    ssh("sudo reboot")



@cli.command()
@click.option("--image", required=True)
@click.option("--disk", required=True)
@click.option("--bs", default="4m")
def flash_sd(image, disk, bs):
    image = Path(image)
    if sys.platform == "darwin":
        cmd(f"diskutil unmountDisk {disk}")
    
    cmd(f"sudo dd bs={bs} if={image} of={disk}")


if __name__ == '__main__':
    cli()